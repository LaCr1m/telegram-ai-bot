import os
import httpx
import base64
import asyncio
import io
from datetime import date, datetime, timedelta
from tavily import TavilyClient
from PyPDF2 import PdfReader
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

SYSTEM_PROMPT = {"role": "system", "content": "Ти розумний і корисний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою, незалежно від мови запиту. Використовуй грамотну, природну українську мову без суржику. Будь точним, лаконічним і дружнім. Структуруй відповіді — використовуй абзаци, списки де доречно. Якщо питання незрозуміле — перепитай. Ніколи не вигадуй факти. В групових чатах відповідай тільки коли тебе згадують через @."}

chat_histories = {}
tavily = TavilyClient(api_key=TAVILY_API_KEY)

or_requests = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190

def get_text_provider():
    global or_requests
    today = date.today()
    if or_requests["date"] != today:
        or_requests = {"count": 0, "date": today}
    return "openrouter" if or_requests["count"] < OR_DAILY_LIMIT else "groq"

async def call_ai(messages):
    provider = get_text_provider()
    if provider == "openrouter":
        url, key, model = OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL
    else:
        url, key, model = GROQ_URL, GROQ_API_KEY, GROQ_MODEL

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages}

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 429 and provider == "openrouter":
            or_requests["count"] = OR_DAILY_LIMIT
            return await call_ai(messages)
        r.raise_for_status()

    if provider == "openrouter":
        or_requests["count"] += 1

    return r.json()["choices"][0]["message"]["content"]

async def call_vision(messages):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
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

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        return f"Помилка читання PDF: {str(e)}"


async def send_reminder(bot, chat_id, text):
    await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я J.A.R.V.I.S. 🤖\n\nМожу:\n• Відповідати на запитання 💬\n• Аналізувати зображення 🖼️\n• Генерувати зображення 🎨\n• Розуміти голосові повідомлення 🎤\n• Шукати в інтернеті 🌐\n• Читати PDF та документи 📄\n• Нагадування 🔔\n• Працювати в групових чатах 👥\n\nКоманди:\n/image опис — генерація зображення\n/remind 30m текст — нагадування\n/search запит — пошук\n/status — статус\n/reset — очистити історію"
    )

async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args)
    if not prompt:
        await update.message.reply_text("Напиши що намалювати: /image захід сонця над морем")
        return

    msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
    try:
        import urllib.parse
        translation = await call_ai([
            {"role": "user", "content": f"Translate this image description to English, return ONLY the translation, no explanations: {prompt}"}
        ])
        encoded_prompt = urllib.parse.quote(translation)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            img_bytes = r.content

        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {str(e)}")

async def handle_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Використання:\n/remind 30m Зателефонувати\n/remind 2h Нарада\n/remind 14:30 Обід")
        return

    time_arg = ctx.args[0]
    reminder_text = " ".join(ctx.args[1:])
    now = datetime.now()

    try:
        if time_arg.endswith("m"):
            delay = int(time_arg[:-1]) * 60
            when = f"через {time_arg[:-1]} хвилин"
        elif time_arg.endswith("h"):
            delay = int(time_arg[:-1]) * 3600
            when = f"через {time_arg[:-1]} годин"
        elif ":" in time_arg:
            t = datetime.strptime(time_arg, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            if t < now:
                t += timedelta(days=1)
            delay = int((t - now).total_seconds())
            when = f"о {time_arg}"
        else:
            await update.message.reply_text("❌ Формат невірний. Використай: 30m, 2h або 14:30")
            return
    except ValueError:
        await update.message.reply_text("❌ Формат невірний. Використай: 30m, 2h або 14:30")
        return

    chat_id = update.effective_chat.id
    bot = ctx.bot

    async def delayed():
        await asyncio.sleep(delay)
        await send_reminder(bot, chat_id, reminder_text)

    asyncio.create_task(delayed())
    await update.message.reply_text(f"✅ Нагадаю {when}: {reminder_text}")

async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    provider = get_text_provider()
    remaining = max(0, OR_DAILY_LIMIT - or_requests["count"])
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• Провайдер: {'OpenRouter 🟢' if provider == 'openrouter' else 'Groq 🔵'}\n"
        f"• OpenRouter залишилось: {remaining}/{OR_DAILY_LIMIT}\n"
        f"• Ліміт скидається: щодня опівночі"
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    user_text = update.message.text
    bot_username = ctx.bot.username

    if chat_type in ["group", "supergroup"]:
        if f"@{bot_username}" not in user_text:
            return
        user_text = user_text.replace(f"@{bot_username}", "").strip()

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

    prompt = f"Користувач шукав: '{query}'\n\nРезультати:\n{results}\n\nСтисло підсумуй українською."
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
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": caption}
            ]}
        ]
        reply = await call_vision(messages)
        await msg.edit_text(reply)
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {str(e)}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("📄 Наразі підтримуються тільки PDF файли.")
        return

    msg = await update.message.reply_text("📄 Читаю PDF...")
    try:
        file = await ctx.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        text = extract_pdf_text(bytes(pdf_bytes))

        if not text:
            await msg.edit_text("❌ Не вдалось витягти текст з PDF.")
            return

        text_preview = text[:4000] + ("..." if len(text) > 4000 else "")
        caption = update.message.caption or "Стисло підсумуй цей документ українською мовою."

        user_id = update.message.from_user.id
        if user_id not in chat_histories:
            chat_histories[user_id] = [SYSTEM_PROMPT]

        prompt = f"{caption}\n\nВміст документу:\n{text_preview}"
        chat_histories[user_id].append({"role": "user", "content": prompt})

        reply = await call_ai(chat_histories[user_id])
        chat_histories[user_id].append({"role": "assistant", "content": reply})
        await msg.edit_text(f"📄 *{doc.file_name}*\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці PDF: {str(e)}")

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
    app.add_handler(CommandHandler("remind", handle_remind))
    app.add_handler(CommandHandler("image", handle_image))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
