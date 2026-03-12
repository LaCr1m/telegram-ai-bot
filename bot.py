import os
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
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY    = os.environ.get("TAVILY_API_KEY")
HF_TOKEN          = os.environ.get("HF_TOKEN")

# ── URL та моделі ────────────────────────────────────────────────────────────
OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
GROQ_MODEL       = "llama-3.3-70b-versatile"
VISION_MODEL     = "openrouter/auto"                  # авто-вибір моделі з vision
HF_IMAGE_URL     = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"

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

MAX_HISTORY_MESSAGES = 20   # максимум повідомлень (не враховуючи system prompt)
REMINDERS_FILE       = "reminders.json"  # файл для збереження нагадувань

# ── Стан ─────────────────────────────────────────────────────────────────────
chat_histories: dict[int, list] = {}
tavily = TavilyClient(api_key=TAVILY_API_KEY)
or_requests = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: історія чату
# ════════════════════════════════════════════════════════════════════════════

def get_history(user_id: int) -> list:
    """Повертає історію чату, ініціалізуючи при потребі."""
    if user_id not in chat_histories:
        chat_histories[user_id] = [SYSTEM_PROMPT]
    return chat_histories[user_id]


def append_and_trim(user_id: int, role: str, content) -> None:
    """Додає повідомлення в історію та обрізає до MAX_HISTORY_MESSAGES."""
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    # Зберігаємо system prompt + останні MAX_HISTORY_MESSAGES повідомлень
    if len(history) > MAX_HISTORY_MESSAGES + 1:
        chat_histories[user_id] = [SYSTEM_PROMPT] + history[-MAX_HISTORY_MESSAGES:]


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: провайдер AI
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
        url, key, model = OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL
    else:
        url, key, model = GROQ_URL, GROQ_API_KEY, GROQ_MODEL

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body    = {"model": model, "messages": messages}

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 429 and provider == "openrouter":
            or_requests["count"] = OR_DAILY_LIMIT
            return await call_ai(messages)
        r.raise_for_status()

    if provider == "openrouter":
        or_requests["count"] += 1

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
# Утиліти: пошук
# ════════════════════════════════════════════════════════════════════════════

def search_web(query: str) -> str:
    try:
        results = tavily.search(query=query, max_results=3)
        output = ""
        for item in results.get("results", []):
            output += f"**{item['title']}**\n{item['content'][:300]}\n🔗 {item['url']}\n\n"
        return output or "Нічого не знайдено."
    except Exception as e:
        return f"Помилка пошуку: {e}"


# ════════════════════════════════════════════════════════════════════════════
# Утиліти: PDF
# ════════════════════════════════════════════════════════════════════════════

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as e:
        return f"Помилка читання PDF: {e}"


# ════════════════════════════════════════════════════════════════════════════
# Нагадування з персистентністю
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
    """Планує нагадування і зберігає його у файл."""
    entry = {
        "chat_id": chat_id,
        "text": text,
        "fire_at": fire_at.isoformat()
    }
    reminders = load_reminders()
    reminders.append(entry)
    save_reminders(reminders)

    delay = max(0, (fire_at - datetime.now()).total_seconds())

    async def _run():
        await asyncio.sleep(delay)
        await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
        # Видаляємо з файлу після спрацювання
        current = load_reminders()
        current = [r for r in current if not (r["chat_id"] == chat_id and r["text"] == text and r["fire_at"] == fire_at.isoformat())]
        save_reminders(current)

    asyncio.create_task(_run())


async def restore_reminders(bot) -> None:
    """Відновлює нагадування після рестарту бота."""
    reminders = load_reminders()
    now = datetime.now()
    valid = []

    for r in reminders:
        fire_at = datetime.fromisoformat(r["fire_at"])
        if fire_at <= now:
            # Час вже минув — надсилаємо одразу з поміткою
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
                current = load_reminders()
                current = [x for x in current if not (x["chat_id"] == chat_id and x["text"] == text and x["fire_at"] == fi.isoformat())]
                save_reminders(current)

            asyncio.create_task(_run())

    save_reminders(valid)


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
        "• Нагадування (зберігаються після рестарту) 🔔\n"
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

        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(HF_IMAGE_URL, headers=headers, json={"inputs": translation})
            r.raise_for_status()

        await update.message.reply_photo(photo=r.content, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {e}")


async def handle_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Використання:\n/remind 30m Зателефонувати\n/remind 2h Нарада\n/remind 14:30 Обід"
        )
        return

    time_arg = ctx.args[0]
    reminder_text = " ".join(ctx.args[1:])
    now = datetime.now()

    try:
        if time_arg.endswith("m"):
            fire_at = now + timedelta(minutes=int(time_arg[:-1]))
            when = f"через {time_arg[:-1]} хв"
        elif time_arg.endswith("h"):
            fire_at = now + timedelta(hours=int(time_arg[:-1]))
            when = f"через {time_arg[:-1]} год"
        elif ":" in time_arg:
            t = datetime.strptime(time_arg, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            if t < now:
                t += timedelta(days=1)
            fire_at = t
            when = f"о {time_arg}"
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
    reminders = load_reminders()
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• Провайдер: {'OpenRouter 🟢' if provider == 'openrouter' else 'Groq 🔵'}\n"
        f"• OpenRouter залишилось: {remaining}/{OR_DAILY_LIMIT}\n"
        f"• Активних нагадувань: {len(reminders)}\n"
        f"• Ліміт скидається: щодня опівночі"
    )


async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Напиши що шукати: /search новини України")
        return

    msg = await update.message.reply_text(f"🌐 Шукаю: {query}...")
    results = search_web(query)

    # Пошуковий промпт передаємо окремо — НЕ зберігаємо в основну історію,
    # щоб не засмічувати контекст великими блоками результатів.
    search_messages = [
        SYSTEM_PROMPT,
        {"role": "user", "content": f"Користувач шукав: '{query}'\n\nРезультати:\n{results}\n\nСтисло підсумуй українською."}
    ]

    try:
        reply = await call_ai(search_messages)
        # В основну історію додаємо тільки стислу відповідь
        user_id = update.message.from_user.id
        append_and_trim(user_id, "user", f"Пошук: {query}")
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"🌐 *{query}*\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Помилка: {e}")


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    chat_histories.pop(user_id, None)
    await update.message.reply_text("Історію чату очищено! 🔄")


# ════════════════════════════════════════════════════════════════════════════
# Обробники повідомлень
# ════════════════════════════════════════════════════════════════════════════

def _is_bot_addressed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    """
    Перевіряє, чи звернулись до бота у груповому чаті.
    Повертає (addressed, cleaned_text).
    """
    user_text    = update.message.text or ""
    bot_username = ctx.bot.username
    chat_type    = update.message.chat.type

    if chat_type not in ("group", "supergroup"):
        return True, user_text

    # Згадка через @
    if f"@{bot_username}" in user_text:
        return True, user_text.replace(f"@{bot_username}", "").strip()

    # Відповідь на повідомлення бота
    reply = update.message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == ctx.bot.id:
        return True, user_text

    return False, user_text


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addressed, user_text = _is_bot_addressed(update, ctx)
    if not addressed:
        return

    user_id = update.message.from_user.id
    append_and_trim(user_id, "user", user_text)

    try:
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await update.message.reply_text(reply)
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
        await msg.edit_text(reply)
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
        pdf_bytes = bytes(await file.download_as_bytearray())
        text      = extract_pdf_text(pdf_bytes)

        if not text:
            await msg.edit_text("❌ Не вдалось витягти текст з PDF.")
            return

        caption      = update.message.caption or "Стисло підсумуй цей документ українською мовою."
        text_preview = text[:4000] + ("..." if len(text) > 4000 else "")
        user_id      = update.message.from_user.id

        append_and_trim(user_id, "user", f"{caption}\n\nВміст документу:\n{text_preview}")
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"📄 *{doc.file_name}*\n\n{reply}", parse_mode="Markdown")
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

        await msg.edit_text(f"🎤 Ти сказав: _{text}_\n\n⏳ Обробляю...", parse_mode="Markdown")

        user_id = update.message.from_user.id
        append_and_trim(user_id, "user", text)
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"🎤 Ти сказав: _{text}_\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці голосового: {e}")


# ════════════════════════════════════════════════════════════════════════════
# Запуск
# ════════════════════════════════════════════════════════════════════════════

async def post_init(app):
    """Виконується після запуску бота — відновлює нагадування."""
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
