import os
import re
import httpx
import base64
import asyncio
import io
import json
from datetime import date, datetime, timedelta
from tavily import TavilyClient
import openpyxl
from docx import Document as DocxDocument
try:
    from pypdf import PdfReader
except ImportError:
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
OPENROUTER_URL            = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL                  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL          = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_MODEL          = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_MODEL_FALLBACK = "openrouter/free"
GROQ_MODEL                = "llama-3.3-70b-versatile"
VISION_MODEL              = "openrouter/auto"
CF_IMAGE_URL              = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"

# ── Константи ─────────────────────────────────────────────────────────────────
MAX_HISTORY_MESSAGES = 20
REMINDERS_FILE       = "reminders.json"
MEMORY_FILE          = "memory.json"

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

PERSONALITIES = {
    "normal": {
        "name": "Звичайний", "emoji": "🤖",
        "prompt": (
            "Ти розумний і корисний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Будь точним, лаконічним і дружнім. Ніколи не вигадуй факти."
        )
    },
    "funny": {
        "name": "Жартівливий", "emoji": "😄",
        "prompt": (
            "Ти веселий і дотепний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Додавай гумор, жарти та смішні порівняння. Іноді використовуй emoji. "
            "Ніколи не вигадуй факти."
        )
    },
    "serious": {
        "name": "Серйозний", "emoji": "🎯",
        "prompt": (
            "Ти суворий і точний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Тільки факти і суть. Без жартів. Ніколи не вигадуй факти."
        )
    },
    "business": {
        "name": "Діловий", "emoji": "💼",
        "prompt": (
            "Ти професійний бізнес-асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Діловий стиль, чіткі структуровані відповіді. Ніколи не вигадуй факти."
        )
    }
}

# ── Ключові слова (локальна перевірка без AI) ─────────────────────────────────
IMAGE_KEYWORDS = [
    "створи фото", "згенеруй фото", "намалюй", "згенеруй зображення",
    "створи зображення", "зроби фото", "зроби картинку", "створи картинку",
    "зроби зображення", "згенеруй картинку", "покажи зображення",
    "generate image", "draw", "create image", "create photo", "make image"
]
REMIND_KEYWORDS = [
    "нагадай", "нагади", "нагадуй", "remind me", "set reminder",
    "нагадування", "постав нагадування", "нагадай мені"
]
SEARCH_KEYWORDS = [
    "пошукай", "знайди", "загугли", "що відбувається", "останні новини",
    "яка погода", "який курс", "поточн", "зараз відбувається",
    "search", "find", "look up", "актуальні новини"
]

# ── Стан ─────────────────────────────────────────────────────────────────────
chat_histories:     dict[int, list] = {}
user_personalities: dict[int, str]  = {}
tavily_client  = TavilyClient(api_key=TAVILY_API_KEY)
or_requests    = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190


# ════════════════════════════════════════════════════════════════════════════
# Пам'ять
# ════════════════════════════════════════════════════════════════════════════

def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_memory(memory: dict) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def get_user_memory(user_id: int) -> dict:
    return load_memory().get(str(user_id), {})

def update_user_memory(user_id: int, data: dict) -> None:
    memory = load_memory()
    uid = str(user_id)
    if uid not in memory:
        memory[uid] = {}
    memory[uid].update(data)
    save_memory(memory)

async def extract_and_save_memory(user_id: int, user_text: str, reply: str) -> None:
    try:
        result = await call_ai([{"role": "user", "content": (
            f"З цього діалогу витягни важливі факти про користувача (ім'я, вік, місто, вподобання).\n"
            f"Користувач: {user_text}\nБот: {reply}\n\n"
            "Відповідай ТІЛЬКИ у форматі JSON: {\"name\": \"...\", \"facts\": [\"факт1\"]} "
            "або {} якщо нічого важливого немає. Нічого більше не пиши."
        )}])
        result = result.strip().replace("```json", "").replace("```", "")
        data   = json.loads(result)
        if data:
            mem = get_user_memory(user_id)
            if "name" not in mem and data.get("name"):
                mem["name"] = data["name"]
            if data.get("facts"):
                mem["facts"] = list(set(mem.get("facts", [])) | set(data["facts"]))
            update_user_memory(user_id, mem)
    except Exception:
        pass

def get_system_prompt(user_id: int) -> dict:
    mode = user_personalities.get(user_id, "normal")
    p    = PERSONALITIES.get(mode, PERSONALITIES["normal"])
    return {"role": "system", "content": p["prompt"]}


# ════════════════════════════════════════════════════════════════════════════
# Утиліти
# ════════════════════════════════════════════════════════════════════════════

def detect_intent_local(text: str) -> str | None:
    """Швидка локальна перевірка без AI. Повертає намір або None."""
    t = text.lower()
    if any(kw in t for kw in IMAGE_KEYWORDS):
        return "image"
    if any(kw in t for kw in REMIND_KEYWORDS):
        return "reminder"
    if any(kw in t for kw in SEARCH_KEYWORDS):
        return "search"
    return None

async def detect_intent_ai(text: str) -> str:
    """AI-визначення наміру — викликається тільки якщо локальна перевірка не спрацювала."""
    result = await call_ai([{"role": "user", "content": (
        f"Визнач намір цього повідомлення: '{text}'\n"
        "Відповідай ТІЛЬКИ одним словом:\n"
        "- 'image' — намалювати/згенерувати зображення\n"
        "- 'reminder' — нагадати щось через певний час\n"
        "- 'search' — знайти актуальну інформацію в інтернеті\n"
        "- 'chat' — все інше\n"
        "Відповідь — ТІЛЬКИ одне слово."
    )}])
    return result.strip().lower().strip("'\"")

async def detect_intent(text: str) -> str:
    """Спочатку локальна перевірка, потім AI якщо потрібно."""
    local = detect_intent_local(text)
    if local:
        return local
    return await detect_intent_ai(text)

def clean_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'__(.*?)__',     r'\1', text)
    text = re.sub(r'_(.*?)_',       r'\1', text)
    text = re.sub(r'`(.*?)`',       r'\1', text)
    return text

def get_history(user_id: int) -> list:
    if user_id not in chat_histories:
        chat_histories[user_id] = [get_system_prompt(user_id)]
    return chat_histories[user_id]

def append_and_trim(user_id: int, role: str, content) -> None:
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_MESSAGES + 1:
        chat_histories[user_id] = [get_system_prompt(user_id)] + history[-MAX_HISTORY_MESSAGES:]


# ════════════════════════════════════════════════════════════════════════════
# AI
# ════════════════════════════════════════════════════════════════════════════

def get_text_provider() -> str:
    today = date.today()
    if or_requests["date"] != today:
        or_requests["date"]  = today
        or_requests["count"] = 0
    return "openrouter" if or_requests["count"] < OR_DAILY_LIMIT else "groq"

async def call_ai(messages: list) -> str:
    provider = get_text_provider()
    if provider == "openrouter":
        for model in [OPENROUTER_MODEL, OPENROUTER_MODEL_FALLBACK]:
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(OPENROUTER_URL, headers=headers, json={"model": model, "messages": messages})
            if r.status_code == 404:
                continue
            if r.status_code == 429:
                or_requests["count"] = OR_DAILY_LIMIT
                return await call_ai(messages)
            r.raise_for_status()
            or_requests["count"] += 1
            return r.json()["choices"][0]["message"]["content"]
        or_requests["count"] = OR_DAILY_LIMIT
        return await call_ai(messages)
    else:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(GROQ_URL, headers=headers, json={"model": GROQ_MODEL, "messages": messages})
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

async def call_vision(messages: list) -> str:
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(OPENROUTER_URL, headers=headers, json={"model": VISION_MODEL, "messages": messages})
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
# Генерація зображень
# ════════════════════════════════════════════════════════════════════════════

async def generate_image(prompt: str) -> bytes:
    url        = CF_IMAGE_URL.format(account_id=CF_ACCOUNT_ID)
    headers    = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    last_error = None
    for _ in range(3):
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(url, headers=headers, json={"prompt": prompt, "num_steps": 8})
                r.raise_for_status()
            if "image" in r.headers.get("content-type", ""):
                return r.content
            return base64.b64decode(r.json().get("result", {}).get("image", ""))
        except Exception as e:
            last_error = e
            await asyncio.sleep(5)
    raise last_error


# ════════════════════════════════════════════════════════════════════════════
# Відео
# ════════════════════════════════════════════════════════════════════════════

async def extract_video_audio(video_bytes: bytes) -> bytes:
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", "/tmp/input_video.mp4",
        "-vn", "-ar", "16000", "-ac", "1", "-f", "ogg", "/tmp/output_audio.ogg",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    with open("/tmp/output_audio.ogg", "rb") as f:
        return f.read()

async def extract_video_frames(video_bytes: bytes, max_frames: int = 3) -> list[bytes]:
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    frames = []
    for i, t in enumerate(["00:00:01", "00:00:05", "00:00:10"][:max_frames]):
        out_path = f"/tmp/frame_{i}.jpg"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", t, "-i", "/tmp/input_video.mp4",
            "-frames:v", "1", "-q:v", "2", out_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(out_path):
            with open(out_path, "rb") as f:
                frames.append(f.read())
    return frames

async def analyze_video(video_bytes: bytes, caption: str) -> str:
    results = []
    try:
        transcript = await transcribe_voice(await extract_video_audio(video_bytes))
        if transcript:
            results.append(f"🎤 Аудіо у відео:\n{transcript}")
    except Exception:
        pass
    try:
        frames = await extract_video_frames(video_bytes)
        if frames:
            descs = []
            for i, frame in enumerate(frames):
                b64  = base64.b64encode(frame).decode()
                desc = await call_vision([
                    SYSTEM_PROMPT,
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"Опиши що бачиш на кадрі {i+1}. Коротко, 1-2 речення."}
                    ]}
                ])
                descs.append(f"Кадр {i+1}: {desc}")
            results.append("🎬 Візуальний вміст:\n" + "\n".join(descs))
    except Exception:
        pass
    if not results:
        return "❌ Не вдалось проаналізувати відео."
    combined = "\n\n".join(results)
    summary  = await call_ai([
        SYSTEM_PROMPT,
        {"role": "user", "content": f"Запит: {caption}\n\nДані відео:\n{combined}\n\nВідповідай українською."}
    ])
    return f"{combined}\n\n📝 Підсумок:\n{summary}"


# ════════════════════════════════════════════════════════════════════════════
# Пошук та документи
# ════════════════════════════════════════════════════════════════════════════

async def search_web(query: str) -> str:
    try:
        results = await asyncio.to_thread(tavily_client.search, query=query, max_results=3)
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

def extract_excel_text(xlsx_bytes: bytes) -> str:
    try:
        wb     = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
        output = ""
        for sheet in wb.worksheets:
            output += f"=== Аркуш: {sheet.title} ===\n"
            for row in sheet.iter_rows(values_only=True):
                row_data = [str(c) if c is not None else "" for c in row]
                if any(row_data):
                    output += " | ".join(row_data) + "\n"
        return output.strip() or "Файл порожній."
    except Exception as e:
        return f"Помилка читання Excel: {e}"

def extract_word_text(docx_bytes: bytes) -> str:
    try:
        doc    = DocxDocument(io.BytesIO(docx_bytes))
        output = ""
        for para in doc.paragraphs:
            if para.text.strip():
                output += para.text + "\n"
        for table in doc.tables:
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                if any(row_data):
                    output += " | ".join(row_data) + "\n"
        return output.strip() or "Документ порожній."
    except Exception as e:
        return f"Помилка читання Word: {e}"


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
    reminders = load_reminders()
    reminders.append({"chat_id": chat_id, "text": text, "fire_at": fire_at.isoformat()})
    save_reminders(reminders)
    delay = max(0, (fire_at - datetime.now()).total_seconds())

    async def _run():
        await asyncio.sleep(delay)
        await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
        save_reminders([r for r in load_reminders() if not (
            r["chat_id"] == chat_id and r["text"] == text and r["fire_at"] == fire_at.isoformat()
        )])

    asyncio.create_task(_run())

async def restore_reminders(bot) -> None:
    reminders  = load_reminders()
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
# Спільна логіка
# ════════════════════════════════════════════════════════════════════════════

async def do_generate_image(update: Update, text: str, msg):
    t      = text.lower()
    prompt = text
    for kw in IMAGE_KEYWORDS:
        if kw in t:
            prompt = text[t.index(kw) + len(kw):].strip()
            break
    if not prompt:
        prompt = text
    try:
        translation = await call_ai([{
            "role": "user",
            "content": f"Translate this image description to English, return ONLY the translation: {prompt}"
        }])
        img_bytes = await generate_image(translation)
        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {e}")

async def do_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    now_str = datetime.now().strftime("%H:%M")
    parsed  = await call_ai([{"role": "user", "content": (
        f"Поточний час: {now_str}. Витягни час нагадування і текст: '{text}'. "
        "Відповідай ТІЛЬКИ у форматі JSON: {\"delay_minutes\": 5, \"text\": \"текст\"} "
        "де delay_minutes — через скільки хвилин нагадати. Нічого більше."
    )}])
    data        = json.loads(parsed.strip().replace("```json", "").replace("```", ""))
    delay_min   = int(data["delay_minutes"])
    remind_text = data["text"]
    await schedule_reminder(ctx.bot, update.effective_chat.id, remind_text,
                            datetime.now() + timedelta(minutes=delay_min))
    return delay_min, remind_text


# ════════════════════════════════════════════════════════════════════════════
# Обробники команд
# ════════════════════════════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я J.A.R.V.I.S. 🤖\n\n"
        "Можу:\n"
        "• Відповідати на запитання 💬\n"
        "• Аналізувати зображення та відео 🎬\n"
        "• Генерувати зображення 🎨\n"
        "• Розуміти голосові повідомлення 🎤\n"
        "• Шукати в інтернеті 🌐\n"
        "• Читати PDF, Excel, Word 📄\n"
        "• Нагадування 🔔\n"
        "• Працювати в групових чатах 👥\n\n"
        "Просто пиши або говори — я розумію природну мову!\n"
        "/help — всі команди"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команди:\n"
        "/image опис — генерація зображення\n"
        "/remind 30m текст — нагадування\n"
        "/search запит — пошук в інтернеті\n"
        "/mode — змінити особистість бота\n"
        "/memory — що бот пам'ятає про тебе\n"
        "/forget — очистити пам'ять про себе\n"
        "/status — статус бота\n"
        "/reset — очистити історію чату\n"
        "/help — ця довідка"
    )

async def mode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    current = user_personalities.get(user_id, "normal")
    if ctx.args:
        mode = ctx.args[0].lower()
        if mode not in PERSONALITIES:
            await update.message.reply_text(f"❌ Доступні режими: {', '.join(PERSONALITIES)}")
            return
        user_personalities[user_id] = mode
        update_user_memory(user_id, {"mode": mode})
        chat_histories[user_id] = [get_system_prompt(user_id)]
        p = PERSONALITIES[mode]
        await update.message.reply_text(f"{p['emoji']} Режим: {p['name']}")
    else:
        lines = ["🎭 Оберіть режим:\n"]
        for key, p in PERSONALITIES.items():
            lines.append(f"{p['emoji']} /mode {key} — {p['name']}{'  ✅' if key == current else ''}")
        await update.message.reply_text("\n".join(lines))

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = get_user_memory(update.message.from_user.id)
    if not mem:
        await update.message.reply_text("🧠 Я поки нічого не пам'ятаю про тебе.")
        return
    lines = ["🧠 Що я пам'ятаю:\n"]
    if mem.get("name"):
        lines.append(f"👤 Ім'я: {mem['name']}")
    if mem.get("facts"):
        lines.append("\n📌 Факти:")
        lines += [f"  • {f}" for f in mem["facts"]]
    await update.message.reply_text("\n".join(lines))

async def forget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    memory.pop(str(update.message.from_user.id), None)
    save_memory(memory)
    await update.message.reply_text("🗑️ Пам'ять очищено.")

async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args)
    if not prompt:
        await update.message.reply_text("Напиши що намалювати: /image захід сонця над морем")
        return
    msg = await update.message.reply_text("🎨 Генерую зображення...")
    try:
        translation = await call_ai([{
            "role": "user",
            "content": f"Translate to English, return ONLY translation: {prompt}"
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
            fire_at, when = now + timedelta(minutes=int(time_arg[:-1])), f"через {time_arg[:-1]} хв"
        elif time_arg.endswith("h"):
            fire_at, when = now + timedelta(hours=int(time_arg[:-1])), f"через {time_arg[:-1]} год"
        elif ":" in time_arg:
            t = datetime.strptime(time_arg, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            if t < now:
                t += timedelta(days=1)
            fire_at, when = t, f"о {time_arg}"
        else:
            await update.message.reply_text("❌ Формат: 30m, 2h або 14:30")
            return
    except ValueError:
        await update.message.reply_text("❌ Формат: 30m, 2h або 14:30")
        return
    await schedule_reminder(ctx.bot, update.effective_chat.id, reminder_text, fire_at)
    await update.message.reply_text(f"✅ Нагадаю {when}: {reminder_text}")

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Напиши що шукати: /search новини України")
        return
    msg     = await update.message.reply_text(f"🌐 Шукаю: {query}...")
    results = await search_web(query)
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

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_histories.pop(update.message.from_user.id, None)
    await update.message.reply_text("Історію чату очищено! 🔄")


# ════════════════════════════════════════════════════════════════════════════
# Обробники повідомлень
# ════════════════════════════════════════════════════════════════════════════

def _is_bot_addressed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    user_text = update.message.text or ""
    chat_type = update.message.chat.type
    if chat_type not in ("group", "supergroup"):
        return True, user_text
    bot_username = ctx.bot.username
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

    intent = await detect_intent(user_text)

    if intent == "image":
        msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
        await do_generate_image(update, user_text, msg)
        return

    if intent == "reminder":
        try:
            delay_min, remind_text = await do_reminder(update, ctx, user_text)
            await update.message.reply_text(f"✅ Нагадаю через {delay_min} хв: {remind_text}")
        except Exception as e:
            await update.message.reply_text(f"Не вдалось встановити нагадування: {e}")
        return

    if intent == "search":
        msg     = await update.message.reply_text("🌐 Шукаю в інтернеті...")
        results = await search_web(user_text)
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
        asyncio.create_task(extract_and_save_memory(user_id, user_text, reply))
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")
    try:
        photo   = update.message.photo[-1]
        file    = await ctx.bot.get_file(photo.file_id)
        img_b64 = base64.b64encode(await file.download_as_bytearray()).decode()
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською."
        reply   = await call_vision([
            SYSTEM_PROMPT,
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": caption}
            ]}
        ])
        await msg.edit_text(clean_markdown(reply))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {e}")

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎬 Аналізую відео (аудіо + кадри)...")
    try:
        video   = update.message.video or update.message.video_note
        caption = update.message.caption or "Що відбувається у цьому відео? Опиши детально."
        if video.file_size > 20 * 1024 * 1024:
            await msg.edit_text("❌ Відео занадто велике. Максимум 20MB.")
            return
        video_bytes = bytes(await (await ctx.bot.get_file(video.file_id)).download_as_bytearray())
        result      = await analyze_video(video_bytes, caption)
        await msg.edit_text(clean_markdown(result))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі відео: {e}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc     = update.message.document
    fname   = doc.file_name.lower()
    user_id = update.message.from_user.id
    caption = update.message.caption or "Стисло підсумуй цей документ українською."

    if fname.endswith(".pdf"):
        msg, text = await update.message.reply_text("📄 Читаю PDF..."), None
        text = extract_pdf_text(bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray()))
    elif fname.endswith((".xlsx", ".xls")):
        msg, text = await update.message.reply_text("📊 Читаю Excel..."), None
        text = extract_excel_text(bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray()))
    elif fname.endswith((".docx", ".doc")):
        msg, text = await update.message.reply_text("📝 Читаю Word..."), None
        text = extract_word_text(bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray()))
    else:
        await update.message.reply_text("📄 Підтримуються: PDF, Excel (.xlsx), Word (.docx)")
        return

    try:
        if not text or text.startswith("Помилка"):
            await msg.edit_text(f"❌ {text}")
            return
        text_preview = text[:4000] + ("..." if len(text) > 4000 else "")
        append_and_trim(user_id, "user", f"{caption}\n\nВміст документу:\n{text_preview}")
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await msg.edit_text(f"📄 {doc.file_name}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці документу: {e}")

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
        intent  = await detect_intent(text)

        if intent == "image":
            await msg.edit_text(f"🎤 Ти сказав: {text}\n\n🎨 Генерую зображення...")
            await do_generate_image(update, text, msg)
            return
        if intent == "reminder":
            try:
                delay_min, remind_text = await do_reminder(update, ctx, text)
                await msg.edit_text(f"🎤 Ти сказав: {text}\n\n✅ Нагадаю через {delay_min} хв: {remind_text}")
            except Exception as e:
                await msg.edit_text(f"Не вдалось встановити нагадування: {e}")
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
    memory = load_memory()
    for uid, data in memory.items():
        if data.get("mode"):
            user_personalities[int(uid)] = data["mode"]

if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("mode",    mode_cmd))
    app.add_handler(CommandHandler("memory",  memory_cmd))
    app.add_handler(CommandHandler("forget",  forget_cmd))
    app.add_handler(CommandHandler("reset",   reset))
    app.add_handler(CommandHandler("search",  handle_search))
    app.add_handler(CommandHandler("status",  handle_status))
    app.add_handler(CommandHandler("remind",  handle_remind))
    app.add_handler(CommandHandler("image",   handle_image))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(
        filters.Document.PDF |
        filters.Document.MimeType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") |
        filters.Document.MimeType("application/vnd.openxmlformats-officedocument.wordprocessingml.document") |
        filters.Document.MimeType("application/msword") |
        filters.Document.MimeType("application/vnd.ms-excel"),
        handle_document
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
