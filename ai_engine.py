import httpx
from config import OPENROUTER_API_KEY

# ============================================
# ВСЕ ТОПОВЫЕ МОДЕЛИ 2025 (от лучшей к запасной)
# ============================================
MODELS = [
    # TIER 1: Самые умные (думают перед ответом)
    "anthropic/claude-sonnet-4",           # Claude Sonnet 4 — топ для кода
    "deepseek/deepseek-r1",                      # DeepSeek R1 — chain of thought
    "google/gemini-2.5-pro-preview",    # Gemini 2.5 Pro

    # TIER 2: Быстрые и умные
    "anthropic/claude-3.5-sonnet",               # Claude 3.5 Sonnet
    "openai/gpt-4o",                             # GPT-4o
    "meta-llama/llama-3.3-70b-instruct",         # Llama 3.3

    # TIER 3: Запасные (если всё лежит)
    "mistralai/mixtral-8x7b-instruct",           # Mixtral
    "google/gemini-2.0-flash-001",               # Gemini Flash
]

# Хранилище истории по пользователям
user_context = {}

async def ask_openrouter(messages: list, user_id: int) -> tuple[str, str]:
    """
    Возвращает (ответ, название_модели)
    """
    if user_id not in user_context:
        user_context[user_id] = []

    # Собираем контекст (последние 8 сообщений)
    history = user_context[user_id][-8:]
    
    full_messages = [
        {"role": "system", "content": messages[0]["content"]}
    ] + history + [
        {"role": "user", "content": messages[1]["content"]}
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://bothost.ru",  # Для статистики OpenRouter
        "X-Title": "BotHost AI Support"
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in MODELS:
            try:
                payload = {
                    "model": model,
                    "messages": full_messages,
                    "temperature": 0.3,
                    "max_tokens": 8192
                }

                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload
                )

                if response.status_code == 200:
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]
                    
                    # Сохраняем в историю
                    user_context[user_id].append({
                        "role": "user", 
                        "content": messages[1]["content"][:1500]
                    })
                    user_context[user_id].append({
                        "role": "assistant", 
                        "content": answer[:1500]
                    })
                    
                    # Возвращаем ответ + какая модель ответила
                    model_name = model.split("/")[-1]
                    return answer, model_name

                elif response.status_code in [429, 503, 529]:
                    # Rate limit или перегруз — пробуем следующую
                    continue
                else:
                    print(f"[{model}] Error {response.status_code}: {response.text[:200]}")
                    continue

            except Exception as e:
                print(f"[{model}] Exception: {e}")
                continue

    return "⚠️ Все серверы ИИ сейчас перегружены. Попробуй через минуту.", "none"


# Очистка контекста пользователя
def clear_context(user_id: int):
    if user_id in user_context:
        user_context[user_id] = []
