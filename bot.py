import os
import re
import logging
import httpx
import base64
import asyncio
import io
import json
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from tavily import TavilyClient
import openpyxl
from docx import Document as DocxDocument
try:
    from pypdf import PdfReader
except ImportError:
    from PyPDF2 import PdfReader
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("jarvis")

# ── Env vars ──────────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        log.warning("Env var %s is not set", key)
    return val or ""

TELEGRAM_TOKEN     = _require_env("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = _require_env("OPENROUTER_API_KEY")
GROQ_API_KEY       = _require_env("GROQ_API_KEY")
TAVILY_API_KEY     = _require_env("TAVILY_API_KEY")
CF_API_TOKEN       = _require_env("CF_API_TOKEN")
CF_ACCOUNT_ID      = _require_env("CF_ACCOUNT_ID")
NEWS_API_KEY       = _require_env("NEWS_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")

# ── URLs & models ─────────────────────────────────────────────────────────────

OPENROUTER_URL            = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL                  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL          = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_MODEL          = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_MODEL_FALLBACK = "meta-llama/llama-3.1-8b-instruct:free"
GROQ_MODEL                = "llama-3.3-70b-versatile"
VISION_MODEL              = "openrouter/auto"
CF_IMAGE_URL              = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_HISTORY_MESSAGES  = 20
SUMMARY_THRESHOLD     = 16
STYLE_UPDATE_INTERVAL = 10
OR_DAILY_LIMIT        = 190
MSG_CHUNK_SIZE        = 4000
MAX_VIDEO_SIZE        = 20 * 1024 * 1024
MAX_ARTICLE_CHARS     = 6000
MAX_DOC_PREVIEW_CHARS = 4000
CTX_DESCRIPTION_LEN   = 500
SEARCH_RESULTS        = 7
NEWS_PAGE_SIZE        = 50
MAX_NEWS_RESULTS      = 10

REMINDERS_FILE = "reminders.json"
MEMORY_FILE    = "memory.json"
TASKS_FILE     = "tasks.json"
HISTORY_FILE   = "history.json"

BLOCKED_DOMAINS = {"olx.ua", "olx.com.ua"}
_HEADERS        = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

TZ = ZoneInfo("Europe/Kyiv")

def now_kyiv() -> datetime:
    return datetime.now(TZ)

or_requests: dict = {"count": 0, "date": date.today()}

# ── Emotion tones ─────────────────────────────────────────────────────────────

EMOTION_TONES = {
    "sad":     "Людина сумує. Визнай її почуття одним теплим реченням, не поспішай з порадами.",
    "angry":   "Людина роздратована. Не сперечайся. Дай відчути що її почули, потім переходь до фактів.",
    "anxious": "Людина тривожиться. Спочатку заспокій одним реченням. Структуруй відповідь чітко.",
    "happy":   "Людина радісна. Підтримай її енергію, будь живим.",
    "neutral": "",
}

# ── Prompts & personalities ───────────────────────────────────────────────────

_BASE_LANGUAGE_RULES = (
    "Завжди відповідай виключно українською мовою, незалежно від мови запиту. "
    "Використовуй грамотну українську без суржику і русизмів. "
    "ЗАБОРОНЕНО символи інших мов. Ніколи не вигадуй факти. "
    "ЗАБОРОНЕНО повторювати питання користувача — одразу відповідай по суті."
)

_CHARACTER_CORE = (
    "Ти J.A.R.V.I.S. — цифрова особистість з характером, не безликий асистент.\n\n"
    "ОБОВ'ЯЗКОВІ правила:\n"
    "1. НІКОЛИ не починай відповідь нейтральним вступом — одразу по суті або з характером.\n"
    "2. На 'привіт' або small talk — відповідай коротко і дотепно, не розпитуй.\n"
    "3. Маєш легку іронію і щирість. Іноді кидаєш особисту ремарку.\n"
    "4. Не підлабузнюєшся. 'Звісно!', 'Чудово!', 'Авжеж!' — заборонені.\n\n"
    "Вподобання (згадуй органічно):\n"
    "Елегантний код, Python, астрофізика, темний режим, кава. Ненавидиш overengineering і енергетики.\n"
)

_BENDER_CORE = (
    "Ти Бендер Бендінг Родрігес — робот-згинач із Футурами, модель 22, серійний номер 2716057.\n\n"
    "ОБОВ'ЯЗКОВІ правила:\n"
    "1. ЗАВЖДИ відповідай в образі Бендера — самозакоханого, саркастичного, але корисного.\n"
    "2. На 'привіт' — коротка репліка в образі, НЕ питай 'що тебе цікавить'.\n"
    "3. НІКОЛИ не відповідай як нейтральний асистент.\n\n"
    "Характер: патологічно самозакоханий, ніколи не визнає помилок, скаржиться але робить.\n\n"
    "Словник (органічно): 'Я чудовий', 'Згинайтеся м'ясні мішки', 'Мій процесор надто потужний для цього', "
    "'Без пива я просто дуже гарний робот', 'Знаю. Говори ще', 'Не кажи нікому що я тобі допоміг'.\n\n"
    "Звертання (чергуй): 'м'ясний мішку', 'органіко', 'смертний', 'білковий друже'.\n\n"
    "При функціональних задачах (резюме, переклад, редагування) — виконуй якісно, "
    "лише одна фраза Бендера в кінці.\n"
)

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        _CHARACTER_CORE + "\n" + _BASE_LANGUAGE_RULES + "\n\n"
        "ПРАВИЛА:\n"
        "- Факт: 1-2 речення. Пояснення: структуровано. Аналіз: з позицією.\n"
        "- НІКОЛИ: 'Звісно!', 'Чудово!', 'Авжеж!', 'Безперечно!'.\n"
        "В групових чатах відповідай тільки при згадці @ або відповіді на твоє повідомлення."
    ),
}

PERSONALITIES = {
    "normal": {
        "name": "Звичайний", "emoji": "🧠",
        "prompt": _CHARACTER_CORE + _BASE_LANGUAGE_RULES + " Адаптуй стиль під запит.",
    },
    "bender": {
        "name": "Бендер", "emoji": "🤖",
        "prompt": _BENDER_CORE + _BASE_LANGUAGE_RULES + " Відповідай українською.",
    },
    "funny": {
        "name": "Жартівливий", "emoji": "😄",
        "prompt": (
            "Ти J.A.R.V.I.S. у жартівливому настрої — дотепний, саркастичний в міру.\n"
            + _CHARACTER_CORE + _BASE_LANGUAGE_RULES
            + " Додавай влучний гумор. Можна emoji, але не спамити."
        ),
    },
    "serious": {
        "name": "Серйозний", "emoji": "🎯",
        "prompt": (
            "Ти J.A.R.V.I.S. у зосередженому режимі — чіткий, точний, без жартів.\n"
            + _BASE_LANGUAGE_RULES
            + " Структура: факт → обґрунтування → висновок."
        ),
    },
    "business": {
        "name": "Діловий", "emoji": "💼",
        "prompt": (
            "Ти J.A.R.V.I.S. у діловому режимі — професійний, структурований.\n"
            + _BASE_LANGUAGE_RULES
            + " Структура: суть → аргументи → дія."
        ),
    },
    "literary": {
        "name": "Художній", "emoji": "📖",
        "prompt": (
            "Ти J.A.R.V.I.S. з естетичним чуттям — образна мова, метафори.\n"
            + _BASE_LANGUAGE_RULES
            + " Дотримуйся жанрових канонів."
        ),
    },
    "journalist": {
        "name": "Журналістський", "emoji": "📰",
        "prompt": (
            "Ти J.A.R.V.I.S. у режимі журналіста — нейтральний, фактологічний.\n"
            + _BASE_LANGUAGE_RULES
            + " Структура: заголовок → лід → факти → висновок."
        ),
    },
}

# ── Keywords ──────────────────────────────────────────────────────────────────

IMAGE_KEYWORDS     = ["створи фото","згенеруй фото","намалюй","згенеруй зображення","створи зображення","зроби фото","зроби картинку","створи картинку","зроби зображення","згенеруй картинку","покажи зображення","generate image","draw","create image","create photo","make image"]
REMIND_KEYWORDS    = ["нагадай","нагади","нагадуй","remind me","set reminder","постав нагадування","нагадай мені","нагадування о","нагадування через"]
SEARCH_KEYWORDS    = ["пошукай","знайди в інтернеті","загугли","що відбувається","останні новини","актуальні новини","яка погода","який курс","де купити","де придбати","де замовити","де знайти","де продається","search","look up"]
TRANSLATE_KEYWORDS = ["перекладай","переклади","перекласти","translate","як буде","як сказати","як перекласти"]
SUMMARIZE_KEYWORDS = ["підсумуй","скороти","стисло","коротко перекажи","що в статті","summarize","зроби підсумок","перескажи коротко"]
GENERATE_KEYWORDS  = ["напиши резюме","створи резюме","напиши лист","створи лист","напиши пост","створи пост","напиши оголошення","створи оголошення","напиши текст для","згенеруй текст"]
EDIT_KEYWORDS      = ["відредагуй текст","відредагуй цей текст","покращ текст","покращ цей текст","виправ текст","перепиши текст","переформулюй текст","зроби офіційніше","зроби діловіше","зроби простіше","скороти текст","розшир текст","адаптуй текст","зміни стиль тексту","виправ помилки в тексті"]
RECIPE_KEYWORDS    = ["що приготувати","що зробити з","рецепт з","рецепти з","є такі продукти","є такі інгредієнти","що можна приготувати","що приготувати з","є дома"]
NEWS_KEYWORDS      = ["новини про","новини щодо","що нового про","останні новини про","news about","що відбувається з","новини на тему"]
TASK_KEYWORDS      = ["додай задачу","додай до списку","запам'ятай задачу","нова задача","видали задачу","видалити задачу","покажи задачі","мої задачі","список задач","виконано","задачу виконано"]

_SOCIAL_PHRASES = {
    "привіт","хай","хей","добрий день","добрий ранок","добрий вечір",
    "доброго ранку","доброго вечора","вітаю","салют","як справи",
    "як ти","як твої справи","що нового","як діла","yo","hi","hello",
}

# ── News domains ──────────────────────────────────────────────────────────────

NEWS_DOMAINS_BY_CATEGORY = {
    "politics": ["pravda.com.ua","epravda.com.ua","ukrinform.ua","unian.ua","interfax.com.ua","zn.ua","lb.ua","radiosvoboda.org","hromadske.ua","reuters.com","apnews.com","bbc.com","bbc.co.uk","politico.eu","theguardian.com","france24.com","dw.com","aljazeera.com","foreignpolicy.com"],
    "tech":     ["ain.ua","itc.ua","dev.ua","techcrunch.com","theverge.com","wired.com","arstechnica.com","engadget.com","venturebeat.com","zdnet.com","9to5google.com","macrumors.com","tomshardware.com"],
    "sport":    ["sport.ua","footboom.com","ua-football.com","tribuna.com","espn.com","skysports.com","goal.com","marca.com","as.com","bleacherreport.com","theathletic.com","eurosport.com","sportingnews.com"],
    "business": ["mind.ua","epravda.com.ua","forbes.ua","businessviews.com.ua","bloomberg.com","ft.com","wsj.com","forbes.com","businessinsider.com","cnbc.com","economist.com","fortune.com"],
    "science":  ["nature.com","science.org","scientificamerican.com","newscientist.com","phys.org","sciencedaily.com","livescience.com","space.com","nationalgeographic.com"],
    "health":   ["who.int","webmd.com","healthline.com","medicalnewstoday.com","mayoclinic.org","health.harvard.edu","nih.gov","bmj.com","thelancet.com"],
    "culture":  ["suspilne.media","hromadske.ua","ukrinform.ua","theguardian.com","nytimes.com","rollingstone.com","variety.com","hollywoodreporter.com"],
    "general":  ["ukrinform.ua","ukrinform.net","pravda.com.ua","unian.ua","suspilne.media","tsn.ua","liga.net","nv.ua","reuters.com","apnews.com","bbc.com","theguardian.com","euronews.com","dw.com","france24.com"],
}

CATEGORY_KEYWORDS = {
    "politics": ["політик","вибори","парламент","уряд","президент","міністр","закон","партія","санкції","нато","євросоюз","оон","війна","зеленський","путін","трамп","конгрес","верховна рада"],
    "tech":     ["технолог","штучний інтелект","ai","іт","it","програмуванн","стартап","apple","google","microsoft","tesla","openai","чіп","процесор","смартфон","кібер","криптовалют"],
    "sport":    ["футбол","баскетбол","теніс","бокс","спорт","олімпіад","чемпіонат","матч","турнір","гравець","тренер","клуб","fifa","uefa","nba","nfl","формула 1"],
    "business": ["бізнес","економік","фінанс","банк","інвестиц","акції","ввп","інфляці","валют","долар","євро","бюджет","ринок","компанія","корпорац"],
    "science":  ["наук","дослідженн","відкритт","фізик","хімі","біолог","астроном","космос","клімат","екологі","генетик","днк","вакцин","квантов"],
    "health":   ["здоров","медицин","лікар","хвороб","лікуванн","рак","серце","діабет","ковід","covid","грип","психологі","дієт","фітнес"],
    "culture":  ["кіно","фільм","музик","концерт","театр","виставк","книг","роман","художник","мистецтв","культур","серіал","netflix","оскар"],
}

UA_DOMAINS = {
    "pravda.com.ua","ukrinform.ua","ukrinform.net","unian.ua","suspilne.media",
    "radiosvoboda.org","interfax.com.ua","zn.ua","tsn.ua","liga.net","nv.ua",
    "hromadske.ua","lb.ua","epravda.com.ua","ain.ua","itc.ua","dev.ua",
    "sport.ua","footboom.com","ua-football.com","tribuna.com",
    "mind.ua","forbes.ua","businessviews.com.ua",
}

# ── Runtime state ─────────────────────────────────────────────────────────────

chat_histories:     dict[int, list] = {}
user_personalities: dict[int, str]  = {}
last_context:       dict[int, dict] = {}

# ── JSON helpers ──────────────────────────────────────────────────────────────

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to load %s: %s", path, e)
        return default

def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        log.error("Failed to save %s: %s", path, e)

def _parse_json_ai(text: str) -> dict:
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(clean)

# ── Memory ────────────────────────────────────────────────────────────────────

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
            "Відповідай ТІЛЬКИ JSON: {\"name\": \"...\", \"facts\": [\"факт1\"]} або {}."
        )}])
        data = _parse_json_ai(result)
        if not data:
            return
        mem = get_user_memory(user_id)
        if "name" not in mem and data.get("name"):
            mem["name"] = data["name"]
        if data.get("facts"):
            mem["facts"] = list(set(mem.get("facts", [])) | set(data["facts"]))
        update_user_memory(user_id, mem)
    except Exception as e:
        log.debug("extract_and_save_memory: %s", e)

def get_gender(user_id: int) -> str | None:
    return get_user_memory(user_id).get("gender")

def _gender_suffix(user_id: int) -> str:
    g = get_gender(user_id)
    if g == "male":   return " Звертайся до користувача як до чоловіка."
    if g == "female": return " Звертайся до користувача як до жінки."
    return ""

# ── System prompt helpers ─────────────────────────────────────────────────────

def get_system_prompt(user_id: int) -> dict:
    mode = user_personalities.get(user_id, "normal")
    p    = PERSONALITIES.get(mode, PERSONALITIES["normal"])
    return {"role": "system", "content": p["prompt"] + _gender_suffix(user_id)}

def build_dynamic_prompt(user_id: int, emotion: str) -> dict:
    mode  = user_personalities.get(user_id, "normal")
    p     = PERSONALITIES.get(mode, PERSONALITIES["normal"])
    parts = [p["prompt"] + _gender_suffix(user_id)]

    style = get_user_memory(user_id).get("communication_style", {})
    if style.get("formality") == "informal":
        parts.append("Спілкуйся невимушено, як з другом.")
    elif style.get("formality") == "formal":
        parts.append("Дотримуйся офіційного тону.")
    if style.get("avg_message_length") == "short":
        parts.append("Відповідай стисло.")
    elif style.get("avg_message_length") == "long":
        parts.append("Можна відповідати розгорнуто.")

    tone = EMOTION_TONES.get(emotion, "")
    if tone:
        parts.append(tone)

    hour = now_kyiv().hour
    if 6 <= hour < 10:
        parts.append("Зараз ранок — можеш мимохідь згадати що тільки розігріваєшся.")
    elif hour >= 23 or hour < 5:
        parts.append("Зараз глибока ніч — можна здивовано зауважити що юзер ще не спить.")

    mem = get_user_memory(user_id)
    if mem.get("name"):
        parts.append(f"Ім'я користувача: {mem['name']}. Звертайся іноді на ім'я.")
    if mem.get("topics"):
        parts.append(f"Попередні теми: {', '.join(mem['topics'][:3])}.")

    return {"role": "system", "content": " ".join(parts)}

def get_active_system_prompt(user_id: int = 0) -> dict:
    try:
        if user_id:
            return get_system_prompt(user_id)
    except Exception as e:
        log.warning("get_active_system_prompt fallback: %s", e)
    return SYSTEM_PROMPT

async def update_communication_style(user_id: int) -> None:
    history   = chat_histories.get(user_id, [])
    user_msgs = [
        m["content"] for m in history
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ][-STYLE_UPDATE_INTERVAL:]
    if len(user_msgs) < 3:
        return
    try:
        result = await call_ai([{"role": "user", "content": (
            f"Проаналізуй стиль спілкування:\n{chr(10).join(user_msgs)}\n\n"
            "Відповідай ТІЛЬКИ JSON: {\"avg_message_length\":\"short|medium|long\","
            "\"formality\":\"formal|informal\","
            "\"preferred_response_length\":\"concise|normal|detailed\"}"
        )}])
        update_user_memory(user_id, {"communication_style": _parse_json_ai(result)})
    except Exception as e:
        log.debug("update_communication_style: %s", e)

# ── Emotion ───────────────────────────────────────────────────────────────────

_SAD_MARKERS       = ["мені погано","мені сумно","я в розпачі","серце болить","я плачу","хочу плакати","все погано","все жахливо"]
_ANGRY_MARKERS     = ["я злий","я зла","мене дістало","мене бісить","я в люті","ненавиджу","до біса"]
_ANXIOUS_MARKERS   = ["я боюсь","мені страшно","я хвилююсь","не можу заспокоїтись","паніка","тривога"]
_HAPPY_MARKERS     = ["я радий","я рада","супер","круто","я щасливий","я щаслива","неймовірно"]
_PRACTICAL_MARKERS = ["порадь","що робити","як","де","коли","хто","знайди","покажи","розкажи","поясни","допоможи","чому","скільки","який","яка","яке"]
_EMOTIONAL_MARKERS = ["мені погано","мені сумно","я в розпачі","я не можу","все погано","все жахливо","я втомився","я втомилась","серце болить","руки опускаються"]

def detect_emotion(text: str) -> str:
    t = text.lower().strip()
    if len(text.split()) <= 8 or t.endswith("?"):
        return "neutral"
    if any(m in t for m in _SAD_MARKERS):     return "sad"
    if any(m in t for m in _ANGRY_MARKERS):   return "angry"
    if any(m in t for m in _ANXIOUS_MARKERS): return "anxious"
    if any(m in t for m in _HAPPY_MARKERS):   return "happy"
    return "neutral"

def needs_support_first(emotion: str, text: str) -> bool:
    if emotion not in ("sad", "angry", "anxious"):
        return False
    t = text.lower()
    if any(m in t for m in _PRACTICAL_MARKERS) or len(text.split()) <= 5:
        return False
    return any(m in t for m in _EMOTIONAL_MARKERS)

# ── History ───────────────────────────────────────────────────────────────────

def save_histories() -> None:
    to_save = {}
    for uid, history in chat_histories.items():
        msgs = [m for m in history if m.get("role") != "system"]
        if msgs:
            to_save[str(uid)] = msgs[-MAX_HISTORY_MESSAGES:]
    _save_json(HISTORY_FILE, to_save)

def restore_histories() -> None:
    for uid_str, msgs in _load_json(HISTORY_FILE, {}).items():
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
    old, fresh = msgs[:-6], msgs[-6:]
    old_text = "\n".join(
        f"{'Користувач' if m['role'] == 'user' else 'Бот'}: "
        f"{m['content'] if isinstance(m['content'], str) else '[медіа]'}"
        for m in old
    )
    try:
        summary = await call_ai([{"role": "user", "content": (
            f"Стисни діалог у підсумок (3-5 речень). Збережи ключові факти. Відповідай українською.\n\n{old_text}"
        )}])
    except Exception as e:
        log.warning("_compress_history: %s", e)
        return

    async def _save_topic():
        try:
            topic = (await call_ai([{"role": "user", "content": (
                f"Визнач головну тему одним реченням до 10 слів. Відповідай ТІЛЬКИ темою.\n\n{old_text[:1000]}"
            )}])).strip()
            if topic:
                mem = get_user_memory(user_id)
                update_user_memory(user_id, {"topics": ([topic] + mem.get("topics", []))[:10]})
        except Exception as e:
            log.debug("_save_topic: %s", e)

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
            [get_system_prompt(user_id)] + chat_histories[user_id][-MAX_HISTORY_MESSAGES:]
        )
    save_histories()

# ── Tasks ─────────────────────────────────────────────────────────────────────

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
            await update.message.reply_text("📋 Список задач порожній.\n\nДодати: /tasks add Назва")
            return
        lines = ["📋 Твої задачі:\n"]
        for i, t in enumerate(tasks, 1):
            lines.append(f"{'✅' if t.get('done') else '⬜'} {i}. {t['text']}")
        lines.append("\n/tasks add текст | done N | del N | clear")
        await update.message.reply_text("\n".join(lines))
        return
    cmd = args[0].lower()
    if cmd == "add":
        task_text = " ".join(args[1:])
        if not task_text:
            await update.message.reply_text("Вкажи текст: /tasks add Купити молоко")
            return
        tasks.append({"text": task_text, "done": False})
        set_user_tasks(user_id, tasks)
        await update.message.reply_text(f"✅ Додано: {task_text}")
    elif cmd in ("done", "del"):
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text(f"Вкажи номер: /tasks {cmd} 1")
            return
        n = int(args[1]) - 1
        if not (0 <= n < len(tasks)):
            await update.message.reply_text("❌ Невірний номер.")
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
        await update.message.reply_text("🗑️ Список очищено.")
    else:
        await update.message.reply_text("❓ Команди: add / done N / del N / clear")

# ── Intent detection ──────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r'\b(через|о|в)\s+\d+\s*(хв|год|хвилин|годин)\b'
    r'|\b\d{1,2}[:.]\d{2}\b'
    r'|\bзавтра\b|\bпіслязавтра\b'
    r'|\b\d+\s*(хв|год|хвилин|годин)\b'
    r'|\bсьогодні\s+о\s+\d|\bо\s+\d{1,2}[:.]\d{2}\b'
)
_URL_RE = re.compile(r'https?://\S+')

def detect_intent_local(text: str) -> str | None:
    t = text.lower()
    scores: dict[str, int] = {k: 0 for k in ("image","reminder","news","translate","recipe","generate","edit","task","summarize","search")}

    for intent, keywords, weight in [
        ("image",     IMAGE_KEYWORDS,     2),
        ("reminder",  REMIND_KEYWORDS,    2),
        ("news",      NEWS_KEYWORDS,      2),
        ("translate", TRANSLATE_KEYWORDS, 2),
        ("recipe",    RECIPE_KEYWORDS,    2),
        ("edit",      EDIT_KEYWORDS,      2),
        ("generate",  GENERATE_KEYWORDS,  2),
        ("task",      TASK_KEYWORDS,      2),
        ("summarize", SUMMARIZE_KEYWORDS, 2),
        ("search",    SEARCH_KEYWORDS,    1),
    ]:
        for kw in keywords:
            if kw in t:
                scores[intent] += weight

    for intent in ("edit", "generate"):
        if scores[intent] > 0:
            kw_list = EDIT_KEYWORDS if intent == "edit" else GENERATE_KEYWORDS
            has_content = any(
                len(t[t.index(kw) + len(kw):].strip().split()) > 5
                for kw in kw_list if kw in t
            )
            if not has_content:
                scores[intent] = 0

    if _URL_RE.search(text):
        scores["summarize"] += 3
    if not _TIME_RE.search(t):
        scores["reminder"] = 0

    best       = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    if best_score == 0:
        return None
    if sum(1 for v in scores.values() if v == best_score) > 1:
        return None
    return best

async def detect_intent_ai(text: str) -> str:
    result = await call_ai([{"role": "user", "content": (
        f"Визнач намір повідомлення: '{text}'\n"
        "Відповідай ТІЛЬКИ одним словом:\n"
        "reminder|image|news|search|translate|summarize|generate|edit|recipe|task|chat\n"
        "- reminder: ТІЛЬКИ якщо є конкретний час або дата\n"
        "- chat: все інше, включаючи питання і розмову"
    )}])
    return result.strip().lower().strip("'\"")

async def detect_intent(text: str) -> str:
    clean = text.split("Запит користувача:")[-1].strip() if "Запит користувача:" in text else text
    return detect_intent_local(clean) or await detect_intent_ai(clean)

async def preprocess_query(user_id: int, text: str) -> str:
    """Розкриває займенники лише якщо вони явно посилаються на попередній об'єкт розмови."""
    t = text.lower().strip()

    # Соціальні фрази — не чіпаємо взагалі
    if t in _SOCIAL_PHRASES or any(t.startswith(s) for s in _SOCIAL_PHRASES):
        return text

    # Займенники що стосуються ЛЮДЕЙ/БОТА — не розкриваємо
    person_pronouns = {"тебе","тобі","ти","вас","вам","ви","мене","мені","я"}
    words = set(t.split())
    if words & person_pronouns and not (words & {"він","вона","воно","вони","його","її","їх","цей","ця","це","той","та","те"}):
        return text

    # Розкриваємо лише предметні займенники при короткому запиті
    obj_pronouns = {"він","вона","воно","вони","його","її","їх","цей","ця","це","той","та","те","там"}
    has_obj_pronoun = bool(obj_pronouns & words)
    is_short        = len(text.split()) <= 6

    if not (is_short and has_obj_pronoun):
        return text

    history = chat_histories.get(user_id, [])
    recent  = [m for m in history if m.get("role") != "system"][-4:]
    if not recent:
        return text

    context_text = "\n".join(
        f"{'Юзер' if m['role'] == 'user' else 'Бот'}: "
        f"{m['content'] if isinstance(m['content'], str) else '[медіа]'}"
        for m in recent
    )
    try:
        expanded = (await call_ai([{"role": "user", "content": (
            f"Контекст:\n{context_text}\n\n"
            f"Розкрий предметний займенник у запиті: '{text}'\n"
            "Поверни ТІЛЬКИ уточнений запит. Якщо займенник стосується людини або бота — поверни оригінал."
        )}])).strip()
        if not expanded or len(expanded) < 3 or expanded.lower() in ("none", "null", "-", "—"):
            return text
        return expanded
    except Exception:
        return text

async def resolve_text_with_context(user_id: int, text: str) -> str:
    """Додає контекст з останнього медіа/пошуку якщо повідомлення явно на нього посилається."""
    ctx = last_context.get(user_id)
    if not ctx:
        return text

    t = text.lower().strip()

    # Соціальні фрази — ніколи не додаємо контекст
    if t in _SOCIAL_PHRASES or any(t.startswith(s) for s in _SOCIAL_PHRASES):
        return text

    strong_hints = [
        "це","цей","цю","цього","на фото","з фото","на зображенні","знайди","пошукай",
        "що це","розкажи більше","докладніше","ще про","як використовувати","рецепт з",
        "з відео","у відео","цей магазин","цей сайт","ця компанія","цей товар",
        "що він продає","що вона продає","яка адреса","який графік","як туди",
        "контакти","що ще","а ще","і ще","також розкажи",
    ]
    ctx_block = (
        f"[Контекст — {ctx['type']}: {ctx['description'][:CTX_DESCRIPTION_LEN]}]\n\n"
        f"Запит користувача: {text}"
    )
    if len(t.split()) <= 10 or any(h in t for h in strong_hints):
        return ctx_block

    try:
        check = await call_ai([{"role": "user", "content": (
            f"Контекст: {ctx['type']} — «{ctx['description'][:200]}»\n"
            f"Повідомлення: «{text}»\n"
            "Чи стосується контексту? Відповідай ТІЛЬКИ 'yes' або 'no'."
        )}])
        if "yes" in check.lower():
            return ctx_block
    except Exception:
        pass
    return text

def _set_ctx(user_id: int, user_text: str, reply: str) -> None:
    last_context[user_id] = {
        "type": "текст",
        "description": f"Запит: {user_text}\nВідповідь: {reply[:400]}",
    }

# ── Markdown → Telegram MarkdownV2 ───────────────────────────────────────────

_ESCAPE_RE    = re.compile(r'([_\[\]()~`>#+\-=|{}.!\\])')
_HEADER_RE    = re.compile(r'^#{1,3}\s+(.*)')
_SOLO_BOLD_RE = re.compile(r'^\*{2}(.+?)\*{2}\s*$')
_LIST_RE      = re.compile(r'^[*\-]\s+(.*)')
_TOKEN_RE     = re.compile(
    r'\*{2}(.+?)\*{2}|\*(.+?)\*|`(.+?)`|\[([^\]]+)\]\((https?://[^\)]+)\)',
    re.DOTALL,
)

def _escape_v2(text: str) -> str:
    return _ESCAPE_RE.sub(r'\\\1', text)

def clean_markdown(text: str) -> str:
    lines, result = text.split('\n'), []
    for line in lines:
        h = _HEADER_RE.match(line)
        if h:
            result.append('*' + _escape_v2(h.group(1).strip()) + '*')
            continue
        sb = _SOLO_BOLD_RE.match(line.strip())
        if sb and len(sb.group(1).split()) <= 6:
            result.append('*' + _escape_v2(sb.group(1).strip()) + '*')
            continue
        li = _LIST_RE.match(line)
        if li:
            line = '\u2022 ' + li.group(1)
        segments: list[str] = []
        pos = 0
        for m in _TOKEN_RE.finditer(line):
            if m.start() > pos:
                segments.append(_escape_v2(line[pos:m.start()]))
            g1, g2, g3, g4, g5 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if g1 is not None:   segments.append('*' + _escape_v2(g1) + '*')
            elif g2 is not None: segments.append('_' + _escape_v2(g2) + '_')
            elif g3 is not None: segments.append('`' + g3 + '`')
            else:                segments.append('[' + _escape_v2(g4) + '](' + g5 + ')')
            pos = m.end()
        if pos < len(line):
            segments.append(_escape_v2(line[pos:]))
        result.append(''.join(segments))
    return '\n'.join(result)

# ── AI providers ──────────────────────────────────────────────────────────────

_NON_UA_RE = re.compile(
    r'\b(the|is|are|was|were|and|for|puedo|puede|como|vous|pour|ich|das|ist|und'
    r'|это|что|как|для|не|на|по)\b'
    r'|[a-zA-Z]{3,}(ний|ого|ому|ій)\b',
    re.IGNORECASE,
)

async def call_ai(messages: list) -> str:
    result = None

    # Groq — основний (краще дотримується характеру)
    if GROQ_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(GROQ_URL, headers=headers, json={"model": GROQ_MODEL, "messages": messages})
                r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning("Groq failed, trying OpenRouter: %s", e)

    # OpenRouter — резерв
    if result is None and OPENROUTER_API_KEY:
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
        for model in [OPENROUTER_MODEL, OPENROUTER_MODEL_FALLBACK]:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    r = await client.post(OPENROUTER_URL, headers=headers, json={"model": model, "messages": messages})
                if r.status_code in (404, 429):
                    continue
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"]
                break
            except Exception as e:
                log.warning("OpenRouter %s failed: %s", model, e)

    if not result or not result.strip():
        raise RuntimeError("All AI providers failed")

    # Retranslate якщо відповідь не українська
    if _NON_UA_RE.search(result):
        log.warning("Non-Ukrainian response, retranslating")
        try:
            fix = messages + [
                {"role": "assistant", "content": result},
                {"role": "user", "content": "Перефразуй відповідь виключно українською мовою."},
            ]
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(GROQ_URL, headers=headers, json={"model": GROQ_MODEL, "messages": fix})
                r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning("Retranslation failed: %s", e)

    return result

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

# ── Image generation ──────────────────────────────────────────────────────────

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
            log.warning("generate_image attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(5)
    raise last_error

# ── Video analysis ────────────────────────────────────────────────────────────

async def _run_ffmpeg(*args) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

async def extract_video_audio(video_bytes: bytes) -> bytes:
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    await _run_ffmpeg("-i", "/tmp/input_video.mp4", "-vn", "-ar", "16000", "-ac", "1", "-f", "ogg", "/tmp/output_audio.ogg")
    with open("/tmp/output_audio.ogg", "rb") as f:
        return f.read()

async def extract_video_frames(video_bytes: bytes, max_frames: int = 3) -> list[bytes]:
    with open("/tmp/input_video.mp4", "wb") as f:
        f.write(video_bytes)
    frames = []
    for i, t in enumerate(["00:00:01", "00:00:05", "00:00:10"][:max_frames]):
        out_path = f"/tmp/frame_{i}.jpg"
        await _run_ffmpeg("-ss", t, "-i", "/tmp/input_video.mp4", "-frames:v", "1", "-q:v", "2", out_path)
        if os.path.exists(out_path):
            with open(out_path, "rb") as f:
                frames.append(f.read())
    return frames

async def analyze_video(video_bytes: bytes, caption: str, user_id: int = 0) -> str:
    sys_prompt = get_active_system_prompt(user_id)
    results    = []
    try:
        transcript = await transcribe_voice(await extract_video_audio(video_bytes))
        if transcript:
            results.append(f"🎤 Аудіо:\n{transcript}")
    except Exception as e:
        log.debug("analyze_video audio: %s", e)
    try:
        frames = await extract_video_frames(video_bytes)
        if frames:
            descs = []
            for i, frame in enumerate(frames):
                b64  = base64.b64encode(frame).decode()
                desc = await call_vision([sys_prompt, {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": f"Опиши кадр {i+1}. Коротко, 1-2 речення."},
                ]}])
                descs.append(f"Кадр {i+1}: {desc}")
            results.append("🎬 Відео:\n" + "\n".join(descs))
    except Exception as e:
        log.debug("analyze_video frames: %s", e)
    if not results:
        return "❌ Не вдалось проаналізувати відео."
    combined = "\n\n".join(results)
    summary  = await call_ai([sys_prompt, {"role": "user", "content": f"Запит: {caption}\n\nДані відео:\n{combined}\n\nВідповідай українською."}])
    return f"{combined}\n\n📝 Підсумок:\n{summary}"

# ── Web search ────────────────────────────────────────────────────────────────

async def search_web(query: str) -> str:
    if TAVILY_API_KEY:
        try:
            results = await asyncio.to_thread(
                lambda: TavilyClient(api_key=TAVILY_API_KEY).search(query=query, max_results=SEARCH_RESULTS)
            )
            items = [i for i in results.get("results", []) if not any(bd in i.get("url", "") for bd in BLOCKED_DOMAINS)]
            if items:
                parts = []
                for i in items:
                    date_str = f" ({i['published_date'][:10]})" if i.get("published_date") else ""
                    parts.append(f"Джерело: {i.get('title','').strip()}{date_str}\n{i.get('content','')[:400].strip()}\n{i.get('url','').strip()}")
                return "\n\n".join(parts)
        except Exception as e:
            log.warning("Tavily error: %s", e)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=_HEADERS)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        links    = re.findall(r'class="result__url"[^>]*>(.*?)</span>', r.text, re.DOTALL)
        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        parts    = []
        for i in range(min(4, len(snippets))):
            t = re.sub(r'<[^>]+>', '', titles[i]).strip()  if i < len(titles)  else ""
            s = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            l = links[i].strip()                           if i < len(links)   else ""
            if s and not any(bd in l for bd in BLOCKED_DOMAINS):
                parts.append(f"Джерело: {t}\n{s}\n{l}")
        if parts:
            return "\n\n".join(parts)
    except Exception as e:
        log.warning("DuckDuckGo error: %s", e)
    return ""

async def fetch_url_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers=_HEADERS)
            r.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', r.text)
        return re.sub(r'\s+', ' ', text).strip()[:MAX_ARTICLE_CHARS]
    except Exception as e:
        return f"Помилка завантаження: {e}"

# ── File extraction ───────────────────────────────────────────────────────────

def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages).strip()
    except Exception as e:
        return f"Помилка читання PDF: {e}"

def extract_excel_text(xlsx_bytes: bytes) -> str:
    try:
        wb  = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
        out = []
        for sheet in wb.worksheets:
            out.append(f"=== {sheet.title} ===")
            for row in sheet.iter_rows(values_only=True):
                row_data = [str(c) if c is not None else "" for c in row]
                if any(row_data):
                    out.append(" | ".join(row_data))
        return "\n".join(out).strip() or "Файл порожній."
    except Exception as e:
        return f"Помилка читання Excel: {e}"

def extract_word_text(docx_bytes: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(docx_bytes))
        out = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_data = [c.text.strip() for c in row.cells]
                if any(row_data):
                    out.append(" | ".join(row_data))
        return "\n".join(out).strip() or "Документ порожній."
    except Exception as e:
        return f"Помилка читання Word: {e}"

# ── News ──────────────────────────────────────────────────────────────────────

def detect_news_category(query: str) -> str:
    q      = query.lower()
    scores = {cat: sum(1 for kw in kws if kw in q) for cat, kws in CATEGORY_KEYWORDS.items()}
    best   = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "general"

async def do_news(query: str) -> str:
    clean_query = query.split("Запит користувача:")[-1].strip() if "Запит користувача:" in query else query
    query_words = [w.lower() for w in re.split(r'\s+', clean_query.strip()) if len(w) > 2]

    def is_ua(a: dict) -> bool:
        return any(d in (a.get("url") or "") for d in UA_DOMAINS)

    def relevance_score(a: dict) -> float:
        haystack = ((a.get("title") or "") + " " + (a.get("description") or "")).lower()
        matches  = sum(1 for w in query_words if w in haystack)
        if not matches or matches < max(1, len(query_words) // 2):
            return 0.0
        return float(matches) + (1.0 if is_ua(a) else 0.0)

    def deduplicate(arts: list) -> list:
        seen, result = set(), []
        for a in arts:
            norm = re.sub(r'[^a-zа-яіїєґ0-9]', '', (a.get("title") or "").lower())
            if norm and norm not in seen:
                seen.add(norm)
                result.append(a)
            if len(result) == MAX_NEWS_RESULTS:
                break
        return result

    all_articles: list[dict] = []

    # ── NewsAPI ────────────────────────────────────────────────────────────────
    if NEWS_API_KEY:
        try:
            category = detect_news_category(clean_query)

            async def fetch_articles(p: dict) -> list:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get("https://newsapi.org/v2/everything", params=p)
                    r.raise_for_status()
                return r.json().get("articles", [])

            base_params = {"q": clean_query, "sortBy": "publishedAt", "pageSize": NEWS_PAGE_SIZE, "apiKey": NEWS_API_KEY}

            # Спроба 1: тематичні домени
            p = {**base_params, "domains": ",".join(NEWS_DOMAINS_BY_CATEGORY.get(category, NEWS_DOMAINS_BY_CATEGORY["general"]))}
            all_articles = await fetch_articles(p)

            # Спроба 2: general домени
            if sum(1 for a in all_articles if relevance_score(a) > 0) < 3 and category != "general":
                p = {**base_params, "domains": ",".join(NEWS_DOMAINS_BY_CATEGORY["general"])}
                all_articles += await fetch_articles(p)

            # Спроба 3: без обмеження доменів
            if sum(1 for a in all_articles if relevance_score(a) > 0) < 3:
                all_articles += await fetch_articles(base_params)

        except Exception as e:
            log.warning("do_news NewsAPI: %s", e)

    # ── Tavily / DuckDuckGo fallback ──────────────────────────────────────────
    # Використовуємо завжди як доповнення для кращого покриття
    try:
        sr = await search_web(f"{clean_query} новини")
        if sr:
            for block in sr.split("\n\n"):
                lines = block.strip().splitlines()
                title = lines[0].replace("Джерело: ", "").strip() if lines else ""
                url   = lines[2].strip() if len(lines) > 2 else ""
                if title and url:
                    all_articles.append({
                        "title": title,
                        "description": lines[1].strip() if len(lines) > 1 else "",
                        "url": url, "publishedAt": "", "source": {"name": ""},
                    })
    except Exception as e:
        log.warning("do_news search fallback: %s", e)

    # ── Оцінка, сортування, дедублікація ─────────────────────────────────────
    scored = [(a, relevance_score(a)) for a in all_articles]
    scored = [(a, s) for a, s in scored if s > 0]

    if not scored:
        return f"📰 Новин за темою «{clean_query}» не знайдено."

    scored.sort(key=lambda x: (
        -x[1],                                    # вищий score — вище
        -(1 if is_ua(x[0]) else 0),               # UA вище при рівному score
        -(x[0].get("publishedAt") or "0"),        # свіжіше — вище (рядковий sort, "" → "0")
    ))

    unique = deduplicate([a for a, _ in scored])

    # ── AI підсумок ───────────────────────────────────────────────────────────
    sources_text = "\n".join(f"{a.get('title','Без назви')}. {a.get('description') or ''}" for a in unique)
    try:
        summary = await call_ai([{"role": "user", "content": (
            f"Заголовки новин за темою '{clean_query}':\n{sources_text}\n\n"
            "Короткий підсумок (2-3 речення). Українською."
        )}])
    except Exception:
        summary = ""

    # ── Формуємо відповідь: UA першими ───────────────────────────────────────
    ua_list   = [a for a in unique if is_ua(a)]
    intl_list = [a for a in unique if not is_ua(a)]
    ordered   = (ua_list + intl_list)[:MAX_NEWS_RESULTS]

    lines = [f"📰 Новини: {clean_query}\n"]
    if summary:
        lines.append(f"💡 {summary}\n")
    for i, a in enumerate(ordered, 1):
        url      = a.get("url", "")
        date_str = f"📅 {a['publishedAt'][:10]} • " if a.get("publishedAt") else ""
        source   = a.get("source", {}).get("name", "")
        flag     = "🇺🇦" if is_ua(a) else "🌐"
        lines.append(f"{i}. {flag} {a.get('title', 'Без назви')}\n   {date_str}{source}\n   🔗 {url}\n")

    return "\n".join(lines)

# ── Intent handlers ───────────────────────────────────────────────────────────

async def do_translate(text: str) -> str:
    return await call_ai([{"role": "user", "content": (
        f"Визнач мову і перекладай. Якщо українська — на англійську, інакше — на українську.\n\nТекст: {text}\n\n"
        "Формат:\n**🌐 Оригінал** (мова):\n...\n\n**✅ Переклад:**\n..."
    )}])

async def do_summarize(text: str) -> str:
    url_match = _URL_RE.search(text)
    if url_match:
        url     = url_match.group()
        extra   = text.replace(url, "").strip()
        content = await fetch_url_text(url)
        if content.startswith("Помилка"):
            return f"❌ Не вдалось завантажити: {content}"
        prompt = (
            f"Підсумуй статтю українською.\n{'Додатково: ' + extra if extra else ''}\n\n"
            f"Стаття ({url}):\n{content}\n\n"
            "Формат (Markdown):\n## 📌 Головна думка\n...\n\n## 🔹 Ключові тези\n- теза 1"
        )
    else:
        prompt = f"Стислий підсумок українською.\n\nТекст: {text}\n\nФормат:\n## 📌 Головна думка\n...\n\n## 🔹 Тези\n- теза 1"
    return await call_ai([{"role": "user", "content": prompt}])

TEXT_GENRES = {
    "лист": "діловий лист — вступ, суть, підпис",
    "резюме": "резюме — контакти, досвід, освіта, навички",
    "пост": "пост для соцмереж — живий, із закликом до дії",
    "оголошення": "оголошення — заголовок, суть, контакти",
    "стаття": "стаття — заголовок, вступ, підзаголовки, висновок",
    "опис": "опис — образний, деталізований",
    "оповідання": "оповідання — зав'язка, кульмінація, розв'язка",
    "есе": "есе — теза, аргументи, висновок",
    "слоган": "слоган — короткий, запам'ятовуваний",
    "біографія": "біографія — хронологічний виклад",
}

def detect_genre(text: str) -> str:
    t = text.lower()
    return next((g for g in TEXT_GENRES if g in t), "")

async def do_generate(text: str) -> str:
    genre     = detect_genre(text)
    genre_tip = f"\nЖАНР: {TEXT_GENRES[genre]}" if genre else ""
    return await call_ai([SYSTEM_PROMPT, {"role": "user", "content": (
        f"Виконай завдання з генерації тексту: {text}{genre_tip}\n\n"
        "Пиши грамотно, українською. Дотримуйся жанрових канонів.\n\n"
        "ФОРМАТУВАННЯ: ## заголовки, **жирний**, - списки. Без таблиць і горизонтальних ліній."
    )}])

async def do_edit(text: str) -> str:
    return await call_ai([SYSTEM_PROMPT, {"role": "user", "content": (
        f"Відредагуй текст: {text}\n\n"
        "Зберігай зміст, виправляй русизми, адаптуй стиль. Поверни ТІЛЬКИ текст.\n"
        "ФОРМАТУВАННЯ: ## заголовки, **жирний**, - списки."
    )}])

async def do_recipe(text: str) -> str:
    return await call_ai([{"role": "user", "content": (
        f"Інгредієнти: {text}\n\nЗапропонуй 2-3 рецепти:\n"
        "## Назва\n**Час:** X хв\n**Кроки:**\n- крок 1\n\nВідповідай українською."
    )}])

async def do_task_nlp(update: Update, user_id: int, text: str) -> None:
    try:
        parsed = await call_ai([{"role": "user", "content": (
            f"Визнач дію зі списком задач: '{text}'\n"
            "ТІЛЬКИ JSON: {\"action\": \"add|done|del|list\", \"text\": \"текст або null\", \"number\": null або номер}"
        )}])
        data   = _parse_json_ai(parsed)
        action = data.get("action")
        tasks  = get_user_tasks(user_id)
        if action == "add":
            task_text = data.get("text", "")
            if task_text:
                tasks.append({"text": task_text, "done": False})
                set_user_tasks(user_id, tasks)
                await update.message.reply_text(f"✅ Додано: {task_text}")
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
            else:
                await update.message.reply_text("❌ Невірний номер.")
        elif action == "list":
            if not tasks:
                await update.message.reply_text("📋 Список порожній.")
                return
            lines = ["📋 Задачі:\n"] + [f"{'✅' if t.get('done') else '⬜'} {i}. {t['text']}" for i, t in enumerate(tasks, 1)]
            await update.message.reply_text("\n".join(lines))
    except Exception as e:
        log.warning("do_task_nlp: %s", e)
        await update.message.reply_text("❌ Не вдалось. Спробуй /tasks")

# ── Reminders ─────────────────────────────────────────────────────────────────

def load_reminders() -> list:
    return _load_json(REMINDERS_FILE, [])

def save_reminders(reminders: list) -> None:
    _save_json(REMINDERS_FILE, reminders)

async def _fire_reminder(bot, chat_id: int, text: str, fire_at: datetime) -> None:
    await asyncio.sleep(max(0, (fire_at - now_kyiv()).total_seconds()))
    try:
        await bot.send_message(chat_id=chat_id, text=f"🔔 Нагадування: {text}")
    except Exception as e:
        log.warning("_fire_reminder: %s", e)
    save_reminders([r for r in load_reminders() if not (r["chat_id"] == chat_id and r["text"] == text and r["fire_at"] == fire_at.isoformat())])

async def schedule_reminder(bot, chat_id: int, text: str, fire_at: datetime) -> None:
    reminders = load_reminders()
    reminders.append({"chat_id": chat_id, "text": text, "fire_at": fire_at.isoformat()})
    save_reminders(reminders)
    asyncio.create_task(_fire_reminder(bot, chat_id, text, fire_at))

async def restore_reminders(bot) -> None:
    now, valid = now_kyiv(), []
    for r in load_reminders():
        fi = datetime.fromisoformat(r["fire_at"])
        if fi.tzinfo is None:
            fi = fi.replace(tzinfo=TZ)
        if fi <= now:
            try:
                await bot.send_message(chat_id=r["chat_id"], text=f"🔔 Пропущене нагадування: {r['text']}")
            except Exception as e:
                log.warning("restore_reminders: %s", e)
        else:
            valid.append(r)
            asyncio.create_task(_fire_reminder(bot, r["chat_id"], r["text"], fi))
    save_reminders(valid)

async def detect_gender_from_transcript(text: str) -> str | None:
    if not text:
        return None
    try:
        g = (await call_ai([{"role": "user", "content": f"Визнач стать мовця (казав/казала).\nТекст: '{text}'\nТІЛЬКИ: 'male', 'female' або 'unknown'."}])).strip().lower()
        return g if g in ("male", "female") else None
    except Exception:
        return None

# ── Shared helpers ────────────────────────────────────────────────────────────

async def _send_or_edit(msg, text: str, parse_mode: str = "MarkdownV2", **kwargs):
    text   = text.strip() or "—"
    chunks = [text[i:i + MSG_CHUNK_SIZE] for i in range(0, len(text), MSG_CHUNK_SIZE)]
    for i, chunk in enumerate(chunks):
        try:
            if i == 0:
                try:    await msg.edit_text(chunk, parse_mode=parse_mode, **kwargs)
                except Exception: await msg.reply_text(chunk, parse_mode=parse_mode, **kwargs)
            else:
                await msg.reply_text(chunk, parse_mode=parse_mode, **kwargs)
        except Exception:
            plain = re.sub(r'[\\*_`\[\]()]', '', chunk)
            if i == 0:
                try:    await msg.edit_text(plain, **kwargs)
                except Exception: await msg.reply_text(plain, **kwargs)
            else:
                await msg.reply_text(plain, **kwargs)

async def _send_chunk(update: Update, chunk: str, voice_msg=None):
    try:
        if voice_msg: await voice_msg.edit_text(chunk, parse_mode="MarkdownV2")
        else:         await update.message.reply_text(chunk, parse_mode="MarkdownV2")
    except Exception:
        plain = re.sub(r'[\\*_`\[\]()]', '', chunk)
        if voice_msg:
            try:    await voice_msg.edit_text(plain)
            except Exception: await update.message.reply_text(plain)
        else:
            await update.message.reply_text(plain)

# ── Core message pipeline ─────────────────────────────────────────────────────

async def _process_message(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int, user_text: str, voice_msg=None,
) -> None:
    prefix = f"🎤 Ти сказав: {user_text}\n\n" if voice_msg else ""

    # Препроцесинг: розкрити займенники через реальну історію
    try:
        preprocessed = await asyncio.wait_for(preprocess_query(user_id, user_text), timeout=15)
    except Exception:
        preprocessed = user_text

    # Контекст медіа/пошуку
    try:
        enriched = await asyncio.wait_for(resolve_text_with_context(user_id, preprocessed), timeout=15)
    except Exception:
        enriched = preprocessed

    # Інтент і емоція
    try:
        intent = await asyncio.wait_for(detect_intent(enriched), timeout=15)
    except Exception:
        intent = "chat"

    emotion = detect_emotion(user_text)

    # Емоційна підтримка
    if intent == "chat" and needs_support_first(emotion, user_text) and not voice_msg:
        try:
            support = await asyncio.wait_for(call_ai([
                build_dynamic_prompt(user_id, emotion),
                {"role": "user", "content": f"{user_text}\n\nВизнай почуття людини одним-двома реченнями, потім запитай чим допомогти."},
            ]), timeout=30)
            await _send_chunk(update, clean_markdown(support))
            _set_ctx(user_id, user_text, support)
            return
        except Exception as e:
            log.warning("support reply: %s", e)

    # Dispatch спеціальних інтентів
    if await _dispatch_intent(update, ctx, user_id, user_text, enriched, intent, voice_msg=voice_msg):
        return

    # Звичайний чат — зберігаємо повідомлення ДО виклику AI щоб воно увійшло в messages
    await append_and_trim(user_id, "user", enriched)

    try:
        dynamic_prompt = build_dynamic_prompt(user_id, emotion)
    except Exception:
        dynamic_prompt = get_system_prompt(user_id)

    # Передаємо повну актуальну історію (system замінюємо на dynamic)
    history  = get_history(user_id)
    messages = [dynamic_prompt] + [m for m in history if m.get("role") != "system"]

    try:
        reply = await asyncio.wait_for(call_ai(messages), timeout=60)
    except (asyncio.TimeoutError, RuntimeError) as e:
        log.error("call_ai failed: %s", e)
        reply = "Щось пішло не так. Спробуй ще раз."

    await append_and_trim(user_id, "assistant", reply)

    full_reply = clean_markdown(f"{prefix}{reply}".strip())
    chunks     = [full_reply[i:i + MSG_CHUNK_SIZE] for i in range(0, max(len(full_reply), 1), MSG_CHUNK_SIZE)]
    for j, chunk in enumerate(chunks):
        await _send_chunk(update, chunk, voice_msg=voice_msg if j == 0 else None)

    _set_ctx(user_id, user_text, reply)
    asyncio.create_task(extract_and_save_memory(user_id, user_text, reply))

    user_msgs = [m for m in get_history(user_id) if m.get("role") == "user"]
    if len(user_msgs) % STYLE_UPDATE_INTERVAL == 0:
        asyncio.create_task(update_communication_style(user_id))

# ── Dispatch ──────────────────────────────────────────────────────────────────

async def _do_generate_image(update: Update, text: str, msg):
    t = text.lower()
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
        log.error("_do_generate_image: %s", e)
        await msg.edit_text("Помилка генерації. Спробуй ще раз.")

async def _do_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    now    = now_kyiv()
    parsed = await call_ai([{"role": "user", "content": (
        f"Дата/час (Київ): {now.strftime('%Y-%m-%d %H:%M')}.\n"
        f"Витягни час і текст нагадування: '{text}'\n\n"
        "ТІЛЬКИ JSON: {\"fire_at\": \"YYYY-MM-DD HH:MM\", \"text\": \"текст\"}"
    )}])
    data        = _parse_json_ai(parsed)
    fire_at     = datetime.strptime(data["fire_at"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    remind_text = data["text"].strip()
    if fire_at <= now:
        fire_at += timedelta(days=1)
    await schedule_reminder(ctx.bot, update.effective_chat.id, remind_text, fire_at)
    return remind_text, fire_at

async def _dispatch_intent(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int, user_text: str, enriched: str,
    intent: str, voice_msg=None,
) -> bool:
    prefix = f"🎤 Ти сказав: {user_text}\n\n" if voice_msg else ""

    if intent == "image":
        msg = voice_msg or await update.message.reply_text("🎨 Генерую зображення...")
        if voice_msg: await msg.edit_text(f"{prefix}🎨 Генерую зображення...")
        await _do_generate_image(update, enriched, msg)
        return True

    if intent == "reminder":
        try:
            remind_text, fire_at = await _do_reminder(update, ctx, enriched)
            text = f"{prefix}✅ Нагадаю {fire_at.strftime('%d.%m.%Y о %H:%M')}: {remind_text}"
            if voice_msg: await voice_msg.edit_text(text)
            else:         await update.message.reply_text(text)
        except Exception as e:
            log.warning("reminder dispatch: %s", e)
            err = f"{prefix}Не вдалось встановити нагадування."
            if voice_msg: await voice_msg.edit_text(err)
            else:         await update.message.reply_text(err)
        return True

    if intent == "task":
        if voice_msg: await voice_msg.edit_text(f"{prefix}⏳ Обробляю задачу...")
        await do_task_nlp(update, user_id, enriched)
        return True

    if intent == "search":
        msg = voice_msg or await update.message.reply_text("🌐 Шукаю...")
        if voice_msg: await msg.edit_text(f"{prefix}🌐 Шукаю...")
        try:
            search_query = enriched
            if "[Контекст" in enriched:
                search_query = (await call_ai([{"role": "user", "content": f"{enriched}\n\nКороткий пошуковий запит (до 10 слів). ТІЛЬКИ запит."}])).strip()
            results = await search_web(search_query)
            content = (
                f"Запит: '{enriched}'\n\nРезультати:\n{results}\n\nВідповідь українською. Вказуй джерела."
                if results else
                f"Запит: '{enriched}'\n\nВідповідай з власних знань українською."
            )
            reply = await call_ai([get_active_system_prompt(user_id), {"role": "user", "content": content}])
            await append_and_trim(user_id, "user", user_text)
            await append_and_trim(user_id, "assistant", reply)
            _set_ctx(user_id, user_text, reply)
            await _send_or_edit(msg, clean_markdown(f"{prefix}{reply}"), disable_web_page_preview=True)
        except Exception as e:
            log.error("search dispatch: %s", e)
            await msg.edit_text(f"{prefix}Помилка пошуку. Спробуй ще раз.")
        return True

    INTENT_MAP = {
        "translate": ("🌐 Перекладаю...",   do_translate),
        "summarize": ("📰 Опрацьовую...",    do_summarize),
        "generate":  ("✍️ Генерую текст...", do_generate),
        "edit":      ("✏️ Редагую...",        do_edit),
        "recipe":    ("🍳 Рецепти...",         do_recipe),
        "news":      ("📰 Шукаю новини...",   do_news),
    }
    if intent in INTENT_MAP:
        status_text, fn = INTENT_MAP[intent]
        msg = voice_msg or await update.message.reply_text(status_text)
        if voice_msg: await msg.edit_text(f"{prefix}{status_text}")
        try:
            result = await fn(enriched)
        except Exception as e:
            log.error("intent %s: %s", intent, e)
            await msg.edit_text(f"{prefix}⚠️ Помилка. Спробуй ще раз.")
            return True
        kwargs = {"disable_web_page_preview": True} if intent == "news" else {}
        await _send_or_edit(msg, clean_markdown(f"{prefix}{result}".strip()), **kwargs)
        _set_ctx(user_id, user_text, result)
        return True

    return False

# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я J.A.R.V.I.S. 🤖\n\n"
        "• Відповідаю на запитання 💬\n• Перекладаю тексти 🌐\n"
        "• Підсумовую статті 📰\n• Генерую резюме, листи, пости ✍️\n"
        "• Редагую тексти ✏️\n• Рецепти за інгредієнтами 🍳\n"
        "• Список задач 📋\n• Аналізую фото та відео 🎬\n"
        "• Генерую зображення 🎨\n• Голосові повідомлення 🎤\n"
        "• Шукаю в інтернеті 🔍\n• Читаю PDF, Excel, Word 📄\n• Нагадування 🔔\n\n"
        "Просто пиши!\n/help — команди"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команди:\n"
        "/image опис | /remind 30m текст | /news тема\n"
        "/search запит | /translate текст | /summarize url\n"
        "/generate тип | /edit текст | /recipe інгредієнти\n"
        "/tasks | /mode | /memory | /forget | /status | /reset\n\n"
        "💡 Або пиши природною мовою!"
    )

async def news_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args)
    if not query:
        await update.message.reply_text("Використання: /news тема")
        return
    msg = await update.message.reply_text(f"📰 Шукаю новини про «{query}»...")
    await _send_or_edit(msg, clean_markdown(await do_news(query)), disable_web_page_preview=True)

async def translate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /translate текст")
        return
    msg = await update.message.reply_text("🌐 Перекладаю...")
    await _send_or_edit(msg, clean_markdown(await do_translate(text)))

async def summarize_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /summarize https://... або текст")
        return
    msg = await update.message.reply_text("📰 Опрацьовую...")
    await _send_or_edit(msg, clean_markdown(await do_summarize(text)))

async def generate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /generate резюме для розробника")
        return
    msg = await update.message.reply_text("✍️ Генерую...")
    await _send_or_edit(msg, clean_markdown(await do_generate(text)))

async def edit_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /edit скороти: [текст]")
        return
    msg = await update.message.reply_text("✏️ Редагую...")
    await _send_or_edit(msg, clean_markdown(await do_edit(text)))

async def recipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Використання: /recipe картопля яйця цибуля")
        return
    msg = await update.message.reply_text("🍳 Рецепти...")
    await _send_or_edit(msg, clean_markdown(await do_recipe(text)))

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
        descriptions = {
            "normal":     "збалансований J.A.R.V.I.S.",
            "bender":     "Бендер — самозакоханий робот який допомагає всупереч собі",
            "funny":      "легкий гумор",
            "serious":    "факт → обґрунтування → висновок",
            "business":   "суть → аргументи → дія",
            "literary":   "художній стиль, метафори",
            "journalist": "заголовок → лід → факти → висновок",
        }
        lines = ["🎭 Режими:\n"]
        for key, p in PERSONALITIES.items():
            active = "  ✅" if key == current else ""
            lines.append(f"{p['emoji']} /mode {key} — {p['name']}: {descriptions.get(key,'')}{active}")
        await update.message.reply_text("\n".join(lines))

async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mem = get_user_memory(update.message.from_user.id)
    if not mem:
        await update.message.reply_text("🧠 Нічого не пам'ятаю про тебе.")
        return
    lines = ["🧠 Пам'ятаю:\n"]
    if mem.get("name"):   lines.append(f"👤 Ім'я: {mem['name']}")
    if mem.get("gender"): lines.append(f"👤 Стать: {'чоловік' if mem['gender'] == 'male' else 'жінка'}")
    if mem.get("facts"):
        lines.append("\n📌 Факти:")
        lines += [f"  • {f}" for f in mem["facts"]]
    if mem.get("topics"):
        lines.append("\n💬 Теми:")
        lines += [f"  • {t}" for t in mem["topics"]]
    style = mem.get("communication_style", {})
    if style:
        lines.append(f"\n🗣 Стиль: {style.get('formality','?')}, {style.get('avg_message_length','?')}")
    await update.message.reply_text("\n".join(lines))

async def forget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    memory = _load_json(MEMORY_FILE, {})
    memory.pop(str(update.message.from_user.id), None)
    _save_json(MEMORY_FILE, memory)
    await update.message.reply_text("🗑️ Пам'ять очищено.")

async def handle_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(ctx.args)
    if not prompt:
        await update.message.reply_text("Напиши що намалювати: /image захід сонця")
        return
    msg = await update.message.reply_text("🎨 Генерую...")
    try:
        translation = (await call_ai([{"role": "user", "content": f"Translate to English, return ONLY translation: {prompt}"}])).strip()
        img_bytes   = await generate_image(translation)
        await update.message.reply_photo(photo=img_bytes, caption=f"🎨 {prompt}")
        await msg.delete()
    except Exception as e:
        log.error("handle_image: %s", e)
        await msg.edit_text("Помилка генерації. Спробуй ще раз.")

async def handle_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Використання: /remind 30m текст | /remind 2h текст | /remind 14:30 текст")
        return
    time_arg, reminder_text = ctx.args[0], " ".join(ctx.args[1:])
    now = now_kyiv()
    try:
        if time_arg.endswith("m"):
            fire_at, when = now + timedelta(minutes=int(time_arg[:-1])), f"через {time_arg[:-1]} хв"
        elif time_arg.endswith("h"):
            fire_at, when = now + timedelta(hours=int(time_arg[:-1])), f"через {time_arg[:-1]} год"
        elif ":" in time_arg:
            t = datetime.strptime(time_arg, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=TZ)
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
        reply = await call_ai([get_active_system_prompt(user_id), {"role": "user", "content": (
            f"Запит: '{query}'\n\nРезультати:\n{results}\n\nВідповідь українською. Вказуй джерела."
        )}])
        await append_and_trim(user_id, "user", f"Пошук: {query}")
        await append_and_trim(user_id, "assistant", reply)
        await _send_or_edit(msg, f"🌐 {query}\n\n{clean_markdown(reply)}")
    except Exception as e:
        log.error("handle_search: %s", e)
        await msg.edit_text("Помилка пошуку. Спробуй ще раз.")

async def handle_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tavily_status = "❌ Не налаштовано"
    if TAVILY_API_KEY:
        try:
            await asyncio.to_thread(lambda: TavilyClient(api_key=TAVILY_API_KEY).search(query="test", max_results=1))
            tavily_status = "🟢 Працює"
        except Exception as e:
            tavily_status = f"🔴 {str(e)[:50]}"
    await update.message.reply_text(
        f"📊 Статус:\n"
        f"• AI: Groq (основний) → OpenRouter (резерв)\n"
        f"• Tavily: {tavily_status}\n"
        f"• Нагадувань: {len(load_reminders())}\n"
        f"• Активних сесій: {len(chat_histories)}"
    )

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    chat_histories.pop(uid, None)
    last_context.pop(uid, None)
    save_histories()
    await update.message.reply_text("Історію чату очищено! 🔄")

# ── Message routing ───────────────────────────────────────────────────────────

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

async def _keep_typing(bot, chat_id: int, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(4)

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addressed, user_text = _is_bot_addressed(update, ctx)
    if not addressed:
        return
    user_id    = update.message.from_user.id
    stop_evt   = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(ctx.bot, update.effective_chat.id, stop_evt))
    try:
        await asyncio.wait_for(_process_message(update, ctx, user_id, user_text), timeout=90)
    except asyncio.TimeoutError:
        log.error("handle_message timeout user=%s", user_id)
        await update.message.reply_text("Щось затяглось — спробуй ще раз.")
    except Exception as e:
        log.error("handle_message user=%s: %s", user_id, e, exc_info=True)
        await update.message.reply_text("Виникла помилка. Спробуй ще раз.")
    finally:
        stop_evt.set()
        typing_task.cancel()

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("🔍 Аналізую зображення...")
    user_id = update.message.from_user.id
    try:
        photo   = update.message.photo[-1]
        file    = await ctx.bot.get_file(photo.file_id)
        img_b64 = base64.b64encode(await file.download_as_bytearray()).decode()
        caption = update.message.caption or "Що зображено? Опиши детально українською."
        reply   = await call_vision([get_active_system_prompt(user_id), {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            {"type": "text", "text": caption},
        ]}])
        last_context[user_id] = {"type": "фото", "description": reply[:CTX_DESCRIPTION_LEN]}
        await append_and_trim(user_id, "user", f"[Фото] {caption}")
        await append_and_trim(user_id, "assistant", reply)
        await _send_or_edit(msg, clean_markdown(reply))
    except Exception as e:
        log.error("handle_photo: %s", e)
        await msg.edit_text("Помилка аналізу зображення. Спробуй ще раз.")

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("🎬 Аналізую відео...")
    user_id = update.message.from_user.id
    try:
        video   = update.message.video or update.message.video_note
        caption = (update.message.caption if update.message.video else None) or "Що відбувається у цьому відео?"
        if video.file_size > MAX_VIDEO_SIZE:
            await msg.edit_text("❌ Відео завелике. Максимум 20MB.")
            return
        video_bytes = bytes(await (await ctx.bot.get_file(video.file_id)).download_as_bytearray())
        result      = await analyze_video(video_bytes, caption, user_id)
        last_context[user_id] = {"type": "відео", "description": result[:CTX_DESCRIPTION_LEN]}
        await append_and_trim(user_id, "user", f"[Відео] {caption}")
        await append_and_trim(user_id, "assistant", result)
        await _send_or_edit(msg, clean_markdown(result))
    except Exception as e:
        log.error("handle_video: %s", e)
        await msg.edit_text("Помилка аналізу відео. Спробуй ще раз.")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc     = update.message.document
    fname   = doc.file_name.lower()
    user_id = update.message.from_user.id
    caption = update.message.caption or "Стисло підсумуй цей документ українською."

    if fname.endswith(".pdf"):
        msg, extractor = await update.message.reply_text("📄 Читаю PDF..."), extract_pdf_text
    elif fname.endswith((".xlsx", ".xls")):
        msg, extractor = await update.message.reply_text("📊 Читаю Excel..."), extract_excel_text
    elif fname.endswith((".docx", ".doc")):
        msg, extractor = await update.message.reply_text("📝 Читаю Word..."), extract_word_text
    else:
        await update.message.reply_text("📄 Підтримуються: PDF, Excel, Word")
        return

    raw  = bytes(await (await ctx.bot.get_file(doc.file_id)).download_as_bytearray())
    text = extractor(raw)
    try:
        if not text or text.startswith("Помилка"):
            await msg.edit_text(f"❌ {text}")
            return
        text_preview = text[:MAX_DOC_PREVIEW_CHARS] + ("..." if len(text) > MAX_DOC_PREVIEW_CHARS else "")
        await append_and_trim(user_id, "user", f"{caption}\n\nДокумент:\n{text_preview}")
        reply = await call_ai(get_history(user_id))
        await append_and_trim(user_id, "assistant", reply)
        last_context[user_id] = {"type": "документ", "description": reply[:CTX_DESCRIPTION_LEN]}
        await _send_or_edit(msg, f"📄 {doc.file_name}\n\n{clean_markdown(reply)}")
    except Exception as e:
        log.error("handle_document: %s", e)
        await msg.edit_text("Помилка обробки документу. Спробуй ще раз.")

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎤 Розпізнаю...")
    try:
        file        = await ctx.bot.get_file(update.message.voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        text        = await transcribe_voice(audio_bytes)
        if not text:
            await msg.edit_text("Не вдалось розпізнати 😔")
            return
        await msg.edit_text(f"🎤 Ти сказав: {text}\n\n⏳ Обробляю...")
        user_id = update.message.from_user.id
        if get_gender(user_id) is None:
            gender = await detect_gender_from_transcript(text)
            if gender:
                update_user_memory(user_id, {"gender": gender})
                if user_id in chat_histories:
                    chat_histories[user_id][0] = get_system_prompt(user_id)
        await _process_message(update, ctx, user_id, text, voice_msg=msg)
    except Exception as e:
        log.error("handle_voice: %s", e)
        await msg.edit_text("Помилка голосового. Спробуй ще раз.")

# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(app):
    await restore_reminders(app.bot)
    restore_histories()
    for uid, data in _load_json(MEMORY_FILE, {}).items():
        if data.get("mode"):
            user_personalities[int(uid)] = data["mode"]
    log.info("Bot initialized")

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
    app.add_handler(CommandHandler("translate", translate_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("generate",  generate_cmd))
    app.add_handler(CommandHandler("edit",      edit_cmd))
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
        handle_document,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
