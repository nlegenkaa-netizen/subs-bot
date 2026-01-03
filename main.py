import os
import sqlite3
import logging
import calendar
from typing import Optional
from datetime import date, datetime, time, timedelta
from io import BytesIO

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

MAX_NAME_LENGTH = 100
MAX_PRICE = 1_000_000
MAX_SUBSCRIPTIONS_PER_USER = 50

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


# -----------------------------
# KNOWN SERVICES
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

MONTHS_RU_SHORT = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр",
    5: "май", 6: "июн", 7: "июл", 8: "авг",
    9: "сен", 10: "окт", 11: "ноя", 12: "дек",
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
    while candidate < today:
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

    category = "other"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        service = KNOWN_SERVICES[name_lower]
        name = service["name"]
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


DEFAULT_PERIOD = "month"


def period_label(period: str) -> str:
    return "ежемесячно" if period == "month" else "ежегодно"


# -----------------------------
# DB LAYER
# -----------------------------
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            reminder_days INTEGER DEFAULT 1,
            reminder_enabled INTEGER DEFAULT 1,
            timezone_offset INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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


def find_duplicate_subscription(user_id: int, name: str) -> Optional[tuple]:
    """Ищет подписку с похожим названием (без учёта регистра)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, name, price, day, period, last_charge_date, category, is_paused 
           FROM subscriptions 
           WHERE user_id = ? AND LOWER(name) = LOWER(?)""",
        (user_id, name),
    )
    row = cur.fetchone()
    conn.close()
    return row


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


def get_subscription_payment_history(user_id: int, sub_id: int) -> list[tuple]:
    """Получить всю историю платежей по конкретной подписке"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT ph.id, ph.amount, ph.paid_at 
           FROM payment_history ph
           WHERE ph.user_id = ? AND ph.subscription_id = ?
           ORDER BY ph.paid_at DESC""",
        (user_id, sub_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_yearly_stats(user_id: int, year: int) -> dict:
    """Получить статистику платежей за год"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"
    
    # Платежи по месяцам
    cur.execute(
        """SELECT strftime('%m', paid_at) as month, amount
           FROM payment_history
           WHERE user_id = ? AND paid_at >= ? AND paid_at <= ?
           ORDER BY paid_at""",
        (user_id, start_date, end_date),
    )
    payments = cur.fetchall()
    
    # Группируем по месяцам и валютам
    monthly: dict[int, dict[str, float]] = {}
    total_by_currency: dict[str, float] = {}
    
    for month_str, amount in payments:
        month = int(month_str)
        pp = unpack_price(amount)
        if not pp:
            continue
        amt, curr = pp
        
        if month not in monthly:
            monthly[month] = {}
        monthly[month][curr] = monthly[month].get(curr, 0) + amt
        total_by_currency[curr] = total_by_currency.get(curr, 0) + amt
    
    # Платежи по подпискам
    cur.execute(
        """SELECT s.name, SUM(
               CAST(SUBSTR(ph.amount, 1, INSTR(ph.amount, ' ') - 1) AS REAL)
           ) as total, 
           SUBSTR(ph.amount, INSTR(ph.amount, ' ') + 1) as currency,
           COUNT(*) as count
           FROM payment_history ph
           JOIN subscriptions s ON ph.subscription_id = s.id
           WHERE ph.user_id = ? AND ph.paid_at >= ? AND ph.paid_at <= ?
           GROUP BY ph.subscription_id, currency
           ORDER BY total DESC""",
        (user_id, start_date, end_date),
    )
    by_subscription = cur.fetchall()
    
    conn.close()
    
    return {
        "year": year,
        "monthly": monthly,
        "total_by_currency": total_by_currency,
        "by_subscription": by_subscription,
        "payment_count": len(payments),
    }


# -----------------------------
# EXPORT
# -----------------------------
def export_to_csv(user_id: int) -> str:
    rows = list_subscriptions(user_id)
    lines = ["name,price,currency,day,period,category,is_paused,last_charge_date"]

    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        pp = unpack_price(price)
        if pp:
            amount, currency = pp
        else:
            amount, currency = 0, "NOK"

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
        [KeyboardButton("📊 Статистика"), KeyboardButton("⚙️ Настройки")],
        [KeyboardButton("ℹ️ Помощь")],
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


async def duplicate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора при дубликате"""
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("dup_payment:"):
        # 💰 Записать платёж — добавляет в историю + обновляет дату
        try:
            parts = data.split(":", 2)
            existing_id = int(parts[1])
            new_data = parts[2]
            
            data_parts = new_data.split("|")
            if len(data_parts) >= 4:
                name = data_parts[0]
                amount = float(data_parts[1])
                currency = data_parts[2]
                last_date = data_parts[3]
                
                if last_date:
                    new_price = pack_price(amount, currency)
                    
                    # Записываем платёж в историю
                    add_payment(user_id, existing_id, new_price, last_date)
                    
                    # Обновляем дату и цену
                    update_subscription_field(user_id, existing_id, "last_charge_date", last_date)
                    update_subscription_field(user_id, existing_id, "price", new_price)
                    
                    try:
                        d = date.fromisoformat(last_date)
                        date_str = format_date_ru(d)
                    except:
                        date_str = last_date
                    
                    price_view = format_price(amount, currency)
                    
                    await query.edit_message_text(
                        f"✅ Платёж записан!\n\n"
                        f"*{name}*\n"
                        f"💰 {price_view}\n"
                        f"📅 {date_str}",
                        parse_mode="Markdown",
                    )
                    return
        except Exception as e:
            logger.error(f"dup_payment error: {e}")
        
        await query.edit_message_text("Ошибка 😕")
    
    elif data.startswith("dup_update:"):
        # 🔄 Исправить данные — только обновляет, НЕ записывает в историю
        try:
            parts = data.split(":", 2)
            existing_id = int(parts[1])
            new_data = parts[2]
            
            data_parts = new_data.split("|")
            if len(data_parts) >= 4:
                amount = float(data_parts[1])
                currency = data_parts[2]
                last_date = data_parts[3]
                
                if last_date:
                    update_subscription_field(user_id, existing_id, "last_charge_date", last_date)
                
                if amount and currency:
                    new_price = pack_price(amount, currency)
                    update_subscription_field(user_id, existing_id, "price", new_price)
                
                await query.edit_message_text(
                    f"✅ Данные исправлены\n\n"
                    f"Подписка #{existing_id} обновлена",
                )
                return
        except Exception as e:
            logger.error(f"dup_update error: {e}")
        
        await query.edit_message_text("Ошибка 😕")
    
    elif data.startswith("dup_create:"):
        # ➕ Создать новую — отдельная подписка
        try:
            new_data = data.split(":", 1)[1]
            data_parts = new_data.split("|")
            
            if len(data_parts) >= 5:
                name = data_parts[0]
                amount = float(data_parts[1])
                currency = data_parts[2]
                last_date = data_parts[3]
                category = data_parts[4]
                day = int(data_parts[5]) if len(data_parts) > 5 else date.fromisoformat(last_date).day
                
                price = pack_price(amount, currency)
                new_id = add_subscription(
                    user_id=user_id,
                    name=name,
                    price=price,
                    day=day,
                    period=DEFAULT_PERIOD,
                    last_charge_date=last_date if last_date else None,
                    category=category,
                )
                
                price_view = format_price(amount, currency)
                
                await query.edit_message_text(
                    f"Добавлено ✅\n\n*#{new_id} • {name}*\n💰 {price_view}",
                    parse_mode="Markdown",
                )
                
                await query.message.reply_text(
                    "Период?",
                    reply_markup=period_keyboard(new_id),
                )
                return
        except Exception as e:
            logger.error(f"dup_create error: {e}")
        
        await query.edit_message_text("Ошибка 😕")
    
    elif data == "dup_cancel":
        await query.edit_message_text("Отменено 👌")



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
            InlineKeyboardButton("📜 История платежей", callback_data=f"sub_history:{sub_id}"),
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
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data="settings:edit_subs"),
            InlineKeyboardButton("🗑 Удалить", callback_data="settings:delete_subs"),
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


def build_year_keyboard(current_year: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора года статистики"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(str(current_year - 1), callback_data=f"stats_year:{current_year - 1}"),
            InlineKeyboardButton(f"📊 {current_year}", callback_data=f"stats_year:{current_year}"),
            InlineKeyboardButton(str(current_year + 1), callback_data=f"stats_year:{current_year + 1}"),
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
        "• Считать расходы\n"
        "• Показывать статистику за год\n\n"
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
        "📊 *Статистика* — история за год\n"
        "⚙️ *Настройки* — напоминания, экспорт\n\n"
        "*Быстрое добавление:*\n"
        "`Netflix 129 кр 15.01.26`\n\n"
        "*Категории:*\n"
        "🎬 Видео • 🎵 Музыка • 💻 Софт\n"
        "☁️ Облако • 🎮 Игры • 📦 Другое\n\n"
        "*Валюты:* NOK, EUR, USD, RUB, SEK, DKK, GBP",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


# -----------------------------
# ADD FLOW WITH DUPLICATE CHECK
# -----------------------------
async def add_flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    count = count_user_subscriptions(user_id)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"У тебя уже {count} подписок — максимум 😅",
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
            f"У тебя уже {count} подписок — максимум 😅",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    # Быстрое добавление
    parsed = try_parse_quick_add(text)
    if parsed:
        name, amount, currency, last_dt, category = parsed
        
        # Проверка дубликата
        duplicate = find_duplicate_subscription(user_id, name)
        if duplicate:
            dup_id, dup_name, dup_price, dup_day, dup_period, dup_last, dup_cat, dup_paused = duplicate
            pp = unpack_price(dup_price)
            dup_price_view = format_price(pp[0], pp[1]) if pp else dup_price
            
            # Сохраняем данные для callback
            new_data = f"{name}|{amount}|{currency}|{last_dt.isoformat()}|{category}"
            
            await update.message.reply_text(
                f"⚠️ *«{dup_name}» уже есть:*\n\n"
                f"#{dup_id} • {dup_price_view} • {period_label(dup_period)}\n\n"
                "Что сделать?",
                parse_mode="Markdown",
                reply_markup=duplicate_keyboard(dup_id, new_data),
            )
            return ConversationHandler.END

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
            f"📌 Последнее: {format_date_ru(last_dt)}\n"
            f"🏷 {cat_label}\n\n"
            "Период?",
            parse_mode="Markdown",
            reply_markup=period_keyboard(new_id),
        )
        return ConversationHandler.END

    if not text:
        await update.message.reply_text("Название не может быть пустым 🙂", reply_markup=main_menu_keyboard())
        return ADD_NAME

    if len(text) > MAX_NAME_LENGTH:
        await update.message.reply_text(f"Максимум {MAX_NAME_LENGTH} символов", reply_markup=main_menu_keyboard())
        return ADD_NAME

    # Проверка дубликата по названию
    name_to_check = text
    text_lower = text.lower()
    if text_lower in KNOWN_SERVICES:
        name_to_check = KNOWN_SERVICES[text_lower]["name"]
    
    duplicate = find_duplicate_subscription(user_id, name_to_check)
    if duplicate:
        # Сохраняем имя и ждём остальные данные
        context.user_data["add_name"] = name_to_check
        context.user_data["add_duplicate"] = duplicate
        
        if text_lower in KNOWN_SERVICES:
            service = KNOWN_SERVICES[text_lower]
            context.user_data["add_category"] = service["category"]
            context.user_data["add_suggested_period"] = service["period"]
        else:
            context.user_data["add_category"] = "other"

    else:
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
        "Примеры: `128.30` | `12,99 евро` | `199 руб`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ADD_PRICE


async def add_flow_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    parsed = parse_price(raw)

    if not parsed:
        await update.message.reply_text(
            "Не поняла цену 😕\n\nПримеры: `128.30` | `12,99 евро` | `199 руб`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_PRICE

    amount, currency = parsed
    context.user_data["add_amount"] = amount
    context.user_data["add_currency"] = currency

    await update.message.reply_text(
        "Когда было списание?\n\nФормат: `29.12.25` или `29.12.2025`",
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
    duplicate = context.user_data.get("add_duplicate")

    if not name or amount is None or not currency:
        await update.message.reply_text("Данные потеряны 😕 Нажми ➕ Добавить", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    last_dt = parse_ru_date(raw)
    if not last_dt:
        await update.message.reply_text(
            "Не поняла дату 😕\n\nФормат: `29.12.25`",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ADD_DATE

    # Если есть дубликат — показываем выбор
    if duplicate:
        dup_id, dup_name, dup_price, dup_day, dup_period, dup_last, dup_cat, dup_paused = duplicate
        pp = unpack_price(dup_price)
        dup_price_view = format_price(pp[0], pp[1]) if pp else dup_price
        
        new_data = f"{name}|{amount}|{currency}|{last_dt.isoformat()}|{category}"
        
        await update.message.reply_text(
            f"⚠️ *«{dup_name}» уже есть:*\n\n"
            f"#{dup_id} • {dup_price_view} • {period_label(dup_period)}\n\n"
            "Что сделать?",
            parse_mode="Markdown",
            reply_markup=duplicate_keyboard(dup_id, new_data),
        )
        
        # Очищаем
        for k in ("add_name", "add_amount", "add_currency", "add_category", "add_duplicate", "add_suggested_period"):
            context.user_data.pop(k, None)
        
        return ConversationHandler.END

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
        f"📌 Последнее: {format_date_ru(last_dt)}\n"
        f"🏷 {cat_label}\n\n"
        "Период?",
        parse_mode="Markdown",
        reply_markup=period_keyboard(new_id),
    )

    for k in ("add_name", "add_amount", "add_currency", "add_category", "add_duplicate", "add_suggested_period"):
        context.user_data.pop(k, None)

    return ConversationHandler.END


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    count = count_user_subscriptions(user_id)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(f"Максимум {MAX_SUBSCRIPTIONS_PER_USER} подписок", reply_markup=main_menu_keyboard())
        return

    if len(args) < 3:
        await update.message.reply_text(
            "Примеры:\n`/add Netflix 129 15`\n`/add Spotify 169 руб 01.02.26`",
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
            await update.message.reply_text("День: 1–31 или дата", reply_markup=main_menu_keyboard())
            return

    if len(args) >= 4 and is_currency_token(args[-2]):
        price_raw = f"{args[-3]} {args[-2]}"
        name_parts = args[:-3]
    else:
        price_raw = args[-2]
        name_parts = args[:-2]

    if not name_parts:
        await update.message.reply_text("Не вижу название", reply_markup=main_menu_keyboard())
        return

    name = " ".join(name_parts).strip()
    if len(name) > MAX_NAME_LENGTH:
        await update.message.reply_text(f"Название: максимум {MAX_NAME_LENGTH} символов", reply_markup=main_menu_keyboard())
        return

    parsed = parse_price(price_raw)
    if not parsed:
        await update.message.reply_text("Не поняла цену", reply_markup=main_menu_keyboard())
        return

    amount, currency = parsed

    category = "other"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        service = KNOWN_SERVICES[name_lower]
        name = service["name"]
        category = service["category"]

    # Проверка дубликата
    duplicate = find_duplicate_subscription(user_id, name)
    if duplicate:
        dup_id, dup_name, dup_price, _, dup_period, _, _, _ = duplicate
        pp = unpack_price(dup_price)
        dup_price_view = format_price(pp[0], pp[1]) if pp else dup_price
        
        new_data = f"{name}|{amount}|{currency}|{last_charge_date or ''}|{category}|{day}"
        
        await update.message.reply_text(
            f"⚠️ *«{dup_name}» уже есть:*\n\n"
            f"#{dup_id} • {dup_price_view} • {period_label(dup_period)}\n\n"
            "Что сделать?",
            parse_mode="Markdown",
            reply_markup=duplicate_keyboard(dup_id, new_data),
        )
        return

    price = pack_price(amount, currency)
    new_id = add_subscription(user_id, name, price, day, DEFAULT_PERIOD, last_charge_date, category)

    await update.message.reply_text(
        f"Добавлено ✅\n\n*#{new_id} • {name}*\n💰 {format_price(amount, currency)}\n\nПериод?",
        parse_mode="Markdown",
        reply_markup=period_keyboard(new_id),
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок 📭", reply_markup=main_menu_keyboard())
        return

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

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("`/del <id>`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID — число", reply_markup=main_menu_keyboard())
        return

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Не найдено", reply_markup=main_menu_keyboard())
        return

    _id, name, price, day, period, last_charge_date, category, is_paused = sub
    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price

    await update.message.reply_text(
        f"Удалить?\n\n*#{_id} • {name}*\n💰 {price_view}",
        parse_mode="Markdown",
        reply_markup=delete_confirm_keyboard(sub_id),
    )


async def delete_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Нет подписок 📭", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text("Выбери для удаления:", reply_markup=build_delete_list_keyboard(rows))


async def edit_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Нет подписок 📭", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text("Выбери для редактирования:", reply_markup=build_edit_list_keyboard(rows))


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id, include_paused=False)

    if not rows:
        await update.message.reply_text("Нет активных подписок 📭", reply_markup=main_menu_keyboard())
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

    by_category: dict[str, dict[str, float]] = {}
    totals_month: dict[str, float] = {}
    totals_year: dict[str, float] = {}

    for row in rows:
        _id, name, price, day, period, last_charge_date, category, is_paused = row
        pp = unpack_price(price)
        if not pp:
            continue

        amount, currency = pp

        if category not in by_category:
            by_category[category] = {}

        monthly_amount = amount if period == "month" else amount / 12
        by_category[category][currency] = by_category[category].get(currency, 0.0) + monthly_amount

        if period == "year":
            totals_year[currency] = totals_year.get(currency, 0.0) + amount
        else:
            totals_month[currency] = totals_month.get(currency, 0.0) + amount

    lines = ["💸 *Расходы на подписки*\n"]

    lines.append("*По категориям (в месяц):*")
    for cat_key in ["video", "music", "software", "cloud", "games", "other"]:
        if cat_key not in by_category:
            continue
        cat_label = CATEGORIES.get(cat_key, "📦 Другое")
        amounts = [format_price(amt, curr) for curr, amt in sorted(by_category[cat_key].items())]
        lines.append(f"  {cat_label}: {', '.join(amounts)}")

    lines.append("\n─────────────")

    if totals_month:
        lines.append("*Ежемесячные:*")
        for c in sorted(totals_month.keys()):
            lines.append(f"  • {format_price(totals_month[c], c)}")

    if totals_year:
        lines.append("*Ежегодные:*")
        for c in sorted(totals_year.keys()):
            lines.append(f"  • {format_price(totals_year[c], c)}")

    lines.append("\n*Всего в год:*")
    all_currencies = set(totals_month.keys()) | set(totals_year.keys())
    for c in sorted(all_currencies):
        monthly = totals_month.get(c, 0.0) * 12
        yearly = totals_year.get(c, 0.0)
        lines.append(f"  • {format_price(monthly + yearly, c)}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать статистику за год"""
    user_id = update.effective_user.id
    current_year = date.today().year
    
    await show_yearly_stats(update.message, user_id, current_year)


async def show_yearly_stats(message, user_id: int, year: int) -> None:
    """Показать статистику за конкретный год"""
    stats = get_yearly_stats(user_id, year)
    
    if stats["payment_count"] == 0:
        await message.reply_text(
            f"📊 *Статистика за {year}*\n\n"
            "Нет данных о платежах.\n\n"
            "💡 Отмечай оплату через:\n"
            "✏️ Редактировать → ✅ Отметить оплату",
            parse_mode="Markdown",
            reply_markup=build_year_keyboard(year),
        )
        return
    
    lines = [f"📊 *Статистика за {year}*\n"]
    
    # По месяцам
    lines.append("*По месяцам:*")
    for month in range(1, 13):
        if month in stats["monthly"]:
            amounts = [format_price(amt, curr) for curr, amt in sorted(stats["monthly"][month].items())]
            lines.append(f"  {MONTHS_RU_SHORT[month]}: {', '.join(amounts)}")
    
    # Итого за год
    if stats["total_by_currency"]:
        lines.append("\n*Итого за год:*")
        for curr, amt in sorted(stats["total_by_currency"].items()):
            lines.append(f"  • {format_price(amt, curr)}")
    
    # По подпискам
    if stats["by_subscription"]:
        lines.append("\n*По подпискам:*")
        for name, total, currency, count in stats["by_subscription"][:10]:
            lines.append(f"  • {name}: {format_price(total, currency)} ({count} платежей)")
    
    lines.append(f"\n_Всего платежей: {stats['payment_count']}_")
    
    await message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=build_year_keyboard(year),
    )


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
        await query.edit_message_text("Ошибка")
        return

    user_id = query.from_user.id
    update_subscription_field(user_id, sub_id, "period", period)

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
        f"🏷 {cat_label}{extra}",
        parse_mode="Markdown",
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, sub_id_str, category = query.data.split(":")
        sub_id = int(sub_id_str)
    except Exception:
        await query.edit_message_text("Ошибка")
        return

    user_id = query.from_user.id
    update_subscription_field(user_id, sub_id, "category", category)

    cat_label = CATEGORIES.get(category, "📦 Другое")
    await query.edit_message_text(f"Категория: {cat_label} ✅")


async def duplicate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора при дубликате"""
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("dup_update:"):
        # Обновить дату существующей
        try:
            parts = data.split(":", 2)
            existing_id = int(parts[1])
            new_data = parts[2]
            
            # Парсим данные
            data_parts = new_data.split("|")
            if len(data_parts) >= 4:
                last_date = data_parts[3]
                if last_date:
                    update_subscription_field(user_id, existing_id, "last_charge_date", last_date)
                    
                    # Также обновляем цену если передана
                    if len(data_parts) >= 3:
                        amount = float(data_parts[1])
                        currency = data_parts[2]
                        new_price = pack_price(amount, currency)
                        update_subscription_field(user_id, existing_id, "price", new_price)
                    
                    await query.edit_message_text(
                        f"✅ Подписка #{existing_id} обновлена\n\n"
                        f"📌 Новая дата: {last_date}",
                    )
                    return
        except Exception as e:
            logger.error(f"dup_update error: {e}")
        
        await query.edit_message_text("Ошибка при обновлении 😕")
    
    elif data.startswith("dup_create:"):
        # Создать новую
        try:
            new_data = data.split(":", 1)[1]
            data_parts = new_data.split("|")
            
            if len(data_parts) >= 5:
                name = data_parts[0]
                amount = float(data_parts[1])
                currency = data_parts[2]
                last_date = data_parts[3]
                category = data_parts[4]
                day = int(data_parts[5]) if len(data_parts) > 5 else date.fromisoformat(last_date).day
                
                price = pack_price(amount, currency)
                new_id = add_subscription(
                    user_id=user_id,
                    name=name,
                    price=price,
                    day=day,
                    period=DEFAULT_PERIOD,
                    last_charge_date=last_date if last_date else None,
                    category=category,
                )
                
                price_view = format_price(amount, currency)
                
                await query.edit_message_text(
                    f"Добавлено ✅\n\n*#{new_id} • {name}*\n💰 {price_view}",
                    parse_mode="Markdown",
                )
                
                # Отправляем выбор периода отдельным сообщением
                await query.message.reply_text(
                    "Период?",
                    reply_markup=period_keyboard(new_id),
                )
                return
        except Exception as e:
            logger.error(f"dup_create error: {e}")
        
        await query.edit_message_text("Ошибка при создании 😕")
    
    elif data == "dup_cancel":
        await query.edit_message_text("Отменено 👌")


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data.startswith("delete_ask:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Не найдено")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price

        await query.edit_message_text(
            f"Удалить?\n\n*#{_id} • {name}*\n💰 {price_view}",
            parse_mode="Markdown",
            reply_markup=delete_confirm_keyboard(sub_id),
        )

    elif data.startswith("delete_confirm:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if sub:
            name = sub[1]
            delete_subscription(user_id, sub_id)
            await query.edit_message_text(f"Удалено ✅\n\n_{name}_", parse_mode="Markdown")
        else:
            await query.edit_message_text("Не найдено")

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
            await query.edit_message_text("Ошибка")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Не найдено")
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
            await query.edit_message_text("Ошибка")
            return

        if field == "period":
            await query.edit_message_text("Период:", reply_markup=period_keyboard(sub_id))
        elif field == "category":
            await query.edit_message_text("Категория:", reply_markup=category_keyboard(sub_id))
        else:
            context.user_data["edit_id"] = sub_id
            context.user_data["edit_field"] = field

            prompts = {
                "name": "Новое название:",
                "price": "Новая цена (`129` | `12,99 евро`):",
                "day": "День (1–31):",
            }
            await query.edit_message_text(prompts.get(field, "Значение:"), parse_mode="Markdown")

    elif data == "edit_cancel":
        context.user_data.pop("edit_id", None)
        context.user_data.pop("edit_field", None)
        await query.edit_message_text("Закрыто 👌")

    elif data.startswith("toggle_pause:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка")
            return

        new_state = toggle_pause_subscription(user_id, sub_id)
        if new_state is None:
            await query.edit_message_text("Ошибка")
            return

        status = "приостановлена ⏸" if new_state else "возобновлена ▶️"
        await query.edit_message_text(f"Подписка {status}")

    elif data.startswith("mark_paid:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Не найдено")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        today = date.today()

        add_payment(user_id, sub_id, price, today.isoformat())
        update_subscription_field(user_id, sub_id, "last_charge_date", today.isoformat())

        pp = unpack_price(price)
        price_view = format_price(pp[0], pp[1]) if pp else price

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

    elif data.startswith("sub_history:"):
        try:
            sub_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("Ошибка")
            return

        sub = get_subscription_by_id(user_id, sub_id)
        if not sub:
            await query.edit_message_text("Не найдено")
            return

        _id, name, price, day, period, last_charge_date, category, is_paused = sub
        history = get_subscription_payment_history(user_id, sub_id)

        if not history:
            await query.edit_message_text(
                f"📜 *История: {name}*\n\n"
                "Нет платежей.\n\n"
                "Отмечай через ✅ Отметить оплату",
                parse_mode="Markdown",
            )
            return

        lines = [f"📜 *История: {name}*\n"]
        
        total_by_currency: dict[str, float] = {}
        
        for _pid, amount, paid_at in history:
            pp = unpack_price(amount)
            if pp:
                amt, curr = pp
                price_view = format_price(amt, curr)
                total_by_currency[curr] = total_by_currency.get(curr, 0) + amt
            else:
                price_view = amount
            
            try:
                d = date.fromisoformat(paid_at)
                date_str = format_date_short(d)
            except Exception:
                date_str = paid_at
            
            lines.append(f"• {date_str} — {price_view}")

        lines.append(f"\n*Всего платежей:* {len(history)}")
        
        if total_by_currency:
            totals = [format_price(amt, curr) for curr, amt in sorted(total_by_currency.items())]
            lines.append(f"*Сумма:* {', '.join(totals)}")

        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")


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
            f"⚙️ *Настройки*\n\n✅ За {days} {days_word_ru(days)}",
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

        file = BytesIO(csv_data.encode('utf-8'))
        file.name = "subscriptions.csv"

        await query.message.reply_document(document=file, filename="subscriptions.csv", caption="📤 Экспорт")
        await query.edit_message_text("Экспорт готов ✅")

    elif data == "settings:history":
        history = get_payment_history(user_id, limit=20)

        if not history:
            await query.edit_message_text(
                "История пуста 📭\n\nОтмечай через ✏️ → ✅ Оплачено"
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

    elif data == "settings:edit_subs":
        rows = list_subscriptions(user_id)
        if not rows:
            await query.edit_message_text("Нет подписок 📭")
            return
        await query.edit_message_text("Выбери:", reply_markup=build_edit_list_keyboard(rows))

    elif data == "settings:delete_subs":
        rows = list_subscriptions(user_id)
        if not rows:
            await query.edit_message_text("Нет подписок 📭")
            return
        await query.edit_message_text("Выбери:", reply_markup=build_delete_list_keyboard(rows))


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора года статистики"""
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("stats_year:"):
        try:
            year = int(data.split(":")[1])
        except (ValueError, IndexError):
            return
        
        stats = get_yearly_stats(user_id, year)
        
        if stats["payment_count"] == 0:
            await query.edit_message_text(
                f"📊 *Статистика за {year}*\n\nНет данных",
                parse_mode="Markdown",
                reply_markup=build_year_keyboard(year),
            )
            return
        
        lines = [f"📊 *Статистика за {year}*\n"]
        
        lines.append("*По месяцам:*")
        for month in range(1, 13):
            if month in stats["monthly"]:
                amounts = [format_price(amt, curr) for curr, amt in sorted(stats["monthly"][month].items())]
                lines.append(f"  {MONTHS_RU_SHORT[month]}: {', '.join(amounts)}")
        
        if stats["total_by_currency"]:
            lines.append("\n*Итого за год:*")
            for curr, amt in sorted(stats["total_by_currency"].items()):
                lines.append(f"  • {format_price(amt, curr)}")
        
        if stats["by_subscription"]:
            lines.append("\n*По подпискам:*")
            for name, total, currency, count in stats["by_subscription"][:10]:
                lines.append(f"  • {name}: {format_price(total, currency)} ({count})")
        
        lines.append(f"\n_Платежей: {stats['payment_count']}_")
        
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=build_year_keyboard(year),
        )


# -----------------------------
# EDIT CONVERSATION
# -----------------------------
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("`/edit <id>`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    try:
        sub_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID — число", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    sub = get_subscription_by_id(user_id, sub_id)
    if not sub:
        await update.message.reply_text("Не найдено", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    context.user_data["edit_id"] = sub_id

    _id, name, price, day, period, last_charge_date, category, is_paused = sub
    pp = unpack_price(price)
    price_view = format_price(pp[0], pp[1]) if pp else price

    await update.message.reply_text(
        f"*#{_id}* • {name}\n{price_view} • {day}-го\n\n"
        "Что менять? `name`/`price`/`day`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()
    if text not in ("name", "price", "day"):
        await update.message.reply_text("`name`/`price`/`day`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = text
    prompts = {"name": "Название:", "price": "Цена:", "day": "День (1–31):"}
    await update.message.reply_text(prompts[text], reply_markup=main_menu_keyboard())
    return EDIT_ENTER_VALUE


async def edit_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    sub_id = context.user_data.get("edit_id")
    field = context.user_data.get("edit_field")

    if not sub_id or not field:
        await update.message.reply_text("/edit <id>", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    raw = (update.message.text or "").strip()

    if field == "day":
        try:
            value = int(raw)
            if not (1 <= value <= 31):
                raise ValueError
        except ValueError:
            await update.message.reply_text("1–31", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
    elif field == "name":
        if not raw or len(raw) > MAX_NAME_LENGTH:
            await update.message.reply_text(f"1–{MAX_NAME_LENGTH} символов", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        value = raw
    elif field == "price":
        parsed = parse_price(raw)
        if not parsed:
            await update.message.reply_text("Не поняла цену", reply_markup=main_menu_keyboard())
            return EDIT_ENTER_VALUE
        value = pack_price(parsed[0], parsed[1])
    else:
        value = raw

    update_subscription_field(user_id, sub_id, field, value)
    await update.message.reply_text("Обновлено ✅", reply_markup=main_menu_keyboard())

    context.user_data.pop("edit_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("edit_id", "edit_field", "add_name", "add_amount", "add_currency",
              "add_category", "add_duplicate", "add_suggested_period"):
        context.user_data.pop(k, None)

    await update.message.reply_text("Отменено 👌", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# -----------------------------
# MENU ROUTER
# -----------------------------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    # Inline edit
    if context.user_data.get("edit_id") and context.user_data.get("edit_field"):
        sub_id = context.user_data["edit_id"]
        field = context.user_data["edit_field"]

        if field == "day":
            try:
                value = int(text)
                if not (1 <= value <= 31):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("1–31", reply_markup=main_menu_keyboard())
                return
        elif field == "name":
            if not text or len(text) > MAX_NAME_LENGTH:
                await update.message.reply_text(f"1–{MAX_NAME_LENGTH}", reply_markup=main_menu_keyboard())
                return
            value = text
        elif field == "price":
            parsed = parse_price(text)
            if not parsed:
                await update.message.reply_text("Не поняла", reply_markup=main_menu_keyboard())
                return
            value = pack_price(parsed[0], parsed[1])
        else:
            value = text

        update_subscription_field(user_id, sub_id, field, value)
        context.user_data.pop("edit_id", None)
        context.user_data.pop("edit_field", None)
        await update.message.reply_text("Обновлено ✅", reply_markup=main_menu_keyboard())
        return

    # Menu buttons
    if text == "📋 Список":
        await list_cmd(update, context)
    elif text == "📅 Ближайшее":
        await next_cmd(update, context)
    elif text == "💸 Итого":
        await sum_cmd(update, context)
    elif text == "📊 Статистика":
        await stats_cmd(update, context)
    elif text == "⚙️ Настройки":
        await settings_cmd(update, context)
    elif text == "ℹ️ Помощь":
        await help_cmd(update, context)
    elif text == "✏️ Редактировать":
        await edit_button_handler(update, context)
    elif text == "🗑 Удалить":
        await delete_button_handler(update, context)
    else:
        # Quick add
        parsed = try_parse_quick_add(text)
        if parsed:
            count = count_user_subscriptions(user_id)
            if count >= MAX_SUBSCRIPTIONS_PER_USER:
                await update.message.reply_text(f"Максимум {MAX_SUBSCRIPTIONS_PER_USER}", reply_markup=main_menu_keyboard())
                return

            name, amount, currency, last_dt, category = parsed
            
            # Проверка дубликата
            duplicate = find_duplicate_subscription(user_id, name)
            if duplicate:
                dup_id, dup_name, dup_price, _, dup_period, _, _, _ = duplicate
                pp = unpack_price(dup_price)
                dup_price_view = format_price(pp[0], pp[1]) if pp else dup_price
                
                new_data = f"{name}|{amount}|{currency}|{last_dt.isoformat()}|{category}"
                
                await update.message.reply_text(
                    f"⚠️ *«{dup_name}» уже есть:*\n\n"
                    f"#{dup_id} • {dup_price_view} • {period_label(dup_period)}\n\n"
                    "Что сделать?",
                    parse_mode="Markdown",
                    reply_markup=duplicate_keyboard(dup_id, new_data),
                )
                return

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
                f"Добавлено ✅\n\n*#{new_id} • {name}*\n💰 {price_view}\n🏷 {cat_label}\n\nПериод?",
                parse_mode="Markdown",
                reply_markup=period_keyboard(new_id),
            )
        else:
            await update.message.reply_text("Нажми кнопку 👇", reply_markup=main_menu_keyboard())


# -----------------------------
# REMINDER JOB
# -----------------------------
async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Running reminders...")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT s.user_id, us.reminder_days, us.reminder_enabled
        FROM subscriptions s
        LEFT JOIN user_settings us ON s.user_id = us.user_id
        WHERE s.is_paused = 0
    """)
    users = cur.fetchall()

    today = date.today()

    for user_id, reminder_days, reminder_enabled in users:
        if reminder_enabled == 0:
            continue

        reminder_days = reminder_days or 1
        target_date = today + timedelta(days=reminder_days)

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

            if next_charge == target_date:
                pp = unpack_price(price)
                price_view = format_price(pp[0], pp[1]) if pp else price
                reminders.append(f"• *{name}* — {price_view}")

        if reminders:
            try:
                when = "завтра" if reminder_days == 1 else f"через {reminder_days} {days_word_ru(reminder_days)}"
                text = f"🔔 *Напоминание*\n\n{when} ({format_date_ru(target_date)}):\n\n" + "\n".join(reminders)
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                logger.info(f"Reminder sent to {user_id}")
            except Exception as e:
                logger.error(f"Reminder failed for {user_id}: {e}")

    conn.close()


# -----------------------------
# ERROR HANDLER
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Ошибка 😕 /start", reply_markup=main_menu_keyboard())
    except Exception:
        pass


# -----------------------------
# POST INIT
# -----------------------------
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Запуск"),
        BotCommand("add", "Добавить"),
        BotCommand("list", "Список"),
        BotCommand("next", "Ближайшее"),
        BotCommand("sum", "Итого"),
        BotCommand("stats", "Статистика за год"),
        BotCommand("settings", "Настройки"),
        BotCommand("cancel", "Отмена"),
        BotCommand("help", "Помощь"),
    ]
    await application.bot.set_my_commands(commands)

    job_queue = application.job_queue
    if job_queue:
        job_queue.run_daily(
            send_reminders,
            time=time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE),
            name="daily_reminders",
        )
        logger.info(f"Reminders scheduled at {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} UTC")


# -----------------------------
# MAIN
# -----------------------------
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    init_db()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("del", del_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(CommandHandler("sum", sum_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(CallbackQueryHandler(period_callback, pattern=r"^period:\d+:(month|year)$"))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r"^category:\d+:\w+$"))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^delete_(ask|confirm|cancel):\d+$"))
    application.add_handler(CallbackQueryHandler(duplicate_callback, pattern=r"^dup_(payment|update|create|cancel)"))
    application.add_handler(CallbackQueryHandler(edit_callback, pattern=r"^(edit_select|edit_field|edit_cancel|toggle_pause|mark_paid|sub_history)"))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(settings:|set_reminder_days:)"))
    application.add_handler(CallbackQueryHandler(stats_callback, pattern=r"^stats_year:\d+$"))

    application.add_handler(add_conv)
    application.add_handler(edit_conv)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    application.add_error_handler(error_handler)

    logger.info("Bot starting...")
    application.run_polling()


if __name__ == "__main__":
    main()

