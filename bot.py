import os
import httpx
import base64
from datetime import date
from tavily import TavilyClient
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
GROQ_MODEL = "llama-3.3-70b-versatile"
VISION_MODEL = "openrouter/auto"

SYSTEM_PROMPT = {"role": "system", "content": "Ти корисний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою, незалежно від мови запиту. Будь точним, корисним і дружнім."}

chat_histories = {}
tavily = TavilyClient(api_key=TAVILY_API_KEY)

# Лічильник запитів OpenRouter
or_requests = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190  # трохи менше 200 для запасу
# Логіка: OpenRouter (190/день) → Groq (14400/день) → наступного дня знову OpenRouter

def get_text_provider():
    global or_requests
    today = date.today()
    if or_requests["date"] != today:
        or_requests = {"count": 0, "date": today}

    if or_requests["count"] < OR_DAILY_LIMIT:
        return "openrouter"
    return "groq"

async def call_ai(messages):
    provider = get_text_provider()

    if provider == "openrouter":
        url = OPENROUTER_URL
        key = OPENROUTER_API_KEY
        model = OPENROUTER_MODEL
    else:
        url = GROQ_URL
        key = GROQ_API_KEY
        model = GROQ_MODEL

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    body = {"model": model, "messages": messages}

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 429 and provider == "openrouter":
            # Якщо OpenRouter повернув 429 — форсуємо Groq
            or_requests["count"] = OR_DAILY_LIMIT
            return await call_ai(messages)
        r.raise_for_status()

    if provider == "openrouter":
        or_requests["count"] += 1

    return r.json()["choices"][0]["message"]["content"]

async def call_vision(messages):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {"model": VISION_MODEL, "messages": messages}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(OPENROUTER_URL, headers=headers, json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def transcribe_voice(audio_bytes: bytes) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
    data = {"model": "whisper-large-v3", "language": "uk", "response_format": "text"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(GROQ_WHISPER_URL, headers=headers, files=files, data=data)
        r.raise_for_status()
        return r.text.strip()

def search_web(query: str) -> str:
    try:
        results = tavily.search(query=query, max_results=3)
        output = ""
        for r in results.get("results", []):
            output += f"**{r['title']}**\n{r['content'][:300]}\n🔗 {r['url']}\n\n"
        return output or "Нічого не знайдено."
    except Exception as e:
        return f"Помилка пошуку: {str(e)}"

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я J.A.R.V.I.S. 🤖\n\nМожу:\n• Відповідати на запитання 💬\n• Аналізувати зображення 🖼️\n• Розуміти голосові повідомлення 🎤\n• Шукати в інтернеті 🌐\n\nКоманди:\n/search [запит] — пошук в інтернеті\n/status — статус запитів\n/reset — очистити історію"
    )

async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    provider = get_text_provider()
    remaining = max(0, OR_DAILY_LIMIT - or_requests["count"])
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• Поточний провайдер: {'OpenRouter 🟢' if provider == 'openrouter' else 'Groq 🔵'}\n"
        f"• OpenRouter залишилось: {remaining}/{OR_DAILY_LIMIT} запитів\n"
        f"• Ліміт скидається: щодня опівночі"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text

    if user_id not in chat_histories:
        chat_histories[user_id] = [SYSTEM_PROMPT]

    chat_histories[user_id].append({"role": "user", "content": user_text})

    try:
        reply = await call_ai(chat_histories[user_id])
        chat_histories[user_id].append({"role": "assistant", "content": reply})
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Помилка: {str(e)}")

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Напиши що шукати: /search новини України")
        return

    msg = await update.message.reply_text(f"🌐 Шукаю: {query}...")
    results = search_web(query)

    user_id = update.message.from_user.id
    if user_id not in chat_histories:
        chat_histories[user_id] = [SYSTEM_PROMPT]

    prompt = f"Користувач шукав: '{query}'\n\nРезультати пошуку:\n{results}\n\nСтисло підсумуй результати українською мовою."
    chat_histories[user_id].append({"role": "user", "content": prompt})

    try:
        reply = await call_ai(chat_histories[user_id])
        chat_histories[user_id].append({"role": "assistant", "content": reply})
        await msg.edit_text(f"🌐 *{query}*\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Помилка: {str(e)}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською мовою."

        messages = [
            SYSTEM_PROMPT,
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": caption}
                ]
            }
        ]
        reply = await call_vision(messages)
        await msg.edit_text(reply)
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {str(e)}")

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎤 Розпізнаю голосове повідомлення...")
    try:
        voice = update.message.voice
        file = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()

        text = await transcribe_voice(bytes(audio_bytes))

        if not text:
            await msg.edit_text("Не вдалось розпізнати мову 😔")
            return

        await msg.edit_text(f"🎤 Ти сказав: _{text}_\n\n⏳ Обробляю...", parse_mode="Markdown")

        user_id = update.message.from_user.id
        if user_id not in chat_histories:
            chat_histories[user_id] = [SYSTEM_PROMPT]

        chat_histories[user_id].append({"role": "user", "content": text})
        reply = await call_ai(chat_histories[user_id])
        chat_histories[user_id].append({"role": "assistant", "content": reply})

        await msg.edit_text(f"🎤 Ти сказав: _{text}_\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці голосового: {str(e)}")

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in chat_histories:
        del chat_histories[user_id]
    await update.message.reply_text("Історію чату очищено! 🔄")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("search", handle_search))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
