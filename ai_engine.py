import httpx
import logging

# Твой ключ
GROQ_API_KEY = "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"

# Список моделей: от самой умной к запасным
MODELS = [
    "deepseek-r1-distill-llama-70b", # САМАЯ УМНАЯ (Chain of Thought)
    "llama-3.3-70b-versatile",       # Очень надежная
    "llama-3.1-70b-versatile",       # Классика
    "mixtral-8x7b-32768"             # Быстрая
]

async def ask_ai(messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in MODELS:
            try:
                # print(f"🧠 Думаю через модель: {model}...") # Можно включить для отладки
                
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.6,
                    "max_tokens": 4096 # DeepSeek любит писать много
                }

                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload
                )

                if response.status_code == 200:
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]
                    
                    # Если DeepSeek выдал <think>...</think>, можно это скрыть или оставить
                    # Оставим как есть, это выглядит круто ("Я подумал и решил...")
                    return answer
                
                elif response.status_code == 404:
                    continue # Модели нет, пробуем следующую
                elif response.status_code == 401:
                    return "🔒 Ошибка ключа API. Обратитесь к админу."
                else:
                    print(f"Ошибка {model}: {response.status_code}")
                    continue

            except Exception as e:
                print(f"Сбой {model}: {e}")
                continue

    return "🔥 Все серверы ИИ сейчас перегружены. Попробуй через минуту."
