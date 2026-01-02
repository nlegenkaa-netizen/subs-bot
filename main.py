import os
import re
import sqlite3
import logging
import calendar
from typing import Optional, Tuple
from datetime import datetime, date

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)

# -----------------------------
# CONFIG
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "subs.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
ADD_NAME, ADD_PRICE, ADD_DATE = range(3)

DEFAULT_CURRENCY = "NOK"
DEFAULT_PERIOD = "monthly"  # monthly / yearly


# -----------------------------
# DB
# -----------------------------
def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,              -- packed "130.17|NOK"
            day INTEGER NOT NULL,             -- day of month 1-31
            period TEXT NOT NULL DEFAULT 'monthly',
            last_charge_date TEXT,            -- YYYY-MM-DD
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def add_subscription(
    user_id: int,
    name: str,
    price: str,
    day: int,
    period: str,
    last_charge_date: str,
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subscriptions (user_id, name, price, day, period, last_charge_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, name, price, day, period, last_charge_date),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def list_subscriptions(user_id: int) -> list[tuple]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, price, day, period, last_charge_date
        FROM subscriptions
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def update_period(sub_id: int, user_id: int, period: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE subscriptions
        SET period = ?
        WHERE id = ? AND user_id = ?
        """,
        (period, sub_id, user_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# -----------------------------
# HELPERS (date, price, UI)
# -----------------------------
MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def format_date_ru(dt: date) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"


def clamp_day(year: int, month: int, wanted_day: int) -> int:
    last_day = calendar.monthrange(year, month)[1]
    return min(max(1, wanted_day), last_day)


CURRENCY_ALIASES = {
    "nok": "NOK",
    "kr": "NOK",
    "кр": "NOK",
    "eur": "EUR",
    "€": "EUR",
    "usd": "USD",
    "$": "USD",
    "rub": "RUB",
    "руб": "RUB",
    "₽": "RUB",
}


def parse_price(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (amount_str_with_dot, currency_or_None) или (None, None)."""
    if not text:
        return None, None
    t = text.strip().lower()

    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", t)
    if not m:
        return None, None

    amount = m.group(1).replace(",", ".")
    currency = None
    for k, v in CURRENCY_ALIASES.items():
        if k in t:
            currency = v
            break

    return amount, currency


def parse_ru_date(text: str) -> Optional[date]:
    """Поддерживает 29.12.25 и 29.12.2025"""
    if not text:
        return None
    t = text.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def pack_price(amount: float, currency: str) -> str:
    return f"{amount:.2f}|{currency}"


def unpack_price(packed: str) -> Tuple[float, str]:
    # "130.17|NOK"
    parts = (packed or "").split("|")
    if len(parts) == 2:
        try:
            return float(parts[0]), parts[1]
        except ValueError:
            pass
    return 0.0, DEFAULT_CURRENCY


def format_price(amount: float, currency: str) -> str:
    # красивый вывод с запятой для RU
    amount_txt = f"{amount:.2f}".replace(".", ",")
    if currency == "NOK":
        return f"{amount_txt} NOK"
    if currency == "EUR":
        return f"{amount_txt} EUR"
    if currency == "USD":
        return f"{amount_txt} USD"
    if currency == "RUB":
        return f"{amount_txt} RUB"
    return f"{amount_txt} {currency}"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Добавить")],
            [KeyboardButton("📋 Список")],
        ],
        resize_keyboard=True,
    )


def period_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    # callback_data: period:<id>:monthly
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Ежемесячно", callback_data=f"period:{sub_id}:monthly"),
                InlineKeyboardButton("Ежегодно", callback_data=f"period:{sub_id}:yearly"),
            ]
        ]
    )


# -----------------------------
# BOT COMMANDS / START / LIST
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет!\n\n"
        "Я помогу удобно следить за подписками 💳\n\n"
        "Нажми кнопку снизу — и поехали 👇",
        reply_markup=main_menu_keyboard(),
    )


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text(
            "Пока подписок нет.\nНажми ➕ Добавить 🙂",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = ["📋 Твои подписки:\n"]
    for sub_id, name, price_packed, day, period, last_charge_date in rows:
        amount, cur = unpack_price(price_packed)
        price_view = format_price(amount, cur)
        per = "ежемесячно" if period == "monthly" else "ежегодно"
        last_txt = ""
        if last_charge_date:
            try:
                dt = datetime.fromisoformat(last_charge_date).date()
                last_txt = f" • последнее: {format_date_ru(dt)}"
            except Exception:
                last_txt = f" • последнее: {last_charge_date}"

        lines.append(f"#{sub_id} • {name} • 💰 {price_view} • 📅 {day}-го • {per}{last_txt}")

    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


# -----------------------------
# ADD FLOW (START -> NAME -> PRICE -> DATE)
# -----------------------------
async def add_flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # очищаем хвосты предыдущего добавления
    for k in ("add_name", "add_amount", "add_currency", "add_day", "add_last_date", "add_period"):
        context.user_data.pop(k, None)

    await update.message.reply_text(
        "Как называется подписка?\n"
        "Примеры: Netflix / OpenAI / Spotify",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_NAME


async def add_flow_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Напиши название подписки 🙂", reply_markup=main_menu_keyboard())
        return ADD_NAME

    context.user_data["add_name"] = name

    await update.message.reply_text(
        "Сколько списывается?\n"
        "Примеры:\n"
        "• 128.30\n"
        "• 12,99 евро\n"
        "• 1805,90 кр\n"
        "• 199,5 руб",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_PRICE


async def add_flow_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()

    amount_str, currency = parse_price(raw)
    if not amount_str:
        await update.message.reply_text(
            "Не поняла цену 😕\n"
            "Примеры: 128.30 | 12,99 евро | 1805,90 кр | 199,5 руб",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_PRICE

    context.user_data["add_amount"] = float(amount_str)
    context.user_data["add_currency"] = currency or DEFAULT_CURRENCY

    await update.message.reply_text(
        "Когда было (или будет) последнее списание?\n"
        "Можно так:\n"
        "• 29.12.25\n"
        "• 29.12.2025",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_DATE


async def add_flow_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    name = context.user_data.get("add_name")
    amount = context.user_data.get("add_amount")
    currency = context.user_data.get("add_currency") or DEFAULT_CURRENCY

    # страховка — чтобы не падало
    if not name or amount is None:
        await update.message.reply_text(
            "Кажется, я потерял данные подписки 😕\nНажми ➕ Добавить и попробуем ещё раз",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    raw = (update.message.text or "").strip()
    last_dt = parse_ru_date(raw)

    # strict: only full date accepted
    if not last_dt:
        await update.message.reply_text(
            "Я не поняла дату 😕\n"
            "Напиши дату в формате:\n"
            "• 29.12.25\n"
            "• 29.12.2025",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_DATE

    day = last_dt.day
    context.user_data["add_day"] = day
    context.user_data["add_last_date"] = last_dt.isoformat()  # YYYY-MM-DD

    period = DEFAULT_PERIOD
    context.user_data["add_period"] = period

    price_packed = pack_price(float(amount), currency)

    new_id = add_subscription(
        user_id=user_id,
        name=name,
        price=price_packed,
        day=int(day),
        period=period,
        last_charge_date=context.user_data["add_last_date"],
    )

    price_view = format_price(float(amount), currency)
    last_date_text = f"\n📌 последнее списание: {format_date_ru(last_dt)}"

    await update.message.reply_text(
        "Готово ✅\n"
        f"#{new_id} • {name}\n"
        f"💰 {price_view}\n"
        f"📅 день списания: {day}-го"
        f"{last_date_text}\n\n"
        "Как часто списывается?",
        reply_markup=period_keyboard(new_id),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ок, отмена 🙂", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# -----------------------------
# CALLBACKS
# -----------------------------
async def on_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    # period:<id>:monthly
    try:
        _, sub_id_str, period = data.split(":")
        sub_id = int(sub_id_str)
    except Exception:
        await query.edit_message_text("Не поняла выбор периода 😕")
        return

    if period not in ("monthly", "yearly"):
        await query.edit_message_text("Неизвестный период 😕")
        return

    ok = update_period(sub_id=sub_id, user_id=user_id, period=period)
    if not ok:
        await query.edit_message_text("Не нашла эту подписку 😕")
        return

    per_txt = "ежемесячно" if period == "monthly" else "ежегодно"
    await query.edit_message_text(f"Супер ✅ Период обновлён: {per_txt}")


# -----------------------------
# ERROR HANDLER
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    # безопасный ответ пользователю, если это был message-update
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Упс, ошибка 😕 Напиши /start.", reply_markup=main_menu_keyboard())


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))

    # menu buttons
    app.add_handler(MessageHandler(filters.Regex(r"^📋 Список$"), show_list))

    # add conversation
    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Добавить$"), add_flow_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_price)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(add_conv)

    # inline callbacks
    app.add_handler(CallbackQueryHandler(on_period_callback, pattern=r"^period:\d+:(monthly|yearly)$"))

    # errors
    app.add_error_handler(error_handler)

    app.run_polling()


if __name__ == "__main__":
    main()
