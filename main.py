import os
import sqlite3
import logging
import calendar
from datetime import date

from telegram import (
    Update,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
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
    last_day = calendar.monthrange(year, month)[1]
    return min(max(1, wanted_day), last_day)


def next_charge_date(day_of_month: int, today: date) -> date:
    y, m = today.year, today.month
    d_this_month = clamp_day(y, m, day_of_month)
    candidate = date(y, m, d_this_month)

    if candidate < today:
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        d_next = clamp_day(y, m, day_of_month)
        candidate = date(y, m, d_next)

    return candidate


def format_date_ru(dt: date) -> str:
    # пример: 2 января 2026
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"


def days_word_ru(n: int) -> str:
    n_abs = abs(n)
    if 11 <= (n_abs % 100) <= 14:
        return "дней"
    last = n_abs % 10
    if last == 1:
        return "день"
    if last in (2, 3, 4):
        return "дня"
    return "дней"


# -----------------------------
# PRICE & CURRENCY HELPERS
# -----------------------------
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

# Алиасы валют (русский ввод + символы)
CURRENCY_ALIASES = {
    # RUB
    "руб": "RUB",
    "руб.": "RUB",
    "р": "RUB",
    "р.": "RUB",
    "рублей": "RUB",
    "₽": "RUB",
    # EUR
    "евро": "EUR",
    "€": "EUR",
    # NOK (кроны)
    "крона": "NOK",
    "кроны": "NOK",
    "крон": "NOK",
    "кр": "NOK",
    "кр.": "NOK",
    "nok": "NOK",
    # USD
    "доллар": "USD",
    "доллары": "USD",
    "дол": "USD",
    "дол.": "USD",
    "$": "USD",
    # GBP
    "фунт": "GBP",
    "фунты": "GBP",
    "£": "GBP",
}


def normalize_currency_token(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    low = t.lower()
    if low in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[low]
    return t.upper()


def is_currency_token(token: str) -> bool:
    return normalize_currency_token(token) in SUPPORTED_CURRENCIES


def parse_price(input_str: str) -> tuple[float, str] | None:
    """
    Accepts:
    - "129"
    - "129 NOK" / "129 nok"
    - "12.99 EUR" / "12.99 евро"
    - "199,5 руб"
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
        currency = normalize_currency_token(parts[1])
    else:
        return None

    if currency not in SUPPORTED_CURRENCIES:
        return None

    amount_str = amount_str.replace(",", ".").replace(" ", "")
    try:
        amount = float(amount_str)
        if amount <= 0:
            return None
    except ValueError:
        return None

    return amount, currency


def format_price(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency)

    s = f"{amount:,.2f}"
    s = s.replace(",", " ").replace(".", ",")

    if currency in {"EUR", "USD", "GBP"}:
        return f"{symbol}{s}"
    return f"{s} {symbol}"


def pack_price(amount: float, currency: str) -> str:
    # canonical: "129.00 NOK"
    return f"{amount:.2f} {currency}"


def unpack_price(price_text: str) -> tuple[float, str] | None:
    if not price_text:
        return None
    parts = price_text.strip().split()
    if len(parts) != 2:
        return None
    try:
        amount = float(parts[0])
    except ValueError:
        return None
    currency = normalize_currency_token(parts[1])
    if currency not in SUPPORTED_CURRENCIES:
        return None
    return amount, currency


# -----------------------------
# PERIOD HELPERS
# -----------------------------
DEFAULT_PERIOD = "month"  # month | year

def period_label(period: str) -> str:
    return "ежемесячно" if period == "month" else "ежегодно"


# -----------------------------
# DB LAYER
# -----------------------------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # базовая таблица (с period)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            day INTEGER NOT NULL,
            period TEXT NOT NULL DEFAULT 'month',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # миграция для старых баз: добавить period, если нет
    cur.execute("PRAGMA table_info(subscriptions)")
    cols = {row[1] for row in cur.fetchall()}
    if "period" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN period TEXT NOT NULL DEFAULT 'month'")

    conn.commit()
    conn.close()


def add_subscription(user_id: int, name: str, price: str, day: int, period: str = DEFAULT_PERIOD) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO subscriptions (user_id, name, price, day, period) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, price, day, period),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def list_subscriptions(user_id: int) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day, period FROM subscriptions WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


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
        "SELECT id, name, price, day, period FROM subscriptions WHERE id = ? AND user_id = ?",
        (sub_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def update_subscription_field(user_id: int, sub_id: int, field: str, value) -> bool:
    allowed = {"name", "price", "day", "period"}
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
# UI: MENUS
# -----------------------------
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("➕ Добавить"), KeyboardButton("📋 Список")],
        [KeyboardButton("📅 Ближайшее"), KeyboardButton("💸 Итого/мес")],
        [KeyboardButton("✏️ Редактировать"), KeyboardButton("🗑 Удалить")],
        [KeyboardButton("ℹ️ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def period_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 Ежемесячно", callback_data=f"period:{sub_id}:month"),
            InlineKeyboardButton("📅 Ежегодно", callback_data=f"period:{sub_id}:year"),
        ]
    ])


# -----------------------------
# BOT COMMANDS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет!\n\n"
        "Я помогу удобно следить за подписками 💳\n\n"
        "Нажми кнопку снизу — и поехали 👇",
        reply_markup=main_menu_keyboard(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Добавление подписки:\n"
            "• /add <название> <цена> <день>\n"
            "  пример: /add Netflix 129 15\n"
            "• /add <название> <цена> <валюта> <день>\n"
            "  пример: /add Apple Music 12.99 EUR 5\n\n"
            "Валюту можно по-русски: руб, евро, кр.\n"
            "День списания — числом (1–31).",
            reply_markup=main_menu_keyboard(),
        )
        return

    # day is always last
    day_raw = args[-1]
    try:
        day = int(day_raw)
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "День списания указывается числом (1–31).\n\n"
            "Примеры:\n"
            "• /add Netflix 129 15\n"
            "• /add Apple Music 12.99 EUR 5\n\n"
            "Полную дату списания я покажу сам 😊",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Determine if token before day is a currency
    # Formats supported:
    # /add Name 12.99 5
    # /add Name 12.99 EUR 5
    # /add Name 12.99 евро 5
    if len(args) >= 4 and is_currency_token(args[-2]):
        currency_token = args[-2]
        price_token = args[-3]
        name_parts = args[:-3]
        price_raw = f"{price_token} {currency_token}"
    else:
        name_parts = args[:-2]
        price_raw = args[-2]

    if not name_parts:
        await update.message.reply_text(
            "Не вижу название подписки 😕\n"
            "Пример: /add Apple Music 12.99 EUR 5",
            reply_markup=main_menu_keyboard(),
        )
        return

    name = " ".join(name_parts).strip()

    parsed = parse_price(price_raw)
    if not parsed:
        await update.message.reply_text(
            "Цена должна быть числом или числом с валютой.\n\n"
            "Примеры:\n"
            "• /add Netflix 129 15\n"
            "• /add Spotify 12.99 EUR 5\n"
            "• /add YouTube 199,5 руб 1",
            reply_markup=main_menu_keyboard(),
        )
        return

    amount, currency = parsed
    price = pack_price(amount, currency)

    # default period = month
    new_id = add_subscription(user_id, name, price, day, DEFAULT_PERIOD)

    await update.message.reply_text(
        "Добавлено ✅\n"
        f"#{new_id} • {name} • {format_price(amount, currency)} • списание {day}-го\n\n"
        "Как часто списывается?",
        reply_markup=period_keyboard(new_id),
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text(
            "Пока нет подписок.\nДобавь: /add Netflix 129 15",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = ["Твои подписки:"]
    for _id, name, price, day, period in rows:
        pp = unpack_price(price)
        if pp:
            amount, currency = pp
            price_view = format_price(amount, currency)
        else:
            price_view = price

        lines.append(f"#{_id} • {name} • {price_view} • {day}-го • {period_label(period)}")

    lines.append("\nРедактировать: /edit <id>  |  Удалить: /del <id>")
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Используй так: /del <id>\nПример: /del 3",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "ID должен быть числом. Пример: /del 3",
            reply_markup=main_menu_keyboard(),
        )
        return

    ok = delete_subscription(user_id, sub_id)
    if ok:
        await update.message.reply_text(f"Удалено ✅ (#{sub_id})", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("Не нашла подписку с таким ID (или она не твоя).", reply_markup=main_menu_keyboard())


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Добавь: /add Netflix 129 15", reply_markup=main_menu_keyboard())
        return

    today = date.today()
    best = None  # (charge_date, id, name, price, day, period)

    for _id, name, price, day, period in rows:
        ch = next_charge_date(int(day), today)
        item = (ch, _id, name, price, day, period)
        if best is None or item[0] < best[0]:
            best = item

    charge_date, _id, name, price, day, period = best
    delta_days = (charge_date - today).days

    when_line = format_date_ru(charge_date)
    in_days = f"через {delta_days} {days_word_ru(delta_days)}" if delta_days != 0 else "сегодня"

    pp = unpack_price(price)
    if pp:
        amount, currency = pp
        price_view = format_price(amount, currency)
    else:
        price_view = price

    await update.message.reply_text(
        "Ближайшее списание 💳\n\n"
        f"{name} — {price_view}\n"
        f"📅 {when_line}\n"
        f"⏳ {in_days}\n"
        f"🔁 {period_label(period)}\n\n"
        f"(ID: #{_id}, день списания: {day})",
        reply_markup=main_menu_keyboard(),
    )


async def sum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Добавь: /add Netflix 129 15", reply_markup=main_menu_keyboard())
        return

    totals_month: dict[str, float] = {}
    totals_year: dict[str, float] = {}

    for _id, name, price, day, period in rows:
        pp = unpack_price(price)
        if not pp:
            continue
        amount, currency = pp
        if period == "year":
            totals_year[currency] = totals_year.get(currency, 0.0) + float(amount)
        else:
            totals_month[currency] = totals_month.get(currency, 0.0) + float(amount)

    if not totals_month and not totals_year:
        await update.message.reply_text("Не смогла посчитать суммы 😕 Проверь цены в подписках через /list.", reply_markup=main_menu_keyboard())
        return

    lines = ["Итого 💸"]

    if totals_month:
        lines.append("\nВ месяц:")
        for currency in sorted(totals_month.keys()):
            lines.append(f"• {format_price(totals_month[currency], currency)}")

    if totals_year:
        lines.append("\nВ год:")
        for currency in sorted(totals_year.keys()):
            lines.append(f"• {format_price(totals_year[currency], currency)}")

    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


# -----------------------------
# INLINE: period button handler
# -----------------------------
async def period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    # expected: period:<id>:month|year
    try:
        prefix, sub_id_str, period = data.split(":")
        sub_id = int(sub_id_str)
        if period not in ("month", "year"):
            raise ValueError
    except Exception:
        await query.edit_message_text("Не поняла выбор 😕 Попробуй ещё раз.")
        return

    user_id = query.from_user.id
    ok = update_subscription_field(user_id, sub_id, "period", period)

    if not ok:
        await query.edit_message_text("Не удалось обновить период 😕 Возможно, подписка не найдена.")
        return

    # show updated summary
    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await query.edit_message_text("Готово ✅")
        return

    _id, name, price, day, period = sub
    pp = unpack_price(price)
    if pp:
        amount, currency = pp
        price_view = format_price(amount, currency)
    else:
        price_view = price

    await query.edit_message_text(
        f"Готово ✅\n"
        f"#{_id} • {name}\n"
        f"💰 {price_view}\n"
        f"📅 списание {day}-го\n"
        f"🔁 {period_label(period)}"
    )


# -----------------------------
# BUTTON MENU ROUTER
# -----------------------------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if text == "➕ Добавить":
        await update.message.reply_text(
            "Добавление подписки:\n"
            "• /add <название> <цена> <день>\n"
            "  пример: /add Netflix 129 15\n"
            "• /add <название> <цена> <валюта> <день>\n"
            "  пример: /add Apple Music 12.99 EUR 5\n\n"
            "Валюту можно по-русски: руб, евро, кр.\n"
            "День списания — числом (1–31).",
            reply_markup=main_menu_keyboard(),
        )
        return

    if text == "📋 Список":
        await list_cmd(update, context)
        return

    if text == "📅 Ближайшее":
        await next_cmd(update, context)
        return

    if text == "💸 Итого/мес":
        await sum_cmd(update, context)
        return

    if text == "✏️ Редактировать":
        await update.message.reply_text("Напиши: /edit <id>\nПример: /edit 3", reply_markup=main_menu_keyboard())
        return

    if text == "🗑 Удалить":
        await update.message.reply_text("Напиши: /del <id>\nПример: /del 3", reply_markup=main_menu_keyboard())
        return

    if text == "ℹ️ Помощь":
        await help_cmd(update, context)
        return

    await update.message.reply_text("Нажми кнопку внизу или введи команду через «/».", reply_markup=main_menu_keyboard())


# -----------------------------
# /EDIT CONVERSATION
# -----------------------------
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("Используй так: /edit <id>\nНапример: /edit 3", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /edit 3", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Не нашла подписку с таким ID (или она не твоя).", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    context.user_data["edit_id"] = sub_id

    _id, name, price, day, period = sub
    await update.message.reply_text(
        f"Редактируем подписку #{_id}:\n"
        f"• Название: {name}\n"
        f"• Цена: {price}\n"
        f"• День списания: {day}\n"
        f"• Период: {period_label(period)}\n\n"
        "Что меняем? Напиши: name / price / day\n"
        "Или /cancel чтобы отменить.",
        reply_markup=main_menu_keyboard(),
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()

    if text not in ("name", "price", "day"):
        await update.message.reply_text("Выбери поле: name / price / day (напиши одним словом).", reply_markup=main_menu_keyboard())
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = text

    prompts = {
        "name": "Ок. Введи новое название (например: Netflix).",
        "price": "Ок. Введи новую цену.\nПримеры: 129 | 12.99 EUR | 199,5 руб",
        "day": "Ок. Введи новый день списания (1–31).",
    }
    await update.message.reply_text(prompts[text], reply_markup=main_menu_keyboard())
    return EDIT_ENTER_VALUE


async def edit_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sub_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    if not sub_id or not field:
        await update.message.reply_text("Сломался контекст редактирования. Начни заново: /edit <id>", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    if field == "day":
        try:
            day = int(raw)
            if not (1 <= day <= 31):
                raise ValueError
        except ValueError:
            await update.message.reply_text("День должен быть числом от 1 до 31. Введи ещё раз.", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        value = day
    else:
        if not raw:
            await update.message.reply_text("Пустое значение нельзя. Введи ещё раз.", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE

        if field == "price":
            parsed = parse_price(raw)
            if not parsed:
                await update.message.reply_text(
                    "Цена должна быть числом или числом с валютой.\n"
                    "Примеры: 129 | 12.99 EUR | 199,5 руб\n"
                    "Попробуй ещё раз.",
                    reply_markup=main_menu_keyboard(),
                )
                return EDIT_ENTER_VALUE
            amount, currency = parsed
            value = pack_price(amount, currency)
        else:
            value = raw

    ok = update_subscription_field(user_id, sub_id, field, value)
    if not ok:
        await update.message.reply_text("Не удалось обновить. Начни заново: /edit <id>", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    _id, name, price, day, period = sub
    await update.message.reply_text(
        f"Готово ✅ Подписка #{_id} обновлена:\n"
        f"• Название: {name}\n"
        f"• Цена: {price}\n"
        f"• День списания: {day}\n"
        f"• Период: {period_label(period)}",
        reply_markup=main_menu_keyboard(),
    )

    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    await update.message.reply_text("Ок, отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# -----------------------------
# SAFETY: generic error handler
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "Упс, ошибка 😕 Попробуй ещё раз или напиши /start.",
                reply_markup=main_menu_keyboard(),
            )
    except Exception:
        pass


# -----------------------------
# TELEGRAM COMMAND MENU (slash menu)
# -----------------------------
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Короткая справка"),
        BotCommand("add", "Добавить подписку"),
        BotCommand("list", "Список подписок"),
        BotCommand("next", "Ближайшее списание"),
        BotCommand("sum", "Итого (в месяц/в год)"),
        BotCommand("edit", "Редактировать по ID"),
        BotCommand("del", "Удалить по ID"),
        BotCommand("cancel", "Отмена редактирования"),
        BotCommand("help", "Помощь"),
    ]
    await application.bot.set_my_commands(commands)


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables.")

    init_db()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

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

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("del", del_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(CommandHandler("sum", sum_cmd))

    # Inline buttons
    application.add_handler(CallbackQueryHandler(period_callback, pattern=r"^period:\d+:(month|year)$"))

    # Conversations
    application.add_handler(edit_conv)

    # Button menu router (non-command text)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
