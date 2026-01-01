import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я бот для подписок 👋\n\n"
        "Пока я умею:\n"
        "• /start — показать это сообщение\n\n"
        "Скоро добавим напоминания за 7 дней."
    )


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Я получил сообщение 🙂\n\n"
        "Пока я в режиме заготовки.\n"
        "Доступна команда: /start\n\n"
        "Скоро добавим:\n"
        "• добавление подписок\n"
        "• напоминания за 7 дней"
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в переменных окружения.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
main()
