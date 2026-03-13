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
NEWS_API_KEY       = os.environ.get("NEWS_API_KEY")

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
TASKS_FILE           = "tasks.json"

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

# ── Ключові слова ─────────────────────────────────────────────────────────────
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
    "пошукай", "знайди в інтернеті", "загугли", "що відбувається",
    "останні новини", "актуальні новини", "яка погода", "який курс",
    "де купити", "де придбати", "де замовити", "де знайти", "де продається",
    "скільки коштує", "скільки вартує", "яка ціна", "яка вартість",
    "search", "look up"
]
TRANSLATE_KEYWORDS = [
    "перекладай", "переклади", "перекласти", "translate", "як буде",
    "як сказати", "як перекласти"
]
SUMMARIZE_KEYWORDS = [
    "підсумуй", "скороти", "стисло", "коротко перекажи", "що в статті",
    "summarize", "зроби підсумок", "перескажи коротко"
]
GENERATE_KEYWORDS = [
    "напиши резюме", "створи резюме", "напиши лист", "створи лист",
    "напиши пост", "створи пост", "напиши оголошення", "створи оголошення",
    "напиши текст для", "згенеруй текст"
]
RECIPE_KEYWORDS = [
    "що приготувати", "що зробити з", "рецепт з", "рецепти з",
    "є такі продукти", "є такі інгредієнти", "що можна приготувати",
    "що приготувати з", "є дома"
]
NEWS_KEYWORDS = [
    "новини про", "новини щодо", "що нового про", "останні новини про",
    "news about", "що відбувається з", "новини на тему"
]
TASK_KEYWORDS = [
    "додай задачу", "додай до списку", "запам'ятай задачу", "нова задача",
    "видали задачу", "видалити задачу", "покажи задачі", "мої задачі",
    "список задач", "виконано", "задачу виконано"
]

# ── Стан ─────────────────────────────────────────────────────────────────────
chat_histories:     dict[int, list] = {}
user_personalities: dict[int, str]  = {}
# Короткочасна пам'ять: останній контекст (фото/відео/документ) для кожного юзера
last_context:       dict[int, dict] = {}  # {user_id: {"type": "photo"|"video"|"doc", "description": "..."}}
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

def get_gender(user_id: int) -> str | None:
    return get_user_memory(user_id).get("gender")

def gender_suffix(user_id: int) -> str:
    g = get_gender(user_id)
    if g == "male":
        return " Звертайся до користувача як до чоловіка (він, йому, казав, зробив тощо)."
    if g == "female":
        return " Звертайся до користувача як до жінки (вона, їй, казала, зробила тощо)."
    return ""

def get_system_prompt(user_id: int) -> dict:
    mode = user_personalities.get(user_id, "normal")
    p    = PERSONALITIES.get(mode, PERSONALITIES["normal"])
    return {"role": "system", "content": p["prompt"] + gender_suffix(user_id)}


# ════════════════════════════════════════════════════════════════════════════
# Список задач
# ════════════════════════════════════════════════════════════════════════════

def load_tasks() -> dict:
    if not os.path.exists(TASKS_FILE):
        return {}
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_tasks(tasks: dict) -> None:
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def get_user_tasks(user_id: int) -> list:
    return load_tasks().get(str(user_id), [])

def set_user_tasks(user_id: int, tasks: list) -> None:
    all_tasks = load_tasks()
    all_tasks[str(user_id)] = tasks
    save_tasks(all_tasks)

async def handle_tasks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    args    = ctx.args

    if not args:
        # Показати список
        tasks = get_user_tasks(user_id)
        if not tasks:
            await update.message.reply_text("📋 Список задач порожній.\n\nДодати: /tasks add Назва задачі")
            return
        lines = ["📋 Твої задачі:\n"]
        for i, t in enumerate(tasks, 1):
            done = "✅" if t.get("done") else "⬜"
            lines.append(f"{done} {i}. {t['text']}")
        lines.append("\n/tasks add текст — додати\n/tasks done N — виконано\n/tasks del N — видалити\n/tasks clear — очистити")
        await update.message.reply_text("\n".join(lines))
        return

    cmd = args[0].lower()

    if cmd == "add":
        text = " ".join(args[1:])
        if not text:
            await update.message.reply_text("Вкажи текст задачі: /tasks add Купити молоко")
            return
        tasks = get_user_tasks(user_id)
        tasks.append({"text": text, "done": False})
        set_user_tasks(user_id, tasks)
        await update.message.reply_text(f"✅ Задачу додано: {text}")

    elif cmd == "done":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Вкажи номер: /tasks done 1")
            return
        n     = int(args[1]) - 1
        tasks = get_user_tasks(user_id)
        if 0 <= n < len(tasks):
            tasks[n]["done"] = True
            set_user_tasks(user_id, tasks)
            await update.message.reply_text(f"✅ Виконано: {tasks[n]['text']}")
        else:
            await update.message.reply_text("❌ Невірний номер задачі.")

    elif cmd == "del":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Вкажи номер: /tasks del 1")
            return
        n     = int(args[1]) - 1
        tasks = get_user_tasks(user_id)
        if 0 <= n < len(tasks):
            removed = tasks.pop(n)
            set_user_tasks(user_id, tasks)
            await update.message.reply_text(f"🗑️ Видалено: {removed['text']}")
        else:
            await update.message.reply_text("❌ Невірний номер задачі.")

    elif cmd == "clear":
        set_user_tasks(user_id, [])
        await update.message.reply_text("🗑️ Список задач очищено.")

    else:
        await update.message.reply_text("❓ Невідома команда. Використай: add / done / del / clear")


# ════════════════════════════════════════════════════════════════════════════
# Утиліти
# ════════════════════════════════════════════════════════════════════════════

def detect_intent_local(text: str) -> str | None:
    t = text.lower()
    if any(kw in t for kw in IMAGE_KEYWORDS):    return "image"
    if any(kw in t for kw in REMIND_KEYWORDS):   return "reminder"
    if any(kw in t for kw in NEWS_KEYWORDS):     return "news"
    if any(kw in t for kw in TRANSLATE_KEYWORDS):return "translate"
    if any(kw in t for kw in RECIPE_KEYWORDS):   return "recipe"
    if any(kw in t for kw in GENERATE_KEYWORDS): return "generate"
    if any(kw in t for kw in TASK_KEYWORDS):     return "task"
    if any(kw in t for kw in SUMMARIZE_KEYWORDS):return "summarize"
    if any(kw in t for kw in SEARCH_KEYWORDS):   return "search"
    if re.search(r'https?://\S+', text):         return "summarize"
    return None

async def resolve_text_with_context(user_id: int, text: str) -> str:
    """Визначає чи повідомлення відноситься до попереднього контексту."""
    ctx = last_context.get(user_id)
    if not ctx:
        return text

    t = text.lower().strip()
    words = t.split()

    # 1. Однозначно НЕ контекст — довге самодостатнє повідомлення без натяків
    no_context_hints = ["розкажи про", "що таке ", "поясни ", "напиши про", "хто такий"]
    if len(words) > 12 and not any(h in t for h in no_context_hints):
        return text

    # 2. Швидка локальна перевірка — очевидні тригери
    strong_hints = [
        "це", "цей", "цю", "цього", "на фото", "з фото", "на зображенні",
        "де купити", "де придбати", "де замовити", "скільки коштує",
        "скільки вартує", "яка ціна", "яка вартість", "ціна", "вартість",
        "купити", "придбати", "замовити", "знайди", "пошукай",
        "що це", "розкажи більше", "докладніше", "ще про",
        "як використовувати", "рецепт з", "з відео", "у відео",
    ]
    if len(words) <= 10 or any(h in t for h in strong_hints):
        return (
            f"[Контекст попереднього повідомлення — {ctx['type']}: {ctx['description'][:500]}]\n\n"
            f"Запит користувача: {text}"
        )

    # 3. Сірa зона (10-12 слів, без очевидних тригерів) — питаємо AI
    try:
        check = await call_ai([{"role": "user", "content": (
            f"Контекст: {ctx['type']} — «{ctx['description'][:200]}»\n"
            f"Повідомлення: «{text}»\n"
            "Чи це повідомлення стосується контексту? Відповідай ТІЛЬКИ 'yes' або 'no'."
        )}])
        if "yes" in check.lower():
            return (
                f"[Контекст попереднього повідомлення — {ctx['type']}: {ctx['description'][:500]}]\n\n"
                f"Запит користувача: {text}"
            )
    except Exception:
        pass

    return text
    t = text.lower()
    if any(kw in t for kw in IMAGE_KEYWORDS):    return "image"
    if any(kw in t for kw in REMIND_KEYWORDS):   return "reminder"
    if any(kw in t for kw in NEWS_KEYWORDS):     return "news"
    if any(kw in t for kw in TRANSLATE_KEYWORDS):return "translate"
    if any(kw in t for kw in RECIPE_KEYWORDS):   return "recipe"
    if any(kw in t for kw in GENERATE_KEYWORDS): return "generate"
    if any(kw in t for kw in TASK_KEYWORDS):     return "task"
    if any(kw in t for kw in SUMMARIZE_KEYWORDS):return "summarize"
    if any(kw in t for kw in SEARCH_KEYWORDS):   return "search"
    # Посилання — одразу підсумовуємо
    if re.search(r'https?://\S+', text):         return "summarize"
    return None

async def detect_intent_ai(text: str) -> str:
    result = await call_ai([{"role": "user", "content": (
        f"Визнач намір цього повідомлення: '{text}'\n"
        "Відповідай ТІЛЬКИ одним словом:\n"
        "- 'image' — згенерувати зображення\n"
        "- 'reminder' — нагадати через час\n"
        "- 'news' — новини за темою\n"
        "- 'search' — знайти актуальну інформацію\n"
        "- 'translate' — перекласти текст\n"
        "- 'summarize' — підсумувати текст або статтю\n"
        "- 'generate' — написати резюме/лист/пост\n"
        "- 'recipe' — рецепт за інгредієнтами\n"
        "- 'task' — додати/видалити/переглянути задачі\n"
        "- 'chat' — все інше\n"
        "Відповідь — ТІЛЬКИ одне слово."
    )}])
    return result.strip().lower().strip("'\"")

async def detect_intent(text: str) -> str:
    # Прибираємо префікс контексту перед перевіркою ключових слів
    clean = text.split("Запит користувача:")[-1].strip() if "Запит користувача:" in text else text
    local = detect_intent_local(clean)
    return local if local else await detect_intent_ai(clean)

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
    # Спроба 1: Tavily
    if TAVILY_API_KEY:
        try:
            results = await asyncio.to_thread(tavily_client.search, query=query, max_results=3)
            output  = ""
            for item in results.get("results", []):
                output += f"{item['title']}\n{item['content'][:300]}\n{item['url']}\n\n"
            if output:
                return output
        except Exception as e:
            pass  # Переходимо до fallback

    # Спроба 2: DuckDuckGo через httpx (без API ключа)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            data    = r.json()
            output  = ""
            # AbstractText
            if data.get("AbstractText"):
                output += f"{data['AbstractText']}\n{data.get('AbstractURL','')}\n\n"
            # RelatedTopics
            for topic in data.get("RelatedTopics", [])[:3]:
                if isinstance(topic, dict) and topic.get("Text"):
                    output += f"{topic['Text']}\n{topic.get('FirstURL','')}\n\n"
            if output:
                return output
    except Exception:
        pass

    return ""  # Порожній рядок — AI відповість зі своїх знань

async def fetch_url_text(url: str) -> str:
    """Завантажує текст сторінки за посиланням."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        # Прибираємо HTML теги
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:6000]
    except Exception as e:
        return f"Помилка завантаження: {e}"

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
# Нові функції: переклад, підсумок, генерація, рецепт
# ════════════════════════════════════════════════════════════════════════════

async def do_translate(text: str) -> str:
    reply = await call_ai([{"role": "user", "content": (
        f"Визнач мову цього тексту і перекладай його. Якщо текст українською — перекладай на англійську. "
        f"Якщо іншою мовою — перекладай на українську. "
        f"Якщо у запиті вказана конкретна мова (наприклад 'переклади на іспанську') — використай її.\n\n"
        f"Текст: {text}\n\n"
        "Формат відповіді:\n🌐 Оригінал (мова): ...\n✅ Переклад: ..."
    )}])
    return reply

async def do_summarize(text: str) -> str:
    # Перевіряємо чи є посилання
    url_match = re.search(r'https?://\S+', text)
    if url_match:
        url      = url_match.group()
        msg_text = text.replace(url, "").strip()
        content  = await fetch_url_text(url)
        if content.startswith("Помилка"):
            return f"❌ Не вдалось завантажити статтю: {content}"
        prompt = (
            f"Підсумуй цю статтю українською мовою.\n"
            f"{'Додатковий запит: ' + msg_text if msg_text else ''}\n\n"
            f"Стаття ({url}):\n{content}\n\n"
            "Формат: 📌 Головна думка (1-2 речення)\n🔹 Ключові тези (3-5 пунктів)"
        )
    else:
        prompt = (
            f"Зроби стислий підсумок цього тексту українською мовою.\n\n"
            f"Текст: {text}\n\n"
            "Формат: 📌 Головна думка\n🔹 Ключові тези"
        )
    return await call_ai([{"role": "user", "content": prompt}])

async def do_generate(text: str) -> str:
    reply = await call_ai([
        SYSTEM_PROMPT,
        {"role": "user", "content": (
            f"Виконай це завдання з генерації тексту: {text}\n\n"
            "Пиши грамотно, структуровано, українською мовою. "
            "Якщо це резюме — додай всі стандартні розділи. "
            "Якщо лист — дотримуйся ділового або особистого стилю залежно від запиту. "
            "Якщо пост — зроби його живим і залучальним."
        )}
    ])
    return reply

async def do_recipe(text: str) -> str:
    reply = await call_ai([{"role": "user", "content": (
        f"Користувач має такі інгредієнти або продукти: {text}\n\n"
        "Запропонуй 2-3 рецепти які можна приготувати. "
        "Для кожного рецепту вкажи: назву, час приготування, короткий список кроків. "
        "Відповідай українською мовою. Будь практичним і конкретним."
    )}])
    return reply

async def do_task_nlp(update: Update, user_id: int, text: str) -> None:
    """Обробка задач через природну мову."""
    parsed = await call_ai([{"role": "user", "content": (
        f"З цього повідомлення визнач дію зі списком задач: '{text}'\n"
        "Відповідай ТІЛЬКИ у форматі JSON:\n"
        "{\"action\": \"add|done|del|list\", \"text\": \"текст задачі або null\", \"number\": null або номер}\n"
        "Нічого більше не пиши."
    )}])
    try:
        data   = json.loads(parsed.strip().replace("```json", "").replace("```", ""))
        action = data.get("action")
        tasks  = get_user_tasks(user_id)

        if action == "add":
            task_text = data.get("text", "")
            if task_text:
                tasks.append({"text": task_text, "done": False})
                set_user_tasks(user_id, tasks)
                await update.message.reply_text(f"✅ Задачу додано: {task_text}")
        elif action == "done":
            n = (data.get("number") or 1) - 1
            if 0 <= n < len(tasks):
                tasks[n]["done"] = True
                set_user_tasks(user_id, tasks)
                await update.message.reply_text(f"✅ Виконано: {tasks[n]['text']}")
        elif action == "del":
            n = (data.get("number") or 1) - 1
            if 0 <= n < len(tasks):
                removed = tasks.pop(n)
                set_user_tasks(user_id, tasks)
                await update.message.reply_text(f"🗑️ Видалено: {removed['text']}")
        elif action == "list":
            if not tasks:
                await update.message.reply_text("📋 Список задач порожній.")
                return
            lines = ["📋 Твої задачі:\n"]
            for i, t in enumerate(tasks, 1):
                lines.append(f"{'✅' if t.get('done') else '⬜'} {i}. {t['text']}")
            await update.message.reply_text("\n".join(lines))
    except Exception:
        await update.message.reply_text("❌ Не вдалось обробити задачу. Спробуй /tasks")


async def do_news(query: str) -> str:
    """Отримує новини за темою через NewsAPI і підсумовує їх."""
    if not NEWS_API_KEY:
        return "❌ NEWS_API_KEY не налаштовано."
    try:
        # Перекладаємо запит на англійську для кращих результатів
        query_en = await call_ai([{"role": "user", "content":
            f"Translate this news topic to English, return ONLY translation: {query}"}])
        url    = "https://newsapi.org/v2/everything"
        params = {
            "q":        query_en.strip(),
            "language": "uk",
            "sortBy":   "publishedAt",
            "pageSize": 5,
            "apiKey":   NEWS_API_KEY,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
        articles = r.json().get("articles", [])

        # Якщо українських немає — беремо англійські
        if not articles:
            params["language"] = "en"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
            articles = r.json().get("articles", [])

        if not articles:
            return f"📰 Новин за темою «{query}» не знайдено."

        lines = [f"📰 Новини за темою: {query}\n"]
        sources_text = ""
        for i, a in enumerate(articles[:5], 1):
            title       = a.get("title", "Без назви")
            source      = a.get("source", {}).get("name", "")
            published   = a.get("publishedAt", "")[:10]
            description = a.get("description") or ""
            url_a       = a.get("url", "")
            sources_text += f"{title}. {description}\n"
            lines.append(f"{i}. {title}\n   📅 {published} | {source}\n   🔗 {url_a}\n")

        # AI підсумок
        summary = await call_ai([{"role": "user", "content": (
            f"Ось заголовки новин за темою '{query}':\n{sources_text}\n\n"
            "Зроби короткий підсумок (2-3 речення) що відбувається. Відповідай українською."
        )}])
        lines.insert(1, f"💡 {summary}\n")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Помилка отримання новин: {e}"


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
# Визначення статі за голосом
# ════════════════════════════════════════════════════════════════════════════

async def detect_gender_from_voice(audio_bytes: bytes) -> str | None:
    try:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        files   = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
        data    = {"model": "whisper-large-v3", "language": "uk", "response_format": "verbose_json"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(GROQ_WHISPER_URL, headers=headers, files=files, data=data)
            r.raise_for_status()
        transcript_text = r.json().get("text", "")
        if not transcript_text:
            return None
        result = await call_ai([{"role": "user", "content": (
            f"На основі тексту голосового повідомлення визнач стать мовця.\n"
            f"Текст: '{transcript_text}'\n"
            "Звертай увагу на граматичні форми (казав/казала, зробив/зробила тощо).\n"
            "Відповідай ТІЛЬКИ одним словом: 'male', 'female' або 'unknown'."
        )}])
        gender = result.strip().lower()
        return gender if gender in ("male", "female") else None
    except Exception:
        return None


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
            "content": f"Translate to English, return ONLY translation: {prompt}"
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
        "Відповідай ТІЛЬКИ у форматі JSON: {\"delay_minutes\": 5, \"text\": \"текст\"} Нічого більше."
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
        "• Перекладати тексти 🌐\n"
        "• Підсумовувати статті за посиланням 📰\n"
        "• Генерувати резюме, листи, пости ✍️\n"
        "• Рецепти за інгредієнтами 🍳\n"
        "• Список задач 📋\n"
        "• Аналізувати зображення та відео 🎬\n"
        "• Генерувати зображення 🎨\n"
        "• Голосові повідомлення 🎤\n"
        "• Шукати в інтернеті 🔍\n"
        "• Читати PDF, Excel, Word 📄\n"
        "• Нагадування 🔔\n\n"
        "Просто пиши — я розумію природну мову!\n"
        "/help — всі команди"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команди:\n"
        "/image опис — генерація зображення\n"
        "/remind 30m текст — нагадування\n"
        "/news тема — новини за темою\n"
        "/search запит — пошук в інтернеті\n"
        "/translate текст — переклад\n"
        "/summarize посилання або текст — підсумок\n"
        "/generate резюме/лист/пост — генерація тексту\n"
        "/recipe інгредієнти — рецепти\n"
        "/tasks — список задач\n"
        "/mode — змінити стиль бота\n"
        "/memory — що бот пам'ятає про тебе\n"
        "/forget — очистити пам'ять\n"
        "/status — статус бота\n"
        "/reset — очистити історію чату\n"
        "/help — ця довідка\n\n"
        "💡 Або просто пиши природною мовою:\n"
        "«переклади це на англійську»\n"
        "«що приготувати з картоплі і яєць»\n"
        "«напиши пост про мій бізнес»"
    )

async def news_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Використання: /news тема\nНаприклад: /news штучний інтелект")
        return
    msg    = await update.message.reply_text(f"📰 Шукаю новини про «{query}»...")
    result = await do_news(query)
    await msg.edit_text(clean_markdown(result), disable_web_page_preview=True)

async def translate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /translate текст\nАбо просто: переклади [текст]")
        return
    msg    = await update.message.reply_text("🌐 Перекладаю...")
    result = await do_translate(text)
    await msg.edit_text(clean_markdown(result))

async def summarize_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /summarize https://... або /summarize текст")
        return
    msg    = await update.message.reply_text("📰 Опрацьовую...")
    result = await do_summarize(text)
    await msg.edit_text(clean_markdown(result))

async def generate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text(
            "Використання:\n"
            "/generate резюме для розробника Python\n"
            "/generate лист подяки клієнту\n"
            "/generate пост про новий продукт"
        )
        return
    msg    = await update.message.reply_text("✍️ Генерую текст...")
    result = await do_generate(text)
    await msg.edit_text(clean_markdown(result))

async def recipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /recipe картопля яйця цибуля")
        return
    msg    = await update.message.reply_text("🍳 Шукаю рецепти...")
    result = await do_recipe(text)
    await msg.edit_text(clean_markdown(result))

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
    if mem.get("gender"):
        g = "чоловік" if mem["gender"] == "male" else "жінка"
        lines.append(f"👤 Стать: {g}")
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
            "role": "user", "content": f"Translate to English, return ONLY translation: {prompt}"
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
    user_id  = update.message.from_user.id

    # Показуємо що бот друкує поки обробляє
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        enriched = await resolve_text_with_context(user_id, user_text)
        intent   = await detect_intent(enriched)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка обробки запиту: {e}")
        return

    if intent == "image":
        msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
        await do_generate_image(update, enriched, msg)
        return

    if intent == "reminder":
        try:
            delay_min, remind_text = await do_reminder(update, ctx, enriched)
            await update.message.reply_text(f"✅ Нагадаю через {delay_min} хв: {remind_text}")
        except Exception as e:
            await update.message.reply_text(f"Не вдалось встановити нагадування: {e}")
        return

    if intent == "translate":
        msg    = await update.message.reply_text("🌐 Перекладаю...")
        result = await do_translate(enriched)
        await msg.edit_text(clean_markdown(result))
        return

    if intent == "summarize":
        msg    = await update.message.reply_text("📰 Опрацьовую...")
        result = await do_summarize(enriched)
        await msg.edit_text(clean_markdown(result))
        return

    if intent == "generate":
        msg    = await update.message.reply_text("✍️ Генерую текст...")
        result = await do_generate(enriched)
        await msg.edit_text(clean_markdown(result))
        return

    if intent == "recipe":
        msg    = await update.message.reply_text("🍳 Шукаю рецепти...")
        result = await do_recipe(enriched)
        await msg.edit_text(clean_markdown(result))
        return

    if intent == "task":
        await do_task_nlp(update, user_id, enriched)
        return

    if intent == "news":
        msg    = await update.message.reply_text("📰 Шукаю новини...")
        result = await do_news(enriched)
        await msg.edit_text(clean_markdown(result), disable_web_page_preview=True)
        return

    if intent == "search":
        msg     = await update.message.reply_text("🌐 Шукаю в інтернеті...")
        results = await search_web(enriched)
        try:
            content = f"Запит: '{enriched}'\n\nРезультати пошуку:\n{results}\n\nДай корисну відповідь українською." \
                      if results else \
                      f"Запит: '{enriched}'\n\nВідповідай з власних знань українською. Якщо не знаєш точної інформації — скажи що краще перевірити на офіційних сайтах."
            reply = await call_ai([SYSTEM_PROMPT, {"role": "user", "content": content}])
            append_and_trim(user_id, "user", user_text)
            append_and_trim(user_id, "assistant", reply)
            await msg.edit_text(clean_markdown(reply))
        except Exception as e:
            await msg.edit_text(f"Помилка пошуку: {e}")
        return

    # Звичайна відповідь
    try:
        append_and_trim(user_id, "user", enriched)
        reply = await call_ai(chat_histories[user_id])
        append_and_trim(user_id, "assistant", reply)
        await update.message.reply_text(clean_markdown(reply))
        asyncio.create_task(extract_and_save_memory(user_id, user_text, reply))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка відповіді: {e}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Аналізую зображення...")
    user_id = update.message.from_user.id
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
        # Зберігаємо контекст ДО відповіді
        last_context[user_id] = {"type": "фото", "description": reply[:500]}
        append_and_trim(user_id, "user", f"[Фото] {caption}")
        append_and_trim(user_id, "assistant", reply)
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
        # Зберігаємо опис відео як контекст
        user_id = update.message.from_user.id
        last_context[user_id] = {"type": "відео", "description": result[:500]}
        append_and_trim(user_id, "user", f"[Відео] {caption}")
        append_and_trim(user_id, "assistant", result)
        await msg.edit_text(clean_markdown(result))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі відео: {e}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc     = update.message.document
    fname   = doc.file_name.lower()
    user_id = update.message.from_user.id
    caption = update.message.caption or "Стисло підсумуй цей документ українською."

    if fname.endswith(".pdf"):
        msg  = await update.message.reply_text("📄 Читаю PDF...")
        text = extract_pdf_text(bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray()))
    elif fname.endswith((".xlsx", ".xls")):
        msg  = await update.message.reply_text("📊 Читаю Excel...")
        text = extract_excel_text(bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray()))
    elif fname.endswith((".docx", ".doc")):
        msg  = await update.message.reply_text("📝 Читаю Word...")
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
        # Зберігаємо опис документу як контекст
        last_context[user_id] = {"type": "документ", "description": reply[:500]}
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

        # Визначаємо стать якщо ще не відомо
        if get_gender(user_id) is None:
            gender = await detect_gender_from_voice(audio_bytes)
            if gender:
                update_user_memory(user_id, {"gender": gender})
                if user_id in chat_histories and chat_histories[user_id]:
                    chat_histories[user_id][0] = get_system_prompt(user_id)

        intent = await detect_intent(text)

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
        if intent == "translate":
            result = await do_translate(text)
            await msg.edit_text(f"🎤 Ти сказав: {text}\n\n{clean_markdown(result)}")
            return
        if intent == "recipe":
            result = await do_recipe(text)
            await msg.edit_text(f"🎤 Ти сказав: {text}\n\n{clean_markdown(result)}")
            return
        if intent == "news":
            result = await do_news(text)
            await msg.edit_text(clean_markdown(result), disable_web_page_preview=True)
            return
        if intent == "task":
            await msg.edit_text(f"🎤 Ти сказав: {text}\n\n⏳ Обробляю задачу...")
            await do_task_nlp(update, user_id, text)
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
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CommandHandler("mode",      mode_cmd))
    app.add_handler(CommandHandler("memory",    memory_cmd))
    app.add_handler(CommandHandler("forget",    forget_cmd))
    app.add_handler(CommandHandler("reset",     reset))
    app.add_handler(CommandHandler("search",    handle_search))
    app.add_handler(CommandHandler("status",    handle_status))
    app.add_handler(CommandHandler("remind",    handle_remind))
    app.add_handler(CommandHandler("image",     handle_image))
    app.add_handler(CommandHandler("news",      news_cmd))
    app.add_handler(CommandHandler("translate", translate_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("generate",  generate_cmd))
    app.add_handler(CommandHandler("recipe",    recipe_cmd))
    app.add_handler(CommandHandler("tasks",     handle_tasks_cmd))
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
