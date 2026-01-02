import os
import sqlite3
import logging

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
    price = args[1]
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
        f"#{new_id} • {name} • {price} • списание {day}-го"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    rows = list_subscriptions(user_id)

    if not rows:
        await update.message.reply_text("Пока нет подписок. Добавь: /add Netflix 129 15")
        return

    lines = ["Твои подписки:"]
    for _id, name, price, day in rows:
        lines.append(f"#{_id} • {name} • {price} • день {day}")
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
    # Не всегда update будет Update
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

    # ConversationHandler MUST be added before generic text handlers (if you ever add them later)
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
    application.add_handler(edit_conv)

    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
