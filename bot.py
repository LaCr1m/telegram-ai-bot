import os
import re
import httpx
import base64
import asyncio
import io
import json
from datetime import date, datetime, timedelta
from tavily import TavilyClient
from PyPDF2 import PdfReader
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

# ── Змінні середовища ────────────────────────────────────────────────────────
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY     = os.environ.get("TAVILY_API_KEY")
CF_API_TOKEN       = os.environ.get("CF_API_TOKEN")
CF_ACCOUNT_ID      = os.environ.get("CF_ACCOUNT_ID")

# ── URL та моделі ────────────────────────────────────────────────────────────
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL         = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_MODEL_FALLBACK = "openrouter/free"
GROQ_MODEL       = "llama-3.3-70b-versatile"
VISION_MODEL     = "openrouter/auto"
CF_IMAGE_URL     = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"

# ── Системний промпт ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Ти розумний і корисний AI асистент на ім'я J.A.R.V.I.S. "
        "Завжди відповідай виключно українською мовою, незалежно від мови запиту. "
        "Використовуй грамотну, природну українську мову без суржику. "
        "Будь точним, лаконічним і дружнім. Структуруй відповіді — використовуй абзаци, "
        "списки де доречно. Якщо питання незрозуміле — перепитай. Ніколи не вигадуй факти. "
        "В групових чатах відповідай тільки коли тебе згадують через @ або відповідають на твоє повідомлення."
    )
}

MAX_HISTORY_MESSAGES = 20
REMINDERS_FILE       = "reminders.json"

# ── Ключові слова ─────────────────────────────────────────────────────────────
REMIND_KEYWORDS = [
    "нагадай", "нагади", "нагадуй", "remind me", "set reminder",
    "нагадування", "постав нагадування"
]
IMAGE_KEYWORDS = [
    "створи фото", "згенеруй фото", "намалюй", "згенеруй зображення",
    "створи зображення", "зроби фото", "зроби картинку", "створи картинку",
    "generate image", "draw", "create image", "create photo"
]
SEARCH_KEYWORDS = [
    "пошукай", "знайди", "загугли", "що відбувається", "останні новини",
    "яка погода", "який курс", "поточн", "зараз", "сьогодні", "актуальн",
    "search", "find", "look up"
]

# ── Стан ─────────────────────────────────────────────────────────────────────
chat_histories: dict[int, list] = {}
tavily         = TavilyClient(api_key=TAVILY_API_KEY)
or_requests    = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: визначення намірів
# ════════════════════════════════════════════════════════════════════════════

def needs_reminder(text: str) -> bool:
    return any(kw in text.lower() for kw in REMIND_KEYWORDS)

def needs_image(text: str) -> bool:
    return any(kw in text.lower() for kw in IMAGE_KEYWORDS)

def needs_search(text: str) -> bool:
    return any(kw in text.lower() for kw in SEARCH_KEYWORDS)

def clean_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'__(.*?)__',     r'\1', text)
    text = re.sub(r'_(.*?)_',       r'\1', text)
    text = re.sub(r'`(.*?)`',       r'\1', text)
    return text


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: історія чату
# ════════════════════════════════════════════════════════════════════════════

def get_history(user_id: int) -> list:
    if user_id not in chat_histories:
        chat_histories[user_id] = [SYSTEM_PROMPT]
    return chat_histories[user_id]

def append_and_trim(user_id: int, role: str, content) -> None:
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_MESSAGES + 1:
        chat_histories[user_id] = [SYSTEM_PROMPT] + history[-MAX_HISTORY_MESSAGES:]


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: AI
# ════════════════════════════════════════════════════════════════════════════

def get_text_provider() -> str:
    global or_requests
    today = date.today()
    if or_requests["date"] != today:
        or_requests = {"count": 0, "date": today}
    return "openrouter" if or_requests["count"] < OR_DAILY_LIMIT else "groq"

async def call_ai(messages: list) -> str:
    provider = get_text_provider()
    if provider == "openrouter":
        url, key = OPENROUTER_URL, OPENROUTER_API_KEY
        # Спробуємо основну модель, при 404 — fallback на роутер
        for model in [OPENROUTER_MODEL, OPENROUTER_MODEL_FALLBACK]:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body    = {"model": model, "messages": messages}
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, headers=headers, json=body)
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                or_requests["count"] = OR_DAILY_LIMIT
                return await call_ai(messages)
            r.raise_for_status()
            or_requests["count"] += 1
            return r.json()["choices"][0]["message"]["content"]
        # Якщо обидві не спрацювали — переходимо на Groq
        or_requests["count"] = OR_DAILY_LIMIT
        return await call_ai(messages)
    else:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        body    = {"model": GROQ_MODEL, "messages": messages}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(GROQ_URL, headers=headers, json=body)
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def call_vision(messages: list) -> str:
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    body    = {"model": VISION_MODEL, "messages": messages}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(OPENROUTER_URL, headers=headers, json=body)
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

async def transcribe_voice(audio_bytes: bytes) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files   = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
    data    = {"model": "whisper-large-v3", "language": "uk", "response_format": "text"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(GROQ_WHISPER_URL, headers=headers, files=files, data=data)
        r.raise_for_status()
    return r.text.strip()


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: генерація зображень
# ════════════════════════════════════════════════════════════════════════════

async def generate_image(prompt: str) -> bytes:
    url     = CF_IMAGE_URL.format(account_id=CF_ACCOUNT_ID)
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(url, headers=headers, json={"prompt": prompt, "num_steps": 8})
        r.raise_for_status()
    if "image" in r.headers.get("content-type", ""):
        return r.content
    return base64.b64decode(r.json().get("result", {}).get("image", ""))


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: пошук і PDF
# ════════════════════════════════════════════════════════════════════════════

def search_web(query: str) -> str:
    try:
        results = tavily.search(query=query, max_results=3)
        output  = ""
        for item in results.get("results", []):
            output += f"{item['title']}\n{item['content'][:300]}\n{item['url']}\n\n"
        return output or "Нічого не знайдено."
    except Exception as e:
        return f"Помилка пошуку: {e}"

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as e:
        return f"Помилка читання PDF: {e}"


# ════════════════════════════════════════════════════════════════════════════
# Нагадування
# ════════════════════════════════════════════════════════════════════════════

def load_reminders() -> list:
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_reminders(reminders: list) -> None:
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False)

async def schedule_reminder(bot, chat_id: int, text: str, fire_at: datetime) -> None:
    entry = {"chat_id": chat_id, "text": text, "fire_at": fire_at.isoformat()}
    reminders = load_reminders()
    reminders.append(entry)
    save_reminders(reminders)
    delay = max(0, (fire_at - datetime.now()).total_seconds())

    async def _run():
        await asyncio.sleep(delay)
        await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
        current = [r for r in load_reminders() if not (
            r["chat_id"] == chat_id and r["text"] == text and r["fire_at"] == fire_at.isoformat()
        )]
        save_reminders(current)

    asyncio.create_task(_run())

async def restore_reminders(bot) -> None:
    reminders = load_reminders()
    now, valid = datetime.now(), []
    for r in reminders:
        fire_at = datetime.fromisoformat(r["fire_at"])
        if fire_at <= now:
            await bot.send_message(
                chat_id=r["chat_id"],
                text=f"🔔 Пропущене нагадування (бот був офлайн): {r['text']}"
            )
        else:
            valid.append(r)
            delay = (fire_at - now).total_seconds()
            async def _run(chat_id=r["chat_id"], text=r["text"], fi=fire_at):
                await asyncio.sleep(delay)
                await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
                save_reminders([x for x in load_reminders() if not (
                    x["chat_id"] == chat_id and x["text"] == text and x["fire_at"] == fi.isoformat()
                )])
            asyncio.create_task(_run())
    save_reminders(valid)


# ════════════════════════════════════════════════════════════════════════════
# Спільна логіка: генерація зображення з тексту
# ════════════════════════════════════════════════════════════════════════════

async def do_generate_image(update: Update, text: str, msg):
    t      = text.lower()
    prompt = text
    for kw in IMAGE_KEYWORDS:
        if kw in t:
            prompt = text[t.index(kw) + len(kw):].strip()
            break
    try:
        translation = await call_ai([{
            "role": "user",
            "content": f"Translate this image description to English, return ONLY the translation, no explanations: {prompt}"
        }])
        img_bytes = await generate_image(translation)
        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {e}")

async def do_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    now_str = datetime.now().strftime("%H:%M")
    parsed  = await call_ai([{"role": "user", "content": (
        f"Поточний час: {now_str}. З цього тексту витягни час нагадування і текст: '{text}'. "
        "Відповідай ТІЛЬКИ у форматі JSON: {\"delay_minutes\": 5, \"text\": \"текст\"} "
        "де delay_minutes — через скільки хвилин нагадати. Нічого більше не пиши."
    )}])
    parsed    = parsed.strip().replace("```json", "").replace("```", "")
    data      = json.loads(parsed)
    delay_min = int(data["delay_minutes"])
    remind_text = data["text"]
    fire_at   = datetime.now() + timedelta(minutes=delay_min)
    await schedule_reminder(ctx.bot, update.effective_chat.id, remind_text, fire_at)
    return delay_min, remind_text


# ════════════════════════════════════════════════════════════════════════════
# Обробники команд
# ════════════════════════════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я J.A.R.V.I.S. 🤖\n\n"
        "Можу:\n"
        "• Відповідати на запитання 💬\n"
        "• Аналізувати зображення 🖼️\n"
        "• Генерувати зображення 🎨\n"
        "• Розуміти голосові повідомлення 🎤\n"
        "• Шукати в інтернеті 🌐\n"
        "• Читати PDF та документи 📄\n"
        "• Нагадування 🔔\n"
        "• Працювати в групових чатах 👥\n\n"
        "Команди:\n"
        "/image опис — генерація зображення\n"
        "/remind 30m текст — нагадування\n"
        "/search запит — пошук\n"
        "/status — статус\n"
        "/reset — очистити історію"
    )

async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args)
    if not prompt:
        await update.message.reply_text("Напиши що намалювати: /image захід сонця над морем")
        return
    msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
    try:
        translation = await call_ai([{
            "role": "user",
            "content": f"Translate this image description to English, return ONLY the translation, no explanations: {prompt}"
        }])
        img_bytes = await generate_image(translation)
        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {e}")

async def handle_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Використання:\n/remind 30m Зателефонувати\n/remind 2h Нарада\n/remind 14:30 Обід"
        )
        return
    time_arg, reminder_text = ctx.args[0], " ".join(ctx.args[1:])
    now = datetime.now()
    try:
        if time_arg.endswith("m"):
            fire_at = now + timedelta(minutes=int(time_arg[:-1]))
            when    = f"через {time_arg[:-1]} хв"
        elif time_arg.endswith("h"):
            fire_at = now + timedelta(hours=int(time_arg[:-1]))
            when    = f"через {time_arg[:-1]} год"
        elif ":" in time_arg:
            t = datetime.strptime(time_arg, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            if t < now:
                t += timedelta(days=1)
            fire_at, when = t, f"о {time_arg}"
        else:
            await update.message.reply_text("❌ Формат невірний. Використай: 30m, 2h або 14:30")
            return
    except ValueError:
        await update.message.reply_text("❌ Формат невірний. Використай: 30m, 2h або 14:30")
        return
    await schedule_reminder(ctx.bot, update.effective_chat.id, reminder_text, fire_at)
    await update.message.reply_text(f"✅ Нагадаю {when}: {reminder_text}")

async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    provider  = get_text_provider()
    remaining = max(0, OR_DAILY_LIMIT - or_requests["count"])
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• Провайдер: {'OpenRouter 🟢' if provider == 'openrouter' else 'Groq 🔵'}\n"
        f"• OpenRouter залишилось: {remaining}/{OR_DAILY_LIMIT}\n"
        f"• Активних нагадувань: {len(load_reminders())}\n"
        f"• Ліміт скидається: щодня опівночі"
    )

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Напиши що шукати: /search новини України")
        return
    msg     = await update.message.reply_text(f"🌐 Шукаю: {query}...")
    results = search_web(query)
    try:
        reply = await call_ai([
            SYSTEM_PROMPT,
            {"role": "user", "content": f"Запит: '{query}'\n\nРезультати:\n{results}\n\nСтисло підсумуй українською."}
        ])
        user_id = update.message.from_user.id
        append_and_trim(user_id, "user", f"Пошук: {query}")
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"🌐 {query}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка: {e}")

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_histories.pop(update.message.from_user.id, None)
    await update.message.reply_text("Історію чату очищено! 🔄")


# ════════════════════════════════════════════════════════════════════════════
# Обробники повідомлень
# ════════════════════════════════════════════════════════════════════════════

def _is_bot_addressed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    user_text    = update.message.text or ""
    bot_username = ctx.bot.username
    chat_type    = update.message.chat.type
    if chat_type not in ("group", "supergroup"):
        return True, user_text
    if f"@{bot_username}" in user_text:
        return True, user_text.replace(f"@{bot_username}", "").strip()
    reply = update.message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == ctx.bot.id:
        return True, user_text
    return False, user_text

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addressed, user_text = _is_bot_addressed(update, ctx)
    if not addressed:
        return
    user_id = update.message.from_user.id

    # Нагадування
    if needs_reminder(user_text):
        try:
            delay_min, remind_text = await do_reminder(update, ctx, user_text)
            await update.message.reply_text(f"✅ Нагадаю через {delay_min} хв: {remind_text}")
        except Exception as e:
            await update.message.reply_text(f"Не вдалось встановити нагадування: {e}")
        return

    # Генерація зображення
    if needs_image(user_text):
        msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
        await do_generate_image(update, user_text, msg)
        return

    # Пошук
    if needs_search(user_text):
        msg     = await update.message.reply_text("🌐 Шукаю в інтернеті...")
        results = search_web(user_text)
        try:
            reply = await call_ai([
                SYSTEM_PROMPT,
                {"role": "user", "content": f"Запит: '{user_text}'\n\nРезультати:\n{results}\n\nДай корисну відповідь українською."}
            ])
            append_and_trim(user_id, "user", user_text)
            append_and_trim(user_id, "assistant", reply)
            await msg.edit_text(clean_markdown(reply))
        except Exception as e:
            await msg.edit_text(f"Помилка: {e}")
        return

    # Звичайна відповідь
    append_and_trim(user_id, "user", user_text)
    try:
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await update.message.reply_text(clean_markdown(reply))
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")
    try:
        photo   = update.message.photo[-1]
        file    = await ctx.bot.get_file(photo.file_id)
        img_b64 = base64.b64encode(await file.download_as_bytearray()).decode()
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською мовою."
        messages = [
            SYSTEM_PROMPT,
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": caption}
            ]}
        ]
        reply = await call_vision(messages)
        await msg.edit_text(clean_markdown(reply))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {e}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("📄 Наразі підтримуються тільки PDF файли.")
        return
    msg = await update.message.reply_text("📄 Читаю PDF...")
    try:
        file      = await ctx.bot.get_file(doc.file_id)
        text      = extract_pdf_text(bytes(await file.download_as_bytearray()))
        if not text:
            await msg.edit_text("❌ Не вдалось витягти текст з PDF.")
            return
        caption      = update.message.caption or "Стисло підсумуй цей документ українською мовою."
        text_preview = text[:4000] + ("..." if len(text) > 4000 else "")
        user_id      = update.message.from_user.id
        append_and_trim(user_id, "user", f"{caption}\n\nВміст документу:\n{text_preview}")
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"📄 {doc.file_name}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці PDF: {e}")

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎤 Розпізнаю голосове повідомлення...")
    try:
        file        = await ctx.bot.get_file(update.message.voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        text        = await transcribe_voice(audio_bytes)
        if not text:
            await msg.edit_text("Не вдалось розпізнати мову 😔")
            return

        await msg.edit_text(f"🎤 Ти сказав: {text}\n\n⏳ Обробляю...")
        user_id = update.message.from_user.id

        if needs_reminder(text):
            try:
                delay_min, remind_text = await do_reminder(update, ctx, text)
                await msg.edit_text(f"🎤 Ти сказав: {text}\n\n✅ Нагадаю через {delay_min} хв: {remind_text}")
            except Exception as e:
                await msg.edit_text(f"Не вдалось встановити нагадування: {e}")
            return

        if needs_image(text):
            await msg.edit_text(f"🎤 Ти сказав: {text}\n\n🎨 Генерую зображення...")
            await do_generate_image(update, text, msg)
            return

        append_and_trim(user_id, "user", text)
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"🎤 Ти сказав: {text}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці голосового: {e}")


# ════════════════════════════════════════════════════════════════════════════
# Запуск
# ════════════════════════════════════════════════════════════════════════════

async def post_init(app):
    await restore_reminders(app.bot)

if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("reset",  reset))
    app.add_handler(CommandHandler("search", handle_search))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("remind", handle_remind))
    app.add_handler(CommandHandler("image",  handle_image))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    app.add_handler(MessageHandler(filters.VOICE,        handle_voice))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
