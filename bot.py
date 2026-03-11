import os
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

chat_sessions = {}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я AI асистент на базі Gemini.\n\nМожу:\n• Відповідати на запитання\n• Аналізувати зображення 🖼️\n\nНапиши або надішли фото 😊"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text

    if user_id not in chat_sessions:
        chat_sessions[user_id] = model.start_chat(history=[])

    chat = chat_sessions[user_id]

    try:
        response = chat.send_message(user_text)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"Помилка: {str(e)}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")

    try:
        # Завантажуємо фото
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()

        # Текст від користувача (якщо є підпис до фото)
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською мовою."

        # Відправляємо в Gemini
        response = model.generate_content([
            {"mime_type": "image/jpeg", "data": bytes(img_bytes)},
            caption
        ])

        await msg.edit_text(response.text)
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {str(e)}")

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in chat_sessions:
        del chat_sessions[user_id]
    await update.message.reply_text("Історію чату очищено! 🔄")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
