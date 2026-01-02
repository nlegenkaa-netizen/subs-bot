import os
import sqlite3
import logging
import calendar
import asyncio
from typing import Optional
from datetime import date, datetime, time, timedelta

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

# Ограничения
MAX_NAME_LENGTH = 100
MAX_PRICE = 1_000_000
MAX_SUBSCRIPTIONS_PER_USER = 50

# Время отправки напоминаний (UTC)
REMINDER_HOUR = 9
REMINDER_MINUTE = 0

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
EDIT_CHOOSE_FIELD, EDIT_ENTER_VALUE = range(2)
ADD_NAME, ADD_PRICE, ADD_DATE = range(3)
SETTINGS_REMINDER_DAYS = 10


# -----------------------------
# KNOWN SERVICES (автоопределение)
# -----------------------------
KNOWN_SERVICES = {
    "netflix": {"name": "Netflix", "category": "video", "period": "month"},
    "spotify": {"name": "Spotify", "category": "music", "period": "month"},
    "youtube": {"name": "YouTube Premium", "category": "video", "period": "month"},
    "youtube premium": {"name": "YouTube Premium", "category": "video", "period": "month"},
    "apple music": {"name": "Apple Music", "category": "music", "period": "month"},
    "yandex": {"name": "Яндекс Плюс", "category": "other", "period": "month"},
    "яндекс": {"name": "Яндекс Плюс", "category": "other", "period": "month"},
    "яндекс плюс": {"name": "Яндекс Плюс", "category": "other", "period": "month"},
    "openai": {"name": "OpenAI", "category": "software", "period": "month"},
    "chatgpt": {"name": "ChatGPT Plus", "category": "software", "period": "month"},
    "claude": {"name": "Claude Pro", "category": "software", "period": "month"},
    "notion": {"name": "Notion", "category": "software", "period": "month"},
    "figma": {"name": "Figma", "category": "software", "period": "month"},
    "adobe": {"name": "Adobe CC", "category": "software", "period": "month"},
    "dropbox": {"name": "Dropbox", "category": "cloud", "period": "month"},
    "icloud": {"name": "iCloud+", "category": "cloud", "period": "month"},
    "google one": {"name": "Google One", "category": "cloud", "period": "month"},
    "telegram": {"name": "Telegram Premium", "category": "other", "period": "month"},
    "telegram premium": {"name": "Telegram Premium", "category": "other", "period": "month"},
    "discord": {"name": "Discord Nitro", "category": "other", "period": "month"},
    "discord nitro": {"name": "Discord Nitro", "category": "other", "period": "month"},
    "xbox": {"name": "Xbox Game Pass", "category": "games", "period": "month"},
    "xbox game pass": {"name": "Xbox Game Pass", "category": "games", "period": "month"},
    "playstation": {"name": "PlayStation Plus", "category": "games", "period": "month"},
    "ps plus": {"name": "PlayStation Plus", "category": "games", "period": "month"},
    "nintendo": {"name": "Nintendo Online", "category": "games", "period": "year"},
    "hbo": {"name": "HBO Max", "category": "video", "period": "month"},
    "hbo max": {"name": "HBO Max", "category": "video", "period": "month"},
    "disney": {"name": "Disney+", "category": "video", "period": "month"},
    "disney+": {"name": "Disney+", "category": "video", "period": "month"},
    "amazon prime": {"name": "Amazon Prime", "category": "video", "period": "month"},
    "prime": {"name": "Amazon Prime", "category": "video", "period": "month"},
    "kindle": {"name": "Kindle Unlimited", "category": "other", "period": "month"},
    "audible": {"name": "Audible", "category": "other", "period": "month"},
    "vpn": {"name": "VPN", "category": "software", "period": "month"},
    "nordvpn": {"name": "NordVPN", "category": "software", "period": "month"},
    "expressvpn": {"name": "ExpressVPN", "category": "software", "period": "month"},
    "1password": {"name": "1Password", "category": "software", "period": "month"},
    "lastpass": {"name": "LastPass", "category": "software", "period": "month"},
    "suno": {"name": "Suno", "category": "software", "period": "month"},
    "midjourney": {"name": "Midjourney", "category": "software", "period": "month"},
    "github": {"name": "GitHub Pro", "category": "software", "period": "month"},
    "github copilot": {"name": "GitHub Copilot", "category": "software", "period": "month"},
    "copilot": {"name": "GitHub Copilot", "category": "software", "period": "month"},
}

CATEGORIES = {
    "video": "🎬 Видео",
    "music": "🎵 Музыка",
    "software": "💻 Софт",
    "cloud": "☁️ Облако",
    "games": "🎮 Игры",
    "other": "📦 Другое",
}


# -----------------------------
# DATE HELPERS
# -----------------------------
MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def clamp_day(year: int, month: int, wanted_day: int) -> int:
    last_day = calendar.monthrange(year, month)[1]
    return min(max(1, wanted_day), last_day)


def format_date_ru(dt: date) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"


def format_date_short(dt: date) -> str:
    return f"{dt.day}.{dt.month:02d}.{str(dt.year)[-2:]}"


def parse_ru_date(text: str) -> Optional[date]:
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
    candidate = last
    while candidate <= today:
        if period == "year":
            candidate = add_years(candidate, 1)
        else:
            candidate = add_months(candidate, 1)
    return candidate


def next_by_day(day_of_month: int, today: date) -> date:
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
    "NOK": "NOK", "EUR": "€", "USD": "$", "RUB": "₽",
    "SEK": "SEK", "DKK": "DKK", "GBP": "£",
}

CURRENCY_ALIASES = {
    "руб": "RUB", "руб.": "RUB", "р": "RUB", "р.": "RUB",
    "рублей": "RUB", "₽": "RUB", "rub": "RUB",
    "евро": "EUR", "€": "EUR", "eur": "EUR",
    "крона": "NOK", "кроны": "NOK", "крон": "NOK",
    "кр": "NOK", "кр.": "NOK", "nok": "NOK",
    "kr": "NOK", "kr.": "NOK", "kroner": "NOK",
    "доллар": "USD", "доллары": "USD", "дол": "USD",
    "дол.": "USD", "$": "USD", "usd": "USD",
    "фунт": "GBP", "фунты": "GBP", "£": "GBP", "gbp": "GBP",
    "sek": "SEK", "dkk": "DKK",
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
        if amount <= 0 or amount > MAX_PRICE:
            return None
    except ValueError:
        return None

    return amount, currency


def try_parse_quick_add(text: str) -> Optional[tuple[str, float, str, date, str]]:
    """
    Парсит строку: <название> <цена> [валюта] <дата>
    Возвращает: (name, amount, currency, date, category)
    """
    s = (text or "").strip()
    if not s:
        return None

    parts = s.split()
    if len(parts) < 3:
        return None

    last_token = parts[-1]
    last_dt = parse_ru_date(last_token)
    if not last_dt:
        return None

    if len(parts) >= 4 and is_currency_token(parts[-2]):
        price_raw = f"{parts[-3]} {parts[-2]}"
        name_parts = parts[:-3]
    else:
        price_raw = parts[-2]
        name_parts = parts[:-2]

    if not name_parts:
        return None

    name = " ".join(name_parts).strip()
    if len(name) > MAX_NAME_LENGTH:
        return None

    parsed_price = parse_price(price_raw)
    if not parsed_price:
        return None

    amount, currency = parsed_price
    
    # Определяем категорию по известным сервисам
    category = "other"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        service = KNOWN_SERVICES[name_lower]
        name = service["name"]  # Используем правильное название
        category = service["category"]

    return name, amount, currency, last_dt, category


def format_price(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    s = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
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
DEFAULT_PERIOD = "month"


def period_label(period: str) -> str:
    return "ежемесячно" if period == "month" else "ежегодно"


# -----------------------------
# DB LAYER
# -----------------------------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Таблица подписок
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            day INTEGER NOT NULL,
            period TEXT NOT NULL DEFAULT 'month',
            last_charge_date TEXT,
            category TEXT DEFAULT 'other',
            is_paused INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица настроек пользователя
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            reminder_days INTEGER DEFAULT 1,
            reminder_enabled INTEGER DEFAULT 1,
            timezone_offset INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица истории платежей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount TEXT NOT NULL,
            paid_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
        )
    """)

    # Миграции для старых БД
    cur.execute("PRAGMA table_info(subscriptions)")
    cols = {row[1] for row in cur.fetchall()}

    if "period" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN period TEXT NOT NULL DEFAULT 'month'")
    if "last_charge_date" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN last_charge_date TEXT")
    if "category" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN category TEXT DEFAULT 'other'")
    if "is_paused" not in cols:
        cur.execute("ALTER TABLE subscriptions ADD COLUMN is_paused INTEGER DEFAULT 0")

    conn.commit()
    conn.close()


def count_user_subscriptions(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def add_subscription(
    user_id: int,
    name: str,
    price: str,
    day: int,
    period: str,
    last_charge_date: Optional[str],
    category: str = "other",
) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO subscriptions 
           (user_id, name, price, day, period, last_charge_date, category) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, name, price, day, period, last_charge_date, category),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def list_subscriptions(user_id: int, include_paused: bool = True) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if include_paused:
        cur.execute(
            """SELECT id, name, price, day, period, last_charge_date, category, is_paused 
               FROM subscriptions WHERE user_id = ? ORDER BY is_paused, id DESC""",
            (user_id,),
        )
    else:
        cur.execute(
            """SELECT id, name, price, day, period, last_charge_date, category, is_paused 
               FROM subscriptions WHERE user_id = ? AND is_paused = 0 ORDER BY id DESC""",
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
        """SELECT id, name, price, day, period, last_charge_date, category, is_paused 
           FROM subscriptions WHERE id = ? AND user_id = ?""",
        (sub_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def update_subscription_field(user_id: int, sub_id: int, field: str, value) -> bool:
    allowed = {"name", "price", "day", "period", "last_charge_date", "category", "is_paused"}
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


def toggle_pause_subscription(user_id: int, sub_id: int) -> Optional[bool]:
    """Переключает паузу подписки. Возвращает новое состояние или None при ошибке."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT is_paused FROM subscriptions WHERE id = ? AND user_id = ?",
        (sub_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    
    new_state = 0 if row[0] else 1
    cur.execute(
        "UPDATE subscriptions SET is_paused = ? WHERE id = ? AND user_id = ?",
        (new_state, sub_id, user_id),
    )
    conn.commit()
    conn.close()
    return bool(new_state)


# -----------------------------
# USER SETTINGS
# -----------------------------
def get_user_settings(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT reminder_days, reminder_enabled, timezone_offset FROM user_settings WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    
    if row:
        return {
            "reminder_days": row[0],
            "reminder_enabled": bool(row[1]),
            "timezone_offset": row[2],
        }
    return {
        "reminder_days": 1,
        "reminder_enabled": True,
        "timezone_offset": 0,
    }


def update_user_setting(user_id: int, field: str, value) -> bool:
    allowed = {"reminder_days", "reminder_enabled", "timezone_offset"}
    if field not in allowed:
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Upsert
    cur.execute(
        """INSERT INTO user_settings (user_id, {0}) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET {0} = ?""".format(field),
        (user_id, value, value),
    )
    conn.commit()
    conn.close()
    return True


# -----------------------------
# PAYMENT HISTORY
# -----------------------------
def add_payment(user_id: int, sub_id: int, amount: str, paid_at: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO payment_history (subscription_id, user_id, amount, paid_at) 
           VALUES (?, ?, ?, ?)""",
        (sub_id, user_id, amount, paid_at),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return int(new_id)


def get_payment_history(user_id: int, sub_id: Optional[int] = None, limit: int = 20) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if sub_id:
        cur.execute(
            """SELECT ph.id, ph.subscription_id, s.name, ph.amount, ph.paid_at 
               FROM payment_history ph
               JOIN subscriptions s ON ph.subscription_id = s.id
               WHERE ph.user_id = ? AND ph.subscription_id = ?
               ORDER BY ph.paid_at DESC LIMIT ?""",
            (user_id, sub_id, limit),
        )
    else:
        cur.execute(
            """SELECT ph.id, ph.subscription_id, s.name, ph.amount, ph.paid_at 
               FROM payment_history ph
               JOIN subscriptions s ON ph.subscription_id = s.id
               WHERE ph.user_id = ?
               ORDER BY ph.paid_at DESC LIMIT ?""",
            (user_id, limit),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


# -----------------------------
# EXPORT/IMPORT
# -----------------------------
def export_to_csv(user_id: int) -> str:
    """Экспортирует подписки в CSV формат"""
    rows = list_subscriptions(user_id)
    lines = ["name,price,currency,day,period,category,is_paused,last_charge_date"]
    
    for _id, name, price, day, period, last_charge_date, category, is_paused in rows:
        pp = unpack_price(price)
        if pp:
            amount, currency = pp
        else:
            amount, currency = 0, "NOK"
        
        # Экранируем запятые в названии
        name_escaped = f'"{name}"' if "," in name else name
        lines.append(f"{name_escaped},{amount},{currency},{day},{period},{category},{is_paused},{last_charge_date or ''}")
    
    return "\n".join(lines)


# -----------------------------
# UI: MENUS
# -----------------------------
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("➕ Добавить"), KeyboardButton("📋 Список")],
        [KeyboardButton("📅 Ближайшее"), KeyboardButton("💸 Итого")],
        [KeyboardButton("✏️ Редактировать"), KeyboardButton("🗑 Удалить")],
        [KeyboardButton("⚙️ Настройки"), KeyboardButton("ℹ️ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def period_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Ежемесячно", callback_data=f"period:{sub_id}:month"),
        InlineKeyboardButton("📅 Ежегодно", callback_data=f"period:{sub_id}:year"),
    ]])


def category_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for key, label in CATEGORIES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"category:{sub_id}:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def delete_confirm_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_confirm:{sub_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"delete_cancel:{sub_id}"),
    ]])


def build_delete_list_keyboard(rows: list[tuple]) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price
        pause_icon = "⏸" if is_paused else ""
        buttons.append([
            InlineKeyboardButton(
                f"🗑 #{_id} {pause_icon}{name} ({price_view})",
                callback_data=f"delete_ask:{_id}"
            )
        ])
    return InlineKeyboardMarkup(buttons)


def build_edit_list_keyboard(rows: list[tuple]) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price
        pause_icon = "⏸" if is_paused else ""
        buttons.append([
            InlineKeyboardButton(
                f"✏️ #{_id} {pause_icon}{name} ({price_view})",
                callback_data=f"edit_select:{_id}"
            )
        ])
    return InlineKeyboardMarkup(buttons)


def build_edit_field_keyboard(sub_id: int, is_paused: bool = False) -> InlineKeyboardMarkup:
    pause_text = "▶️ Возобновить" if is_paused else "⏸ Пауза"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Название", callback_data=f"edit_field:{sub_id}:name"),
            InlineKeyboardButton("💰 Цена", callback_data=f"edit_field:{sub_id}:price"),
        ],
        [
            InlineKeyboardButton("📅 День", callback_data=f"edit_field:{sub_id}:day"),
            InlineKeyboardButton("🔁 Период", callback_data=f"edit_field:{sub_id}:period"),
        ],
        [
            InlineKeyboardButton("🏷 Категория", callback_data=f"edit_field:{sub_id}:category"),
            InlineKeyboardButton(pause_text, callback_data=f"toggle_pause:{sub_id}"),
        ],
        [
            InlineKeyboardButton("✅ Отметить оплату", callback_data=f"mark_paid:{sub_id}"),
        ],
        [
            InlineKeyboardButton("❌ Закрыть", callback_data="edit_cancel"),
        ],
    ])


def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    reminder_status = "✅" if settings["reminder_enabled"] else "❌"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🔔 Напоминания: {reminder_status}",
                callback_data="settings:toggle_reminder"
            ),
        ],
        [
            InlineKeyboardButton(
                f"📅 За {settings['reminder_days']} {days_word_ru(settings['reminder_days'])} до списания",
                callback_data="settings:reminder_days"
            ),
        ],
        [
            InlineKeyboardButton("📤 Экспорт в CSV", callback_data="settings:export"),
        ],
        [
            InlineKeyboardButton("📜 История платежей", callback_data="settings:history"),
        ],
    ])


def build_reminder_days_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 день", callback_data="set_reminder_days:1"),
            InlineKeyboardButton("2 дня", callback_data="set_reminder_days:2"),
            InlineKeyboardButton("3 дня", callback_data="set_reminder_days:3"),
        ],
        [
            InlineKeyboardButton("5 дней", callback_data="set_reminder_days:5"),
            InlineKeyboardButton("7 дней", callback_data="set_reminder_days:7"),
        ],
        [
            InlineKeyboardButton("« Назад", callback_data="settings:back"),
        ],
    ])


# -----------------------------
# BOT COMMANDS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Я помогу следить за подписками 💳\n\n"
        "Что умею:\n"
        "• Добавлять подписки\n"
        "• Напоминать о списаниях\n"
        "• Считать расходы по категориям\n"
        "• Показывать историю платежей\n\n"
        "Нажми кнопку снизу 👇",
        reply_markup=main_menu_keyboard(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Как пользоваться ботом*\n\n"
        "*Основные функции:*\n"
        "➕ *Добавить* — новая подписка\n"
        "📋 *Список* — все подписки\n"
        "📅 *Ближайшее* — следующие списания\n"
        "💸 *Итого* — расходы по категориям\n"
        "✏️ *Редактировать* — изменить/пауза\n"
        "🗑 *Удалить* — удалить подписку\n"
        "⚙️ *Настройки* — напоминания, экспорт\n\n"
        "*Быстрое добавление:*\n"
        "Напиши одной строкой:\n"
        "`Netflix 129 кр 15.01.26`\n"
        "`Spotify 169 руб 01.02.26`\n\n"
        "*Категории:*\n"
        "🎬 Видео • 🎵 Музыка • 💻 Софт\n"
        "☁️ Облако • 🎮 Игры • 📦 Другое\n\n"
        "*Валюты:*\n"
        "NOK, EUR, USD, RUB, SEK, DKK, GBP\n\n"
        "*Дополнительно:*\n"
        "• ⏸ Пауза — временно отключить\n"
        "• ✅ Оплачено — отметить платёж\n"
        "• 📤 Экспорт — скачать CSV",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


# -----------------------------
# ADD FLOW
# -----------------------------
async def add_flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    count = count_user_subscriptions(user_id)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"У тебя уже {count} подписок — это максимум 😅\n"
            "Удали ненужные, чтобы добавить новые.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END
    
    for k in ("add_name", "add_amount", "add_currency", "add_day", "add_last_date", "add_period", "add_category"):
        context.user_data.pop(k, None)

    await update.message.reply_text(
        "Как называется подписка?\n\n"
        "💡 Или напиши всё одной строкой:\n"
        "`Netflix 129 кр 15.01.26`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_NAME


async def add_flow_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    count = count_user_subscriptions(user_id)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"У тебя уже {count} подписок — это максимум 😅",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    # Быстрое добавление одной строкой
    parsed = try_parse_quick_add(text)
    if parsed:
        name, amount, currency, last_dt, category = parsed

        price = pack_price(amount, currency)
        new_id = add_subscription(
            user_id=user_id,
            name=name,
            price=price,
            day=last_dt.day,
            period=DEFAULT_PERIOD,
            last_charge_date=last_dt.isoformat(),
            category=category,
        )

        price_view = format_price(amount, currency)
        cat_label = CATEGORIES.get(category, "📦 Другое")

        await update.message.reply_text(
            "Добавлено ✅\n\n"
            f"*#{new_id} • {name}*\n"
            f"💰 {price_view}\n"
            f"📌 Последнее списание: {format_date_ru(last_dt)}\n"
            f"🏷 {cat_label}\n\n"
            "Как часто списывается?",
            parse_mode="Markdown",
            reply_markup=period_keyboard(new_id),
        )
        return ConversationHandler.END

    if not text:
        await update.message.reply_text(
            "Название не может быть пустым 🙂",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_NAME

    if len(text) > MAX_NAME_LENGTH:
        await update.message.reply_text(
            f"Слишком длинное название 😅\nМаксимум {MAX_NAME_LENGTH} символов.",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_NAME

    # Проверяем известные сервисы
    text_lower = text.lower()
    if text_lower in KNOWN_SERVICES:
        service = KNOWN_SERVICES[text_lower]
        context.user_data["add_name"] = service["name"]
        context.user_data["add_category"] = service["category"]
        context.user_data["add_suggested_period"] = service["period"]
    else:
        context.user_data["add_name"] = text
        context.user_data["add_category"] = "other"

    await update.message.reply_text(
        "Сколько списывается?\n\n"
        "Примеры:\n"
        "• `128.30`\n"
        "• `12,99 евро`\n"
        "• `199 руб`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_PRICE


async def add_flow_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    parsed = parse_price(raw)

    if not parsed:
        await update.message.reply_text(
            "Не поняла цену 😕\n\n"
            "Примеры: `128.30` | `12,99 евро` | `199 руб`\n\n"
            f"Максимум: {MAX_PRICE:,}".replace(",", " "),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_PRICE

    amount, currency = parsed
    context.user_data["add_amount"] = amount
    context.user_data["add_currency"] = currency

    await update.message.reply_text(
        "Когда было (или будет) списание?\n\n"
        "Примеры:\n"
        "• `29.12.25`\n"
        "• `29.12.2025`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_DATE


async def add_flow_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    raw = (update.message.text or "").strip()

    name = context.user_data.get("add_name")
    amount = context.user_data.get("add_amount")
    currency = context.user_data.get("add_currency")
    category = context.user_data.get("add_category", "other")

    if not name or amount is None or not currency:
        await update.message.reply_text(
            "Данные потеряны 😕\nНажми ➕ Добавить ещё раз.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    last_dt = parse_ru_date(raw)
    if not last_dt:
        await update.message.reply_text(
            "Не поняла дату 😕\n\n"
            "Формат: `29.12.25` или `29.12.2025`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_DATE

    price = pack_price(amount, currency)
    suggested_period = context.user_data.get("add_suggested_period", DEFAULT_PERIOD)

    new_id = add_subscription(
        user_id=user_id,
        name=name,
        price=price,
        day=last_dt.day,
        period=suggested_period,
        last_charge_date=last_dt.isoformat(),
        category=category,
    )

    price_view = format_price(amount, currency)
    cat_label = CATEGORIES.get(category, "📦 Другое")

    await update.message.reply_text(
        "Готово ✅\n\n"
        f"*#{new_id} • {name}*\n"
        f"💰 {price_view}\n"
        f"📌 Последнее списание: {format_date_ru(last_dt)}\n"
        f"🏷 {cat_label}\n\n"
        "Как часто списывается?",
        parse_mode="Markdown",
        reply_markup=period_keyboard(new_id),
    )

    for k in ("add_name", "add_amount", "add_currency", "add_day", "add_last_date", "add_period", "add_category", "add_suggested_period"):
        context.user_data.pop(k, None)

    return ConversationHandler.END


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    count = count_user_subscriptions(user_id)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"У тебя уже {count} подписок — максимум 😅",
            reply_markup=main_menu_keyboard(),
        )
        return

    if len(args) < 3:
        await update.message.reply_text(
            "Примеры:\n"
            "• `/add Netflix 129 15`\n"
            "• `/add Spotify 169 руб 01.02.26`",
            parse_mode="Markdown",
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
                "День: число 1–31 или дата `29.12.25`",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(),
            )
            return

    if len(args) >= 4 and is_currency_token(args[-2]):
        price_raw = f"{args[-3]} {args[-2]}"
        name_parts = args[:-3]
    else:
        price_raw = args[-2]
        name_parts = args[:-2]

    if not name_parts:
        await update.message.reply_text("Не вижу название 😕", reply_markup=main_menu_keyboard())
        return

    name = " ".join(name_parts).strip()
    if len(name) > MAX_NAME_LENGTH:
        await update.message.reply_text(
            f"Название слишком длинное. Максимум {MAX_NAME_LENGTH} символов.",
            reply_markup=main_menu_keyboard(),
        )
        return

    parsed = parse_price(price_raw)
    if not parsed:
        await update.message.reply_text(
            "Не поняла цену 😕",
            reply_markup=main_menu_keyboard(),
        )
        return

    amount, currency = parsed
    price = pack_price(amount, currency)

    # Определяем категорию
    category = "other"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        service = KNOWN_SERVICES[name_lower]
        name = service["name"]
        category = service["category"]

    new_id = add_subscription(user_id, name, price, day, DEFAULT_PERIOD, last_charge_date, category)

    await update.message.reply_text(
        "Добавлено ✅\n\n"
        f"*#{new_id} • {name}*\n"
        f"💰 {format_price(amount, currency)}\n\n"
        "Как часто списывается?",
        parse_mode="Markdown",
        reply_markup=period_keyboard(new_id),
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text(
            "Пока нет подписок 📭\nНажми ➕ Добавить",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Группируем по категориям
    by_category: dict[str, list] = {}
    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        if category not in by_category:
            by_category[category] = []
        by_category[category].append(row)

    lines = ["📋 *Твои подписки:*\n"]
    
    for cat_key in ["video", "music", "software", "cloud", "games", "other"]:
        if cat_key not in by_category:
            continue
        
        cat_label = CATEGORIES.get(cat_key, "📦 Другое")
        lines.append(f"\n{cat_label}")
        
        for row in by_category[cat_key]:
            _id, name, price, day, period, last_charge_date, category, is_paused = row
            pp = unpack_price(price)
            price_view = format_price(pp[0], pp[1]) if pp else price
            
            pause_mark = "⏸ " if is_paused else ""
            period_icon = "🔁" if period == "month" else "📅"
            
            lines.append(f"  *#{_id}* {pause_mark}{name}\n     {price_view} • {period_icon}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Напиши: `/del <id>`\nПример: `/del 3`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.", reply_markup=main_menu_keyboard())
        return

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Подписка не найдена 😕", reply_markup=main_menu_keyboard())
        return

    _id, name, price, day, period, last_charge_date, category, is_paused = sub
    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price

    await update.message.reply_text(
        f"Удалить подписку?\n\n*#{_id} • {name}*\n💰 {price_view}",
        parse_mode="Markdown",
        reply_markup=delete_confirm_keyboard(sub_id),
    )


async def delete_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок 📭", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text(
        "Выбери подписку для удаления:",
        reply_markup=build_delete_list_keyboard(rows),
    )


async def edit_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок 📭", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text(
        "Выбери подписку для редактирования:",
        reply_markup=build_edit_list_keyboard(rows),
    )


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id, include_paused=False)
    
    if not rows:
        await update.message.reply_text(
            "Нет активных подписок 📭",
            reply_markup=main_menu_keyboard(),
        )
        return

    today = date.today()
    upcoming = []

    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        
        if last_charge_date:
            try:
                last_dt = date.fromisoformat(last_charge_date)
                ch = next_from_last(last_dt, period, today)
            except Exception:
                ch = next_by_day(int(day), today)
        else:
            ch = next_by_day(int(day), today)

        upcoming.append((ch, _id, name, price, period, category))

    upcoming.sort(key=lambda x: x[0])

    charge_date, _id, name, price, period, category = upcoming[0]
    delta_days = (charge_date - today).days

    if delta_days == 0:
        in_days = "сегодня! ⚡"
    elif delta_days == 1:
        in_days = "завтра"
    else:
        in_days = f"через {delta_days} {days_word_ru(delta_days)}"

    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price

    text = (
        "📅 *Ближайшие списания*\n\n"
        f"*{name}* — {price_view}\n"
        f"🗓 {format_date_ru(charge_date)}\n"
        f"⏳ {in_days}"
    )

    if len(upcoming) > 1:
        text += "\n\n*Следующие:*"
        for ch, _id2, name2, price2, period2, cat2 in upcoming[1:5]:
            delta2 = (ch - today).days
            pp2 = unpack_price(price2)
            pv2 = format_price(pp2[0], pp2[1]) if pp2 else price2
            text += f"\n• {name2} ({pv2}) — {format_date_short(ch)}"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def sum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id, include_paused=False)
    
    if not rows:
        await update.message.reply_text("Нет активных подписок 📭", reply_markup=main_menu_keyboard())
        return

    # По категориям и валютам
    by_category: dict[str, dict[str, float]] = {}
    totals_month: dict[str, float] = {}
    totals_year: dict[str, float] = {}

    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        pp = unpack_price(price)
        if not pp:
            continue
        
        amount, currency = pp
        
        # По категориям (приводим к месяцу)
        if category not in by_category:
            by_category[category] = {}
        
        monthly_amount = amount if period == "month" else amount / 12
        by_category[category][currency] = by_category[category].get(currency, 0.0) + monthly_amount
        
        # Общие итоги
        if period == "year":
            totals_year[currency] = totals_year.get(currency, 0.0) + amount
        else:
            totals_month[currency] = totals_month.get(currency, 0.0) + amount

    lines = ["💸 *Расходы на подписки*\n"]
    
    # По категориям
    lines.append("*По категориям (в месяц):*")
    for cat_key in ["video", "music", "software", "cloud", "games", "other"]:
        if cat_key not in by_category:
            continue
        cat_label = CATEGORIES.get(cat_key, "📦 Другое")
        amounts = []
        for curr, amt in sorted(by_category[cat_key].items()):
            amounts.append(format_price(amt, curr))
        lines.append(f"  {cat_label}: {', '.join(amounts)}")

    # Итоги
    lines.append("\n─────────────")
    
    if totals_month:
        lines.append("*Ежемесячные:*")
        for c in sorted(totals_month.keys()):
            lines.append(f"  • {format_price(totals_month[c], c)}")

    if totals_year:
        lines.append("*Ежегодные:*")
        for c in sorted(totals_year.keys()):
            lines.append(f"  • {format_price(totals_year[c], c)}")

    # Всего в год
    lines.append("\n*Всего в год:*")
    all_currencies = set(totals_month.keys()) | set(totals_year.keys())
    for c in sorted(all_currencies):
        monthly = totals_month.get(c, 0.0) * 12
        yearly = totals_year.get(c, 0.0)
        lines.append(f"  • {format_price(monthly + yearly, c)}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    await update.message.reply_text(
        "⚙️ *Настройки*",
        parse_mode="Markdown",
        reply_markup=build_settings_keyboard(settings),
    )


# -----------------------------
# INLINE CALLBACKS
# -----------------------------
async def period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, sub_id_str, period = query.data.split(":")
        sub_id = int(sub_id_str)
    except Exception:
        await query.edit_message_text("Ошибка 😕")
        return

    user_id = query.from_user.id
    ok = update_subscription_field(user_id, sub_id, "period", period)
    
    if not ok:
        await query.edit_message_text("Не удалось обновить 😕")
        return

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await query.edit_message_text("Готово ✅")
        return

    _id, name, price, day, period, last_charge_date, category, is_paused = sub
    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price
    cat_label = CATEGORIES.get(category, "📦 Другое")

    extra = ""
    if last_charge_date:
        try:
            d = date.fromisoformat(last_charge_date)
            extra = f"\n📌 Последнее: {format_date_ru(d)}"
        except Exception:
            pass

    await query.edit_message_text(
        f"Готово ✅\n\n"
        f"*#{_id} • {name}*\n"
        f"💰 {price_view}\n"
        f"📅 {day}-го числа\n"
        f"🔁 {period_label(period)}\n"
        f"🏷 {cat_label}"
        f"{extra}",
        parse_mode="Markdown",
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, sub_id_str, category = query.data.split(":")
        sub_id = int(sub_id_str)
    except Exception:
        await query.edit_message_text("Ошибка 😕")
        return

    user_id = query.from_user.id
    ok = update_subscription_field(user_id, sub_id, "category", category)
    
    cat_label = CATEGORIES.get(category, "📦 Другое")
    if ok:
        await query.edit_message_text(f"Категория изменена на {cat_label} ✅")
    else:
        await query.edit_message_text("Не удалось обновить 😕")


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data.startswith("delete_ask:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Подписка не найдена 😕")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price

        await query.edit_message_text(
            f"Удалить подписку?\n\n*#{_id} • {name}*\n💰 {price_view}",
            parse_mode="Markdown",
            reply_markup=delete_confirm_keyboard(sub_id),
        )

    elif data.startswith("delete_confirm:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if sub:
            name = sub[1]
            ok = delete_subscription(user_id, sub_id)
            if ok:
                await query.edit_message_text(f"Удалено ✅\n\n_{name}_", parse_mode="Markdown")
            else:
                await query.edit_message_text("Не удалось удалить 😕")
        else:
            await query.edit_message_text("Подписка не найдена 😕")

    elif data.startswith("delete_cancel:"):
        await query.edit_message_text("Отменено 👌")


async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data.startswith("edit_select:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Подписка не найдена 😕")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price
        cat_label = CATEGORIES.get(category, "📦 Другое")
        pause_status = "⏸ На паузе" if is_paused else "▶️ Активна"

        await query.edit_message_text(
            f"*#{_id} • {name}*\n"
            f"💰 {price_view}\n"
            f"📅 {day}-го • {period_label(period)}\n"
            f"🏷 {cat_label}\n"
            f"📌 {pause_status}\n\n"
            "Что изменить?",
            parse_mode="Markdown",
            reply_markup=build_edit_field_keyboard(sub_id, is_paused),
        )

    elif data.startswith("edit_field:"):
        try:
            parts = data.split(":")
            sub_id = int(parts[1])
            field = parts[2]
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        if field == "period":
            await query.edit_message_text(
                "Выбери период:",
                reply_markup=period_keyboard(sub_id),
            )
        elif field == "category":
            await query.edit_message_text(
                "Выбери категорию:",
                reply_markup=category_keyboard(sub_id),
            )
        else:
            context.user_data["edit_id"] = sub_id
            context.user_data["edit_field"] = field

            prompts = {
                "name": "Введи новое название:",
                "price": "Введи новую цену:\n`129` | `12,99 евро` | `199 руб`",
                "day": "Введи день списания (1–31):",
            }
            await query.edit_message_text(prompts.get(field, "Введи значение:"), parse_mode="Markdown")

    elif data == "edit_cancel":
        context.user_data.pop("edit_id", None)
        context.user_data.pop("edit_field", None)
        await query.edit_message_text("Закрыто 👌")

    elif data.startswith("toggle_pause:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        new_state = toggle_pause_subscription(user_id, sub_id)
        if new_state is None:
            await query.edit_message_text("Не удалось изменить 😕")
            return

        status = "приостановлена ⏸" if new_state else "возобновлена ▶️"
        await query.edit_message_text(f"Подписка {status}")

    elif data.startswith("mark_paid:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка 😕")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Подписка не найдена 😕")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        today = date.today()

        # Записываем платёж в историю
        add_payment(user_id, sub_id, price, today.isoformat())
        
        # Обновляем дату последнего списания
        update_subscription_field(user_id, sub_id, "last_charge_date", today.isoformat())

        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price

        # Вычисляем следующую дату
        if period == "year":
            next_date = add_years(today, 1)
        else:
            next_date = add_months(today, 1)

        await query.edit_message_text(
            f"✅ Оплата отмечена!\n\n"
            f"*{name}* — {price_view}\n"
            f"📅 Оплачено: {format_date_ru(today)}\n"
            f"📅 Следующее: {format_date_ru(next_date)}",
            parse_mode="Markdown",
        )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data == "settings:toggle_reminder":
        settings = get_user_settings(user_id)
        new_value = 0 if settings["reminder_enabled"] else 1
        update_user_setting(user_id, "reminder_enabled", new_value)
        settings["reminder_enabled"] = bool(new_value)
        
        await query.edit_message_text(
            "⚙️ *Настройки*",
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(settings),
        )

    elif data == "settings:reminder_days":
        await query.edit_message_text(
            "За сколько дней напоминать?",
            reply_markup=build_reminder_days_keyboard(),
        )

    elif data.startswith("set_reminder_days:"):
        try:
            days = int(data.split(":")[1])
        except (ValueError, IndexError):
            return
        
        update_user_setting(user_id, "reminder_days", days)
        settings = get_user_settings(user_id)
        
        await query.edit_message_text(
            "⚙️ *Настройки*\n\n"
            f"✅ Напоминание за {days} {days_word_ru(days)}",
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(settings),
        )

    elif data == "settings:back":
        settings = get_user_settings(user_id)
        await query.edit_message_text(
            "⚙️ *Настройки*",
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(settings),
        )

    elif data == "settings:export":
        csv_data = export_to_csv(user_id)
        
        # Отправляем файл
        from io import BytesIO
        file = BytesIO(csv_data.encode('utf-8'))
        file.name = "subscriptions.csv"
        
        await query.message.reply_document(
            document=file,
            filename="subscriptions.csv",
            caption="📤 Экспорт подписок"
        )
        await query.edit_message_text("Экспорт готов ✅")

    elif data == "settings:history":
        history = get_payment_history(user_id, limit=15)
        
        if not history:
            await query.edit_message_text(
                "История платежей пуста 📭\n\n"
                "Отмечай оплату через ✏️ Редактировать → ✅ Отметить оплату"
            )
            return

        lines = ["📜 *История платежей*\n"]
        for _id, sub_id, name, amount, paid_at in history:
            pp = unpack_price(amount)
            price_view = format_price(pp[0], pp[1]) if pp else amount
            try:
                d = date.fromisoformat(paid_at)
                date_str = format_date_short(d)
            except Exception:
                date_str = paid_at
            lines.append(f"• {name} — {price_view} ({date_str})")

        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")


# -----------------------------
# EDIT CONVERSATION (команда /edit)
# -----------------------------
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "Используй: `/edit <id>`\nИли нажми ✏️ Редактировать",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Подписка не найдена 😕", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    context.user_data["edit_id"] = sub_id

    _id, name, price, day, period, last_charge_date, category, is_paused = sub
    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price

    await update.message.reply_text(
        f"Редактируем *#{_id}*:\n\n"
        f"• Название: {name}\n"
        f"• Цена: {price_view}\n"
        f"• День: {day}\n"
        f"• Период: {period_label(period)}\n\n"
        "Что меняем? `name` / `price` / `day`\n"
        "Или /cancel",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()
    if text not in ("name", "price", "day"):
        await update.message.reply_text(
            "Выбери: `name` / `price` / `day`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = text

    prompts = {
        "name": "Новое название:",
        "price": "Новая цена (`129` | `12,99 евро`):",
        "day": "Новый день (1–31):",
    }
    await update.message.reply_text(prompts[text], parse_mode="Markdown", reply_markup=main_menu_keyboard())
    return EDIT_ENTER_VALUE


async def edit_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sub_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    if not sub_id or not field:
        await update.message.reply_text("Начни заново: /edit <id>", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    if field == "day":
        try:
            day = int(raw)
            if not (1 <= day <= 31):
                raise ValueError
            value = day
        except ValueError:
            await update.message.reply_text("День: число 1–31", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
    elif field == "name":
        if not raw or len(raw) > MAX_NAME_LENGTH:
            await update.message.reply_text(
                f"Название: 1–{MAX_NAME_LENGTH} символов",
                reply_markup=main_menu_keyboard(),
            )
            return EDIT_ENTER_VALUE
        value = raw
    elif field == "price":
        parsed = parse_price(raw)
        if not parsed:
            await update.message.reply_text("Не поняла цену 😕", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        value = pack_price(parsed[0], parsed[1])
    else:
        value = raw

    ok = update_subscription_field(user_id, sub_id, field, value)
    await update.message.reply_text("Обновлено ✅" if ok else "Ошибка 😕", reply_markup=main_menu_keyboard())

    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("edit_id", "edit_field", "add_name", "add_amount", "add_currency",
              "add_day", "add_last_date", "add_period", "add_category", "add_suggested_period"):
        context.user_data.pop(k, None)

    await update.message.reply_text("Отменено 👌", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# -----------------------------
# MENU ROUTER
# -----------------------------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    # Проверяем inline-редактирование
    if context.user_data.get("edit_id") and context.user_data.get("edit_field"):
        sub_id = context.user_data["edit_id"]
        field = context.user_data["edit_field"]

        if field == "day":
            try:
                value = int(text)
                if not (1 <= value <= 31):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("День: число 1–31", reply_markup=main_menu_keyboard())
                return
        elif field == "name":
            if not text or len(text) > MAX_NAME_LENGTH:
                await update.message.reply_text(f"Название: 1–{MAX_NAME_LENGTH} символов", reply_markup=main_menu_keyboard())
                return
            value = text
        elif field == "price":
            parsed = parse_price(text)
            if not parsed:
                await update.message.reply_text("Не поняла цену 😕", reply_markup=main_menu_keyboard())
                return
            value = pack_price(parsed[0], parsed[1])
        else:
            value = text

        ok = update_subscription_field(user_id, sub_id, field, value)
        context.user_data.pop("edit_id", None)
        context.user_data.pop("edit_field", None)
        await update.message.reply_text("Обновлено ✅" if ok else "Ошибка 😕", reply_markup=main_menu_keyboard())
        return

    # Кнопки меню
    if text == "📋 Список":
        await list_cmd(update, context)
    elif text == "📅 Ближайшее":
        await next_cmd(update, context)
    elif text == "💸 Итого":
        await sum_cmd(update, context)
    elif text == "✏️ Редактировать":
        await edit_button_handler(update, context)
    elif text == "🗑 Удалить":
        await delete_button_handler(update, context)
    elif text == "⚙️ Настройки":
        await settings_cmd(update, context)
    elif text == "ℹ️ Помощь":
        await help_cmd(update, context)
    else:
        # Быстрое добавление
        parsed = try_parse_quick_add(text)
        if parsed:
            count = count_user_subscriptions(user_id)
            if count >= MAX_SUBSCRIPTIONS_PER_USER:
                await update.message.reply_text(f"Максимум {MAX_SUBSCRIPTIONS_PER_USER} подписок 😅", reply_markup=main_menu_keyboard())
                return

            name, amount, currency, last_dt, category = parsed
            price = pack_price(amount, currency)

            new_id = add_subscription(
                user_id=user_id,
                name=name,
                price=price,
                day=last_dt.day,
                period=DEFAULT_PERIOD,
                last_charge_date=last_dt.isoformat(),
                category=category,
            )

            price_view = format_price(amount, currency)
            cat_label = CATEGORIES.get(category, "📦 Другое")

            await update.message.reply_text(
                "Добавлено ✅\n\n"
                f"*#{new_id} • {name}*\n"
                f"💰 {price_view}\n"
                f"📌 {format_date_ru(last_dt)}\n"
                f"🏷 {cat_label}\n\n"
                "Период?",
                parse_mode="Markdown",
                reply_markup=period_keyboard(new_id),
            )
        else:
            await update.message.reply_text("Нажми кнопку 👇", reply_markup=main_menu_keyboard())


# -----------------------------
# REMINDER JOB
# -----------------------------
async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет напоминания о предстоящих списаниях"""
    logger.info("Running reminder job...")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Получаем всех пользователей с включёнными напоминаниями
    cur.execute("""
        SELECT DISTINCT s.user_id, us.reminder_days, us.reminder_enabled
        FROM subscriptions s
        LEFT JOIN user_settings us ON s.user_id = us.user_id
        WHERE s.is_paused = 0
    """)
    users = cur.fetchall()
    
    today = date.today()
    
    for user_id, reminder_days, reminder_enabled in users:
        # По умолчанию напоминания включены
        if reminder_enabled == 0:
            continue
        
        reminder_days = reminder_days or 1
        target_date = today + timedelta(days=reminder_days)
        
        # Получаем подписки пользователя
        cur.execute("""
            SELECT id, name, price, day, period, last_charge_date
            FROM subscriptions
            WHERE user_id = ? AND is_paused = 0
        """, (user_id,))
        subs = cur.fetchall()
        
        reminders = []
        for _id, name, price, day, period, last_charge_date in subs:
            if last_charge_date:
                try:
                    last_dt = date.fromisoformat(last_charge_date)
                    next_charge = next_from_last(last_dt, period, today)
                except Exception:
                    next_charge = next_by_day(int(day), today)
            else:
                next_charge = next_by_day(int(day), today)
            
            # Проверяем, попадает ли в целевую дату
            if next_charge == target_date:
                pp = unpack_price(price)
                price_view = format_price(pp[0], pp[1]) if pp else price
                reminders.append(f"• *{name}* — {price_view}")
        
        if reminders:
            try:
                if reminder_days == 1:
                    when = "завтра"
                else:
                    when = f"через {reminder_days} {days_word_ru(reminder_days)}"
                
                text = f"🔔 *Напоминание о списаниях*\n\n{when} ({format_date_ru(target_date)}):\n\n" + "\n".join(reminders)
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="Markdown",
                )
                logger.info(f"Sent reminder to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send reminder to {user_id}: {e}")
    
    conn.close()


# -----------------------------
# ERROR HANDLER
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "Ошибка 😕 Попробуй /start",
                reply_markup=main_menu_keyboard(),
            )
    except Exception:
        pass


# -----------------------------
# POST INIT
# -----------------------------
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("add", "Добавить подписку"),
        BotCommand("list", "Список подписок"),
        BotCommand("next", "Ближайшие списания"),
        BotCommand("sum", "Итого расходов"),
        BotCommand("edit", "Редактировать"),
        BotCommand("del", "Удалить"),
        BotCommand("settings", "Настройки"),
        BotCommand("cancel", "Отмена"),
        BotCommand("help", "Помощь"),
    ]
    await application.bot.set_my_commands(commands)
    
    # Запускаем job для напоминаний (каждый день в REMINDER_HOUR:REMINDER_MINUTE UTC)
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_daily(
            send_reminders,
            time=time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE),
            name="daily_reminders",
        )
        logger.info(f"Reminder job scheduled for {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} UTC")


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    init_db()
    
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Conversation handlers
    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Добавить$"), add_flow_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_price)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_ENTER_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_enter_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("del", del_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(CommandHandler("sum", sum_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("cancel", cancel))

    # Callbacks
    application.add_handler(CallbackQueryHandler(period_callback, pattern=r"^period:\d+:(month|year)$"))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r"^category:\d+:\w+$"))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^delete_(ask|confirm|cancel):\d+$"))
    application.add_handler(CallbackQueryHandler(edit_callback, pattern=r"^(edit_select|edit_field|edit_cancel|toggle_pause|mark_paid)"))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(settings:|set_reminder_days:)"))

    # Conversations
    application.add_handler(add_conv)
    application.add_handler(edit_conv)

    # Menu router (последний)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    application.add_error_handler(error_handler)

    logger.info("Bot starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
