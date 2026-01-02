import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Использование:\n"
            "/add <название> <сумма> <день>\n\n"
            "Пример:\n"
            "/add Netflix 12.99 15"
        )
        return

    name = args[0]
    price = args[1]
    day = args[2]

    await update.message.reply_text(
        f"Подписка добавлена (черновик):\n\n"
        f"Сервис: {name}\n"
        f"Сумма: {price}\n"
        f"День списания: {day}\n\n"
        f"⏳ Напоминания скоро появятся"
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

