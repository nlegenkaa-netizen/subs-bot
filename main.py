import os
import sqlite3
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "subs.db")


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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
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
    sub_id = cur.lastrowid
    conn.close()
    return sub_id


def list_subscriptions(user_id: int) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, price, day FROM subscriptions WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_subscription(user_id: int, sub_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM subscriptions WHERE user_id = ? AND id = ?",
        (user_id, sub_id),
    )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я бот для подписок 👋\n\n"
        "Команды:\n"
        "• /add <название> <сумма> <день>\n"
        "  пример: /add Netflix 12.99 15\n"
        "• /list — показать подписки\n"
        "• /del <id> — удалить по id\n"
        "  пример: /del 3\n"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Использование:\n"
            "/add <название> <сумма> <день>\n\n"
            "Пример:\n"
            "/add Netflix 12.99 15"
        )
        return

    def init_db():
    conn = sqlite3.connect("/data/subs.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            price TEXT,
            day TEXT
        )
    """)

    conn.commit()
    conn.close()


    name = args[0]
    price = args[1]

    try:
        day = int(args[2])
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await update.message.reply_text("День списания должен быть числом от 1 до 31. Пример: /add Netflix 12.99 15")
        return

    sub_id = add_subscription(user_id, name, price, day)

    await update.message.reply_text(
        "✅ Подписка добавлена!\n\n"
        f"ID: {sub_id}\n"
        f"Сервис: {name}\n"
        f"Сумма: {price}\n"
        f"День списания: {day}\n\n"
        "Посмотреть список: /list"
    )


async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок.\nДобавь первую: /add Netflix 12.99 15")
        return

    lines = ["📋 Твои подписки (последние сверху):\n"]
    for sub_id, name, price, day in rows:
        lines.append(f"ID {sub_id}: {name} — {price} — списание {day}")
    lines.append("\nУдалить: /del <id> (например /del 3)")
    await update.message.reply_text("\n".join(lines))


async def delete_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 1:
        await update.message.reply_text("Использование: /del <id>  (пример: /del 3)")
        return

    try:
        sub_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом. Пример: /del 3")
        return

    ok = delete_subscription(user_id, sub_id)
    await update.message.reply_text("🗑 Удалено." if ok else "Не нашла подписку с таким ID.")


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Не поняла. Команды: /add, /list, /del")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в переменных окружения.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_subs))
    app.add_handler(CommandHandler("del", delete_sub))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
