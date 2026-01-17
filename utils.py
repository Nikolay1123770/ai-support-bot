import httpx
from config import GROQ_API_KEY

# Модели для текста
TEXT_MODELS = [
    "deepseek-r1-distill-llama-70b", # Гений
    "llama-3.3-70b-versatile",       # Надежный
    "llama-3.1-70b-versatile"
]

# 1. Функция: ТЕКСТ -> РЕШЕНИЕ
async def ask_ai(messages: list, roast_mode=False) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY.strip()}", "Content-Type": "application/json"}
    
    # Если режим прожарки, используем специальный системный промпт внутри
    if roast_mode:
        messages[0]["content"] = "Ты — злой и смешной стендап-комик программист. Твоя задача — жестко, с сарказмом и черным юмором 'прожарить' код пользователя. Ищи костыли, плохие имена переменных и глупые ошибки. Не давай решений, только смейся."

    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in TEXT_MODELS:
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7 if not roast_mode else 1.0, # Для прожарки больше креатива
                    "max_tokens": 3000
                }
                resp = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
            except: continue
            
    return "🤯 Мозг перегрелся. Попробуй позже."

# 2. Функция: ГОЛОС -> ТЕКСТ (Новая фича!)
async def transcribe_voice(file_bytes: bytes, filename: str) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY.strip()}"}
    files = {'file': (filename, file_bytes, 'audio/ogg')}
    data = {'model': 'whisper-large-v3-turbo', 'language': 'ru'} # Супер быстрая модель

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=headers, files=files, data=data)
            if resp.status_code == 200:
                return resp.json().get("text", "")
            else:
                print(f"Ошибка Whisper: {resp.text}")
                return ""
        except Exception as e:
            print(f"Ошибка сети: {e}")
            return ""
