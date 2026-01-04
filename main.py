"""
Telegram Bot для отслеживания подписок
Версия: 7.0 (исправленная + выбор периода)
"""

import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, List, Tuple, Dict, Any
from contextlib import contextmanager
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
from telegram.helpers import escape_markdown

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

# Допустимые поля для обновления (защита от SQL-инъекций)
ALLOWED_SUBSCRIPTION_FIELDS = frozenset({
    "price", "next_date", "period", "last_charge_date", "category", "is_paused"
})
ALLOWED_USER_SETTINGS_FIELDS = frozenset({
    "default_currency", "reminder_enabled", "reminder_days", "reminder_hour"
})

# ─────────────────────────────────────────────────────────────
# CURRENCY HELPERS
# ─────────────────────────────────────────────────────────────
CURRENCY_ALIASES: Dict[str, str] = {
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

CURRENCY_SYMBOL: Dict[str, str] = {
    "NOK": "kr", "EUR": "€", "USD": "$", "RUB": "₽",
    "SEK": "kr", "DKK": "kr", "GBP": "£",
}


def normalize_currency_token(token: str) -> Optional[str]:
    """Нормализует токен валюты к стандартному виду."""
    t = token.strip().lower()
    if t.upper() in SUPPORTED_CURRENCIES:
        return t.upper()
    return CURRENCY_ALIASES.get(t)


def is_currency_token(token: str) -> bool:
    """Проверяет, является ли токен валютой."""
    return normalize_currency_token(token) is not None


# ─────────────────────────────────────────────────────────────
# DATABASE CONTEXT MANAGER
# ─────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    """Контекстный менеджер для безопасной работы с БД."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# PRICE HELPERS
# ─────────────────────────────────────────────────────────────
def parse_price(input_str: str) -> Optional[Tuple[float, str]]:
    """
    Парсит строку с ценой и валютой.
    Поддерживает форматы: "129", "129 kr", "€9.99", "9,99 EUR"
    """
    input_str = input_str.strip()
    if not input_str:
        return None
    
    # Попытка распарсить формат с символом валюты в начале (€100, $50)
    currency_prefix_match = re.match(r'^([€$£₽])\s*(\d+[.,]?\d*)$', input_str)
    if currency_prefix_match:
        symbol, num = currency_prefix_match.groups()
        currency = normalize_currency_token(symbol)
        if currency:
            try:
                amount = float(num.replace(",", "."))
                if 0 < amount <= MAX_PRICE:
                    return (amount, currency)
            except ValueError:
                pass
    
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
            # Попробовать обратный порядок (EUR 100)
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
    """Упаковывает цену и валюту в строку для хранения."""
    return f"{amount:.2f} {currency}"


def unpack_price(price_str: str) -> Tuple[float, str]:
    """Распаковывает строку цены в кортеж (сумма, валюта)."""
    parts = price_str.strip().split()
    if len(parts) == 2:
        try:
            return (float(parts[0]), parts[1])
        except ValueError:
            pass
    return (0.0, DEFAULT_CURRENCY)


def format_price(amount: float, currency: str) -> str:
    """Форматирует цену для отображения пользователю."""
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    formatted = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {symbol}"


# ─────────────────────────────────────────────────────────────
# TEXT HELPERS
# ─────────────────────────────────────────────────────────────
def escape_md(text: str) -> str:
    """Экранирует текст для MarkdownV2."""
    return escape_markdown(str(text), version=2)


def safe_markdown(text: str, bold: bool = False) -> str:
    """Возвращает безопасный текст для Markdown, опционально жирный."""
    escaped = escape_md(text)
    if bold:
        return f"*{escaped}*"
    return escaped


# ─────────────────────────────────────────────────────────────
# KNOWN SERVICES
# ─────────────────────────────────────────────────────────────
KNOWN_SERVICES: Dict[str, Tuple[str, str]] = {
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

CATEGORIES: List[str] = [
    "🎬 Стриминг", "🎵 Музыка", "💻 Софт", "☁️ Облако",
    "🎮 Игры", "💪 Спорт", "📚 Обучение", "📰 Новости", "🔒 VPN", "📦 Другое",
]

# ─────────────────────────────────────────────────────────────
# DATABASE INITIALIZATION
# ─────────────────────────────────────────────────────────────
def init_db():
    """Инициализирует базу данных и выполняет миграции."""
    with get_db() as conn:
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
                reminder_hour INTEGER DEFAULT 9,
                timezone TEXT DEFAULT 'UTC'
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
        
        # Таблица для временных данных (вместо длинных callback_data)
        c.execute("""
            CREATE TABLE IF NOT EXISTS temp_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                data_key TEXT NOT NULL,
                data_value TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL
            )
        """)

        # Создаём индексы для производительности
        c.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_next_date ON subscriptions(next_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payment_history(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payment_history(paid_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_temp_data_user ON temp_data(user_id, data_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_temp_data_expires ON temp_data(expires_at)")

        # Миграции для subscriptions
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(subscriptions)").fetchall()}
        migrations = [
            ("period", "TEXT DEFAULT 'month'"),
            ("last_charge_date", "TEXT"),
            ("category", "TEXT DEFAULT '📦 Другое'"),
            ("is_paused", "INTEGER DEFAULT 0"),
        ]
        for col, col_type in migrations:
            if col not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

        # Миграции для user_settings
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(user_settings)").fetchall()}
        migrations = [
            ("reminder_enabled", "INTEGER DEFAULT 1"),
            ("reminder_days", "TEXT DEFAULT '1,3'"),
            ("reminder_hour", "INTEGER DEFAULT 9"),
            ("timezone", "TEXT DEFAULT 'UTC'"),
        ]
        for col, col_type in migrations:
            if col not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE user_settings ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass


def cleanup_expired_temp_data():
    """Удаляет устаревшие временные данные."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM temp_data WHERE expires_at < datetime('now')")


# ─────────────────────────────────────────────────────────────
# TEMP DATA FUNCTIONS
# ─────────────────────────────────────────────────────────────
def save_temp_data(user_id: int, key: str, value: str, expires_minutes: int = 60) -> int:
    """Сохраняет временные данные и возвращает ID."""
    with get_db() as conn:
        c = conn.cursor()
        expires_at = (datetime.now() + timedelta(minutes=expires_minutes)).isoformat()
        c.execute("""
            INSERT INTO temp_data (user_id, data_key, data_value, expires_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, key, value, expires_at))
        return c.lastrowid


def get_temp_data(temp_id: int, user_id: int) -> Optional[str]:
    """Получает временные данные по ID с проверкой владельца."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT data_value FROM temp_data 
            WHERE id = ? AND user_id = ? AND expires_at > datetime('now')
        """, (temp_id, user_id))
        row = c.fetchone()
        return row[0] if row else None


def delete_temp_data(temp_id: int):
    """Удаляет временные данные."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM temp_data WHERE id = ?", (temp_id,))


# ─────────────────────────────────────────────────────────────
# USER SETTINGS FUNCTIONS
# ─────────────────────────────────────────────────────────────
def get_user_settings(user_id: int) -> Dict[str, Any]:
    """Получает настройки пользователя."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT default_currency, reminder_enabled, reminder_days, reminder_hour, timezone
            FROM user_settings WHERE user_id = ?
        """, (user_id,))
        row = c.fetchone()
        
        if row:
            return {
                "currency": row[0] or DEFAULT_CURRENCY,
                "reminder_enabled": bool(row[1]) if row[1] is not None else True,
                "reminder_days": row[2] or "1,3",
                "reminder_hour": int(row[3]) if row[3] is not None else 9,
                "timezone": row[4] or "UTC"
            }
        return {
            "currency": DEFAULT_CURRENCY,
            "reminder_enabled": True,
            "reminder_days": "1,3",
            "reminder_hour": 9,
            "timezone": "UTC"
        }


def save_user_setting(user_id: int, field: str, value: Any) -> bool:
    """
    Сохраняет настройку пользователя.
    Использует UPSERT для атомарности.
    """
    if field not in ALLOWED_USER_SETTINGS_FIELDS:
        logger.error(f"Попытка обновить недопустимое поле настроек: {field}")
        return False
    
    with get_db() as conn:
        c = conn.cursor()
        # SQLite UPSERT синтаксис
        c.execute(f"""
            INSERT INTO user_settings (user_id, {field}) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET {field} = excluded.{field}
        """, (user_id, value))
        return True


# ─────────────────────────────────────────────────────────────
# SUBSCRIPTION FUNCTIONS
# ─────────────────────────────────────────────────────────────
def add_subscription(user_id: int, name: str, price: str, next_date: str,
                     period: str = "month", last_charge_date: str = None,
                     category: str = "📦 Другое") -> int:
    """Добавляет новую подписку и возвращает её ID."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO subscriptions (user_id, name, price, next_date, period, last_charge_date, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, price, next_date, period, last_charge_date, category))
        return c.lastrowid


def find_duplicate_subscription(user_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Находит существующую подписку с таким же названием."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, name, price, period, next_date, last_charge_date, category, is_paused
            FROM subscriptions WHERE user_id = ? AND LOWER(name) = LOWER(?)
        """, (user_id, name))
        row = c.fetchone()
        if row:
            return {
                "id": row[0], "name": row[1], "price": row[2], "period": row[3],
                "next_date": row[4], "last_charge_date": row[5], 
                "category": row[6], "is_paused": row[7]
            }
        return None


def list_subscriptions(user_id: int) -> List[Dict[str, Any]]:
    """Возвращает список подписок пользователя."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, name, price, next_date, period, category, is_paused
            FROM subscriptions WHERE user_id = ? ORDER BY next_date
        """, (user_id,))
        rows = c.fetchall()
        return [
            {"id": r[0], "name": r[1], "price": r[2], "next_date": r[3],
             "period": r[4], "category": r[5], "is_paused": r[6]}
            for r in rows
        ]


def get_subscription(sub_id: int) -> Optional[Dict[str, Any]]:
    """Получает подписку по ID."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, name, price, next_date, period, last_charge_date, category, is_paused, user_id
            FROM subscriptions WHERE id = ?
        """, (sub_id,))
        row = c.fetchone()
        if row:
            return {
                "id": row[0], "name": row[1], "price": row[2], "next_date": row[3],
                "period": row[4], "last_charge_date": row[5], "category": row[6],
                "is_paused": row[7], "user_id": row[8]
            }
        return None


def get_subscription_if_owner(sub_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Получает подписку только если она принадлежит пользователю."""
    sub = get_subscription(sub_id)
    if sub and sub["user_id"] == user_id:
        return sub
    return None


def delete_subscription(sub_id: int, user_id: int) -> bool:
    """Удаляет подписку с проверкой владельца."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?", (sub_id, user_id))
        return c.rowcount > 0


def update_subscription_field(sub_id: int, field: str, value: Any, user_id: int) -> bool:
    """
    Обновляет поле подписки с проверкой владельца.
    Защита от SQL-инъекций через whitelist полей.
    """
    if field not in ALLOWED_SUBSCRIPTION_FIELDS:
        logger.error(f"Попытка обновить недопустимое поле подписки: {field}")
        return False
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute(f"UPDATE subscriptions SET {field} = ? WHERE id = ? AND user_id = ?", 
                  (value, sub_id, user_id))
        return c.rowcount > 0


def update_subscription_fields(sub_id: int, updates: Dict[str, Any], user_id: int) -> bool:
    """Обновляет несколько полей подписки за один запрос."""
    # Проверяем все поля
    for field in updates.keys():
        if field not in ALLOWED_SUBSCRIPTION_FIELDS:
            logger.error(f"Попытка обновить недопустимое поле подписки: {field}")
            return False
    
    if not updates:
        return False
    
    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [sub_id, user_id]
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute(f"UPDATE subscriptions SET {set_clause} WHERE id = ? AND user_id = ?", values)
        return c.rowcount > 0


def count_user_subscriptions(user_id: int) -> int:
    """Считает количество подписок пользователя."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,))
        return c.fetchone()[0]


def add_payment(user_id: int, subscription_id: int, amount: str, paid_at: str):
    """Добавляет запись о платеже."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO payment_history (user_id, subscription_id, amount, paid_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, subscription_id, amount, paid_at))


def get_payments_for_year(user_id: int, year: int) -> List[Dict[str, Any]]:
    """Получает платежи за указанный год."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT subscription_id, amount, paid_at FROM payment_history
            WHERE user_id = ? AND paid_at LIKE ? ORDER BY paid_at
        """, (user_id, f"{year}-%"))
        return [
            {"subscription_id": r[0], "amount": r[1], "paid_at": r[2]}
            for r in c.fetchall()
        ]


# ─────────────────────────────────────────────────────────────
# DATE HELPERS
# ─────────────────────────────────────────────────────────────
def parse_date(text: str) -> Optional[datetime]:
    """Парсит дату из различных форматов."""
    text = text.strip()
    for fmt in ["%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def next_from_last(last_dt: datetime, period: str = "month") -> datetime:
    """
    Вычисляет следующую дату платежа от последней.
    Если последняя дата в будущем, возвращает её.
    """
    today = datetime.now().date()
    candidate = last_dt.date()
    
    # Если дата уже в будущем или сегодня, возвращаем её
    if candidate >= today:
        return datetime.combine(candidate, datetime.min.time())
    
    while candidate < today:
        if period == "year":
            try:
                candidate = candidate.replace(year=candidate.year + 1)
            except ValueError:
                # 29 февраля -> 28 февраля
                candidate = candidate.replace(year=candidate.year + 1, day=28)
        elif period == "week":
            candidate += timedelta(days=7)
        else:  # month
            month = candidate.month + 1
            year = candidate.year
            if month > 12:
                month = 1
                year += 1
            day = candidate.day
            # Обработка случаев, когда день больше, чем дней в месяце
            while True:
                try:
                    candidate = candidate.replace(year=year, month=month, day=day)
                    break
                except ValueError:
                    day -= 1
                    if day < 1:
                        day = 28
                        break
    
    return datetime.combine(candidate, datetime.min.time())


def format_date(dt: datetime) -> str:
    """Форматирует дату для отображения."""
    return dt.strftime("%d.%m.%Y")


# ─────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню бота."""
    return ReplyKeyboardMarkup([
        ["📋 Мои подписки", "➕ Добавить"],
        ["📅 Ближайшие", "📊 Статистика"],
        ["⚙️ Настройки", "❓ Помощь"]
    ], resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    return ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)


def settings_keyboard(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Клавиатура настроек."""
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
    """Клавиатура выбора валюты."""
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
    """Клавиатура выбора дней напоминаний."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("За 1 день", callback_data="set_days:1")],
        [InlineKeyboardButton("За 3 дня", callback_data="set_days:3")],
        [InlineKeyboardButton("За 1 и 3 дня", callback_data="set_days:1,3")],
        [InlineKeyboardButton("За 7 дней", callback_data="set_days:7")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings:back")]
    ])


def reminder_hour_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора часа напоминаний."""
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
    """Клавиатура выбора периода подписки (после создания)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Месяц", callback_data=f"period:{sub_id}:month"),
            InlineKeyboardButton("📅 Год", callback_data=f"period:{sub_id}:year"),
            InlineKeyboardButton("📅 Неделя", callback_data=f"period:{sub_id}:week"),
        ],
        [InlineKeyboardButton("✅ Готово", callback_data=f"period_done:{sub_id}")]
    ])


def add_period_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора периода при добавлении подписки."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Ежемесячная", callback_data="add_period:month"),
            InlineKeyboardButton("📅 Годовая", callback_data="add_period:year"),
        ],
        [
            InlineKeyboardButton("📅 Еженедельная", callback_data="add_period:week"),
        ]
    ])


def delete_confirm_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_confirm:{sub_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"delete_cancel:{sub_id}")
    ]])


def duplicate_keyboard(existing_id: int, temp_data_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура обработки дубликата.
    Использует temp_data_id вместо передачи всех данных в callback_data.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Записать платёж", callback_data=f"dup_payment:{existing_id}:{temp_data_id}")],
        [InlineKeyboardButton("🔄 Обновить данные", callback_data=f"dup_update:{existing_id}:{temp_data_id}")],
        [InlineKeyboardButton("➕ Создать новую", callback_data=f"dup_create:{temp_data_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"dup_cancel:{temp_data_id}")]
    ])


def subscription_keyboard(sub_id: int, is_paused: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура управления подпиской."""
    pause_btn = InlineKeyboardButton(
        "▶️ Возобновить" if is_paused else "⏸ Пауза",
        callback_data=f"pause:{sub_id}"
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit:{sub_id}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{sub_id}")
        ],
        [
            InlineKeyboardButton("✅ Оплачено", callback_data=f"paid:{sub_id}"),
            pause_btn
        ],
        [InlineKeyboardButton("📅 Период", callback_data=f"change_period:{sub_id}")]
    ])


def year_keyboard(current_year: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора года для статистики."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"◀️ {current_year - 1}", callback_data=f"stats_year:{current_year - 1}"),
        InlineKeyboardButton(f"{current_year}", callback_data=f"stats_year:{current_year}"),
        InlineKeyboardButton(f"{current_year + 1} ▶️", callback_data=f"stats_year:{current_year + 1}"),
    ]])


def edit_subscription_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    """Клавиатура редактирования подписки."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Изменить цену", callback_data=f"edit_price:{sub_id}")],
        [InlineKeyboardButton("📅 Изменить период", callback_data=f"change_period:{sub_id}")],
        [InlineKeyboardButton("🏷 Изменить категорию", callback_data=f"edit_category:{sub_id}")],
        [InlineKeyboardButton("📝 Изменить название", callback_data=f"edit_name:{sub_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"edit_back:{sub_id}")]
    ])


def category_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора категории."""
    buttons = []
    row = []
    for cat in CATEGORIES:
        row.append(InlineKeyboardButton(cat, callback_data=f"set_category:{sub_id}:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"edit:{sub_id}")])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────────────────────
# QUICK ADD PARSER
# ─────────────────────────────────────────────────────────────
def try_parse_quick_add(text: str) -> Optional[Dict[str, Any]]:
    """
    Парсит быстрое добавление подписки.
    Формат: "Netflix 129 kr 15.01.26"
    """
    text = text.strip()
    if not text:
        return None
    
    # Ищем дату в конце
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
# BOT HANDLERS - CONVERSATION STATES
# ─────────────────────────────────────────────────────────────
ADD_NAME, ADD_PRICE, ADD_DATE, ADD_PERIOD = range(4)
EDIT_PRICE, EDIT_NAME = range(10, 12)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {escape_md(user.first_name)}\\! 👋\n\n"
        "Я помогу отслеживать твои подписки\\.\n\n"
        "Используй кнопки меню или просто напиши:\n"
        "📝 `Netflix 129 kr 15\\.01\\.26`\n\n"
        "И я добавлю подписку\\!",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    await update.message.reply_text(
        "📖 *Как пользоваться ботом*\n\n"
        "*Быстрое добавление:*\n"
        "Просто напиши название, цену и дату:\n"
        "`Netflix 129 kr 15\\.01\\.26`\n\n"
        "*Команды:*\n"
        "/add — добавить подписку\n"
        "/list — список подписок\n"
        "/next — ближайшие платежи\n"
        "/stats — статистика расходов\n"
        "/settings — настройки\n"
        "/help — эта справка",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик отмены."""
    context.user_data.clear()
    await update.message.reply_text("Отменено 👌", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# SETTINGS HANDLERS
# ─────────────────────────────────────────────────────────────
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /settings."""
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    
    await update.message.reply_text(
        "⚙️ *Настройки*\n\n"
        "Выбери что хочешь изменить:",
        parse_mode="Markdown",
        reply_markup=settings_keyboard(settings)
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback-кнопок настроек."""
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
        if currency in SUPPORTED_CURRENCIES:
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
        try:
            hour = int(data.split(":")[1])
            if 0 <= hour <= 23:
                save_user_setting(user_id, "reminder_hour", hour)
                settings = get_user_settings(user_id)
                await query.edit_message_text(
                    f"✅ Время напоминаний: *{hour}:00*\n\n"
                    "⚙️ *Настройки*",
                    parse_mode="Markdown",
                    reply_markup=settings_keyboard(settings)
                )
        except ValueError:
            pass


# ─────────────────────────────────────────────────────────────
# ADD FLOW
# ─────────────────────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало добавления подписки."""
    user_id = update.effective_user.id
    if count_user_subscriptions(user_id) >= MAX_SUBSCRIPTIONS_PER_USER:
        await update.message.reply_text(
            f"❌ Достигнут лимит: {MAX_SUBSCRIPTIONS_PER_USER} подписок.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📝 Введи название подписки:\n\n"
        "Или сразу всё: `Netflix 129 kr 15.01.26`\n\n"
        "Для отмены нажми /cancel или кнопку ❌ Отмена",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    return ADD_NAME


async def add_flow_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода названия подписки."""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Проверка на отмену
    if text == "❌ Отмена":
        return await cancel(update, context)
    
    # Попытка быстрого добавления
    quick = try_parse_quick_add(text)
    if quick:
        return await process_quick_add(update, context, quick)
    
    if len(text) > MAX_NAME_LENGTH:
        await update.message.reply_text(
            f"❌ Слишком длинное название (макс. {MAX_NAME_LENGTH} символов)",
            reply_markup=cancel_keyboard()
        )
        return ADD_NAME
    
    context.user_data["add_name"] = text
    
    # Получаем валюту пользователя
    settings = get_user_settings(user_id)
    currency = settings["currency"]
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    
    await update.message.reply_text(
        f"💰 Введи цену (например: 129 {symbol} или 9.99 EUR):",
        reply_markup=cancel_keyboard()
    )
    return ADD_PRICE


async def add_flow_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода цены."""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if text == "❌ Отмена":
        return await cancel(update, context)
    
    settings = get_user_settings(user_id)
    
    parsed = parse_price(text)
    if not parsed:
        await update.message.reply_text(
            "❌ Не понял цену. Введи число и валюту:\n129 kr, 9.99 EUR, 100",
            reply_markup=cancel_keyboard()
        )
        return ADD_PRICE
    
    amount, currency = parsed
    # Если валюта по умолчанию (не указана явно), используем настройки пользователя
    if currency == DEFAULT_CURRENCY:
        # Проверяем, была ли валюта указана в вводе
        has_currency_in_input = any(is_currency_token(p) for p in text.split())
        if not has_currency_in_input:
            currency = settings["currency"]
    
    context.user_data["add_amount"] = amount
    context.user_data["add_currency"] = currency
    
    await update.message.reply_text(
        "📅 Введи дату последней оплаты (дд.мм.гг):\nНапример: 15.01.26",
        reply_markup=cancel_keyboard()
    )
    return ADD_DATE


async def add_flow_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода даты."""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if text == "❌ Отмена":
        return await cancel(update, context)
    
    date_obj = parse_date(text)
    if not date_obj:
        await update.message.reply_text(
            "❌ Не понял дату. Формат: дд.мм.гг",
            reply_markup=cancel_keyboard()
        )
        return ADD_DATE
    
    name = context.user_data.get("add_name", "Подписка")
    amount = context.user_data.get("add_amount", 0)
    currency = context.user_data.get("add_currency", DEFAULT_CURRENCY)
    
    # Проверка на дубликат
    existing = find_duplicate_subscription(user_id, name)
    if existing:
        # Сохраняем данные во временную таблицу
        temp_data = f"{name}|{amount}|{currency}|{date_obj.isoformat()}"
        temp_id = save_temp_data(user_id, "duplicate_add", temp_data)
        
        ex_amount, ex_cur = unpack_price(existing["price"])
        await update.message.reply_text(
            f"⚠️ Подписка *{escape_md(existing['name'])}* уже существует\\!\n"
            f"Текущая цена: {escape_md(format_price(ex_amount, ex_cur))}\n\nЧто сделать?",
            parse_mode="MarkdownV2",
            reply_markup=duplicate_keyboard(existing["id"], temp_id)
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    # Сохраняем дату и показываем выбор периода
    context.user_data["add_date"] = date_obj
    
    # Определение категории
    category = "📦 Другое"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        proper_name, category = KNOWN_SERVICES[name_lower]
        context.user_data["add_name"] = proper_name
        context.user_data["add_category"] = category
    else:
        context.user_data["add_category"] = category
    
    await update.message.reply_text(
        f"📅 *Выбери тип подписки:*\n\n"
        f"• *Ежемесячная* — списание каждый месяц\n"
        f"• *Годовая* — списание раз в год\n"
        f"• *Еженедельная* — списание каждую неделю",
        parse_mode="MarkdownV2",
        reply_markup=add_period_keyboard()
    )
    return ADD_PERIOD


async def add_flow_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора периода при добавлении подписки (через callback)."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data or ""
    
    if not data.startswith("add_period:"):
        return ADD_PERIOD
    
    period = data.split(":")[1]
    if period not in ("month", "year", "week"):
        return ADD_PERIOD
    
    # Получаем данные из контекста
    name = context.user_data.get("add_name", "Подписка")
    amount = context.user_data.get("add_amount", 0)
    currency = context.user_data.get("add_currency", DEFAULT_CURRENCY)
    date_obj = context.user_data.get("add_date")
    category = context.user_data.get("add_category", "📦 Другое")
    
    if not date_obj:
        await query.edit_message_text("❌ Ошибка: данные утеряны. Начни заново /add")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Создаём подписку
    next_dt = next_from_last(date_obj, period)
    price = pack_price(amount, currency)
    
    new_id = add_subscription(
        user_id=user_id, name=name, price=price,
        next_date=next_dt.strftime("%Y-%m-%d"),
        period=period,
        last_charge_date=date_obj.strftime("%Y-%m-%d"),
        category=category
    )
    add_payment(user_id, new_id, price, date_obj.strftime("%Y-%m-%d"))
    
    period_names = {"month": "ежемесячная", "year": "годовая", "week": "еженедельная"}
    
    await query.edit_message_text(
        f"✅ Добавлено: *{escape_md(name)}*\n"
        f"💰 {escape_md(format_price(amount, currency))}\n"
        f"📅 Тип: {period_names.get(period, period)}\n"
        f"📅 Следующий платёж: {escape_md(format_date(next_dt))}\n"
        f"🏷 Категория: {escape_md(category)}",
        parse_mode="MarkdownV2"
    )
    
    context.user_data.clear()
    return ConversationHandler.END


async def process_quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE, quick: Dict[str, Any]) -> int:
    """Обработка быстрого добавления подписки."""
    user_id = update.effective_user.id
    name = quick["name"]
    amount = quick["amount"]
    currency = quick["currency"]
    date_obj = quick["date"]
    
    # Если валюта не указана, используем настройки пользователя
    if currency == DEFAULT_CURRENCY and not any(is_currency_token(p) for p in quick["name"].split()):
        settings = get_user_settings(user_id)
        currency = settings["currency"]
    
    # Проверка на дубликат
    existing = find_duplicate_subscription(user_id, name)
    if existing:
        temp_data = f"{name}|{amount}|{currency}|{date_obj.isoformat() if date_obj else ''}"
        temp_id = save_temp_data(user_id, "duplicate_add", temp_data)
        
        ex_amount, ex_cur = unpack_price(existing["price"])
        await update.message.reply_text(
            f"⚠️ Подписка *{escape_md(existing['name'])}* уже существует\\!\n"
            f"Текущая цена: {escape_md(format_price(ex_amount, ex_cur))}\n\nЧто сделать?",
            parse_mode="MarkdownV2",
            reply_markup=duplicate_keyboard(existing["id"], temp_id)
        )
        return ConversationHandler.END
    
    # Определение категории
    category = "📦 Другое"
    name_lower = name.lower()
    if name_lower in KNOWN_SERVICES:
        proper_name, category = KNOWN_SERVICES[name_lower]
        name = proper_name
    
    # Сохраняем данные для выбора периода
    last_dt = date_obj if date_obj else datetime.now()
    context.user_data["add_name"] = name
    context.user_data["add_amount"] = amount
    context.user_data["add_currency"] = currency
    context.user_data["add_date"] = last_dt
    context.user_data["add_category"] = category
    
    await update.message.reply_text(
        f"📅 *Выбери тип подписки для {escape_md(name)}:*\n\n"
        f"• *Ежемесячная* — списание каждый месяц\n"
        f"• *Годовая* — списание раз в год\n"
        f"• *Еженедельная* — списание каждую неделю",
        parse_mode="MarkdownV2",
        reply_markup=add_period_keyboard()
    )
    return ADD_PERIOD


# ─────────────────────────────────────────────────────────────
# LIST / NEXT / STATS
# ─────────────────────────────────────────────────────────────
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список подписок."""
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text(
            "📋 У тебя пока нет подписок\\.\n\nНапиши:\n`Netflix 129 kr 15\\.01\\.26`",
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
        return
    
    for sub in subs:
        amount, currency = unpack_price(sub["price"])
        price_view = format_price(amount, currency)
        status = "⏸ " if sub["is_paused"] else ""
        
        period_names = {"month": "мес", "year": "год", "week": "нед"}
        period_text = period_names.get(sub["period"], sub["period"])
        
        try:
            dt = datetime.strptime(sub["next_date"], "%Y-%m-%d")
            date_text = format_date(dt)
        except ValueError:
            date_text = sub["next_date"]
        
        await update.message.reply_text(
            f"{status}*{escape_md(sub['name'])}*\n"
            f"💰 {escape_md(price_view)} / {escape_md(period_text)}\n"
            f"📅 Следующий: {escape_md(date_text)}\n"
            f"🏷 {escape_md(sub['category'])}",
            parse_mode="MarkdownV2",
            reply_markup=subscription_keyboard(sub["id"], sub["is_paused"])
        )


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает ближайшие платежи."""
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text("📅 Нет подписок.", reply_markup=main_menu_keyboard())
        return
    
    today = datetime.now().date()
    upcoming = []
    
    for sub in subs:
        if sub["is_paused"]:
            continue
        try:
            dt = datetime.strptime(sub["next_date"], "%Y-%m-%d").date()
            days_left = (dt - today).days
            if days_left <= 30:
                amount, currency = unpack_price(sub["price"])
                upcoming.append((days_left, dt, sub["name"], amount, currency))
        except ValueError:
            continue
    
    if not upcoming:
        await update.message.reply_text(
            "📅 В ближайшие 30 дней платежей нет.", 
            reply_markup=main_menu_keyboard()
        )
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
            when = "просрочено"
        else:
            when = f"через {days_left} дн."
        lines.append(f"• *{escape_md(name)}* — {escape_md(price_view)}\n  {dt.strftime('%d.%m.%Y')} \\({escape_md(when)}\\)")
    
    await update.message.reply_text(
        "\n".join(lines), 
        parse_mode="MarkdownV2", 
        reply_markup=main_menu_keyboard()
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику."""
    user_id = update.effective_user.id
    year = datetime.now().year
    await show_stats_for_year(update, user_id, year)


async def show_stats_for_year(update: Update, user_id: int, year: int, edit: bool = False) -> None:
    """Показывает статистику за год с группировкой по валютам."""
    payments = get_payments_for_year(user_id, year)
    
    # Группировка по валютам и месяцам
    stats_by_currency: Dict[str, Dict[int, float]] = {}
    totals_by_currency: Dict[str, float] = {}
    
    for payment in payments:
        amount, currency = unpack_price(payment["amount"])
        try:
            dt = datetime.strptime(payment["paid_at"], "%Y-%m-%d")
            month = dt.month
            
            if currency not in stats_by_currency:
                stats_by_currency[currency] = {}
                totals_by_currency[currency] = 0.0
            
            if month not in stats_by_currency[currency]:
                stats_by_currency[currency][month] = 0.0
            
            stats_by_currency[currency][month] += amount
            totals_by_currency[currency] += amount
        except ValueError:
            continue
    
    month_names = ["", "янв", "фев", "мар", "апр", "май", "июн", 
                   "июл", "авг", "сен", "окт", "ноя", "дек"]
    
    lines = [f"📊 *Статистика за {year} год:*\n"]
    
    if stats_by_currency:
        for currency in sorted(stats_by_currency.keys()):
            months = stats_by_currency[currency]
            total = totals_by_currency[currency]
            symbol = CURRENCY_SYMBOL.get(currency, currency)
            
            lines.append(f"\n*{currency}:*")
            for m in sorted(months.keys()):
                formatted = f"{months[m]:,.0f}".replace(",", " ")
                lines.append(f"{month_names[m]}: {formatted} {symbol}")
            
            total_formatted = f"{total:,.0f}".replace(",", " ")
            lines.append(f"*Итого: {total_formatted} {symbol}*")
    else:
        lines.append("Нет данных о платежах.")
    
    text = "\n".join(lines)
    keyboard = year_keyboard(year)
    
    # Экранируем для MarkdownV2
    text_escaped = text.replace(".", "\\.").replace("-", "\\-").replace("!", "\\!")
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text_escaped, 
            parse_mode="MarkdownV2", 
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            text_escaped, 
            parse_mode="MarkdownV2", 
            reply_markup=keyboard
        )


# ─────────────────────────────────────────────────────────────
# CALLBACK HANDLERS
# ─────────────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Главный роутер callback-кнопок."""
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    # Статистика по годам
    if data.startswith("stats_year:"):
        try:
            year = int(data.split(":")[1])
            await show_stats_for_year(update, user_id, year, edit=True)
        except (ValueError, IndexError):
            pass
        return
    
    # Подтверждение удаления
    if data.startswith("delete_confirm:"):
        try:
            sub_id = int(data.split(":")[1])
            if delete_subscription(sub_id, user_id):
                await query.edit_message_text("🗑 Подписка удалена.")
            else:
                await query.edit_message_text("❌ Не удалось удалить подписку.")
        except (ValueError, IndexError):
            pass
        return
    
    if data.startswith("delete_cancel:"):
        await query.edit_message_text("Отменено 👌")
        return
    
    # Удаление
    if data.startswith("delete:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                await query.edit_message_text(
                    f"Удалить подписку *{escape_md(sub['name'])}*?",
                    parse_mode="MarkdownV2",
                    reply_markup=delete_confirm_keyboard(sub_id)
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Пауза
    if data.startswith("pause:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                new_paused = 0 if sub["is_paused"] else 1
                update_subscription_field(sub_id, "is_paused", new_paused, user_id)
                status = "приостановлена ⏸" if new_paused else "возобновлена ▶️"
                await query.edit_message_text(
                    f"Подписка *{escape_md(sub['name'])}* {status}", 
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Отметка оплаты
    if data.startswith("paid:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                today = datetime.now()
                today_str = today.strftime("%Y-%m-%d")
                new_next = next_from_last(today, sub["period"])
                
                update_subscription_fields(sub_id, {
                    "last_charge_date": today_str,
                    "next_date": new_next.strftime("%Y-%m-%d")
                }, user_id)
                
                add_payment(user_id, sub_id, sub["price"], today_str)
                amount, currency = unpack_price(sub["price"])
                
                await query.edit_message_text(
                    f"✅ *{escape_md(sub['name'])}* — оплата записана\\!\n"
                    f"💰 {escape_md(format_price(amount, currency))}\n"
                    f"📅 Следующий платёж: {escape_md(format_date(new_next))}",
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Выбор периода (после добавления)
    if data.startswith("period:"):
        try:
            parts = data.split(":")
            sub_id = int(parts[1])
            new_period = parts[2]
            
            if new_period not in ("month", "year", "week"):
                return
            
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                updates = {"period": new_period}
                
                if sub["last_charge_date"]:
                    last_dt = datetime.strptime(sub["last_charge_date"], "%Y-%m-%d")
                    new_next = next_from_last(last_dt, new_period)
                    updates["next_date"] = new_next.strftime("%Y-%m-%d")
                
                update_subscription_fields(sub_id, updates, user_id)
                
                period_names = {"month": "месяц", "year": "год", "week": "неделя"}
                await query.edit_message_text(
                    f"✅ Период изменён на: *{period_names.get(new_period, new_period)}*\n\n"
                    f"Подписка *{escape_md(sub['name'])}* сохранена\\!",
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Кнопка "Готово" после выбора периода
    if data.startswith("period_done:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                period_names = {"month": "месяц", "year": "год", "week": "неделя"}
                await query.edit_message_text(
                    f"✅ Подписка *{escape_md(sub['name'])}* сохранена\\!\n"
                    f"📅 Период: {period_names.get(sub['period'], sub['period'])}",
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Изменить период (из списка подписок)
    if data.startswith("change_period:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                await query.edit_message_text(
                    f"📅 *Выбери период для {escape_md(sub['name'])}:*",
                    parse_mode="MarkdownV2",
                    reply_markup=period_keyboard(sub_id)
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Редактирование подписки
    if data.startswith("edit:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                amount, currency = unpack_price(sub["price"])
                await query.edit_message_text(
                    f"✏️ *Редактирование: {escape_md(sub['name'])}*\n\n"
                    f"💰 Цена: {escape_md(format_price(amount, currency))}\n"
                    f"📅 Период: {sub['period']}\n"
                    f"🏷 Категория: {escape_md(sub['category'])}\n\n"
                    f"Что изменить?",
                    parse_mode="MarkdownV2",
                    reply_markup=edit_subscription_keyboard(sub_id)
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Возврат к карточке подписки
    if data.startswith("edit_back:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                amount, currency = unpack_price(sub["price"])
                period_names = {"month": "мес", "year": "год", "week": "нед"}
                
                try:
                    dt = datetime.strptime(sub["next_date"], "%Y-%m-%d")
                    date_text = format_date(dt)
                except ValueError:
                    date_text = sub["next_date"]
                
                status = "⏸ " if sub["is_paused"] else ""
                await query.edit_message_text(
                    f"{status}*{escape_md(sub['name'])}*\n"
                    f"💰 {escape_md(format_price(amount, currency))} / {period_names.get(sub['period'], sub['period'])}\n"
                    f"📅 Следующий: {escape_md(date_text)}\n"
                    f"🏷 {escape_md(sub['category'])}",
                    parse_mode="MarkdownV2",
                    reply_markup=subscription_keyboard(sub_id, sub["is_paused"])
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Редактирование категории
    if data.startswith("edit_category:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                await query.edit_message_text(
                    f"🏷 *Выбери категорию для {escape_md(sub['name'])}:*",
                    parse_mode="MarkdownV2",
                    reply_markup=category_keyboard(sub_id)
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Установка категории
    if data.startswith("set_category:"):
        try:
            parts = data.split(":", 2)
            sub_id = int(parts[1])
            new_category = parts[2]
            
            if new_category not in CATEGORIES:
                return
            
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                update_subscription_field(sub_id, "category", new_category, user_id)
                await query.edit_message_text(
                    f"✅ Категория изменена на: {new_category}",
                    parse_mode="Markdown"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Запрос на редактирование цены
    if data.startswith("edit_price:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                context.user_data["edit_sub_id"] = sub_id
                context.user_data["edit_field"] = "price"
                await query.edit_message_text(
                    f"💰 Введи новую цену для *{escape_md(sub['name'])}*:\n\n"
                    f"Например: 129 kr, 9.99 EUR, 100\n\n"
                    f"Отправь /cancel для отмены",
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return
    
    # Запрос на редактирование названия
    if data.startswith("edit_name:"):
        try:
            sub_id = int(data.split(":")[1])
            sub = get_subscription_if_owner(sub_id, user_id)
            if sub:
                context.user_data["edit_sub_id"] = sub_id
                context.user_data["edit_field"] = "name"
                await query.edit_message_text(
                    f"📝 Введи новое название для подписки:\n\n"
                    f"Текущее: {escape_md(sub['name'])}\n\n"
                    f"Отправь /cancel для отмены",
                    parse_mode="MarkdownV2"
                )
        except (ValueError, IndexError):
            pass
        return


async def duplicate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback-кнопок для дубликатов."""
    query = update.callback_query
    await query.answer()
    
    data = query.data or ""
    user_id = query.from_user.id
    
    if data.startswith("dup_payment:"):
        try:
            parts = data.split(":")
            if len(parts) < 3:
                return
            existing_id = int(parts[1])
            temp_id = int(parts[2])
            
            # Проверяем владельца подписки
            sub = get_subscription_if_owner(existing_id, user_id)
            if not sub:
                await query.edit_message_text("❌ Подписка не найдена.")
                return
            
            # Получаем временные данные
            temp_data = get_temp_data(temp_id, user_id)
            if not temp_data:
                await query.edit_message_text("❌ Данные устарели. Попробуйте снова.")
                return
            
            data_parts = temp_data.split("|")
            if len(data_parts) < 4:
                return
            
            name, amount_str, currency, date_str = data_parts
            amount = float(amount_str)
            price = pack_price(amount, currency)
            
            if date_str:
                last_dt = datetime.fromisoformat(date_str)
                new_next = next_from_last(last_dt, sub["period"])
                
                update_subscription_fields(existing_id, {
                    "last_charge_date": last_dt.strftime("%Y-%m-%d"),
                    "price": price,
                    "next_date": new_next.strftime("%Y-%m-%d")
                }, user_id)
                
                add_payment(user_id, existing_id, price, last_dt.strftime("%Y-%m-%d"))
                
                await query.edit_message_text(
                    f"✅ Платёж записан\\!\n"
                    f"💰 {escape_md(format_price(amount, currency))}\n"
                    f"📅 {escape_md(format_date(last_dt))}",
                    parse_mode="MarkdownV2"
                )
            
            delete_temp_data(temp_id)
            
        except Exception as e:
            logger.error(f"dup_payment error: {e}")
            await query.edit_message_text("❌ Произошла ошибка.")
        return
    
    elif data.startswith("dup_update:"):
        try:
            parts = data.split(":")
            if len(parts) < 3:
                return
            existing_id = int(parts[1])
            temp_id = int(parts[2])
            
            sub = get_subscription_if_owner(existing_id, user_id)
            if not sub:
                await query.edit_message_text("❌ Подписка не найдена.")
                return
            
            temp_data = get_temp_data(temp_id, user_id)
            if not temp_data:
                await query.edit_message_text("❌ Данные устарели. Попробуйте снова.")
                return
            
            data_parts = temp_data.split("|")
            if len(data_parts) < 4:
                return
            
            name, amount_str, currency, date_str = data_parts
            amount = float(amount_str)
            price = pack_price(amount, currency)
            
            updates = {"price": price}
            
            if date_str:
                last_dt = datetime.fromisoformat(date_str)
                new_next = next_from_last(last_dt, sub["period"])
                updates["last_charge_date"] = last_dt.strftime("%Y-%m-%d")
                updates["next_date"] = new_next.strftime("%Y-%m-%d")
            
            update_subscription_fields(existing_id, updates, user_id)
            
            await query.edit_message_text(
                f"✅ Обновлено\\!\n💰 {escape_md(format_price(amount, currency))}",
                parse_mode="MarkdownV2"
            )
            
            delete_temp_data(temp_id)
            
        except Exception as e:
            logger.error(f"dup_update error: {e}")
            await query.edit_message_text("❌ Произошла ошибка.")
        return
    
    elif data.startswith("dup_create:"):
        try:
            parts = data.split(":")
            if len(parts) < 2:
                return
            temp_id = int(parts[1])
            
            temp_data = get_temp_data(temp_id, user_id)
            if not temp_data:
                await query.edit_message_text("❌ Данные устарели. Попробуйте снова.")
                return
            
            data_parts = temp_data.split("|")
            if len(data_parts) < 4:
                return
            
            name, amount_str, currency, date_str = data_parts
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
                f"✅ Создано: *{escape_md(name)}*\n"
                f"💰 {escape_md(format_price(amount, currency))}\n"
                f"📅 {escape_md(format_date(next_dt))}\n\n"
                f"📅 *Выбери период:*",
                parse_mode="MarkdownV2",
                reply_markup=period_keyboard(new_id)
            )
            
            delete_temp_data(temp_id)
            
        except Exception as e:
            logger.error(f"dup_create error: {e}")
            await query.edit_message_text("❌ Произошла ошибка.")
        return
    
    elif data.startswith("dup_cancel:"):
        try:
            parts = data.split(":")
            if len(parts) >= 2:
                temp_id = int(parts[1])
                delete_temp_data(temp_id)
        except (ValueError, IndexError):
            pass
        await query.edit_message_text("Отменено 👌")


# ─────────────────────────────────────────────────────────────
# EDIT HANDLERS (inline editing via messages)
# ─────────────────────────────────────────────────────────────
async def handle_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обрабатывает ввод при редактировании. Возвращает True если обработано."""
    user_id = update.effective_user.id
    edit_sub_id = context.user_data.get("edit_sub_id")
    edit_field = context.user_data.get("edit_field")
    
    if not edit_sub_id or not edit_field:
        return False
    
    text = update.message.text.strip()
    
    sub = get_subscription_if_owner(edit_sub_id, user_id)
    if not sub:
        context.user_data.pop("edit_sub_id", None)
        context.user_data.pop("edit_field", None)
        await update.message.reply_text("❌ Подписка не найдена.", reply_markup=main_menu_keyboard())
        return True
    
    if edit_field == "price":
        parsed = parse_price(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Не понял цену. Введи число и валюту:\n129 kr, 9.99 EUR, 100\n\n"
                "Отправь /cancel для отмены"
            )
            return True
        
        amount, currency = parsed
        price = pack_price(amount, currency)
        update_subscription_field(edit_sub_id, "price", price, user_id)
        
        context.user_data.pop("edit_sub_id", None)
        context.user_data.pop("edit_field", None)
        
        await update.message.reply_text(
            f"✅ Цена обновлена: {escape_md(format_price(amount, currency))}",
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
        return True
    
    elif edit_field == "name":
        if len(text) > MAX_NAME_LENGTH:
            await update.message.reply_text(
                f"❌ Слишком длинное название (макс. {MAX_NAME_LENGTH})\n\n"
                "Отправь /cancel для отмены"
            )
            return True
        
        # Для названия нужно напрямую обновить в БД, т.к. name не в ALLOWED_SUBSCRIPTION_FIELDS
        with get_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE subscriptions SET name = ? WHERE id = ? AND user_id = ?", 
                      (text, edit_sub_id, user_id))
        
        context.user_data.pop("edit_sub_id", None)
        context.user_data.pop("edit_field", None)
        
        await update.message.reply_text(
            f"✅ Название обновлено: *{escape_md(text)}*",
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
        return True
    
    return False


# ─────────────────────────────────────────────────────────────
# MENU ROUTER
# ─────────────────────────────────────────────────────────────
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Роутер главного меню и сообщений."""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Проверяем, не редактируем ли что-то
    if await handle_edit_input(update, context):
        return None
    
    # Кнопки меню
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
            await update.message.reply_text(
                f"❌ Лимит: {MAX_SUBSCRIPTIONS_PER_USER} подписок.", 
                reply_markup=main_menu_keyboard()
            )
            return None
        return await process_quick_add(update, context, quick)
    
    await update.message.reply_text(
        "🤔 Не понял\\. Попробуй:\n`Netflix 129 kr 15\\.01\\.26`",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )
    return None


# ─────────────────────────────────────────────────────────────
# DEBUG & TEST COMMANDS
# ─────────────────────────────────────────────────────────────
async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отладочная команда для просмотра платежей."""
    user_id = update.effective_user.id
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, subscription_id, amount, paid_at FROM payment_history WHERE user_id = ?", 
            (user_id,)
        )
        rows = c.fetchall()
    
    if not rows:
        await update.message.reply_text("Нет платежей в истории")
        return
    
    lines = ["Debug payment_history:\n"]
    for row in rows:
        lines.append(f"id={row[0]} sub={row[1]} amount={row[2]} date={row[3]}")
    await update.message.reply_text("\n".join(lines))


async def test_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тестовая команда для проверки напоминаний."""
    user_id = update.effective_user.id
    subs = list_subscriptions(user_id)
    
    if not subs:
        await update.message.reply_text("У тебя нет подписок для теста")
        return
    
    sub = subs[0]
    amount, currency = unpack_price(sub["price"])
    price_view = format_price(amount, currency)
    
    await update.message.reply_text(
        f"⏰ *Тестовое напоминание*\n\n"
        f"Завтра оплата *{escape_md(sub['name'])}*\n"
        f"💰 {escape_md(price_view)}\n\n"
        f"✅ Напоминания работают\\!",
        parse_mode="MarkdownV2"
    )


# ─────────────────────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────────────────────
async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет напоминания о предстоящих платежах."""
    today = datetime.now().date()
    
    with get_db() as conn:
        c = conn.cursor()
        
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
    
    user_settings = {}
    for row in settings_rows:
        user_settings[row[0]] = {
            "enabled": bool(row[1]) if row[1] is not None else True, 
            "days": row[2] or "1,3"
        }
    
    for sub in all_subs:
        user_id, name, price_str, next_date = sub
        try:
            settings = user_settings.get(user_id, {"enabled": True, "days": "1,3"})
            if not settings["enabled"]:
                continue
            
            dt = datetime.strptime(next_date, "%Y-%m-%d").date()
            days_left = (dt - today).days
            
            try:
                reminder_days = [int(d.strip()) for d in settings["days"].split(",")]
            except ValueError:
                reminder_days = [1, 3]
            
            if days_left in reminder_days:
                amount, currency = unpack_price(price_str)
                price_view = format_price(amount, currency)
                
                if days_left == 1:
                    when = "Завтра"
                elif days_left == 0:
                    when = "Сегодня"
                else:
                    when = f"Через {days_left} дн."
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏰ *Напоминание*\n\n{when} оплата *{escape_md(name)}*\n💰 {escape_md(price_view)}",
                    parse_mode="MarkdownV2"
                )
                logger.info(f"Reminder sent to {user_id} for {name}")
                
        except Exception as e:
            logger.error(f"Failed to send reminder to {user_id}: {e}")


async def cleanup_temp_data_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job для очистки устаревших временных данных."""
    cleanup_expired_temp_data()
    logger.info("Cleaned up expired temp data")


# ─────────────────────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────────────────────
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок."""
    logger.error(f"Exception: {context.error}", exc_info=True)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Произошла ошибка 😕 Попробуй /start", 
                reply_markup=main_menu_keyboard()
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
async def post_init(app: Application) -> None:
    """Инициализация после запуска."""
    await app.bot.delete_webhook(drop_pending_updates=True)
    me = await app.bot.get_me()
    logger.info(f"✅ Bot running: @{me.username} (id={me.id})")


def main() -> None:
    """Главная функция запуска бота."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    init_db()
    logger.info("🚀 CODE VERSION: 2026-01-04 v7 (fixed + period selection)")
    
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Настройка job queue для напоминаний
    job_queue = application.job_queue
    if job_queue:
        # Напоминания каждый день в 9:00 UTC
        job_queue.run_daily(
            send_reminders,
            time=dt_time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE),
            name="daily_reminders"
        )
        logger.info(f"Reminders scheduled at {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} UTC")
        
        # Очистка временных данных каждый час
        job_queue.run_repeating(
            cleanup_temp_data_job,
            interval=3600,
            first=60,
            name="cleanup_temp_data"
        )
        logger.info("Temp data cleanup scheduled")
    
    # Conversation handler для добавления подписок
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"^➕ Добавить$"), add_start),
        ],
        states={
            ADD_NAME: [
                MessageHandler(filters.Regex(r"^❌ Отмена$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_name),
            ],
            ADD_PRICE: [
                MessageHandler(filters.Regex(r"^❌ Отмена$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_price),
            ],
            ADD_DATE: [
                MessageHandler(filters.Regex(r"^❌ Отмена$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_flow_date),
            ],
            ADD_PERIOD: [
                CallbackQueryHandler(add_flow_period_callback, pattern=r"^add_period:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(r"^❌ Отмена$"), cancel),
        ],
        allow_reentry=True,
    )
    
    # Регистрация handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("next", next_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("debug", debug_cmd))
    application.add_handler(CommandHandler("test_reminder", test_reminder_cmd))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(add_conv)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^(settings:|set_)"))
    application.add_handler(CallbackQueryHandler(duplicate_callback, pattern=r"^dup_"))
    application.add_handler(CallbackQueryHandler(callback_router))
    
    # Обработчик текстовых сообщений (меню и быстрое добавление)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    logger.info("Bot starting v7 (fixed + period selection)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
