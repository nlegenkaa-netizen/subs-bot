import os
import sqlite3
import logging
import calendar
from datetime import date

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
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

# Conversation states for /edit
EDIT_CHOOSE_FIELD, EDIT_ENTER_VALUE = range(2)


# -----------------------------
# DATE HELPERS for /next
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


def clamp_day(year: int, month: int, wanted_day: int) -> int:
    """If wanted_day doesn't exist in that month, clamp to last day of month."""
    last_day = calendar.monthrange(year, month)[1]
    return min(max(1, wanted_day), last_day)


def next_charge_date(day_of_month: int, today: date) -> date:
    """
    Returns the next charge date for a subscription with 'day_of_month'.
    If the day doesn't exist in current/next month, clamps to month end.
    """
    y, m = today.year, today.month
    d_this_month = clamp_day(y, m, day_of_month)
    candidate = date(y, m, d_this_month)

    if candidate < today:
        # move to next month
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        d_next = clamp_day(y, m, day_of_month)
        candidate = date(y, m, d_next)

    return candidate


def format_date_ru(dt: date) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"


def days_word_ru(n: int) -> str:
    # 1 день, 2-4 дня, 5-20 дней, 21 день, 22-24 дня, 25-30 дней...
    n_abs = abs(n)
    if 11 <= (n_abs % 100) <= 14:
        return "дней"
    last = n_abs % 10
    if last == 1:
        return "день"
    if last in (2, 3, 4):
        return "дня"
    return "дней"


SUPPORTED_CURRENCIES = {"NOK", "EUR", "USD", "RUB", "SEK", "DKK", "GBP"}
DEFAULT_CURRENCY = "NOK"

CURRENCY_SYMBOL = {
    "NOK": "NOK",
    "EUR": "€",
    "USD": "$",
    "RUB": "₽",
    "SEK": "SEK",
    "DKK": "DKK",
    "GBP": "£",
}

def parse_price(input_str: str) -> tuple[float, str] | None:
    """
    Accepts:
    - "129"
    - "129 NOK"
    - "12.99 EUR"
    - "199,5 rub"
    Returns (amount, currency) or None
    """
    s = (input_str or "").strip()
    if not s:
        return None

    parts = s.split()
    if len(parts) == 1:
        amount_str = parts[0]
        currency = DEFAULT_CURRENCY
    elif len(parts) == 2:
        amount_str = parts[0]
        currency = parts[1].upper()
    else:
        return None

    if currency not in SUPPORTED_CURRENCIES:
        return None

    # normalize decimal comma to dot and remove spaces
    amount_str = amount_str.replace(",", ".").replace(" ", "")
    try:
        amount = float(amount_str)
        if amount <= 0:
            return None
    except ValueError:
        return None

    return amount, currency


def format_price(amount: float, currency: str) -> str:
    """
    129 -> '129,00 NOK'
    12.99 EUR -> '€12,99'
    """
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    # format with 2 decimals, comma separator
    s = f"{amount:,.2f}"
    # python uses ',' for thousands and '.' for decimals in default locale:
    # convert: 1,234.50 -> 1 234,50
    s = s.replace(",", " ").replace(".", ",")

    if currency in {"EUR", "USD", "GBP"}:
        return f"{symbol}{s}"
    else:
        return f"{s} {symbol}"


def pack_price(amount: float, currency: str) -> str:
    """Store canonical in DB as '129.00 NOK' with dot decimal."""
    return f"{amount:.2f} {currency}"


def unpack_price(price_text: str) -> tuple[float, str] | None:
    """Read from DB '129.00 NOK' -> (129.0, 'NOK')"""
    if not price_text:
        return None
    parts = price_text.strip().split()
    if len(parts) != 2:
        return None
    try:
        amount = float(parts[0])
    except ValueError:
        return None
    currency = parts[1].upper()
    return (amount, currency)



# -----------------------------
# DB LAYER
# -----------------------------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            day INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


def add_subscription(user_id: int, name: str, price: str, day: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO subscriptions (user_id, name, price, day) VALUES (?, ?, ?, ?)",
        (user_id, name, price, day),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def list_subscriptions(user_id: int) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day FROM subscriptions WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows  # [(id, name, price, day), ...]


def delete_subscription(user_id: int, sub_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
        (sub_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_subscription_by_id(user_id: int, sub_id: int) -> tuple | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day FROM subscriptions WHERE id = ? AND user_id = ?",
        (sub_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row  # (id, name, price, day) or None


def update_subscription_field(user_id: int, sub_id: int, field: str, value) -> bool:
    allowed = {"name", "price", "day"}
    if field not in allowed:
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        f"UPDATE subscriptions SET {field} = ? WHERE id = ? AND user_id = ?",
        (value, sub_id, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


# -----------------------------
# BOT COMMANDS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я бот для подписок.\n\n"
        "Команды:\n"
        "• /add <название> <цена> <день>\n"
        "  пример: /add Netflix 129 15\n"
        "• /list — список подписок\n"
        "• /del <id> — удалить подписку\n"
        "  пример: /del 3\n"
        "• /edit <id> — редактировать подписку\n"
        "  пример: /edit 3\n"
        "• /next — ближайшее списание\n"
        "• /cancel — отменить диалог\n"
    )


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Используй так: /add <название> <цена> <день>\n"
            "Пример: /add Netflix 129 15\n\n"
            "Если в названии пробелы — пока без пробелов (потом улучшим)."
        )
        return

    name = args[0]
    price_raw = args[1]
parsed = parse_price(price_raw)
if not parsed:
    await update.message.reply_text(
        "Цена должна быть числом или числом с валютой.\n"
        "Примеры:\n"
        "• /add Netflix 129 15\n"
        "• /add Spotify 12.99 EUR 5\n"
        "• /add YT 199,5 RUB 1"
    )
    return

amount, currency = parsed
price = pack_price(amount, currency)

    day_raw = args[2]

    try:
        day = int(day_raw)
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await update.message.reply_text("День должен быть числом от 1 до 31. Пример: /add Netflix 129 15")
        return

    new_id = add_subscription(user_id, name, price, day)
    await update.message.reply_text(
        f"Добавлено ✅\n"
        f"#{new_id} • {name} • {format_price(amount, currency)} • списание {day}-го"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Добавь: /add Netflix 129 15")
        return

    lines = ["Твои подписки:"]
    for _id, name, price, day in rows:
        pp = unpack_price(price)
if pp:
    amount, currency = pp
    price_view = format_price(amount, currency)
else:
    price_view = price  # fallback для старых записей

lines.append(f"#{_id} • {name} • {price_view} • день {day}")

    lines.append("\nРедактировать: /edit <id>  |  Удалить: /del <id>")
    await update.message.reply_text("\n".join(lines))


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Используй так: /del <id>\nПример: /del 3")
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /del 3")
        return

    ok = delete_subscription(user_id, sub_id)
    if ok:
        await update.message.reply_text(f"Удалено ✅ (#{sub_id})")
    else:
        await update.message.reply_text("Не нашла подписку с таким ID (или она не твоя).")


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Добавь: /add Netflix 129 15")
        return

    today = date.today()

    best = None
    # best: (charge_date, id, name, price, day)
    for _id, name, price, day in rows:
        ch = next_charge_date(int(day), today)
        item = (ch, _id, name, price, day)
        if best is None or item[0] < best[0]:
            best = item

    charge_date, _id, name, price, day = best
    delta_days = (charge_date - today).days

    when_line = format_date_ru(charge_date)
    in_days = f"через {delta_days} {days_word_ru(delta_days)}" if delta_days != 0 else "сегодня"

    await update.message.reply_text(
        "Ближайшее списание 💳\n\n"
        f"{name} — {price}\n"
        f"📅 {when_line}\n"
        f"⏳ {in_days}\n\n"
        f"(ID: #{_id}, день списания: {day})"
    )


# -----------------------------
# /EDIT CONVERSATION
# -----------------------------
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("Используй так: /edit <id>\nНапример: /edit 3")
        return ConversationHandler.END

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /edit 3")
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Не нашла подписку с таким ID (или она не твоя).")
        return ConversationHandler.END

    context.user_data["edit_id"] = sub_id

    _id, name, price, day = sub
    await update.message.reply_text(
        f"Редактируем подписку #{_id}:\n"
        f"• Название: {name}\n"
        f"• Цена: {price}\n"
        f"• День списания: {day}\n\n"
        f"Что меняем? Напиши: name / price / day\n"
        f"Или /cancel чтобы отменить."
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()

    if text not in ("name", "price", "day"):
        await update.message.reply_text("Выбери поле: name / price / day (напиши одним словом).")
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = text

    prompts = {
        "name": "Ок. Введи новое название (например: Netflix).",
        "price": "Ок. Введи новую цену (пока просто текст, например: 129).",
        "day": "Ок. Введи новый день списания (1–31).",
    }
    await update.message.reply_text(prompts[text])
    return EDIT_ENTER_VALUE


async def edit_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sub_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    if not sub_id or not field:
        await update.message.reply_text("Сломался контекст редактирования. Начни заново: /edit <id>")
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    if field == "day":
        try:
            day = int(raw)
            if not (1 <= day <= 31):
                raise ValueError
        except ValueError:
            await update.message.reply_text("День должен быть числом от 1 до 31. Введи ещё раз.")
            return EDIT_ENTER_VALUE
        value = day
    else:
        if not raw:
            await update.message.reply_text("Пустое значение нельзя. Введи ещё раз.")
            return EDIT_ENTER_VALUE
        value = raw

    ok = update_subscription_field(user_id, sub_id, field, value)
    if not ok:
        await update.message.reply_text("Не удалось обновить. Начни заново: /edit <id>")
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    _id, name, price, day = sub
    await update.message.reply_text(
        f"Готово ✅ Подписка #{_id} обновлена:\n"
        f"• Название: {name}\n"
        f"• Цена: {price}\n"
        f"• День списания: {day}"
    )

    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    await update.message.reply_text("Ок, отменено.")
    return ConversationHandler.END


# -----------------------------
# SAFETY: generic error handler
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Упс, ошибка 😕 Попробуй ещё раз или напиши /start.")
    except Exception:
        pass


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables.")

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_CHOOSE_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)
            ],
            EDIT_ENTER_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_enter_value)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("del", del_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(edit_conv)

    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
