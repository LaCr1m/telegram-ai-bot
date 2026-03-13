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
SUMMARY_THRESHOLD    = 16
REMINDERS_FILE       = "reminders.json"
MEMORY_FILE          = "memory.json"
TASKS_FILE           = "tasks.json"
HISTORY_FILE         = "history.json"
BLOCKED_DOMAINS      = {"olx.ua", "olx.com.ua"}

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
    "normal":   {"name": "Звичайний", "emoji": "🤖", "prompt": "Ти розумний і корисний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою. Будь точним, лаконічним і дружнім. Ніколи не вигадуй факти."},
    "funny":    {"name": "Жартівливий", "emoji": "😄", "prompt": "Ти веселий і дотепний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою. Додавай гумор, жарти та смішні порівняння. Іноді використовуй emoji. Ніколи не вигадуй факти."},
    "serious":  {"name": "Серйозний",  "emoji": "🎯", "prompt": "Ти суворий і точний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою. Тільки факти і суть. Без жартів. Ніколи не вигадуй факти."},
    "business": {"name": "Діловий",    "emoji": "💼", "prompt": "Ти професійний бізнес-асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою. Діловий стиль, чіткі структуровані відповіді. Ніколи не вигадуй факти."},
}

# ── Ключові слова ─────────────────────────────────────────────────────────────
IMAGE_KEYWORDS     = ["створи фото","згенеруй фото","намалюй","згенеруй зображення","створи зображення","зроби фото","зроби картинку","створи картинку","зроби зображення","згенеруй картинку","покажи зображення","generate image","draw","create image","create photo","make image"]
REMIND_KEYWORDS    = ["нагадай","нагади","нагадуй","remind me","set reminder","нагадування","постав нагадування","нагадай мені"]
PRICE_KEYWORDS     = ["скільки коштує","скільки вартує","яка ціна","яка вартість","де купити дешевше","де найдешевше","порівняй ціни","ціна на","почім","по чім","знайди ціну","ціни на","купити дешево","де дешевше купити"]
SEARCH_KEYWORDS    = ["пошукай","знайди в інтернеті","загугли","що відбувається","останні новини","актуальні новини","яка погода","який курс","де купити","де придбати","де замовити","де знайти","де продається","search","look up"]
TRANSLATE_KEYWORDS = ["перекладай","переклади","перекласти","translate","як буде","як сказати","як перекласти"]
SUMMARIZE_KEYWORDS = ["підсумуй","скороти","стисло","коротко перекажи","що в статті","summarize","зроби підсумок","перескажи коротко"]
GENERATE_KEYWORDS  = ["напиши резюме","створи резюме","напиши лист","створи лист","напиши пост","створи пост","напиши оголошення","створи оголошення","напиши текст для","згенеруй текст"]
RECIPE_KEYWORDS    = ["що приготувати","що зробити з","рецепт з","рецепти з","є такі продукти","є такі інгредієнти","що можна приготувати","що приготувати з","є дома"]
NEWS_KEYWORDS      = ["новини про","новини щодо","що нового про","останні новини про","news about","що відбувається з","новини на тему"]
TASK_KEYWORDS      = ["додай задачу","додай до списку","запам'ятай задачу","нова задача","видали задачу","видалити задачу","покажи задачі","мої задачі","список задач","виконано","задачу виконано"]

# ── Стан ─────────────────────────────────────────────────────────────────────
chat_histories:     dict[int, list] = {}
user_personalities: dict[int, str]  = {}
last_context:       dict[int, dict] = {}
or_requests = {"count": 0, "date": date.today()}
OR_DAILY_LIMIT = 190
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ════════════════════════════════════════════════════════════════════════════
# Утиліти — JSON файли
# ════════════════════════════════════════════════════════════════════════════

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════════════════
# Пам'ять
# ════════════════════════════════════════════════════════════════════════════

def get_user_memory(user_id: int) -> dict:
    return _load_json(MEMORY_FILE, {}).get(str(user_id), {})

def update_user_memory(user_id: int, data: dict) -> None:
    memory = _load_json(MEMORY_FILE, {})
    memory.setdefault(str(user_id), {}).update(data)
    _save_json(MEMORY_FILE, memory)

async def extract_and_save_memory(user_id: int, user_text: str, reply: str) -> None:
    try:
        result = await call_ai([{"role": "user", "content": (
            f"З цього діалогу витягни важливі факти про користувача (ім'я, вік, місто, вподобання).\n"
            f"Користувач: {user_text}\nБот: {reply}\n\n"
            "Відповідай ТІЛЬКИ у форматі JSON: {\"name\": \"...\", \"facts\": [\"факт1\"]} "
            "або {} якщо нічого важливого немає. Нічого більше не пиши."
        )}])
        data = json.loads(result.strip().replace("```json", "").replace("```", ""))
        if not data:
            return
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
    if g == "male":   return " Звертайся до користувача як до чоловіка (він, йому, казав, зробив тощо)."
    if g == "female": return " Звертайся до користувача як до жінки (вона, їй, казала, зробила тощо)."
    return ""

def get_system_prompt(user_id: int) -> dict:
    mode = user_personalities.get(user_id, "normal")
    p    = PERSONALITIES.get(mode, PERSONALITIES["normal"])
    return {"role": "system", "content": p["prompt"] + gender_suffix(user_id)}


# ════════════════════════════════════════════════════════════════════════════
# Персистентна історія + стиснення
# ════════════════════════════════════════════════════════════════════════════

def save_histories() -> None:
    to_save = {}
    for uid, history in chat_histories.items():
        msgs = [m for m in history if m.get("role") != "system"]
        if msgs:
            to_save[str(uid)] = msgs[-MAX_HISTORY_MESSAGES:]
    _save_json(HISTORY_FILE, to_save)

def restore_histories() -> None:
    data = _load_json(HISTORY_FILE, {})
    for uid_str, msgs in data.items():
        uid = int(uid_str)
        chat_histories[uid] = [get_system_prompt(uid)] + msgs

def get_history(user_id: int) -> list:
    if user_id not in chat_histories:
        chat_histories[user_id] = [get_system_prompt(user_id)]
    return chat_histories[user_id]

async def _compress_history(user_id: int) -> None:
    history = chat_histories.get(user_id, [])
    msgs    = [m for m in history if m.get("role") != "system"]
    if len(msgs) < 8:
        return
    old   = msgs[:-6]
    fresh = msgs[-6:]
    old_text = "\n".join(
        f"{'Користувач' if m['role'] == 'user' else 'Бот'}: "
        f"{m['content'] if isinstance(m['content'], str) else '[медіа]'}"
        for m in old
    )
    try:
        summary = await call_ai([{"role": "user", "content": (
            f"Стисни цей діалог у короткий підсумок (3-5 речень). "
            f"Збережи ключові факти, теми, імена, числа. Відповідай українською.\n\n{old_text}"
        )}])
    except Exception:
        return

    async def _save_topic():
        try:
            topic = await call_ai([{"role": "user", "content": (
                f"Визнач головну тему цього діалогу одним реченням до 10 слів. "
                f"Відповідай ТІЛЬКИ темою.\n\n{old_text[:1000]}"
            )}])
            topic = topic.strip()
            if topic:
                mem    = get_user_memory(user_id)
                topics = ([topic] + mem.get("topics", []))[:10]
                update_user_memory(user_id, {"topics": topics})
        except Exception:
            pass
    asyncio.create_task(_save_topic())

    chat_histories[user_id] = (
        [get_system_prompt(user_id),
         {"role": "assistant", "content": f"[Підсумок попереднього діалогу]: {summary}"}]
        + fresh
    )

async def append_and_trim(user_id: int, role: str, content) -> None:
    get_history(user_id).append({"role": role, "content": content})
    msgs = [m for m in chat_histories[user_id] if m.get("role") != "system"]
    if len(msgs) >= SUMMARY_THRESHOLD:
        await _compress_history(user_id)
    elif len(chat_histories[user_id]) > MAX_HISTORY_MESSAGES + 1:
        chat_histories[user_id] = (
            [get_system_prompt(user_id)]
            + chat_histories[user_id][-MAX_HISTORY_MESSAGES:]
        )
    save_histories()


# ════════════════════════════════════════════════════════════════════════════
# Список задач
# ════════════════════════════════════════════════════════════════════════════

def get_user_tasks(user_id: int) -> list:
    return _load_json(TASKS_FILE, {}).get(str(user_id), [])

def set_user_tasks(user_id: int, tasks: list) -> None:
    all_tasks = _load_json(TASKS_FILE, {})
    all_tasks[str(user_id)] = tasks
    _save_json(TASKS_FILE, all_tasks)

async def handle_tasks_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    args    = ctx.args or []
    tasks   = get_user_tasks(user_id)
    if not args:
        if not tasks:
            await update.message.reply_text("📋 Список задач порожній.\n\nДодати: /tasks add Назва задачі")
            return
        lines = ["📋 Твої задачі:\n"]
        for i, t in enumerate(tasks, 1):
            lines.append(f"{'✅' if t.get('done') else '⬜'} {i}. {t['text']}")
        lines.append("\n/tasks add текст — додати\n/tasks done N — виконано\n/tasks del N — видалити\n/tasks clear — очистити")
        await update.message.reply_text("\n".join(lines))
        return
    cmd = args[0].lower()
    if cmd == "add":
        text = " ".join(args[1:])
        if not text:
            await update.message.reply_text("Вкажи текст задачі: /tasks add Купити молоко")
            return
        tasks.append({"text": text, "done": False})
        set_user_tasks(user_id, tasks)
        await update.message.reply_text(f"✅ Задачу додано: {text}")
    elif cmd in ("done", "del"):
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text(f"Вкажи номер: /tasks {cmd} 1")
            return
        n = int(args[1]) - 1
        if not (0 <= n < len(tasks)):
            await update.message.reply_text("❌ Невірний номер задачі.")
            return
        if cmd == "done":
            tasks[n]["done"] = True
            set_user_tasks(user_id, tasks)
            await update.message.reply_text(f"✅ Виконано: {tasks[n]['text']}")
        else:
            removed = tasks.pop(n)
            set_user_tasks(user_id, tasks)
            await update.message.reply_text(f"🗑️ Видалено: {removed['text']}")
    elif cmd == "clear":
        set_user_tasks(user_id, [])
        await update.message.reply_text("🗑️ Список задач очищено.")
    else:
        await update.message.reply_text("❓ Невідома команда. Використай: add / done / del / clear")


# ════════════════════════════════════════════════════════════════════════════
# Визначення інтенту
# ════════════════════════════════════════════════════════════════════════════

def detect_intent_local(text: str) -> str | None:
    t = text.lower()
    if any(kw in t for kw in IMAGE_KEYWORDS):     return "image"
    if any(kw in t for kw in REMIND_KEYWORDS):    return "reminder"
    if any(kw in t for kw in NEWS_KEYWORDS):      return "news"
    if any(kw in t for kw in PRICE_KEYWORDS):     return "price"
    if any(kw in t for kw in TRANSLATE_KEYWORDS): return "translate"
    if any(kw in t for kw in RECIPE_KEYWORDS):    return "recipe"
    if any(kw in t for kw in GENERATE_KEYWORDS):  return "generate"
    if any(kw in t for kw in TASK_KEYWORDS):      return "task"
    if any(kw in t for kw in SUMMARIZE_KEYWORDS): return "summarize"
    if any(kw in t for kw in SEARCH_KEYWORDS):    return "search"
    if re.search(r'https?://\S+', text):          return "summarize"
    return None

async def detect_intent_ai(text: str) -> str:
    result = await call_ai([{"role": "user", "content": (
        f"Визнач намір цього повідомлення: '{text}'\n"
        "Відповідай ТІЛЬКИ одним словом:\n"
        "- 'image'    — згенерувати зображення\n"
        "- 'reminder' — нагадати через час\n"
        "- 'news'     — новини за темою\n"
        "- 'price'    — пошук цін на товар\n"
        "- 'search'   — знайти актуальну інформацію\n"
        "- 'translate'— перекласти текст\n"
        "- 'summarize'— підсумувати текст або статтю\n"
        "- 'generate' — написати резюме/лист/пост\n"
        "- 'recipe'   — рецепт за інгредієнтами\n"
        "- 'task'     — додати/видалити/переглянути задачі\n"
        "- 'chat'     — все інше\n"
        "Відповідь — ТІЛЬКИ одне слово."
    )}])
    return result.strip().lower().strip("'\"")

async def detect_intent(text: str) -> str:
    clean = text.split("Запит користувача:")[-1].strip() if "Запит користувача:" in text else text
    return detect_intent_local(clean) or await detect_intent_ai(clean)

async def resolve_text_with_context(user_id: int, text: str) -> str:
    ctx = last_context.get(user_id)
    if not ctx:
        return text
    t     = text.lower().strip()
    words = t.split()
    if len(words) > 12:
        if not any(h in t for h in ["розкажи про", "що таке ", "поясни ", "напиши про", "хто такий"]):
            return text
    strong_hints = [
        "це","цей","цю","цього","на фото","з фото","на зображенні",
        "де купити","де придбати","де замовити","скільки коштує","скільки вартує",
        "яка ціна","яка вартість","ціна","вартість","купити","придбати","замовити",
        "знайди","пошукай","що це","розкажи більше","докладніше","ще про",
        "як використовувати","рецепт з","з відео","у відео",
        "цей магазин","цей сайт","ця компанія","цей товар","цей продукт",
        "що він продає","що вона продає","що там є","що там продають",
        "яка адреса","який графік","як туди","контакти","телефон магазину",
        "що ще","що інше","а ще","і ще","також розкажи",
        "щоб купити","хочу купити","можна купити","як купити",
        "де продається","чи є в продажі","є в наявності",
    ]
    if len(words) <= 10 or any(h in t for h in strong_hints):
        return (
            f"[Контекст попереднього повідомлення — {ctx['type']}: {ctx['description'][:500]}]\n\n"
            f"Запит користувача: {text}"
        )
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

def clean_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'__(.*?)__',     r'\1', text)
    text = re.sub(r'_(.*?)_',       r'\1', text)
    text = re.sub(r'`(.*?)`',       r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'\1: \2', text)
    return text

def _set_ctx(user_id: int, user_text: str, reply: str) -> None:
    last_context[user_id] = {
        "type": "текст",
        "description": f"Запит: {user_text}\nВідповідь: {reply[:400]}"
    }


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
    url     = CF_IMAGE_URL.format(account_id=CF_ACCOUNT_ID)
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
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
                desc = await call_vision([SYSTEM_PROMPT, {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": f"Опиши що бачиш на кадрі {i+1}. Коротко, 1-2 речення."}
                ]}])
                descs.append(f"Кадр {i+1}: {desc}")
            results.append("🎬 Візуальний вміст:\n" + "\n".join(descs))
    except Exception:
        pass
    if not results:
        return "❌ Не вдалось проаналізувати відео."
    combined = "\n\n".join(results)
    summary  = await call_ai([SYSTEM_PROMPT, {"role": "user", "content": f"Запит: {caption}\n\nДані відео:\n{combined}\n\nВідповідай українською."}])
    return f"{combined}\n\n📝 Підсумок:\n{summary}"


# ════════════════════════════════════════════════════════════════════════════
# Пошук та документи
# ════════════════════════════════════════════════════════════════════════════

def _filter_results(items: list[dict]) -> list[dict]:
    return [i for i in items if not any(bd in i.get("url", "") for bd in BLOCKED_DOMAINS)]

def _parse_prices(text: str) -> list[int]:
    raw = re.findall(r'[\s>](\d[\d\s\xa0]{1,7})\s*(?:грн|₴)', text)
    prices = []
    for p in raw:
        try:
            val = int(re.sub(r'[\s\xa0]', '', p))
            if 500 < val < 500_000:
                prices.append(val)
        except ValueError:
            pass
    return prices

async def search_web(query: str) -> str:
    if TAVILY_API_KEY:
        try:
            results = await asyncio.to_thread(
                lambda: TavilyClient(api_key=TAVILY_API_KEY).search(query=query, max_results=7)
            )
            filtered = _filter_results(results.get("results", []))
            output   = "".join(
                f"{i['title']}\n{i['content'][:400]}\n{i['url']}\n\n"
                for i in filtered
            )
            if output:
                return output
        except Exception as e:
            print(f"[Tavily error]: {e}")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=_HEADERS)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        links    = re.findall(r'class="result__url"[^>]*>(.*?)</span>', r.text, re.DOTALL)
        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        output   = ""
        for i in range(min(4, len(snippets))):
            t = re.sub(r'<[^>]+>', '', titles[i]).strip()   if i < len(titles)   else ""
            s = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            l = links[i].strip()                            if i < len(links)    else ""
            if s and not any(bd in l for bd in BLOCKED_DOMAINS):
                output += f"{t}\n{s}\n{l}\n\n"
        if output:
            return output
    except Exception as e:
        print(f"[DuckDuckGo error]: {e}")
    return ""

async def fetch_url_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers=_HEADERS)
            r.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', r.text)
        return re.sub(r'\s+', ' ', text).strip()[:6000]
    except Exception as e:
        return f"Помилка завантаження: {e}"

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "".join(p.extract_text() or "" for p in reader.pages).strip()
    except Exception as e:
        return f"Помилка читання PDF: {e}"

def extract_excel_text(xlsx_bytes: bytes) -> str:
    try:
        wb  = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
        out = ""
        for sheet in wb.worksheets:
            out += f"=== Аркуш: {sheet.title} ===\n"
            for row in sheet.iter_rows(values_only=True):
                row_data = [str(c) if c is not None else "" for c in row]
                if any(row_data):
                    out += " | ".join(row_data) + "\n"
        return out.strip() or "Файл порожній."
    except Exception as e:
        return f"Помилка читання Excel: {e}"

def extract_word_text(docx_bytes: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(docx_bytes))
        out = ""
        for para in doc.paragraphs:
            if para.text.strip():
                out += para.text + "\n"
        for table in doc.tables:
            for row in table.rows:
                row_data = [c.text.strip() for c in row.cells]
                if any(row_data):
                    out += " | ".join(row_data) + "\n"
        return out.strip() or "Документ порожній."
    except Exception as e:
        return f"Помилка читання Word: {e}"


# ════════════════════════════════════════════════════════════════════════════
# Функції обробки інтентів
# ════════════════════════════════════════════════════════════════════════════

async def do_translate(text: str) -> str:
    return await call_ai([{"role": "user", "content": (
        f"Визнач мову цього тексту і перекладай його. Якщо текст українською — перекладай на англійську. "
        f"Якщо іншою мовою — перекладай на українську. "
        f"Якщо у запиті вказана конкретна мова — використай її.\n\n"
        f"Текст: {text}\n\nФормат:\n🌐 Оригінал (мова): ...\n✅ Переклад: ..."
    )}])

async def do_summarize(text: str) -> str:
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
            f"Текст: {text}\n\nФормат: 📌 Головна думка\n🔹 Ключові тези"
        )
    return await call_ai([{"role": "user", "content": prompt}])

async def do_generate(text: str) -> str:
    return await call_ai([SYSTEM_PROMPT, {"role": "user", "content": (
        f"Виконай це завдання з генерації тексту: {text}\n\n"
        "Пиши грамотно, структуровано, українською мовою. "
        "Якщо це резюме — додай всі стандартні розділи. "
        "Якщо лист — дотримуйся ділового або особистого стилю. "
        "Якщо пост — зроби його живим і залучальним."
    )}])

async def do_recipe(text: str) -> str:
    return await call_ai([{"role": "user", "content": (
        f"Користувач має такі інгредієнти: {text}\n\n"
        "Запропонуй 2-3 рецепти. Для кожного: назва, час приготування, короткі кроки. "
        "Відповідай українською. Будь практичним і конкретним."
    )}])

async def do_task_nlp(update: Update, user_id: int, text: str) -> None:
    parsed = await call_ai([{"role": "user", "content": (
        f"З цього повідомлення визнач дію зі списком задач: '{text}'\n"
        "Відповідай ТІЛЬКИ у форматі JSON:\n"
        "{\"action\": \"add|done|del|list\", \"text\": \"текст або null\", \"number\": null або номер}\n"
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
        elif action in ("done", "del"):
            n = (data.get("number") or 1) - 1
            if 0 <= n < len(tasks):
                if action == "done":
                    tasks[n]["done"] = True
                    set_user_tasks(user_id, tasks)
                    await update.message.reply_text(f"✅ Виконано: {tasks[n]['text']}")
                else:
                    removed = tasks.pop(n)
                    set_user_tasks(user_id, tasks)
                    await update.message.reply_text(f"🗑️ Видалено: {removed['text']}")
        elif action == "list":
            if not tasks:
                await update.message.reply_text("📋 Список задач порожній.")
                return
            lines = ["📋 Твої задачі:\n"] + [
                f"{'✅' if t.get('done') else '⬜'} {i}. {t['text']}"
                for i, t in enumerate(tasks, 1)
            ]
            await update.message.reply_text("\n".join(lines))
    except Exception:
        await update.message.reply_text("❌ Не вдалось обробити задачу. Спробуй /tasks")

async def do_news(query: str) -> str:
    clean_query = query.split("Запит користувача:")[-1].strip() if "Запит користувача:" in query else query
    if not NEWS_API_KEY:
        return "❌ NEWS_API_KEY не налаштовано."
    try:
        query_en = await call_ai([{"role": "user", "content": f"Translate this news topic to English, return ONLY translation: {clean_query}"}])
        params   = {"q": query_en.strip(), "language": "uk", "sortBy": "publishedAt", "pageSize": 5, "apiKey": NEWS_API_KEY}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://newsapi.org/v2/everything", params=params)
            r.raise_for_status()
        articles = r.json().get("articles", [])
        if not articles:
            params["language"] = "en"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://newsapi.org/v2/everything", params=params)
                r.raise_for_status()
            articles = r.json().get("articles", [])
        if not articles:
            return f"📰 Новин за темою «{clean_query}» не знайдено."
        lines        = [f"📰 Новини за темою: {clean_query}\n"]
        sources_text = ""
        for i, a in enumerate(articles[:5], 1):
            title       = a.get("title", "Без назви")
            source      = a.get("source", {}).get("name", "")
            published   = a.get("publishedAt", "")[:10]
            description = a.get("description") or ""
            sources_text += f"{title}. {description}\n"
            lines.append(f"{i}. {title}\n   📅 {published} | {source}\n   🔗 {a.get('url', '')}\n")
        summary = await call_ai([{"role": "user", "content": f"Ось заголовки новин за темою '{clean_query}':\n{sources_text}\n\nЗроби короткий підсумок (2-3 речення). Відповідай українською."}])
        lines.insert(1, f"💡 {summary}\n")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Помилка отримання новин: {e}"

async def do_price(query: str) -> str:
    aliases_hint = (
        "'5090' або 'відеокарта 5090' → 'GeForce RTX 5090'\n"
        "'4090' → 'GeForce RTX 4090', '4080' → 'GeForce RTX 4080'\n"
        "'4070' → 'GeForce RTX 4070', '4060' → 'GeForce RTX 4060'\n"
        "'3080' → 'GeForce RTX 3080', '3070' → 'GeForce RTX 3070'\n"
        "'iPhone 16' → 'Apple iPhone 16'\n"
        "'i9 14900' → 'Intel Core i9-14900'\n"
    )
    if "[Контекст попереднього повідомлення" in query:
        prompt = (
            f"{query}\n\n"
            f"Витягни з контексту та запиту ТОЧНУ комерційну назву товару (бренд + модель, наприклад 'GameSir Nova 2') і місто.\n"
            f"Якщо модель чітко вказана в контексті — використай саме її, не узагальнюй.\n"
            f"Розшифруй скорочення:\n{aliases_hint}"
            "Відповідай ТІЛЬКИ JSON: {\"product\": \"...\", \"city\": \"...\" або null}"
        )
    else:
        clean  = query.split("Запит користувача:")[-1].strip()
        prompt = (
            f"Витягни з цього запиту точну назву товару і місто.\n"
            f"Розшифруй скорочення:\n{aliases_hint}"
            f"Запит: '{clean}'\n"
            "Відповідай ТІЛЬКИ JSON: {\"product\": \"...\", \"city\": \"...\" або null}"
        )
    try:
        raw     = (await call_ai([{"role": "user", "content": prompt}])).strip()
        raw     = raw.replace("```json", "").replace("```", "")
        parsed  = json.loads(raw)
        product = parsed.get("product", "").strip()
        city    = (parsed.get("city") or "").strip()
        city    = "" if city.lower() in ("null", "none", "") else city
    except Exception:
        clean   = query.split("Запит користувача:")[-1].strip() if "Запит користувача:" in query else query
        product = clean
        city    = ""

    if re.fullmatch(r'\d{4}', product.strip()):
        product = f"GeForce RTX {product.strip()}"
    elif re.search(r'(?:відеокарта|видеокарта)\s+(\d{4})', product.lower()):
        num     = re.search(r'\d{4}', product).group()
        product = f"GeForce RTX {num}"

    if not product:
        return "❌ Не вдалось визначити назву товару."

    encoded  = product.replace(" ", "+")
    city_str = city or "Україна"
    links    = (
        f"• https://hotline.ua/search/?q={encoded}\n"
        f"• https://price.ua/search/?text={encoded}\n"
        f"• https://rozetka.com.ua/ua/search/?text={encoded}\n"
        f"• https://comfy.ua/ua/search/?q={encoded}"
    )

    # ── Ключові слова для фільтрації нерелевантних результатів ─────────────
    product_keywords = [w.lower() for w in re.split(r'\s+', product) if len(w) > 2]

    def _is_relevant(title: str) -> bool:
        t = title.lower()
        return all(kw in t for kw in product_keywords)

    # ── Спроба 1: Tavily advanced ─────────────────────────────────────────
    if TAVILY_API_KEY:
        try:
            tavily_query = (
                f'"{product}" ціна купити {city_str} '
                "site:hotline.ua OR site:price.ua OR site:rozetka.com.ua OR site:comfy.ua"
            )
            raw_results = await asyncio.to_thread(
                lambda: TavilyClient(api_key=TAVILY_API_KEY).search(
                    query=tavily_query, max_results=8, search_depth="advanced"
                )
            )
            items = _filter_results(raw_results.get("results", []))
            found_prices = []
            for item in items:
                title   = item.get("title", "")
                content = item.get("content", "")
                url     = item.get("url", "")
                domain  = next((d for d in ("hotline.ua", "price.ua", "rozetka.com.ua", "comfy.ua") if d in url), "")

                # Пропускаємо сторінки категорій (містять змішані товари)
                # Rozetka: /videocards/c80087/ — це категорія, не конкретний товар
                is_category_page = bool(re.search(r'/c\d{3,}/', url))
                if is_category_page:
                    print(f"[Price filter] Пропущено сторінку категорії: {url[:80]}")
                    continue

                # Пропускаємо нерелевантні результати
                if not _is_relevant(title) and not _is_relevant(content[:200]):
                    print(f"[Price filter] Пропущено нерелевантний: {title[:60]}")
                    continue

                prices = _parse_prices(content + " " + title)
                if prices and domain:
                    found_prices.append({
                        "site":  domain,
                        "price": min(prices),
                        "url":   url,
                        "title": title[:60],
                    })

            if found_prices:
                found_prices.sort(key=lambda x: x["price"])
                city_note = f" у {city}" if city else ""
                lines     = [f"💰 Ціни на: {product}{city_note}\n"]
                seen: set[str] = set()
                for fp in found_prices[:6]:
                    marker = " 🏆" if not seen else ""
                    seen.add(fp["site"])
                    lines.append(
                        (
                            f"• {fp['site']}: {fp['price']:,} грн{marker}\n"
                            f"  {fp['title']}\n"
                            f"  🔗 {fp['url']}"
                        ).replace(",", " ")
                    )
                lines.append(
                    f"\n💡 Найдешевше: {found_prices[0]['price']:,} грн на {found_prices[0]['site']}"
                    .replace(",", " ")
                )
                if city:
                    lines.append(f"📍 Наявність у {city} — уточнюй безпосередньо в магазині.")
                return "\n".join(lines)
        except Exception as e:
            print(f"[Tavily price error]: {e}")

    # ── Спроба 2: прямий парсинг HTML ────────────────────────────────────
    sites = {
        "Hotline":  f"https://hotline.ua/search/?q={encoded}",
        "Price.ua": f"https://price.ua/search/?text={encoded}",
        "Rozetka":  f"https://rozetka.com.ua/ua/search/?text={encoded}",
        "Comfy":    f"https://comfy.ua/ua/search/?q={encoded}",
    }
    html_results: dict[str, dict] = {}

    async def _fetch_prices(name: str, url: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(url, headers=_HEADERS)

            if name == "Rozetka":
                # Варіант 1: "title":"...RTX 5090...","price":NNN
                pairs = re.findall(r'"title"\s*:\s*"([^"]{5,150})"[^}]{0,200}?"price"\s*:\s*(\d+)', r.text)
                prices = [
                    int(p) for nm, p in pairs
                    if _is_relevant(nm) and 200 < int(p) < 1_000_000
                ]
                # Варіант 2: "name":"...RTX 5090...","price":NNN
                if not prices:
                    pairs = re.findall(r'"name"\s*:\s*"([^"]{5,150})"[^}]{0,200}?"price"\s*:\s*(\d+)', r.text)
                    prices = [
                        int(p) for nm, p in pairs
                        if _is_relevant(nm) and 200 < int(p) < 1_000_000
                    ]
                # Варіант 3: data-price або :price на сторінці з фільтрацією
                if not prices:
                    # Шукаємо блоки що містять назву товару поруч з ціною
                    blocks = re.findall(r'(?:' + re.escape(product_keywords[-1]) + r')[^<]{0,300}?(\d{4,7})\s*(?:грн|₴)', r.text, re.IGNORECASE)
                    prices = [int(p) for p in blocks if 200 < int(p) < 1_000_000]
                # НЕ робимо fallback без фільтрації — краще не показати Rozetka ніж показати неправильну ціну

            elif name == "Comfy":
                prices = [int(p) for p in re.findall(r'data-price="(\d+)"', r.text) if 200 < int(p) < 1_000_000]
                if not prices:
                    prices = _parse_prices(r.text)

            elif name == "Hotline":
                # Hotline: ціни в data-price або у spans з класом price
                prices = [int(p) for p in re.findall(r'data-price=["\'](\d+)["\']', r.text) if 1000 < int(p) < 2_000_000]
                if not prices:
                    # Fallback: шукаємо ціни в JSON-LD або мета-тегах
                    prices = [int(p) for p in re.findall(r'"price"\s*:\s*"?(\d+)"?', r.text) if 1000 < int(p) < 2_000_000]
                if not prices:
                    prices = [p for p in _parse_prices(r.text) if p >= 1000]

            elif name == "Price.ua":
                # Price.ua: ціни в data-price або spans
                prices = [int(p) for p in re.findall(r'data-price=["\'](\d+)["\']', r.text) if 1000 < int(p) < 2_000_000]
                if not prices:
                    prices = [p for p in _parse_prices(r.text) if p >= 1000]

            if prices:
                html_results[name] = {"min": min(prices), "max": max(prices), "url": url}
        except Exception as e:
            print(f"[{name} error]: {e}")

    await asyncio.gather(*[_fetch_prices(n, u) for n, u in sites.items()])

    if html_results:
        cheapest  = min(html_results, key=lambda s: html_results[s]["min"])
        city_note = f" у {city}" if city else ""
        out_lines = [f"💰 Ціни на: {product}{city_note}\n"]
        for site, data in html_results.items():
            tag       = " 🏆" if site == cheapest else ""
            price_str = (
                f"{data['min']:,} грн".replace(",", " ")
                if data["min"] == data["max"]
                else f"{data['min']:,} – {data['max']:,} грн".replace(",", " ")
            )
            out_lines.append(f"• {site}: {price_str}{tag}\n  🔗 {data['url']}")
        out_lines.append(
            f"\n💡 Найдешевше на {cheapest}: {html_results[cheapest]['min']:,} грн"
            .replace(",", " ")
        )
        if city:
            out_lines.append(f"📍 Наявність у {city} — уточнюй безпосередньо в магазині.")
        return "\n".join(out_lines)

    # ── Спроба 3: загальний пошук + посилання ────────────────────────────
    search_results = await search_web(f'"{product}" купити ціна {city_str}')
    if not search_results:
        return f"❌ Не вдалось знайти ціни на «{product}».\n\nПошукай вручну:\n{links}"

    summary_raw = await call_ai([{"role": "user", "content": (
        f"З цих результатів знайди конкретні числові ціни на '{product}'"
        f"{' у ' + city if city else ' в Україні'}.\n"
        f"СУВОРІ ПРАВИЛА:\n"
        f"- Відповідай ТІЛЬКИ у форматі JSON-масиву, нічого більше:\n"
        f'  [{{"shop":"Назва","price":999,"url":"https://..."}},...]\n'
        f"- Вказуй ТІЛЬКИ ціни що дослівно є в тексті результатів\n"
        f"- Кожен елемент масиву — УНІКАЛЬНИЙ url (не дублювати)\n"
        f"- ІГНОРУЙ ціни на інші моделі чи схожі товари\n"
        f"- НЕ вигадуй ціни. НЕ згадуй OLX\n"
        f"- Якщо цін немає — поверни порожній масив []\n\n"
        f"{search_results}"
    )}])

    # Парсимо JSON від AI і форматуємо самостійно
    try:
        clean_json = summary_raw.strip().replace("```json", "").replace("```", "")
        price_items = json.loads(clean_json)
    except Exception:
        price_items = []

    if price_items:
        # Дедуплікація за URL
        seen_urls: set[str] = set()
        unique_items = []
        for item in price_items:
            u = item.get("url", "").split("?")[0]  # ігноруємо query-параметри при порівнянні
            if u not in seen_urls:
                seen_urls.add(u)
                unique_items.append(item)

        unique_items.sort(key=lambda x: x.get("price", 0))
        city_note = f" у {city}" if city else ""
        lines = [f"💰 Ціни на: {product}{city_note}\n"]
        for i, item in enumerate(unique_items[:6]):
            marker = " 🏆" if i == 0 else ""
            price_val = item.get("price", "?")
            price_str = f"{price_val:,} грн".replace(",", " ") if isinstance(price_val, int) else f"{price_val} грн"
            lines.append(f"• {item.get('shop','?')}: {price_str}{marker}\n  🔗 {item.get('url','')}")
        lines.append(f"\n💡 Найдешевше: {unique_items[0].get('price','?'):,} грн на {unique_items[0].get('shop','?')}".replace(",", " "))
        if city:
            lines.append(f"📍 Наявність у {city} — уточнюй безпосередньо в магазині.")
        return "\n".join(lines)

    # AI не знайшов цін — повертаємо посилання для пошуку
    return (
        f"💰 Ціни на: {product}\n\n"
        f"Не вдалось знайти конкретні ціни автоматично.\n"
        f"Пошукай вручну:\n{links}"
    )


# ════════════════════════════════════════════════════════════════════════════
# Нагадування
# ════════════════════════════════════════════════════════════════════════════

def load_reminders() -> list:
    return _load_json(REMINDERS_FILE, [])

def save_reminders(reminders: list) -> None:
    _save_json(REMINDERS_FILE, reminders)

async def schedule_reminder(bot, chat_id: int, text: str, fire_at: datetime) -> None:
    reminders = load_reminders()
    reminders.append({"chat_id": chat_id, "text": text, "fire_at": fire_at.isoformat()})
    save_reminders(reminders)
    async def _run():
        delay = max(0, (fire_at - datetime.now()).total_seconds())
        await asyncio.sleep(delay)
        await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
        save_reminders([r for r in load_reminders() if not (
            r["chat_id"] == chat_id and r["text"] == text and r["fire_at"] == fire_at.isoformat()
        )])
    asyncio.create_task(_run())

async def restore_reminders(bot) -> None:
    now, valid = datetime.now(), []
    for r in load_reminders():
        fire_at = datetime.fromisoformat(r["fire_at"])
        if fire_at <= now:
            await bot.send_message(chat_id=r["chat_id"], text=f"🔔 Пропущене нагадування: {r['text']}")
        else:
            valid.append(r)
            async def _run(chat_id=r["chat_id"], text=r["text"], fi=fire_at):
                delay = max(0, (fi - datetime.now()).total_seconds())
                await asyncio.sleep(delay)
                await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
                save_reminders([x for x in load_reminders() if not (
                    x["chat_id"] == chat_id and x["text"] == text and x["fire_at"] == fi.isoformat()
                )])
            asyncio.create_task(_run())
    save_reminders(valid)


# ════════════════════════════════════════════════════════════════════════════
# Стать
# ════════════════════════════════════════════════════════════════════════════

async def detect_gender_from_transcript(text: str) -> str | None:
    if not text:
        return None
    try:
        result = await call_ai([{"role": "user", "content": (
            f"Визнач стать мовця за граматичними формами (казав/казала тощо).\n"
            f"Текст: '{text}'\nВідповідай ТІЛЬКИ: 'male', 'female' або 'unknown'."
        )}])
        g = result.strip().lower()
        return g if g in ("male", "female") else None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════
# Спільні дії
# ════════════════════════════════════════════════════════════════════════════

async def do_generate_image(update: Update, text: str, msg):
    t      = text.lower()
    prompt = text
    for kw in IMAGE_KEYWORDS:
        if kw in t:
            prompt = text[t.index(kw) + len(kw):].strip()
            break
    prompt = prompt or text
    try:
        translation = (await call_ai([{"role": "user", "content": f"Translate to English, return ONLY translation: {prompt}"}])).strip()
        img_bytes   = await generate_image(translation)
        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Помилка генерації: {e}")

async def do_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    now_str = datetime.now().strftime("%H:%M")
    parsed  = await call_ai([{"role": "user", "content": (
        f"Поточний час: {now_str}. Витягни час нагадування і текст: '{text}'. "
        "Відповідай ТІЛЬКИ JSON: {\"delay_minutes\": 5, \"text\": \"текст\"}"
    )}])
    data        = json.loads(parsed.strip().replace("```json", "").replace("```", ""))
    delay_min   = int(data["delay_minutes"])
    remind_text = data["text"]
    await schedule_reminder(ctx.bot, update.effective_chat.id, remind_text, datetime.now() + timedelta(minutes=delay_min))
    return delay_min, remind_text

async def _send_or_edit(msg, text: str, **kwargs):
    text   = text.strip()
    chunks = [text[i:i+4000] for i in range(0, max(len(text), 1), 4000)]
    try:
        await msg.edit_text(chunks[0], **kwargs)
    except Exception:
        await msg.reply_text(chunks[0], **kwargs)
    for chunk in chunks[1:]:
        await msg.reply_text(chunk, **kwargs)


# ════════════════════════════════════════════════════════════════════════════
# Команди
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
        "• Порівнювати ціни 💰\n"
        "• Читати PDF, Excel, Word 📄\n"
        "• Нагадування 🔔\n\n"
        "Просто пиши — я розумію природну мову!\n/help — всі команди"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команди:\n"
        "/image опис — генерація зображення\n"
        "/remind 30m текст — нагадування\n"
        "/news тема — новини за темою\n"
        "/search запит — пошук в інтернеті\n"
        "/price товар — порівняння цін\n"
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
        "💡 Або просто пиши природною мовою!"
    )

async def news_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Використання: /news тема")
        return
    msg    = await update.message.reply_text(f"📰 Шукаю новини про «{query}»...")
    result = await do_news(query)
    await _send_or_edit(msg, clean_markdown(result), disable_web_page_preview=True)

async def price_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Використання: /price iPhone 16")
        return
    msg    = await update.message.reply_text(f"💰 Шукаю ціни на «{query}»...")
    result = await do_price(query)
    await _send_or_edit(msg, clean_markdown(result), disable_web_page_preview=True)

async def translate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /translate текст")
        return
    msg    = await update.message.reply_text("🌐 Перекладаю...")
    result = await do_translate(text)
    await _send_or_edit(msg, clean_markdown(result))

async def summarize_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /summarize https://... або текст")
        return
    msg    = await update.message.reply_text("📰 Опрацьовую...")
    result = await do_summarize(text)
    await _send_or_edit(msg, clean_markdown(result))

async def generate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text(
            "Використання:\n/generate резюме для розробника\n"
            "/generate лист подяки\n/generate пост про продукт"
        )
        return
    msg    = await update.message.reply_text("✍️ Генерую текст...")
    result = await do_generate(text)
    await _send_or_edit(msg, clean_markdown(result))

async def recipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /recipe картопля яйця цибуля")
        return
    msg    = await update.message.reply_text("🍳 Шукаю рецепти...")
    result = await do_recipe(text)
    await _send_or_edit(msg, clean_markdown(result))

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
        lines = ["🎭 Оберіть режим:\n"] + [
            f"{p['emoji']} /mode {key} — {p['name']}{'  ✅' if key == current else ''}"
            for key, p in PERSONALITIES.items()
        ]
        await update.message.reply_text("\n".join(lines))

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = get_user_memory(update.message.from_user.id)
    if not mem:
        await update.message.reply_text("🧠 Я поки нічого не пам'ятаю про тебе.")
        return
    lines = ["🧠 Що я пам'ятаю:\n"]
    if mem.get("name"):   lines.append(f"👤 Ім'я: {mem['name']}")
    if mem.get("gender"): lines.append(f"👤 Стать: {'чоловік' if mem['gender'] == 'male' else 'жінка'}")
    if mem.get("facts"):
        lines.append("\n📌 Факти:")
        lines += [f"  • {f}" for f in mem["facts"]]
    if mem.get("topics"):
        lines.append("\n💬 Теми розмов:")
        lines += [f"  • {t}" for t in mem["topics"]]
    await update.message.reply_text("\n".join(lines))

async def forget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    memory = _load_json(MEMORY_FILE, {})
    memory.pop(str(update.message.from_user.id), None)
    _save_json(MEMORY_FILE, memory)
    await update.message.reply_text("🗑️ Пам'ять очищено.")

async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args)
    if not prompt:
        await update.message.reply_text("Напиши що намалювати: /image захід сонця над морем")
        return
    msg = await update.message.reply_text("🎨 Генерую зображення...")
    try:
        translation = (await call_ai([{"role": "user", "content": f"Translate to English, return ONLY translation: {prompt}"}])).strip()
        img_bytes   = await generate_image(translation)
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
    user_id = update.message.from_user.id
    msg     = await update.message.reply_text(f"🌐 Шукаю: {query}...")
    results = await search_web(query)
    try:
        reply = await call_ai([SYSTEM_PROMPT, {"role": "user", "content": f"Запит: '{query}'\n\nРезультати:\n{results}\n\nСтисло підсумуй українською."}])
        await append_and_trim(user_id, "user", f"Пошук: {query}")
        await append_and_trim(user_id, "assistant", reply)
        await _send_or_edit(msg, f"🌐 {query}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка: {e}")

async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    provider  = get_text_provider()
    remaining = max(0, OR_DAILY_LIMIT - or_requests["count"])
    tavily_status = "❌ Не налаштовано"
    if TAVILY_API_KEY:
        try:
            await asyncio.to_thread(lambda: TavilyClient(api_key=TAVILY_API_KEY).search(query="test", max_results=1))
            tavily_status = "🟢 Працює"
        except Exception as e:
            tavily_status = f"🔴 Помилка: {str(e)[:50]}"
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• Провайдер: {'OpenRouter 🟢' if provider == 'openrouter' else 'Groq 🔵'}\n"
        f"• OpenRouter залишилось: {remaining}/{OR_DAILY_LIMIT}\n"
        f"• Tavily пошук: {tavily_status}\n"
        f"• Активних нагадувань: {len(load_reminders())}\n"
        f"• Ліміт скидається: щодня опівночі"
    )

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    chat_histories.pop(uid, None)
    save_histories()
    await update.message.reply_text("Історію чату очищено! 🔄")


# ════════════════════════════════════════════════════════════════════════════
# Диспетчер інтентів + обробники повідомлень
# ════════════════════════════════════════════════════════════════════════════

def _is_bot_addressed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    user_text = update.message.text or ""
    if update.message.chat.type not in ("group", "supergroup"):
        return True, user_text
    bot_username = ctx.bot.username
    if f"@{bot_username}" in user_text:
        return True, user_text.replace(f"@{bot_username}", "").strip()
    reply = update.message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == ctx.bot.id:
        return True, user_text
    return False, user_text

async def _dispatch_intent(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int, user_text: str, enriched: str,
    intent: str, voice_msg=None
) -> bool:
    prefix = f"🎤 Ти сказав: {user_text}\n\n" if voice_msg else ""

    if intent == "image":
        msg = voice_msg or await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
        if voice_msg:
            await msg.edit_text(f"{prefix}🎨 Генерую зображення...")
        await do_generate_image(update, enriched, msg)
        return True

    if intent == "reminder":
        try:
            delay_min, remind_text = await do_reminder(update, ctx, enriched)
            text = f"{prefix}✅ Нагадаю через {delay_min} хв: {remind_text}"
            if voice_msg: await voice_msg.edit_text(text)
            else:         await update.message.reply_text(text)
        except Exception as e:
            err = f"{prefix}Не вдалось встановити нагадування: {e}"
            if voice_msg: await voice_msg.edit_text(err)
            else:         await update.message.reply_text(err)
        return True

    if intent == "task":
        if voice_msg:
            await voice_msg.edit_text(f"{prefix}⏳ Обробляю задачу...")
        await do_task_nlp(update, user_id, enriched)
        return True

    if intent == "search":
        msg = voice_msg or await update.message.reply_text("🌐 Шукаю в інтернеті...")
        if voice_msg:
            await msg.edit_text(f"{prefix}🌐 Шукаю в інтернеті...")
        try:
            search_query = enriched
            if "[Контекст попереднього повідомлення" in enriched:
                search_query = (await call_ai([{"role": "user", "content": (
                    f"{enriched}\n\nСклади короткий пошуковий запит (до 10 слів). Відповідай ТІЛЬКИ запитом."
                )}])).strip()
            results = await search_web(search_query)
            content = (
                f"Запит: '{enriched}'\n\nРезультати пошуку:\n{results}\n\nДай корисну відповідь українською з посиланнями."
                if results else
                f"Запит: '{enriched}'\n\nВідповідай з власних знань українською."
            )
            reply = await call_ai([SYSTEM_PROMPT, {"role": "user", "content": content}])
            await append_and_trim(user_id, "user", user_text)
            await append_and_trim(user_id, "assistant", reply)
            _set_ctx(user_id, user_text, reply)
            await _send_or_edit(msg, clean_markdown(f"{prefix}{reply}"), disable_web_page_preview=True)
        except Exception as e:
            await msg.edit_text(f"{prefix}Помилка пошуку: {e}")
        return True

    INTENT_MAP = {
        "translate": ("🌐 Перекладаю...",       do_translate),
        "summarize": ("📰 Опрацьовую...",        do_summarize),
        "generate":  ("✍️ Генерую текст...",     do_generate),
        "recipe":    ("🍳 Шукаю рецепти...",     do_recipe),
        "news":      ("📰 Шукаю новини...",      do_news),
        "price":     ("💰 Шукаю ціни...",        do_price),
    }
    if intent in INTENT_MAP:
        status_text, fn = INTENT_MAP[intent]
        msg = voice_msg or await update.message.reply_text(status_text)
        if voice_msg:
            await msg.edit_text(f"{prefix}{status_text}")
        try:
            result = await fn(enriched)
        except Exception as e:
            await msg.edit_text(f"{prefix}⚠️ Помилка: {e}")
            return True
        kwargs = {"disable_web_page_preview": True} if intent in ("news", "price") else {}
        await _send_or_edit(msg, clean_markdown(f"{prefix}{result}".strip()), **kwargs)
        _set_ctx(user_id, user_text, result)
        return True

    return False

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addressed, user_text = _is_bot_addressed(update, ctx)
    if not addressed:
        return
    user_id = update.message.from_user.id
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        enriched = await resolve_text_with_context(user_id, user_text)
        intent   = await detect_intent(enriched)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка обробки запиту: {e}")
        return

    if await _dispatch_intent(update, ctx, user_id, user_text, enriched, intent):
        return

    try:
        await append_and_trim(user_id, "user", enriched)
        reply  = await call_ai(get_history(user_id))
        await append_and_trim(user_id, "assistant", reply)
        chunks = [reply[i:i+4000] for i in range(0, max(len(reply), 1), 4000)]
        for chunk in chunks:
            await update.message.reply_text(clean_markdown(chunk))
        _set_ctx(user_id, user_text, reply)
        asyncio.create_task(extract_and_save_memory(user_id, user_text, reply))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка відповіді: {e}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("🔍 Аналізую зображення...")
    user_id = update.message.from_user.id
    try:
        photo   = update.message.photo[-1]
        file    = await ctx.bot.get_file(photo.file_id)
        img_b64 = base64.b64encode(await file.download_as_bytearray()).decode()
        caption = update.message.caption or "Що зображено на цьому фото? Опиши детально українською."
        reply   = await call_vision([SYSTEM_PROMPT, {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": caption}
        ]}])
        last_context[user_id] = {"type": "фото", "description": reply[:500]}
        await append_and_trim(user_id, "user", f"[Фото] {caption}")
        await append_and_trim(user_id, "assistant", reply)
        await _send_or_edit(msg, clean_markdown(reply))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі зображення: {e}")

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("🎬 Аналізую відео (аудіо + кадри)...")
    user_id = update.message.from_user.id
    try:
        video   = update.message.video or update.message.video_note
        caption = (update.message.caption if update.message.video else None) or "Що відбувається у цьому відео?"
        if video.file_size > 20 * 1024 * 1024:
            await msg.edit_text("❌ Відео занадто велике. Максимум 20MB.")
            return
        video_bytes = bytes(await (await ctx.bot.get_file(video.file_id)).download_as_bytearray())
        result      = await analyze_video(video_bytes, caption)
        last_context[user_id] = {"type": "відео", "description": result[:500]}
        await append_and_trim(user_id, "user", f"[Відео] {caption}")
        await append_and_trim(user_id, "assistant", result)
        await _send_or_edit(msg, clean_markdown(result))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі відео: {e}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc     = update.message.document
    fname   = doc.file_name.lower()
    user_id = update.message.from_user.id
    caption = update.message.caption or "Стисло підсумуй цей документ українською."

    if   fname.endswith(".pdf"):             msg = await update.message.reply_text("📄 Читаю PDF...")
    elif fname.endswith((".xlsx", ".xls")): msg = await update.message.reply_text("📊 Читаю Excel...")
    elif fname.endswith((".docx", ".doc")): msg = await update.message.reply_text("📝 Читаю Word...")
    else:
        await update.message.reply_text("📄 Підтримуються: PDF, Excel (.xlsx), Word (.docx)")
        return

    raw = bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray())
    if   fname.endswith(".pdf"):             text = extract_pdf_text(raw)
    elif fname.endswith((".xlsx", ".xls")): text = extract_excel_text(raw)
    else:                                    text = extract_word_text(raw)

    try:
        if not text or text.startswith("Помилка"):
            await msg.edit_text(f"❌ {text}")
            return
        text_preview = text[:4000] + ("..." if len(text) > 4000 else "")
        await append_and_trim(user_id, "user", f"{caption}\n\nВміст документу:\n{text_preview}")
        reply = await call_ai(get_history(user_id))
        await append_and_trim(user_id, "assistant", reply)
        last_context[user_id] = {"type": "документ", "description": reply[:500]}
        await _send_or_edit(msg, f"📄 {doc.file_name}\n\n{clean_markdown(reply)}")
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

        if get_gender(user_id) is None:
            gender = await detect_gender_from_transcript(text)
            if gender:
                update_user_memory(user_id, {"gender": gender})
                if user_id in chat_histories:
                    chat_histories[user_id][0] = get_system_prompt(user_id)

        enriched = await resolve_text_with_context(user_id, text)
        intent   = await detect_intent(enriched)

        if await _dispatch_intent(update, ctx, user_id, text, enriched, intent, voice_msg=msg):
            return

        await append_and_trim(user_id, "user", enriched)
        reply = await call_ai(get_history(user_id))
        await append_and_trim(user_id, "assistant", reply)
        await _send_or_edit(msg, f"🎤 Ти сказав: {text}\n\n{clean_markdown(reply)}")
    except Exception as e:
        await msg.edit_text(f"Помилка при обробці голосового: {e}")


# ════════════════════════════════════════════════════════════════════════════
# Запуск
# ════════════════════════════════════════════════════════════════════════════

async def post_init(app):
    await restore_reminders(app.bot)
    restore_histories()
    memory = _load_json(MEMORY_FILE, {})
    for uid, data in memory.items():
        if data.get("mode"):
            user_personalities[int(uid)] = data["mode"]

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
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
    app.add_handler(CommandHandler("price",     price_cmd))
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
