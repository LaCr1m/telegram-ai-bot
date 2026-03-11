import os
import httpx
import base64
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

API_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TEXT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
VISION_MODEL = "openrouter/free"

SYSTEM_PROMPT = {"role": "system", "content": "Ти корисний AI асистент на ім'я J.A.R.V.I.S. Завжди відповідай виключно українською мовою, незалежно від мови запиту. Будь точним, корисним і дружнім."}

chat_histories = {}

async def call_openrouter(messages, model):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {"model": model, "messages": messages}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(API_URL, headers=headers, json=body)
        r.raise_for_status()
        return r.json()[
