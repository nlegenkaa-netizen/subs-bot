import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, List, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "subs.db")

MAX_NAME_LENGTH = 100
MAX_PRICE = 1_000_000
MAX_SUBSCRIPTIONS_PER_USER = 50
REMINDER_HOUR = 9
REMINDER_MINUTE = 0
DEFAULT_PERIOD = "month"
DEFAULT_CURRENCY = "NOK"

SUPPORTED_CURRENCIES = {"NOK", "EUR", "USD", "RUB", "SEK", "DKK", "GBP"}

# ─────────────────────────────────────────────────────────────
# CURRENCY HELPERS
# ─────────────────────────────────────────────────────────────
CURRENCY_ALIASES = {
    "nok": "NOK", "кр": "NOK", "kr": "NOK", "крон": "NOK", "крона": "NOK", "кроны": "NOK",
    "норвежских": "NOK", "норвежские": "NOK", "норвежская": "NOK",
    "eur": "EUR", "€": "EUR", "евро": "EUR", "euro": "EUR", "euros": "EUR",
    "usd": "USD", "$": "USD", "доллар": "USD", "долларов": "USD", "доллара": "USD",
    "баксов": "USD", "баксы": "USD", "бакс": "USD",
    "rub": "RUB", "₽": "RUB", "руб": "RUB", "рубль": "RUB", "рублей": "RUB", "рубля": "RUB", "р": "RUB",
    "sek": "SEK", "шведских": "SEK", "шведские": "SEK", "шведская": "SEK",
    "dkk": "DKK", "датских": "DKK", "датские": "DKK", "датская": "DKK",
    "gbp": "GBP", "£": "GBP", "фунт": "GBP", "фунтов": "GBP", "фунта": "GBP",
}

CURRENCY_SYMBOL = {
    "NOK": "kr", "EUR": "€", "USD": "$", "RUB": "₽",
    "SEK": "kr", "DKK": "kr", "GBP": "£",
}


def normalize_currency_token(token: str) -> Optional[str]:
    t = token.strip().lower()
    if t.upper() in SUPPORTED_CURRENCIES:
        return t.upper()
    return CURRENCY_ALIASES.get(t)


def is_currency_token(token: str) -> bool:
    return normalize_currency_token(token) is not None


# ─────────────────────────────────────────────────────────────
# PRICE HELPERS
# ─────────────────────────────────────────────────────────────
def parse_price(input_str: str) -> Optional[Tuple[float, str]]:
    input_str = input_str.strip()
    if not input_str:
        return None
    parts = input_str.split()
    if len(parts) == 1:
        try:
            amount = float(parts[0].replace(",", ".").replace(" ", ""))
            if 0 < amount <= MAX_PRICE:
                return (amount, DEFAULT_CURRENCY)
        except ValueError:
            return None
    elif len(parts) == 2:
        num_part, cur_part = parts[0], parts[1]
        currency = normalize_currency_token(cur_part)
        if not currency:
            currency = normalize_currency_token(num_part)
            if currency:
                num_part = cur_part
            else:
                return None
        try:
            amount = float(num_part.replace(",", ".").replace(" ", ""))
            if 0 < amount <= MAX_PRICE:
                return (amount, currency)
        except ValueError:
            return None
    return None


def pack_price(amount: float, currency: str) -> str:
    return f"{amount:.2f} {currency}"


def unpack_price(price_str: str) -> Tuple[float, str]:
    parts = price_str.strip().split()
    if len(parts) == 2:
        try:
            return (float(parts[0]), parts[1])
        except ValueError:
            pass
    return (0.0, DEFAULT_CURRENCY)


def format_price(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    formatted = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {symbol}"


# ─────────────────────────────────────────────────────────────
# KNOWN SERVICES
# ─────────────────────────────────────────────────────────────
KNOWN_SERVICES = {
    "netflix": ("Netflix", "🎬 Стриминг"),
    "spotify": ("Spotify", "🎵 Музыка"),
    "youtube": ("YouTube Premium", "🎬 Стриминг"),
    "youtube premium": ("YouTube Premium", "🎬 Стриминг"),
    "apple music": ("Apple Music", "🎵 Музыка"),
    "yandex": ("Яндекс Плюс", "🎵 Музыка"),
    "яндекс": ("Яндекс Плюс", "🎵 Музыка"),
    "vk": ("VK Музыка", "🎵 Музыка"),
    "вк": ("VK Музыка", "🎵 Музыка"),
    "adobe": ("Adobe CC", "💻 Софт"),
    "figma": ("Figma", "💻 Софт"),
    "notion": ("Notion", "💻 Софт"),
    "chatgpt": ("ChatGPT Plus", "💻 Софт"),
    "github": ("GitHub Pro", "💻 Софт"),
    "dropbox": ("Dropbox", "☁️ Облако"),
    "icloud": ("iCloud+", "☁️ Облако"),
    "google one": ("Google One", "☁️ Облако"),
    "xbox": ("Xbox Game Pass", "🎮 Игры"),
    "playstation": ("PlayStation Plus", "🎮 Игры"),
    "gym": ("Спортзал", "💪 Спорт"),
    "фитнес": ("Фитнес", "💪 Спорт"),
    "спортзал": ("Спортзал", "💪 Спорт"),
}

CATEGORIES = [
    "🎬 Стриминг", "🎵 Музыка", "💻 Софт", "☁️ Облако",
    "🎮 Игры", "💪 Спорт", "📚 Обучение", "📰 Новости", "🔒 VPN", "📦 Другое",
]

# ─────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            next_date TEXT NOT NULL,
            period TEXT DEFAULT 'month',
            last_charge_date TEXT,
            category TEXT DEFAULT '📦 Другое',
            is_paused INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            default_currency TEXT DEFAULT 'NOK',
            reminder_enabled INTEGER DEFAULT 1,
            reminder_days TEXT DEFAULT '1,3',
            reminder_hour INTEGER DEFAULT 9
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subscription_id INTEGER NOT NULL,
            amount TEXT NOT NULL,
            paid_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Миграции
    for col, default in [
        ("period", "'month'"), ("last_charge_date", "NULL"),
        ("category", "'📦 Другое'"), ("is_paused", "0")
    ]:
        try:
            c.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} TEXT DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    # Миграция user_settings
    for col, default in [
        ("reminder_enabled", "1"), ("reminder_days", "'1,3'"), ("reminder_hour", "9")
    ]:
        try:
            c.execute(f"ALTER TABLE user_settings ADD COLUMN {col} TEXT DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
# USER SETTINGS FUNCTIONS
# ─────────────────────────────────────────────────────────────
def get_user_settings(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT default_currency, reminder_enabled, reminder_days, reminder_hour
        FROM user_settings WHERE user_id = ?
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            "currency": row[0] or "NOK",
            "reminder_enabled": bool(row[1]) if row[1] is not None else True,
            "reminder_days": row[2] or "1,3",
            "reminder_hour": int(row[3]) if row[3] else 9
        }
    return {
        "currency": "NOK",
        "reminder_enabled": True,
        "reminder_days": "1,3",
        "reminder_hour": 9
    }


def save_user_setting(user_id: int, field: str, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO user_settings (user_id, {}) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET {} = ?
    """.format(field, field), (user_id, value, value))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
# SUBSCRIPTION FUNCTIONS
# ─────────────────────────────────────────────────────────────
def add_subscription(user_id: int, name: str, price: str, next_date: str,
                     period: str = "month", last_charge_date: str = None,
                     category: str = "📦 Другое") -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO subscriptions (user_id, name, price, next_date, period, last_charge_date, category)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, name, price, next_date, period, last_charge_date, category))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return int(new_id)


def find_duplicate_subscription(user_id: int, name: str) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, period, next_date, last_charge_date, category, is_paused
        FROM subscriptions WHERE user_id = ? AND LOWER(name) = LOWER(?)
    """, (user_id, name))
    row = c.fetchone()
    conn.close()
    return row


def list_subscriptions(user_id: int) -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, next_date, period, category, is_paused
        FROM subscriptions WHERE user_id = ? ORDER BY next_date
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_subscription(sub_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, next_date, period, last_charge_date, category, is_paused, user_id
        FROM subscriptions WHERE id = ?
    """, (sub_id,))
    row = c.fetchone()
    conn.close()
    return row


def delete_subscription(sub_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    conn.commit()
    conn.close()


def update_subscription_field(sub_id: int, field: str, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE subscriptions SET {field} = ? WHERE id = ?", (value, sub_id))
    conn.commit()
    conn.close()


def count_user_subscriptions(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count


def add_payment(user_id: int, subscription_id: int, amount: str, paid_at: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO payment_history (user_id, subscription_id, amount, paid_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, subscription_id, amount, paid_at))
    conn.commit()
    conn.close()


def get_payments_for_year(user_id: int, year: int) -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT subscription_id, amount, paid_at FROM payment_history
        WHERE user_id = ? AND paid_at LIKE ? ORDER BY paid_at
    """, (user_id, f"{year}-%"))
    rows = c.fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────
# DATE HELPERS
# ─────────────────────────────────────────────────────────────
def parse_date(text: str) -> Optional[datetime]:
    text = text.strip()
    for fmt in ["%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def next_from_last(last_dt: datetime, period: str = "month") -> datetime:
    today = datetime.now().date()
    candidate = last_dt.date()
    while candidate < today:
        if period == "year":
            try:
                candidate = candidate.replace(year=candidate.year + 1)
            except ValueError:
                candidate = candidate.replace(year=candidate.year + 1, day=28)
        elif period == "week":
            candidate += timedelta(days=7)
        else:
            month = candidate.month + 1
            year = candidate.year
            if month > 12:
                month = 1
                year += 1
            try:
                candidate = candidate.replace(year=year, month=month)
            except ValueError:
                candidate = candidate.replace(year=year, month=month, day=28)
    return datetime.combine(candidate, datetime.min.time())


def format_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")


# ─────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["📋 Мои подписки", "➕ Добавить"],
        ["📅 Ближайшие", "📊 Статистика"],
        ["⚙️ Настройки", "❓ Помощь"]
    ], resize_keyboard=True)


def settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    currency = settings["currency"]
    reminder_on = settings["reminder_enabled"]
    reminder_days = settings["reminder_days"]
    hour = settings["reminder_hour"]
    
    reminder_status = "✅ Вкл" if reminder_on else "❌ Выкл"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💱 Валюта: {currency}", callback_data="settings:currency")],
        [InlineKeyboardButton(f"🔔 Напоминания: {reminder_status}", callback_data="settings:reminder_toggle")],
        [InlineKeyboardButton(f"📅 За дней: {reminder_days}", callback_data="settings:reminder_days")],
        [InlineKeyboardButton(f"🕐 Время: {hour}:00", callback_data="settings:reminder_hour")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="settings:close")]
    ])


def currency_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for cur in ["NOK", "EUR", "USD", "RUB", "SEK", "DKK", "GBP"]:
        symbol = CURRENCY_SYMBOL.get(cur, cur)
        row.append(InlineKeyboardButton(f"{cur} {symbol}", callback_data=f"set_currency:{cur}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="settings:back")])
    return InlineKeyboardMarkup(buttons)


def reminder_days_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("За 1 день", callback_data="set_days:1")],
        [InlineKeyboardButton("За 3 дня", callback_data="set_days:3")],
        [InlineKeyboardButton("За 1 и 3 дня", callback_data="set_days:1,3")],
        [InlineKeyboardButton("За 7 дней", callback_data="set_days:7")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings:back")]
    ])


def reminder_hour_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for h in [7, 8, 9, 10, 12, 14, 18, 20, 21]:
        row.append(InlineKeyboardButton(f"{h}:00", callback_data=f"set_hour:{h}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="settings:back")])
    return InlineKeyboardMarkup(buttons)


def period_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Месяц", callback_data=f"period:{sub_id}:month"),
        InlineKeyboardButton("Год", callback_data=f"period:{sub_id}:year"),
        InlineKeyboardButton("Неделя", callback_data=f"period:{sub_id}:week"),
    ]])


def delete_confirm_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_confirm:{sub_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"delete_cancel:{sub_id}")
    ]])


def duplicate_keyboard(existing_id: int, new_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Записать платёж", callback_data=f"dup_payment:{existing_id}:{new_data}")],
        [InlineKeyboardButton("🔄 Исправить данные", callback_data=f"dup_update:{existing_id}:{new_data}")],
        [InlineKeyboardButton("➕ Создать новую", callback_data=f"dup_create:{new_data}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="dup_cancel")]
    ])


def subscription_keyboard(sub_id: int, is_paused: bool = False) -> InlineKeyboardMarkup:
    pause_btn = InlineKeyboardButton(
        "▶️ Возобновить" if is_paused else "⏸ Приостановить",
        callback_data=f"pause:{sub_id}"
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit:{sub_id}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{sub_id}")
        ],
        [InlineKeyboardButton("✅ Оплачено", callback_data=f"paid:{sub_id}"), pause_btn]
    ])


def year_keyboard(current_year: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"◀️ {current_year - 1}", callback_data=f"stats_year:{current_year - 1}"),
        InlineKeyboardButton(f"{current_year}", callback_data=f"stats_year:{current_year}"),
        InlineKeyboardButton(f"{current_year + 1} ▶️", callback_data=f"stats_year:{current_year + 1}"),
    ]])


# ─────────────────────────────────────────────────────────────
# QUICK ADD PARSER
# ─────────────────────────────────────────────────────────────
def try_parse_quick_add(text: str) -> Optional[dict]:
    text = text.strip()
    if not text:
        return None
    
    date_pattern = r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})$"
    date_match = re.search(date_pattern, text)
    date_str = None
    if date_match:
        date_str = date_match.group(1)
        text = text[:date_match.start()].strip()
    
    parts = text.split()
    if len(parts) < 2:
        return None
    
    name_parts = []
    amount = None
    currency = DEFAULT_CURRENCY
    
    i = len(parts) - 1
    while i >= 0:
        part = parts[i]
        if is_currency_token(part) and amount is None:
            currency = normalize_currency_token(part)
            i -= 1
            continue
        try:
            num = float(part.replace(",", "."))
            if 0 < num <= MAX_PRICE and amount is None:
                amount = num
                i -= 1
                continue
        except ValueError:
            pass
        name_parts.insert(0, part)
        i -= 1
    
    if not name_parts or amount is None:
        return None
    
    name = " ".join(name_parts)
    date_obj = parse_date(date_str) if date_str else None
    
    return {"name": name, "amount": amount, "currency": currency, "date": date_obj}


# ─────────────────────────────────────────────────────────────
# BOT HANDLERS
# ─────────────────────────────────────────────────────────────
ADD_NAME, ADD_PRICE, ADD_DATE = range(3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу отслеживать твои подписки.\n\n"
        "Используй кнопки меню или просто напиши:\n"
        "📝 Netflix 129 kr 15.01.26\n\n"
        "И я добавлю подписку!",
        reply_markup=main_menu_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Как пользоваться ботом*\n\n"
        "*Быстрое добавление:*\n"
        "Просто напиши название, цену и дату:\n"
        "`Netflix 129 kr 15.01.26`\n\n"
        "*Команды:*\n"
        "/add — добавить подписку\n"
        "/list — список подписок\n"
        "/next — ближайшие платежи\n"
        "/stats — статистика расходов\n"
        "/settings — настройки\n"
        "/help — эта справка",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Отменено 👌", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# SETTINGS HANDLERS
# ─────────────────────────────────────────────────────────────
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    
    await update.message.reply_text(
        "⚙️ *Настройки*\n\n"
        "Выбери что хочешь изменить:",
        parse_mode="Markdown",
        reply_markup=settings_keyboard(settings)
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data or ""
    
    if data == "settings:currency":
        await query.edit_message_text(
            "💱 *Выбери валюту по умолчанию:*",
            parse_mode="Markdown",
            reply_markup=currency_keyboard()
        )
    
    elif data == "settings:reminder_toggle":
        settings = get_user_settings(user_id)
        new_value = 0 if settings["reminder_enabled"] else 1
        save_user_setting(user_id, "reminder_enabled", new_value)
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            "⚙️ *Настройки*\n\n"
            "Выбери что хочешь изменить:",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(settings)
        )
    
    elif data == "settings:reminder_days":
        await query.edit_message_text(
            "📅 *За сколько дней напоминать?*",
            parse_mode="Markdown",
            reply_markup=reminder_days_keyboard()
        )
    
    elif data == "settings:reminder_hour":
        await query.edit_message_text(
            "🕐 *В какое время присылать напоминания?*",
            parse_mode="Markdown",
            reply_markup=reminder_hour_keyboard()
        )
    
    elif data == "settings:back":
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            "⚙️ *Настройки*\n\n"
            "Выбери что хочешь изменить:",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(settings)
        )
    
    elif data == "settings:close":
        await query.edit_message_text("✅ Настройки сохранены!")
    
    elif data.startswith("set_currency:"):
        currency = data.split(":")[1]
        save_user_setting(user_id, "default_currency", currency)
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            f"✅ Валюта изменена на *{currency}*\n\n"
            "⚙️ *Настройки*",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(settings)
        )
    
    elif data.startswith("set_days:"):
        days = data.split(":")[1]
        save_user_setting(user_id, "reminder_days", days)
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            f"✅ Напоминания за *{days}* дн.\n\n"
            "⚙️ *Настройки*",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(settings)
        )
    
    elif data.startswith("set_hour:"):
        hour = int(data.split(":")[1])
        save_user_setting(user_id, "reminder_hour", hour)
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            f"✅ Время напоминаний: *{hour}:00*\n\n"
            "⚙️ *Настройки*",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(settings)
        )


# ─────────────────────────────────────────────────────────────
# ADD FLOW
# ─────────────────────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if count_user_subscriptions(user_id) >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"❌ Достигнут лимит: {MAX_SUBSCRIPTIONS_PER_USER} подписок.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📝 Введи название подписки:\n\n"
        "Или сразу всё: `Netflix 129 kr 15.01.26`",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return ADD_NAME


async def add_flow_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    quick = try_parse_quick_add(text)
    if quick:
        return await process_quick_add(update, context, quick)
    
    if len(text) > MAX_NAME_LENGTH:
        await update.message.reply_text(f"❌ Слишком длинное название (макс. {MAX_NAME_LENGTH})")
        return ADD_NAME
    
    context.user_data["add_name"] = text
    await update.message.reply_text("💰 Введи цену (например: 129 kr или 9.99 EUR):")
    return ADD_PRICE


async def add_flow_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    parsed = parse_price(text)
    if not parsed:
        await update.message.reply_text("❌ Не понял цену. Введи число и валюту:\n129 kr, 9.99 EUR, 100")
        return ADD_PRICE
    
    amount, currency = parsed
    context.user_data["add_amount"] = amount
    context.user_data["add_currency"] = currency
    await update.message.reply_text("📅 Введи дату последней оплаты (дд.мм.гг):\nНапример: 15.01.26")
    return ADD_DATE


async def add_flow_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    date_obj = parse_date(text)
    if not date_obj:
        await update.message.reply_text("❌ Не понял дату. Формат: дд.мм.гг")
        return ADD_DATE
    
    name = context.user_data.get("add_name", "Подписка")
    amount = context.user_data.get("add_amount", 0)
    currency = context.user_data.get("add_currency", DEFAULT_CURRENCY)
    
    existing = find_duplicate_subscription(user_id, name)
    if existing:
        new_data = f"{name}|{amount}|{currency}|{date_obj.isoformat()}"
        ex_id, ex_name, ex_price, *_ = existing
        ex_amount, ex_cur = unpack_price(ex_price)
        await update.message.reply_text(
            f"⚠️ Подписка *{ex_name}* уже существует!\n"
            f"Текущая цена: {format_price(ex_amount, ex_cur)}\n\nЧто сделать?",
            parse_mode="Markdown",
            reply_markup=duplicate_keyboard(ex_id, new_data)
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    category = "📦 Другое"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        proper_name, category = KNOWN_SERVICES[name_lower]
        name = proper_name
    
    next_dt = next_from_last(date_obj, DEFAULT_PERIOD)
    price = pack_price(amount, currency)
    
    new_id = add_subscription(
        user_id=user_id, name=name, price=price,
        next_date=next_dt.strftime("%Y-%m-%d"),
        period=DEFAULT_PERIOD,
        last_charge_date=date_obj.strftime("%Y-%m-%d"),
        category=category
    )
    add_payment(user_id, new_id, price, date_obj.strftime("%Y-%m-%d"))
    
    await update.message.reply_text(
        f"✅ Добавлено: *{name}*\n"
        f"💰 {format_price(amount, currency)}\n"
        f"📅 Следующий платёж: {format_date(next_dt)}\n"
        f"🏷 Категория: {category}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def process_quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE, quick: dict) -> int:
    user_id = update.effective_user.id
    name = quick["name"]
    amount = quick["amount"]
    currency = quick["currency"]
    date_obj = quick["date"]
    
    existing = find_duplicate_subscription(user_id, name)
    if existing:
        new_data = f"{name}|{amount}|{currency}|{date_obj.isoformat() if date_obj else ''}"
        ex_id, ex_name, ex_price, *_ = existing
        ex_amount, ex_cur = unpack_price(ex_price)
        await update.message.reply_text(
            f"⚠️ Подписка *{ex_name}* уже существует!\n"
            f"Текущая цена: {format_price(ex_amount, ex_cur)}\n\nЧто сделать?",
            parse_mode="Markdown",
            reply_markup=duplicate_keyboard(ex_id, new_data)
        )
        return ConversationHandler.END
    
    category = "📦 Другое"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        proper_name, category = KNOWN_SERVICES[name_lower]
        name = proper_name
    
    last_dt = date_obj if date_obj else datetime.now()
    next_dt = next_from_last(last_dt, DEFAULT_PERIOD)
    price = pack_price(amount, currency)
    
    new_id = add_subscription(
        user_id=user_id, name=name, price=price,
        next_date=next_dt.strftime("%Y-%m-%d"),
        period=DEFAULT_PERIOD,
        last_charge_date=last_dt.strftime("%Y-%m-%d"),
        category=category
    )
    add_payment(user_id, new_id, price, last_dt.strftime("%Y-%m-%d"))
    
    await update.message.reply_text(
        f"✅ Добавлено: *{name}*\n"
        f"💰 {format_price(amount, currency)}\n"
        f"📅 Следующий платёж: {format_date(next_dt)}\n"
        f"🏷 Категория: {category}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# LIST / NEXT / STATS
# ─────────────────────────────────────────────────────────────
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text(
            "📋 У тебя пока нет подписок.\n\nНапиши:\n`Netflix 129 kr 15.01.26`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        return
    
    lines = ["📋 *Твои подписки:*\n"]
    for sub_id, name, price_str, next_date, period, category, is_paused in subs:
        amount, currency = unpack_price(price_str)
        price_view = format_price(amount, currency)
        status = "⏸ " if is_paused else ""
        lines.append(f"{status}*{name}* — {price_view}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text("📅 Нет подписок.", reply_markup=main_menu_keyboard())
        return
    
    today = datetime.now().date()
    upcoming = []
    
    for sub_id, name, price_str, next_date, period, category, is_paused in subs:
        if is_paused:
            continue
        try:
            dt = datetime.strptime(next_date, "%Y-%m-%d").date()
            days_left = (dt - today).days
            if days_left <= 30:
                amount, currency = unpack_price(price_str)
                upcoming.append((days_left, dt, name, amount, currency))
        except ValueError:
            continue
    
    if not upcoming:
        await update.message.reply_text("📅 В ближайшие 30 дней платежей нет.", reply_markup=main_menu_keyboard())
        return
    
    upcoming.sort(key=lambda x: x[0])
    lines = ["📅 *Ближайшие платежи:*\n"]
    
    for days_left, dt, name, amount, currency in upcoming:
        price_view = format_price(amount, currency)
        if days_left == 0:
            when = "сегодня"
        elif days_left == 1:
            when = "завтра"
        elif days_left < 0:
            when = f"просрочено"
        else:
            when = f"через {days_left} дн."
        lines.append(f"• *{name}* — {price_view}\n  {dt.strftime('%d.%m.%Y')} ({when})")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    year = datetime.now().year
    await show_stats_for_year(update, user_id, year)


async def show_stats_for_year(update: Update, user_id: int, year: int, edit: bool = False) -> None:
    payments = get_payments_for_year(user_id, year)
    months = {}
    total = 0.0
    
    for sub_id, amount_str, paid_at in payments:
        amount, currency = unpack_price(amount_str)
        try:
            dt = datetime.strptime(paid_at, "%Y-%m-%d")
            month = dt.month
            if month not in months:
                months[month] = 0.0
            months[month] += amount
            total += amount
        except ValueError:
            continue
    
    month_names = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    lines = [f"📊 *Статистика за {year} год:*\n"]
    
    if months:
        for m in sorted(months.keys()):
            lines.append(f"{month_names[m]}: {months[m]:,.0f}".replace(",", " "))
        lines.append(f"\n*Итого: {total:,.0f}*".replace(",", " "))
    else:
        lines.append("Нет данных о платежах.")
    
    text = "\n".join(lines)
    keyboard = year_keyboard(year)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ─────────────────────────────────────────────────────────────
# CALLBACK HANDLERS
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("stats_year:"):
        year = int(data.split(":")[1])
        await show_stats_for_year(update, user_id, year, edit=True)
        return
    
    if data.startswith("delete_confirm:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription(sub_id)
        if sub and sub[8] == user_id:
            delete_subscription(sub_id)
            await query.edit_message_text("🗑 Подписка удалена.")
        return
    
    if data.startswith("delete_cancel:"):
        await query.edit_message_text("Отменено 👌")
        return
    
    if data.startswith("delete:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription(sub_id)
        if sub and sub[8] == user_id:
            await query.edit_message_text(
                f"Удалить подписку *{sub[1]}*?",
                parse_mode="Markdown",
                reply_markup=delete_confirm_keyboard(sub_id)
            )
        return
    
    if data.startswith("pause:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription(sub_id)
        if sub and sub[8] == user_id:
            new_paused = 0 if sub[7] else 1
            update_subscription_field(sub_id, "is_paused", new_paused)
            status = "приостановлена ⏸" if new_paused else "возобновлена ▶️"
            await query.edit_message_text(f"Подписка *{sub[1]}* {status}", parse_mode="Markdown")
        return
    
    if data.startswith("paid:"):
        sub_id = int(data.split(":")[1])
        sub = get_subscription(sub_id)
        if sub and sub[8] == user_id:
            name, price_str, next_date, period = sub[1], sub[2], sub[3], sub[4]
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            new_next = next_from_last(today, period)
            update_subscription_field(sub_id, "last_charge_date", today_str)
            update_subscription_field(sub_id, "next_date", new_next.strftime("%Y-%m-%d"))
            add_payment(user_id, sub_id, price_str, today_str)
            amount, currency = unpack_price(price_str)
            await query.edit_message_text(
                f"✅ *{name}* — оплата записана!\n"
                f"💰 {format_price(amount, currency)}\n"
                f"📅 Следующий платёж: {format_date(new_next)}",
                parse_mode="Markdown"
            )
        return
    
    if data.startswith("period:"):
        parts = data.split(":")
        sub_id = int(parts[1])
        new_period = parts[2]
        sub = get_subscription(sub_id)
        if sub and sub[8] == user_id:
            update_subscription_field(sub_id, "period", new_period)
            last_charge = sub[5]
            if last_charge:
                last_dt = datetime.strptime(last_charge, "%Y-%m-%d")
                new_next = next_from_last(last_dt, new_period)
                update_subscription_field(sub_id, "next_date", new_next.strftime("%Y-%m-%d"))
            period_names = {"month": "месяц", "year": "год", "week": "неделя"}
            await query.edit_message_text(f"✅ Период: {period_names.get(new_period, new_period)}")
        return


async def duplicate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("dup_payment:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        existing_id = int(parts[1])
        data_parts = parts[2].split("|")
        if len(data_parts) < 4:
            return
        
        name, amount_str, currency, date_str = data_parts
        try:
            amount = float(amount_str)
            price = pack_price(amount, currency)
            if date_str:
                last_dt = datetime.fromisoformat(date_str)
                update_subscription_field(existing_id, "last_charge_date", last_dt.strftime("%Y-%m-%d"))
                update_subscription_field(existing_id, "price", price)
                sub = get_subscription(existing_id)
                if sub:
                    new_next = next_from_last(last_dt, sub[4])
                    update_subscription_field(existing_id, "next_date", new_next.strftime("%Y-%m-%d"))
                add_payment(user_id, existing_id, price, last_dt.strftime("%Y-%m-%d"))
                await query.edit_message_text(
                    f"✅ Платёж записан!\n💰 {format_price(amount, currency)}\n📅 {format_date(last_dt)}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"dup_payment error: {e}")
        return
    
    elif data.startswith("dup_update:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        existing_id = int(parts[1])
        data_parts = parts[2].split("|")
        if len(data_parts) < 4:
            return
        
        name, amount_str, currency, date_str = data_parts
        try:
            amount = float(amount_str)
            price = pack_price(amount, currency)
            update_subscription_field(existing_id, "price", price)
            if date_str:
                last_dt = datetime.fromisoformat(date_str)
                update_subscription_field(existing_id, "last_charge_date", last_dt.strftime("%Y-%m-%d"))
                sub = get_subscription(existing_id)
                if sub:
                    new_next = next_from_last(last_dt, sub[4])
                    update_subscription_field(existing_id, "next_date", new_next.strftime("%Y-%m-%d"))
            await query.edit_message_text(f"✅ Обновлено!\n💰 {format_price(amount, currency)}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"dup_update error: {e}")
        return
    
    elif data.startswith("dup_create:"):
        parts = data.split(":", 1)
        if len(parts) < 2:
            return
        data_parts = parts[1].split("|")
        if len(data_parts) < 4:
            return
        
        name, amount_str, currency, date_str = data_parts
        try:
            amount = float(amount_str)
            price = pack_price(amount, currency)
            category = "📦 Другое"
            if name.lower() in KNOWN_SERVICES:
                name, category = KNOWN_SERVICES[name.lower()]
            
            last_dt = datetime.fromisoformat(date_str) if date_str else datetime.now()
            next_dt = next_from_last(last_dt, DEFAULT_PERIOD)
            
            new_id = add_subscription(
                user_id=user_id, name=name, price=price,
                next_date=next_dt.strftime("%Y-%m-%d"),
                period=DEFAULT_PERIOD,
                last_charge_date=last_dt.strftime("%Y-%m-%d"),
                category=category
            )
            add_payment(user_id, new_id, price, last_dt.strftime("%Y-%m-%d"))
            await query.edit_message_text(
                f"✅ Создано: *{name}*\n💰 {format_price(amount, currency)}\n📅 {format_date(next_dt)}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"dup_create error: {e}")
        return
    
    elif data == "dup_cancel":
        await query.edit_message_text("Отменено 👌")


# ─────────────────────────────────────────────────────────────
# MENU ROUTER
# ─────────────────────────────────────────────────────────────
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if text == "📋 Мои подписки":
        await list_cmd(update, context)
        return None
    if text == "➕ Добавить":
        return await add_start(update, context)
    if text == "📅 Ближайшие":
        await next_cmd(update, context)
        return None
    if text == "📊 Статистика":
        await stats_cmd(update, context)
        return None
    if text == "⚙️ Настройки":
        await settings_cmd(update, context)
        return None
    if text == "❓ Помощь":
        await help_cmd(update, context)
        return None
    
    # Быстрое добавление
    quick = try_parse_quick_add(text)
    if quick:
        if count_user_subscriptions(user_id) >= MAX_SUBSCRIPTIONS_PER_USER:
            await update.message.reply_text(f"❌ Лимит: {MAX_SUBSCRIPTIONS_PER_USER} подписок.", reply_markup=main_menu_keyboard())
            return None
        return await process_quick_add(update, context, quick)
    
    await update.message.reply_text(
        "🤔 Не понял. Попробуй:\n`Netflix 129 kr 15.01.26`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return None


# ─────────────────────────────────────────────────────────────
# DEBUG & TEST COMMANDS
# ─────────────────────────────────────────────────────────────
async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, subscription_id, amount, paid_at FROM payment_history WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("Нет платежей в истории")
        return
    
    lines = ["Debug payment_history:\n"]
    for _id, sub_id, amount, paid_at in rows:
        lines.append(f"id={_id} sub={sub_id} amount={amount} date={paid_at}")
    await update.message.reply_text("\n".join(lines))


async def test_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text("У тебя нет подписок для теста")
        return
    
    sub_id, name, price_str, next_date, period, category, is_paused = subs[0]
    amount, currency = unpack_price(price_str)
    price_view = format_price(amount, currency)
    
    await update.message.reply_text(
        f"⏰ *Тестовое напоминание*\n\n"
        f"Завтра оплата *{name}*\n"
        f"💰 {price_view}\n\n"
        f"✅ Напоминания работают!",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────────────────────
async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    today = datetime.now().date()
    
    # Получаем все активные подписки
    c.execute("""
        SELECT s.user_id, s.name, s.price, s.next_date
        FROM subscriptions s
        WHERE s.is_paused = 0
    """)
    all_subs = c.fetchall()
    
    # Получаем настройки всех пользователей
    c.execute("SELECT user_id, reminder_enabled, reminder_days FROM user_settings")
    settings_rows = c.fetchall()
    conn.close()
    
    user_settings = {}
    for uid, enabled, days in settings_rows:
        user_settings[uid] = {"enabled": bool(enabled), "days": days or "1,3"}
    
    for user_id, name, price_str, next_date in all_subs:
        try:
            settings = user_settings.get(user_id, {"enabled": True, "days": "1,3"})
            if not settings["enabled"]:
                continue
            
            dt = datetime.strptime(next_date, "%Y-%m-%d").date()
            days_left = (dt - today).days
            
            reminder_days = [int(d) for d in settings["days"].split(",")]
            
            if days_left in reminder_days:
                amount, currency = unpack_price(price_str)
                price_view = format_price(amount, currency)
                
                if days_left == 1:
                    when = "завтра"
                elif days_left == 0:
                    when = "сегодня"
                else:
                    when = f"через {days_left} дн."
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏰ *Напоминание*\n\n{when.capitalize()} оплата *{name}*\n💰 {price_view}",
                    parse_mode="Markdown"
                )
                logger.info(f"Reminder sent to {user_id} for {name}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


# ─────────────────────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────────────────────
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception: {context.error}", exc_info=True)
    if update and update.effective_message:
        await update.effective_message.reply_text("Ошибка 😕 Попробуй /start", reply_markup=main_menu_keyboard())


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info(f"✅ Bot running: @{me.username} (id={me.id})")


def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    init_db()
    logger.info("🚀 CODE VERSION: 2026-01-04 v4 (settings)")
    
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Напоминания каждый день в 9:00
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_daily(
            send_reminders,
            time=dt_time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE),
            name="daily_reminders"
        )
        logger.info(f"Reminders scheduled at {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}")
    
    # Conversation handler
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"^➕ Добавить$"), add_start),
        ],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_price)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("debug", debug_cmd))
    application.add_handler(CommandHandler("test_reminder", test_reminder_cmd))
    application.add_handler(add_conv)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(settings:|set_)"))
    application.add_handler(CallbackQueryHandler(duplicate_callback, pattern=r"^dup_"))
    application.add_handler(CallbackQueryHandler(callback_router))
    
    # Menu handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    logger.info("Bot starting v4...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
