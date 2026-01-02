import os
import sqlite3
import logging
import calendar
from typing import Optional
from datetime import date, datetime

from telegram import (
    Update,
    BotCommand,
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

# Conversation states
EDIT_CHOOSE_FIELD, EDIT_ENTER_VALUE = range(2)
ADD_NAME, ADD_PRICE, ADD_DATE = range(3)  # add flow states


# -----------------------------
# DATE HELPERS
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


def format_date_ru(dt: date) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"

import re

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

def parse_price(text: str):
    """
    Находит первое число (128.30 / 130,17 / 1805,90)
    и валюту (NOK/EUR/USD/RUB), если есть.
    """
    if not text:
        return None, None

    t = text.strip().lower()

    # ищем первое число
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
    """
    Принимает ТОЛЬКО полную дату:
    - 29.12.25
    - 29.12.2025
    """
    text = (text or "").strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


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


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = clamp_day(y, m, d.day)
    return date(y, m, day)


def add_years(d: date, years: int) -> date:
    y = d.year + years
    day = clamp_day(y, d.month, d.day)
    return date(y, d.month, day)


def next_from_last(last: date, period: str, today: date) -> date:
    """
    Calculates next charge date starting from last charge date.
    Ensures result > today (важно: если last == today, следующее будет через период).
    period: month | year
    """
    candidate = last
    while candidate <= today:
        if period == "year":
            candidate = add_years(candidate, 1)
        else:
            candidate = add_months(candidate, 1)
    return candidate


def next_by_day(day_of_month: int, today: date) -> date:
    """
    Fallback logic if last_charge_date is missing:
    next charge based on day of month from today.
    """
    y, m = today.year, today.month
    d_this = clamp_day(y, m, day_of_month)
    candidate = date(y, m, d_this)
    if candidate < today:
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        d_next = clamp_day(y, m, day_of_month)
        candidate = date(y, m, d_next)
    return candidate


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

    # NOK
    "крона": "NOK",
    "кроны": "NOK",
    "крон": "NOK",
    "кр": "NOK",
    "кр.": "NOK",
    "nok": "NOK",
    "kr": "NOK",      # ✅ ДОБАВЬ
    "kr.": "NOK",     # ✅ ДОБАВЬ
    "kroner": "NOK",  # ✅ ДОБАВЬ

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


def parse_price(input_str: str) -> Optional[tuple[float, str]]:
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

def try_parse_quick_add(text: str) -> Optional[tuple[str, float, str, date]]:
    """
    Пытается распарсить строку формата:
    <название...> <цена> [валюта] <дата>

    Примеры:
    - Suno 128,30 кр 29.12.25
    - Netflix 129 NOK 02.01.2026
    - Apple Music 12.99 EUR 05.01.25
    - Genspark 20 $ 01.12.25
    """
    s = (text or "").strip()
    if not s:
        return None

    parts = s.split()
    if len(parts) < 3:
        return None

    # дата — всегда последний токен
    last_token = parts[-1]
    last_dt = parse_ru_date(last_token)
    if not last_dt:
        return None

    # варианты:
    # 1) ... <price> <date>
    # 2) ... <price> <currency> <date>
    if len(parts) >= 4 and is_currency_token(parts[-2]):
        price_raw = f"{parts[-3]} {parts[-2]}"
        name_parts = parts[:-3]
    else:
        price_raw = parts[-2]
        name_parts = parts[:-2]

    if not name_parts:
        return None

    name = " ".join(name_parts).strip()
    parsed_price = parse_price(price_raw)
    if not parsed_price:
        return None

    amount, currency = parsed_price
    return name, float(amount), currency, last_dt


def format_price(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    s = f"{amount:,.2f}"
    s = s.replace(",", " ").replace(".", ",")
    if currency in {"EUR", "USD", "GBP"}:
        return f"{symbol}{s}"
    return f"{s} {symbol}"


def pack_price(amount: float, currency: str) -> str:
    return f"{amount:.2f} {currency}"


def unpack_price(price_text: str) -> Optional[tuple[float, str]]:
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            day INTEGER NOT NULL,
            period TEXT NOT NULL DEFAULT 'month',
            last_charge_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # migrations for older DBs
    cur.execute("PRAGMA table_info(subscriptions)")
    cols = {row[1] for row in cur.fetchall()}

    if "period" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN period TEXT NOT NULL DEFAULT 'month'")
    if "last_charge_date" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN last_charge_date TEXT")

    conn.commit()
    conn.close()


def add_subscription(
    user_id: int,
    name: str,
    price: str,
    day: int,
    period: str,
    last_charge_date: Optional[str],
) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO subscriptions (user_id, name, price, day, period, last_charge_date) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, price, day, period, last_charge_date),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def list_subscriptions(user_id: int) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day, period, last_charge_date FROM subscriptions WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_subscription(user_id: int, sub_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?", (sub_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_subscription_by_id(user_id: int, sub_id: int) -> Optional[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day, period, last_charge_date FROM subscriptions WHERE id = ? AND user_id = ?",
        (sub_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def update_subscription_field(user_id: int, sub_id: int, field: str, value) -> bool:
    allowed = {"name", "price", "day", "period", "last_charge_date"}
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
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🔁 Ежемесячно", callback_data=f"period:{sub_id}:month"),
            InlineKeyboardButton("📅 Ежегодно", callback_data=f"period:{sub_id}:year"),
        ]]
    )


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


# -----------------------------
# ADD FLOW (no commands)
# -----------------------------
async def add_flow_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()

    # 1️⃣ Пытаемся распарсить "одной строкой"
    parsed = try_parse_quick_add(text)
    if parsed:
        name, amount, currency, last_dt = parsed

        user_id = update.effective_user.id
        day = last_dt.day
        period = DEFAULT_PERIOD
        price = pack_price(amount, currency)

        new_id = add_subscription(
            user_id=user_id,
            name=name,
            price=price,
            day=day,
            period=period,
            last_charge_date=last_dt.isoformat(),
        )

        price_view = format_price(amount, currency)

        await update.message.reply_text(
            "Добавлено ✅\n"
            f"#{new_id} • {name}\n"
            f"💰 {price_view}\n"
            f"📌 последнее списание: {format_date_ru(last_dt)}\n\n"
            "Как часто списывается?",
            reply_markup=period_keyboard(new_id),
        )

        return ConversationHandler.END

    # 2️⃣ Если не одна строка — обычный сценарий
    if not text:
        await update.message.reply_text(
            "Название не должно быть пустым. Напиши ещё раз 🙂",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_NAME

    context.user_data["add_name"] = text

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
    parsed = parse_price(raw)
    if not parsed:
        await update.message.reply_text(
            "Не поняла цену 😕\n"
            "Примеры: 128.30 | 12,99 евро | 1805,90 кр | 199,5 руб",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_PRICE

    amount, currency = parsed
    context.user_data["add_amount"] = float(amount)
    context.user_data["add_currency"] = currency

    await update.message.reply_text(
        "Когда было (или будет) списание?\n"
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
    currency = context.user_data.get("add_currency")

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

    price = pack_price(float(amount), currency)

    new_id = add_subscription(
        user_id=user_id,
        name=name,
        price=price,
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


# /add (for power users)
async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Добавление подписки:\n"
            "• /add <название> <цена> <день>\n"
            "  пример: /add Netflix 129 15\n"
            "• /add <название> <цена> <валюта> <день>\n"
            "  пример: /add Apple Music 12.99 EUR 5\n"
            "Можно указать дату вместо дня:\n"
            "  пример: /add Suno 128.30 кр 29.12.25",
            reply_markup=main_menu_keyboard(),
        )
        return

    last_token = args[-1]
    last_dt = parse_ru_date(last_token)
    if last_dt:
        day = last_dt.day
        last_charge_date = last_dt.isoformat()
    else:
        last_charge_date = None
        try:
            day = int(last_token)
            if not (1 <= day <= 31):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "День списания указывается числом (1–31) или датой 29.12.25.\n\n"
                "Примеры:\n"
                "• /add Netflix 129 15\n"
                "• /add Suno 128.30 кр 29.12.25",
                reply_markup=main_menu_keyboard(),
            )
            return

    if len(args) >= 4 and is_currency_token(args[-2]):
        currency_token = args[-2]
        price_token = args[-3]
        name_parts = args[:-3]
        price_raw = f"{price_token} {currency_token}"
    else:
        name_parts = args[:-2]
        price_raw = args[-2]

    if not name_parts:
        await update.message.reply_text("Не вижу название 😕 Пример: /add Apple Music 12.99 EUR 5", reply_markup=main_menu_keyboard())
        return

    name = " ".join(name_parts).strip()

    parsed = parse_price(price_raw)
    if not parsed:
        await update.message.reply_text(
            "Цена должна быть числом или числом с валютой.\n"
            "Примеры: 128.30 | 12,99 евро | 1805,90 кр | 199,5 руб",
            reply_markup=main_menu_keyboard(),
        )
        return

    amount, currency = parsed
    price = pack_price(float(amount), currency)

    period = DEFAULT_PERIOD
    new_id = add_subscription(user_id, name, price, int(day), period, last_charge_date)

    await update.message.reply_text(
        "Добавлено ✅\n"
        f"#{new_id} • {name} • {format_price(float(amount), currency)} • {day}-го\n\n"
        "Как часто списывается?",
        reply_markup=period_keyboard(new_id),
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Нажми ➕ Добавить.", reply_markup=main_menu_keyboard())
        return

    lines = ["Твои подписки:"]
    for _id, name, price, day, period, last_charge_date in rows:
        pp = unpack_price(price)
        if pp:
            amount, currency = pp
            price_view = format_price(float(amount), currency)
        else:
            price_view = price

        extra = ""
        if last_charge_date:
            try:
                d = date.fromisoformat(last_charge_date)
                extra = f" • последнее: {d.day}.{d.month:02d}.{str(d.year)[-2:]}"
            except Exception:
                pass

        lines.append(f"#{_id} • {name} • {price_view} • {day}-го • {period_label(period)}{extra}")

    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Напиши: /del <id>\nПример: /del 3", reply_markup=main_menu_keyboard())
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.", reply_markup=main_menu_keyboard())
        return

    ok = delete_subscription(user_id, sub_id)
    await update.message.reply_text("Удалено ✅" if ok else "Не нашла подписку с таким ID.", reply_markup=main_menu_keyboard())


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)
    if not rows:
        await update.message.reply_text("Пока нет подписок. Нажми ➕ Добавить.", reply_markup=main_menu_keyboard())
        return

    today = date.today()
    best = None  # (charge_date, row...)

    for _id, name, price, day, period, last_charge_date in rows:
        if last_charge_date:
            try:
                last_dt = date.fromisoformat(last_charge_date)
                ch = next_from_last(last_dt, period, today)
            except Exception:
                ch = next_by_day(int(day), today)
        else:
            ch = next_by_day(int(day), today)

        item = (ch, _id, name, price, day, period, last_charge_date)
        if best is None or item[0] < best[0]:
            best = item

    charge_date, _id, name, price, day, period, last_charge_date = best
    delta_days = (charge_date - today).days

    when_line = format_date_ru(charge_date)
    in_days = f"через {delta_days} {days_word_ru(delta_days)}" if delta_days != 0 else "сегодня"

    pp = unpack_price(price)
    if pp:
        amount, currency = pp
        price_view = format_price(float(amount), currency)
    else:
        price_view = price

    extra = ""
    if last_charge_date:
        try:
            d = date.fromisoformat(last_charge_date)
            extra = f"\n📌 последнее списание: {format_date_ru(d)}"
        except Exception:
            pass

    await update.message.reply_text(
        "Ближайшее списание 💳\n\n"
        f"{name} — {price_view}\n"
        f"📅 {when_line}\n"
        f"⏳ {in_days}\n"
        f"🔁 {period_label(period)}"
        f"{extra}\n\n"
        f"(ID: #{_id})",
        reply_markup=main_menu_keyboard(),
    )


async def sum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)
    if not rows:
        await update.message.reply_text("Пока нет подписок. Нажми ➕ Добавить.", reply_markup=main_menu_keyboard())
        return

    totals_month: dict[str, float] = {}
    totals_year: dict[str, float] = {}

    for _id, name, price, day, period, last_charge_date in rows:
        pp = unpack_price(price)
        if not pp:
            continue
        amount, currency = pp
        if period == "year":
            totals_year[currency] = totals_year.get(currency, 0.0) + float(amount)
        else:
            totals_month[currency] = totals_month.get(currency, 0.0) + float(amount)

    lines = ["Итого 💸"]
    if totals_month:
        lines.append("\nВ месяц:")
        for c in sorted(totals_month.keys()):
            lines.append(f"• {format_price(totals_month[c], c)}")
    if totals_year:
        lines.append("\nВ год:")
        for c in sorted(totals_year.keys()):
            lines.append(f"• {format_price(totals_year[c], c)}")

    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


# -----------------------------
# INLINE: period button handler
# -----------------------------
async def period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    try:
        _, sub_id_str, period = data.split(":")
        sub_id = int(sub_id_str)
        if period not in ("month", "year"):
            raise ValueError
    except Exception:
        await query.edit_message_text("Не поняла выбор 😕")
        return

    user_id = query.from_user.id
    ok = update_subscription_field(user_id, sub_id, "period", period)
    if not ok:
        await query.edit_message_text("Не удалось обновить период 😕")
        return

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await query.edit_message_text("Готово ✅")
        return

    _id, name, price, day, period, last_charge_date = sub
    pp = unpack_price(price)
    if pp:
        amount, currency = pp
        price_view = format_price(float(amount), currency)
    else:
        price_view = price

    extra = ""
    if last_charge_date:
        try:
            d = date.fromisoformat(last_charge_date)
            extra = f"\n📌 последнее списание: {format_date_ru(d)}"
        except Exception:
            pass

    await query.edit_message_text(
        "Готово ✅\n"
        f"#{_id} • {name}\n"
        f"💰 {price_view}\n"
        f"📅 списание {day}-го\n"
        f"🔁 {period_label(period)}"
        f"{extra}"
    )


# -----------------------------
# BUTTON MENU ROUTER
# -----------------------------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if text == "➕ Добавить":
        await add_flow_start(update, context)
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

    # --- QUICK ADD: если пользователь просто написал строку подписки ---
    parsed = try_parse_quick_add(text)
    if parsed:
        name, amount, currency, last_dt = parsed

        user_id = update.effective_user.id
        day = last_dt.day
        period = DEFAULT_PERIOD
        price = pack_price(amount, currency)

        new_id = add_subscription(
            user_id=user_id,
            name=name,
            price=price,
            day=day,
            period=period,
            last_charge_date=last_dt.isoformat(),
        )

        price_view = format_price(amount, currency)
        await update.message.reply_text(
            "Добавлено ✅\n"
            f"#{new_id} • {name}\n"
            f"💰 {price_view}\n"
            f"📌 последнее списание: {format_date_ru(last_dt)}\n\n"
            "Как часто списывается?",
            reply_markup=period_keyboard(new_id),
        )
        return
    
    await update.message.reply_text("Нажми кнопку снизу 👇", reply_markup=main_menu_keyboard())


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
        await update.message.reply_text("ID должен быть числом.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Не нашла подписку с таким ID.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    context.user_data["edit_id"] = sub_id

    _id, name, price, day, period, last_charge_date = sub
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
        await update.message.reply_text("Выбери поле: name / price / day", reply_markup=main_menu_keyboard())
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = text

    prompts = {
        "name": "Ок. Введи новое название.",
        "price": "Ок. Введи новую цену.\nПримеры: 129 | 12,99 евро | 199,5 руб",
        "day": "Ок. Введи новый день (1–31).",
    }
    await update.message.reply_text(prompts[text], reply_markup=main_menu_keyboard())
    return EDIT_ENTER_VALUE


async def edit_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sub_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    if not sub_id or not field:
        await update.message.reply_text("Сломался контекст. Начни заново: /edit <id>", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    if field == "day":
        try:
            day = int(raw)
            if not (1 <= day <= 31):
                raise ValueError
        except ValueError:
            await update.message.reply_text("День должен быть числом 1–31.", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        value = day
    else:
        if not raw:
            await update.message.reply_text("Пустое значение нельзя.", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        if field == "price":
            parsed = parse_price(raw)
            if not parsed:
                await update.message.reply_text("Не поняла цену. Примеры: 129 | 12,99 евро | 199,5 руб", reply_markup=main_menu_keyboard())
                return EDIT_ENTER_VALUE
            amount, currency = parsed
            value = pack_price(float(amount), currency)
        else:
            value = raw

    ok = update_subscription_field(user_id, sub_id, field, value)
    await update.message.reply_text("Готово ✅" if ok else "Не удалось обновить 😕", reply_markup=main_menu_keyboard())
    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # clear edit context
    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    # clear add context too
    for k in ("add_name", "add_amount", "add_currency", "add_day", "add_last_date", "add_period"):
        context.user_data.pop(k, None)

    await update.message.reply_text("Ок, отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# -----------------------------
# ERROR HANDLER
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Упс, ошибка 😕 Напиши /start.", reply_markup=main_menu_keyboard())
    except Exception:
        pass


# -----------------------------
# TELEGRAM COMMAND MENU
# -----------------------------
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Старт"),
        BotCommand("add", "Добавить подписку (командой)"),
        BotCommand("list", "Список"),
        BotCommand("next", "Ближайшее"),
        BotCommand("sum", "Итого"),
        BotCommand("edit", "Редактировать по ID"),
        BotCommand("del", "Удалить по ID"),
        BotCommand("cancel", "Отмена"),
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

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Добавить$"), add_flow_name)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_price)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_ENTER_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_enter_value)],
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
    application.add_handler(add_conv)
    application.add_handler(edit_conv)

    # Button menu router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
