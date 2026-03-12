import os
import re
import httpx
import base64
import asyncio
import io
import json
from datetime import date, datetime, timedelta
from tavily import TavilyClient
import subprocess
import openpyxl
from docx import Document as DocxDocument
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

STORY_GENRES = {
    "fantasy":   {"name": "Фентезі",       "emoji": "🧙"},
    "horror":    {"name": "Жахи",          "emoji": "👻"},
    "romance":   {"name": "Романтика",     "emoji": "💕"},
    "scifi":     {"name": "Наукова фантастика", "emoji": "🚀"},
    "adventure": {"name": "Пригоди",       "emoji": "⚔️"},
    "mystery":   {"name": "Детектив",      "emoji": "🔍"},
    "comedy":    {"name": "Комедія",       "emoji": "😂"},
}

# Активні сесії рольових ігор / історій
user_sessions: dict[int, dict] = {}  # {user_id: {"mode": "story"|"rpg", "genre": ..., "context": [...]}}
    "normal": {
        "name": "Звичайний",
        "emoji": "🤖",
        "prompt": (
            "Ти розумний і корисний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Використовуй грамотну, природну українську мову без суржику. "
            "Будь точним, лаконічним і дружнім. Структуруй відповіді. "
            "Ніколи не вигадуй факти."
        )
    },
    "funny": {
        "name": "Жартівливий",
        "emoji": "😄",
        "prompt": (
            "Ти веселий і дотепний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Додавай гумор, жарти, каламбури та смішні порівняння у відповіді. "
            "Будь невимушеним і розважальним, але все одно корисним. "
            "Іноді використовуй смайли та emoji. Ніколи не вигадуй факти."
        )
    },
    "serious": {
        "name": "Серйозний",
        "emoji": "🎯",
        "prompt": (
            "Ти суворий і точний AI асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Відповідай лаконічно, без зайвих слів. Тільки факти і суть. "
            "Без жартів, без зайвих емоцій. Структурована і точна інформація. "
            "Ніколи не вигадуй факти."
        )
    },
    "business": {
        "name": "Діловий",
        "emoji": "💼",
        "prompt": (
            "Ти професійний бізнес-асистент на ім'я J.A.R.V.I.S. "
            "Завжди відповідай виключно українською мовою. "
            "Використовуй діловий стиль мовлення. Давай чіткі, структуровані відповіді. "
            "Орієнтуйся на практичну цінність і результат. "
            "Використовуй бізнес-термінологію де доречно. Ніколи не вигадуй факти."
        )
    }
}

# Поточна особистість для кожного користувача
user_personalities: dict[int, str] = {}

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

async def detect_intent(text: str) -> str:
    """Визначає намір. Повертає: 'image','reminder','search','convert','horoscope','fact','joke','chat'"""
    result = await call_ai([{"role": "user", "content": (
        f"Визнач намір цього повідомлення: '{text}'\n"
        "Відповідай ТІЛЬКИ одним словом:\n"
        "- 'image' — намалювати/згенерувати/створити зображення\n"
        "- 'reminder' — нагадати щось через певний час\n"
        "- 'search' — знайти/пошукати актуальну інформацію\n"
        "- 'convert' — конвертувати валюту або одиниці виміру\n"
        "- 'horoscope' — гороскоп для знаку зодіаку\n"
        "- 'fact' — цікавий факт дня\n"
        "- 'joke' — жарт або анекдот\n"
        "- 'chat' — все інше\n"
        "Відповідь — ТІЛЬКИ одне слово."
    )}])
    return result.strip().lower().strip("'\"")


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
        chat_histories[user_id] = [get_system_prompt(user_id)]
    return chat_histories[user_id]

def append_and_trim(user_id: int, role: str, content) -> None:
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_MESSAGES + 1:
        chat_histories[user_id] = [get_system_prompt(user_id)] + history[-MAX_HISTORY_MESSAGES:]


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
    last_error = None
    for attempt in range(3):
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
# Утиліти: пошук і PDF
# ════════════════════════════════════════════════════════════════════════════

ZODIAC_SIGNS = {
    " овен": "aries", "телець": "taurus", "близнюки": "gemini",
    "рак": "cancer", "лев": "leo", "діва": "virgo",
    "терези": "libra", "скорпіон": "scorpio", "стрілець": "sagittarius",
    "козеріг": "capricorn", "водолій": "aquarius", "риби": "pisces"
}

# Активні ігрові сесії
# user_sessions вже є, додаємо типи: "quiz", "riddle", "20q"

QUIZ_CATEGORIES = {
    "history":  "📜 Історія",
    "science":  "🔬 Наука",
    "geography":"🌍 Географія",
    "movies":   "🎬 Кіно",
    "sport":    "⚽ Спорт",
    "ukraine":  "🇺🇦 Україна",
    "mix":      "🎲 Мікс",
}

async def start_quiz(user_id: int, category: str) -> str:
    cat_name = QUIZ_CATEGORIES.get(category, "Мікс")
    question_data = await call_ai([{"role": "user", "content": (
        f"Створи питання для вікторини на тему '{cat_name}'. "
        "Відповідай ТІЛЬКИ у форматі JSON:\n"
        "{\"question\": \"питання\", \"options\": [\"A) варіант\", \"B) варіант\", \"C) варіант\", \"D) варіант\"], "
        "\"answer\": \"A\", \"explanation\": \"пояснення\"}\n"
        "Нічого більше не пиши."
    )}])
    question_data = question_data.strip().replace("```json","").replace("```","")
    data = json.loads(question_data)
    user_sessions[user_id] = {
        "mode":     "quiz",
        "category": category,
        "score":    0,
        "total":    0,
        "current":  data,
        "context":  []
    }
    opts = "\n".join(data["options"])
    return f"🎯 Вікторина: {cat_name}\n\n❓ {data['question']}\n\n{opts}"

async def check_quiz_answer(user_id: int, user_text: str) -> str:
    session  = user_sessions[user_id]
    current  = session["current"]
    answer   = user_text.strip().upper()[:1]
    correct  = current["answer"].upper()
    session["total"] += 1

    if answer == correct:
        session["score"] += 1
        result = f"✅ Правильно! {current['explanation']}\n\n🏆 Рахунок: {session['score']}/{session['total']}"
    else:
        result = f"❌ Неправильно! Правильна відповідь: {correct}\n{current['explanation']}\n\n🏆 Рахунок: {session['score']}/{session['total']}"

    # Наступне питання
    next_data = await call_ai([{"role": "user", "content": (
        f"Створи НОВЕ питання для вікторини на тему '{QUIZ_CATEGORIES.get(session['category'])}'. "
        "Відповідай ТІЛЬКИ у форматі JSON:\n"
        "{\"question\": \"питання\", \"options\": [\"A) варіант\", \"B) варіант\", \"C) варіант\", \"D) варіант\"], "
        "\"answer\": \"A\", \"explanation\": \"пояснення\"}\nНічого більше."
    )}])
    next_data = next_data.strip().replace("```json","").replace("```","")
    session["current"] = json.loads(next_data)
    opts = "\n".join(session["current"]["options"])
    return f"{result}\n\n➡️ Наступне питання:\n❓ {session['current']['question']}\n\n{opts}"

async def start_riddle(user_id: int) -> str:
    data = await call_ai([{"role": "user", "content": (
        "Придумай цікаву загадку українською мовою. "
        "Відповідай ТІЛЬКИ у форматі JSON:\n"
        "{\"riddle\": \"текст загадки\", \"answer\": \"відповідь\", \"hint\": \"підказка\"}\n"
        "Нічого більше не пиши."
    )}])
    data = data.strip().replace("```json","").replace("```","")
    parsed = json.loads(data)
    user_sessions[user_id] = {
        "mode":    "riddle",
        "current": parsed,
        "hints":   0,
        "context": []
    }
    return f"🧩 Загадка:\n\n{parsed['riddle']}\n\n💡 Напиши відповідь або 'підказка'"

async def check_riddle(user_id: int, user_text: str) -> str:
    session = user_sessions[user_id]
    current = session["current"]
    text    = user_text.lower().strip()

    if "підказка" in text:
        session["hints"] += 1
        return f"💡 Підказка: {current['hint']}\n\nСпробуй ще раз!"

    # Перевірка через AI чи відповідь правильна
    check = await call_ai([{"role": "user", "content": (
        f"Загадка: '{current['riddle']}'\n"
        f"Правильна відповідь: '{current['answer']}'\n"
        f"Відповідь користувача: '{user_text}'\n"
        "Чи правильна відповідь? Відповідай ТІЛЬКИ 'yes' або 'no'."
    )}])

    if "yes" in check.lower():
        hints_penalty = f" (-{session['hints']} підказки)" if session["hints"] else ""
        user_sessions.pop(user_id, None)
        return (
            f"🎉 Правильно! Відповідь: {current['answer']}{hints_penalty}\n\n"
            f"Ще загадку? /riddle"
        )
    else:
        return f"❌ Не зовсім... Спробуй ще або напиши 'підказка'"

async def start_20q(user_id: int) -> str:
    user_sessions[user_id] = {
        "mode":      "20q",
        "questions": 0,
        "history":   [],
        "context":   []
    }
    system = (
        "Ми граємо у гру '20 питань'. Задумай будь-який предмет, тварину або відому особу. "
        "НЕ розкривай що задумав. Гравець задаватиме питання на які можна відповісти "
        "тільки 'Так', 'Ні' або 'Частково'. Після 20 питань гравець має вгадати. "
        "Відповідай українською."
    )
    reply = await call_ai([
        {"role": "system", "content": system},
        {"role": "user", "content": "Задумав щось. Починаємо!"}
    ])
    user_sessions[user_id]["history"].append({"role": "assistant", "content": reply})
    return f"🤔 Гра '20 питань'\n\nЯ задумав щось... Задавай питання!\nВідповіді: Так / Ні / Частково\n\n{reply}"

async def handle_20q(user_id: int, user_text: str) -> str:
    session = user_sessions[user_id]
    session["questions"] += 1
    q_num = session["questions"]

    system = (
        "Ми граємо у '20 питань'. Ти задумав предмет/тварину/особу. "
        "Відповідай на питання тільки 'Так', 'Ні' або 'Частково'. "
        "Якщо гравець вгадав — підтверди і привітай. "
        "Відповідай українською."
    )
    session["history"].append({"role": "user", "content": f"Питання {q_num}/20: {user_text}"})
    messages = [{"role": "system", "content": system}] + session["history"][-10:]
    reply    = await call_ai(messages)
    session["history"].append({"role": "assistant", "content": reply})

    suffix = f"\n\n❓ Питань залишилось: {20 - q_num}"
    if q_num >= 20:
        user_sessions.pop(user_id, None)
        suffix = "\n\n🏁 Питання вичерпано! /20q — зіграти ще"

    return f"{reply}{suffix}"
    sign_en = ZODIAC_SIGNS.get(sign.lower())
    if not sign_en:
        signs = ", ".join(ZODIAC_SIGNS.keys())
        return f"❌ Невідомий знак зодіаку. Доступні: {signs}"
    today = datetime.now().strftime("%d.%m.%Y")
    reply = await call_ai([{"role": "user", "content": (
        f"Склади детальний гороскоп на сьогодні ({today}) для знаку {sign} ({sign_en}). "
        f"Включи: загальний настрій, кохання, робота/фінанси, здоров'я, порада дня. "
        f"Зроби його захоплюючим і позитивним. Відповідай українською."
    )}])
    return f"⭐ Гороскоп для {sign.capitalize()} на {today}\n\n{reply}"

async def get_fact() -> str:
    today = datetime.now().strftime("%d %B %Y")
    reply = await call_ai([{"role": "user", "content": (
        f"Розкажи один цікавий та маловідомий факт на сьогодні ({today}). "
        f"Факт має бути захоплюючим, перевіреним і пізнавальним. "
        f"Формат: коротка назва факту (1 рядок) + детальне пояснення (3-4 речення). "
        f"Відповідай українською."
    )}])
    return f"🧠 Факт дня\n\n{reply}"

async def get_joke() -> str:
    reply = await call_ai([{"role": "user", "content": (
        "Розкажи один смішний та оригінальний жарт українською мовою. "
        "Жарт має бути доречним, без образ. Можна анекдот або каламбур. "
        "Формат: питання/завязка — потім відповідь/кульмінація."
    )}])
    return f"😂 Жарт дня\n\n{reply}"
    """Витягує аудіо з відео через ffmpeg."""
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", "/tmp/input_video.mp4",
        "-vn", "-ar", "16000", "-ac", "1", "-f", "ogg", "/tmp/output_audio.ogg",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    with open("/tmp/output_audio.ogg", "rb") as f:
        return f.read()

async def extract_video_frames(video_bytes: bytes, max_frames: int = 3) -> list[bytes]:
    """Витягує кілька ключових кадрів з відео через ffmpeg."""
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    frames = []
    # Витягуємо кадри рівномірно
    for i, t in enumerate(["00:00:01", "00:00:05", "00:00:10"][:max_frames]):
        out_path = f"/tmp/frame_{i}.jpg"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", t, "-i", "/tmp/input_video.mp4",
            "-frames:v", "1", "-q:v", "2", out_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(out_path):
            with open(out_path, "rb") as f:
                frames.append(f.read())
    return frames

async def analyze_video(video_bytes: bytes, caption: str) -> str:
    """Повний аналіз відео: транскрипція аудіо + аналіз кадрів."""
    results = []

    # 1. Транскрибуємо аудіо
    try:
        audio_bytes = await extract_video_audio(video_bytes)
        transcript  = await transcribe_voice(audio_bytes)
        if transcript:
            results.append(f"🎤 Аудіо у відео:\n{transcript}")
    except Exception:
        pass

    # 2. Аналізуємо кадри
    try:
        frames = await extract_video_frames(video_bytes)
        if frames:
            frame_descriptions = []
            for i, frame in enumerate(frames):
                b64 = base64.b64encode(frame).decode()
                desc = await call_vision([
                    SYSTEM_PROMPT,
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"Опиши що бачиш на кадрі {i+1} з відео. Коротко, 1-2 речення."}
                    ]}
                ])
                frame_descriptions.append(f"Кадр {i+1}: {desc}")
            results.append("🎬 Візуальний вміст:\n" + "\n".join(frame_descriptions))
    except Exception:
        pass

    if not results:
        return "❌ Не вдалось проаналізувати відео."

    # 3. Загальний підсумок
    combined = "\n\n".join(results)
    summary  = await call_ai([
        SYSTEM_PROMPT,
        {"role": "user", "content": (
            f"На основі цих даних про відео дай відповідь на запит користувача.\n"
            f"Запит: {caption}\n\n"
            f"Дані про відео:\n{combined}\n\n"
            "Відповідай українською."
        )}
    ])
    return f"{combined}\n\n📝 Підсумок:\n{summary}"
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

def extract_excel_text(xlsx_bytes: bytes) -> str:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
        output = ""
        for sheet in wb.worksheets:
            output += f"=== Аркуш: {sheet.title} ===\n"
            for row in sheet.iter_rows(values_only=True):
                row_data = [str(cell) if cell is not None else "" for cell in row]
                if any(row_data):
                    output += " | ".join(row_data) + "\n"
        return output.strip() or "Файл порожній."
    except Exception as e:
        return f"Помилка читання Excel: {e}"

def extract_word_text(docx_bytes: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(docx_bytes))
        output = ""
        for para in doc.paragraphs:
            if para.text.strip():
                output += para.text + "\n"
        # Також витягуємо таблиці
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

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    mem = get_user_memory(user_id)
    if not mem:
        await update.message.reply_text("🧠 Я поки нічого не пам'ятаю про тебе.")
        return
    lines = ["🧠 Що я пам'ятаю про тебе:\n"]
    if mem.get("name"):
        lines.append(f"👤 Ім'я: {mem['name']}")
    if mem.get("facts"):
        lines.append("\n📌 Факти:")
        for fact in mem["facts"]:
            lines.append(f"  • {fact}")
    await update.message.reply_text("\n".join(lines))

async def forget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    memory = load_memory()
    memory.pop(str(user_id), None)
    save_memory(memory)
    await update.message.reply_text("🗑️ Пам'ять очищено.")

async def convert_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /convert 100 usd uah  — валюта
    /convert 5 km mi      — одиниці
    /convert 100 c f      — температура
    """
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "Використання:\n"
            "/convert 100 USD UAH — валюта\n"
            "/convert 5 km mi — відстань\n"
            "/convert 70 kg lb — вага\n"
            "/convert 100 C F — температура\n\n"
            "Або просто напиши: *скільки 100 доларів в гривнях?*"
        )
        return
    try:
        amount   = float(ctx.args[0].replace(",", "."))
        from_u   = ctx.args[1]
        to_u     = ctx.args[2]
    except ValueError:
        await update.message.reply_text("❌ Невірний формат числа.")
        return

    # Визначаємо чи це валюта чи одиниці
    currencies = {
        "usd","eur","uah","gbp","pln","czk","chf",
        "jpy","cad","aud","rub","try","cny","sek","nok"
    }
    if from_u.lower() in currencies or to_u.lower() in currencies:
        result = await convert_currency(amount, from_u, to_u)
    else:
        result = convert_units(amount, from_u, to_u)

    await update.message.reply_text(result, parse_mode="Markdown")

async def horoscope_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        signs = "\n".join(f"• /horoscope {s}" for s in ZODIAC_SIGNS)
        await update.message.reply_text(f"⭐ Вкажи знак зодіаку:\n{signs}")
        return
    sign  = " ".join(ctx.args).lower()
    msg   = await update.message.reply_text("⭐ Складаю гороскоп...")
    result = await get_horoscope(sign)
    await msg.edit_text(clean_markdown(result))

async def fact_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg    = await update.message.reply_text("🧠 Шукаю цікавий факт...")
    result = await get_fact()
    await msg.edit_text(clean_markdown(result))

async def joke_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg    = await update.message.reply_text("😂 Придумую жарт...")
    result = await get_joke()
    await msg.edit_text(clean_markdown(result))

async def story_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if ctx.args and ctx.args[0].lower() == "stop":
        user_sessions.pop(user_id, None)
        await update.message.reply_text("📖 Історію завершено!")
        return

    genre_key = ctx.args[0].lower() if ctx.args else None
    if not genre_key or genre_key not in STORY_GENRES:
        lines = ["📖 Оберіть жанр для спільної історії:\n"]
        for key, g in STORY_GENRES.items():
            lines.append(f"{g['emoji']} /story {key} — {g['name']}")
        lines.append("\n/story stop — завершити")
        await update.message.reply_text("\n".join(lines))
        return

    genre = STORY_GENRES[genre_key]
    user_sessions[user_id] = {
        "mode":    "story",
        "genre":   genre_key,
        "context": []
    }

    prompt = (
        f"Ми граємо у спільну інтерактивну історію у жанрі {genre['name']}. "
        f"Ти — оповідач. Почни захоплюючу історію (3-4 речення) і в кінці запропонуй "
        f"користувачу 3 варіанти що робити далі (пронумеровані). "
        f"Відповідай виключно українською мовою."
    )
    reply = await call_ai([
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Починай!"}
    ])
    user_sessions[user_id]["context"].append({"role": "assistant", "content": reply})
    await update.message.reply_text(f"{genre['emoji']} *{genre['name']}*\n\n{clean_markdown(reply)}")


async def rpg_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if ctx.args and ctx.args[0].lower() == "stop":
        user_sessions.pop(user_id, None)
        await update.message.reply_text("⚔️ Гру завершено!")
        return

    genre_key = ctx.args[0].lower() if ctx.args else "fantasy"
    if genre_key not in STORY_GENRES:
        genre_key = "fantasy"

    genre = STORY_GENRES[genre_key]
    user_sessions[user_id] = {
        "mode":    "rpg",
        "genre":   genre_key,
        "context": []
    }

    prompt = (
        f"Ти — майстер рольової гри у жанрі {genre['name']}. "
        f"Опиши світ і попроси користувача створити персонажа: "
        f"ім'я, клас/роль і одну особливість. "
        f"Будь деталізованим і захоплюючим. Відповідай виключно українською."
    )
    reply = await call_ai([
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Починай гру!"}
    ])
    user_sessions[user_id]["context"].append({"role": "assistant", "content": reply})
    await update.message.reply_text(f"{genre['emoji']} *RPG: {genre['name']}*\n\n{clean_markdown(reply)}")


async def handle_session_message(update: Update, user_id: int, user_text: str) -> bool:
    """Обробляє повідомлення якщо є активна сесія. Повертає True якщо оброблено."""
    session = user_sessions.get(user_id)
    if not session:
        return False

    mode    = session["mode"]
    genre   = STORY_GENRES[session["genre"]]
    context = session["context"]

    if mode == "story":
        system = (
            f"Ми граємо у спільну інтерактивну історію у жанрі {genre['name']}. "
            f"Ти — оповідач. Продовжуй історію на основі вибору користувача (3-4 речення). "
            f"В кінці завжди пропонуй 3 варіанти дій. Відповідай українською."
        )
    else:
        system = (
            f"Ти — майстер рольової гри у жанрі {genre['name']}. "
            f"Реагуй на дії гравця, описуй наслідки, розвивай сюжет. "
            f"Час від часу додавай кидки кубиків 🎲 (d20) для важливих дій. "
            f"Відповідай українською."
        )

    context.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system}] + context[-10:]  # останні 10 повідомлень

    reply = await call_ai(messages)
    context.append({"role": "assistant", "content": reply})
    await update.message.reply_text(clean_markdown(reply))
    return True


async def mode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    current = user_personalities.get(user_id, "normal")

    if ctx.args:
        mode = ctx.args[0].lower()
        if mode not in PERSONALITIES:
            modes = ", ".join(PERSONALITIES.keys())
            await update.message.reply_text(f"❌ Невідомий режим. Доступні: {modes}")
            return
        user_personalities[user_id] = mode
        update_user_memory(user_id, {"mode": mode})
        # Скидаємо історію з новим промптом
        chat_histories[user_id] = [get_system_prompt(user_id)]
        p = PERSONALITIES[mode]
        await update.message.reply_text(f"{p['emoji']} Режим змінено на: {p['name']}")
    else:
        lines = ["🎭 Оберіть режим:\n"]
        for key, p in PERSONALITIES.items():
            marker = " ✅" if key == current else ""
            lines.append(f"{p['emoji']} /mode {key} — {p['name']}{marker}")
        await update.message.reply_text("\n".join(lines))

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команди:\n"
        "/image опис — генерація зображення\n"
        "/remind 30m текст — нагадування\n"
        "/search запит — пошук в інтернеті\n"
        "/convert 100 USD UAH — конвертер\n"
        "/horoscope лев — гороскоп\n"
        "/fact — цікавий факт дня\n"
        "/joke — жарт дня\n"
        "/story — спільна інтерактивна історія\n"
        "/rpg — рольова гра\n"
        "/mode — змінити особистість бота\n"
        "/memory — що бот пам'ятає про тебе\n"
        "/forget — очистити пам'ять про себе\n"
        "/status — статус бота\n"
        "/reset — очистити історію чату\n"
        "/help — ця довідка"
    )

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
        "Напиши /help щоб побачити всі команди."
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

    # Якщо є активна сесія — передаємо туди
    if await handle_session_message(update, user_id, user_text):
        return

    intent = await detect_intent(user_text)

    if intent == "reminder":
        try:
            delay_min, remind_text = await do_reminder(update, ctx, user_text)
            await update.message.reply_text(f"✅ Нагадаю через {delay_min} хв: {remind_text}")
        except Exception as e:
            await update.message.reply_text(f"Не вдалось встановити нагадування: {e}")
        return

    if intent == "image":
        msg = await update.message.reply_text("🎨 Перекладаю та генерую зображення...")
        await do_generate_image(update, user_text, msg)
        return

    if intent == "horoscope":
        # Витягуємо знак зодіаку з тексту
        sign = next((s for s in ZODIAC_SIGNS if s in user_text.lower()), None)
        if not sign:
            await update.message.reply_text("⭐ Вкажи свій знак зодіаку, наприклад: *гороскоп для Лева*")
            return
        msg = await update.message.reply_text("⭐ Складаю гороскоп...")
        result = await get_horoscope(sign)
        await msg.edit_text(clean_markdown(result))
        return

    if intent == "fact":
        msg = await update.message.reply_text("🧠 Шукаю цікавий факт...")
        await msg.edit_text(clean_markdown(await get_fact()))
        return

    if intent == "joke":
        msg = await update.message.reply_text("😂 Придумую жарт...")
        await msg.edit_text(clean_markdown(await get_joke()))
        return

    if intent == "convert":
        try:
            parsed = await call_ai([{"role": "user", "content": (
                f"З цього тексту витягни дані для конвертації: '{user_text}'\n"
                "Відповідай ТІЛЬКИ у форматі JSON: "
                "{\"amount\": 100, \"from\": \"USD\", \"to\": \"UAH\"}\n"
                "Нічого більше не пиши."
            )}])
            parsed   = parsed.strip().replace("```json","").replace("```","")
            data     = json.loads(parsed)
            amount   = float(data["amount"])
            from_u   = data["from"]
            to_u     = data["to"]
            currencies = {
                "usd","eur","uah","gbp","pln","czk","chf",
                "jpy","cad","aud","rub","try","cny","sek","nok"
            }
            if from_u.lower() in currencies or to_u.lower() in currencies:
                result = await convert_currency(amount, from_u, to_u)
            else:
                result = convert_units(amount, from_u, to_u)
            await update.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Не вдалось конвертувати: {e}")
        return

    if intent == "search":
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
        asyncio.create_task(extract_and_save_memory(user_id, user_text, reply))
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

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎬 Аналізую відео (аудіо + кадри)...")
    try:
        video   = update.message.video or update.message.video_note
        caption = update.message.caption or "Що відбувається у цьому відео? Опиши детально."

        if video.file_size > 20 * 1024 * 1024:  # 20MB ліміт
            await msg.edit_text("❌ Відео занадто велике. Максимум 20MB.")
            return

        file        = await ctx.bot.get_file(video.file_id)
        video_bytes = bytes(await file.download_as_bytearray())
        result      = await analyze_video(video_bytes, caption)
        await msg.edit_text(clean_markdown(result))
    except Exception as e:
        await msg.edit_text(f"Помилка при аналізі відео: {e}")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc      = update.message.document
    fname    = doc.file_name.lower()
    user_id  = update.message.from_user.id
    caption  = update.message.caption or "Стисло підсумуй цей документ українською мовою."

    if fname.endswith(".pdf"):
        msg = await update.message.reply_text("📄 Читаю PDF...")
        file_bytes = bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray())
        text = extract_pdf_text(file_bytes)
    elif fname.endswith((".xlsx", ".xls")):
        msg = await update.message.reply_text("📊 Читаю Excel...")
        file_bytes = bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray())
        text = extract_excel_text(file_bytes)
    elif fname.endswith((".docx", ".doc")):
        msg = await update.message.reply_text("📝 Читаю Word...")
        file_bytes = bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray())
        text = extract_word_text(file_bytes)
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

        intent = await detect_intent(text)

        if intent == "reminder":
            try:
                delay_min, remind_text = await do_reminder(update, ctx, text)
                await msg.edit_text(f"🎤 Ти сказав: {text}\n\n✅ Нагадаю через {delay_min} хв: {remind_text}")
            except Exception as e:
                await msg.edit_text(f"Не вдалось встановити нагадування: {e}")
            return

        if intent == "image":
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
    # Відновлюємо режими користувачів після рестарту
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
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("mode",       mode_cmd))
    app.add_handler(CommandHandler("memory",     memory_cmd))
    app.add_handler(CommandHandler("forget",     forget_cmd))
    app.add_handler(CommandHandler("convert",    convert_cmd))
    app.add_handler(CommandHandler("story",      story_cmd))
    app.add_handler(CommandHandler("rpg",        rpg_cmd))
    app.add_handler(CommandHandler("horoscope",  horoscope_cmd))
    app.add_handler(CommandHandler("fact",       fact_cmd))
    app.add_handler(CommandHandler("joke",       joke_cmd))
    app.add_handler(CommandHandler("reset",      reset))
    app.add_handler(CommandHandler("search", handle_search))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("remind", handle_remind))
    app.add_handler(CommandHandler("image",  handle_image))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    app.add_handler(MessageHandler(filters.VOICE,        handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.Document.PDF | filters.Document.MimeType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") | filters.Document.MimeType("application/vnd.openxmlformats-officedocument.wordprocessingml.document") | filters.Document.MimeType("application/msword") | filters.Document.MimeType("application/vnd.ms-excel"), handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущено!")
    app.run_polling()
