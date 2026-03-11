import os
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-2.0-flash"

chat_sessions = {}

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я AI асистент на базі Gemini.\n\nМожу:\n• Відповідати на запитання\n• Аналізувати зображення 🖼️\n\nНапиши або надішли фото 😊"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text

    if user_id not in chat_sessions:
        chat_sessions[user_id] = []

    chat_sessions[user_id].append(
        types.Content(role="user", parts=[types.Part(text=user_text)])
    )

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=chat_sessions[user_id]
        )
        reply = response.text
        chat_sessions[user_id].append(
            types.Content(role="model", parts=[types.Part(text=reply)])
        )
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Помилка: {str(e)}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською мовою."

        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=bytes(img_bytes))),
                types.Part(text=caption)
            ]
        )
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
