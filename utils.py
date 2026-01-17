import httpx
import asyncio
import logging

# Твой ключ (жестко вшит, чтобы работало)
GROQ_API_KEY = "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"

# СПИСОК МОДЕЛЕЙ (от самой умной к самой быстрой)
# Бот будет пробовать их по очереди, пока не сработает
AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",  # Топ-1 сейчас
    "llama-3.1-70b-versatile",  # Топ-2
    "llama3-70b-8192",          # Классика
    "mixtral-8x7b-32768",       # Если Лама лежит
    "gemma2-9b-it",             # От Google (запасной)
    "llama-3.1-8b-instant"      # Самая быстрая (если всё лежит)
]

async def ask_groq(messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        # Перебираем модели по очереди
        for model in AVAILABLE_MODELS:
            print(f"🔄 Пробую модель: {model}...")
            
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.6,
                "max_tokens": 2048
            }

            try:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload
                )

                # Если успех (200) — возвращаем ответ
                if response.status_code == 200:
                    print(f"✅ Успех через модель: {model}")
                    return response.json()["choices"][0]["message"]["content"]
                
                # Если ошибка 404 (модель не найдена) или 400 — пробуем следующую
                elif response.status_code in [404, 400]:
                    print(f"⚠️ Модель {model} недоступна, пробую следующую...")
                    continue # Идем к следующей модели в списке
                
                # Если ошибка с ключом (401) — сразу стоп, перебор не поможет
                elif response.status_code == 401:
                    return f"🔒 Ошибка ключа API. Проверь баланс или правильность ключа."

            except Exception as e:
                print(f"❌ Ошибка соединения с {model}: {e}")
                continue

    return "🔥 Извини, сейчас все серверы ИИ перегружены. Попробуй через минуту."
