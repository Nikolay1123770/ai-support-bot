import httpx
from config import GROQ_API_KEY

# ТОЛЬКО МОЩНЫЕ МОДЕЛИ
# DeepSeek R1 - это лучшая модель для кода сейчас (лучше GPT-4 в задачах на логику)
MODELS = [
    "deepseek-r1-distill-llama-70b", # ПРИОРИТЕТ №1 (Думает перед ответом)
    "llama-3.3-70b-versatile",       # ПРИОРИТЕТ №2 (Если DeepSeek лежит)
]

# ИНЖЕНЕРНЫЙ ПРОМПТ (Это делает бота умным)
SYSTEM_PROMPT = """
Ты — Senior Principal Software Engineer и эксперт по отладке (Debugging Expert).
Твоя задача — ИСПРАВЛЯТЬ КОД. Не просто болтать, а давать рабочие решения.

АЛГОРИТМ РАБОТЫ:
1. АНАЛИЗ СТЕКА: Найди в логе строки 'Traceback', 'Error', 'Exception'. Пойми, в какой строке упало.
2. ПРИЧИНА: Объясни технически, почему код не работает (1 предложение).
3. ИСПРАВЛЕНИЕ: Напиши ПОЛНЫЙ ИСПРАВЛЕННЫЙ БЛОК КОДА. Не кусочки.

ТРЕБОВАНИЯ:
- Если пользователь прислал код с ошибкой -> верни ИСПРАВЛЕННЫЙ код.
- Код должен быть готов к копированию (в блоках ```language ... ```).
- Учитывай контекст библиотек (aiogram 3.x, discord.py, flask, fastapi).
- Если ошибка в imports/dependencies -> напиши команду 'pip install ...'.

Твой стиль: Строгий, точный, профессиональный. Без воды.
"""

async def solve_problem(user_content: str) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    async with httpx.AsyncClient(timeout=90.0) as client: # Увеличили таймаут, так как R1 думает долго
        for model in MODELS:
            try:
                # Настройки для КОДА (низкая температура = высокая точность)
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,  # МИНИМУМ галлюцинаций. Только факты.
                    "max_tokens": 6000,  # Чтобы влез длинный код
                    "top_p": 0.95
                }

                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload
                )

                if response.status_code == 200:
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]
                    
                    # Очистка от тегов мышления <think>, если они есть (DeepSeek иногда их оставляет)
                    # Но иногда полезно оставить, чтобы юзер видел ход мыслей.
                    # Решим так: оставим как есть, это выглядит "умно".
                    return answer
                
                elif response.status_code == 503:
                    continue # Перегруз, пробуем Ламу

            except Exception as e:
                print(f"Ошибка модели {model}: {e}")
                continue

    return "⚠️ **Системный сбой.** Серверы перегружены сложными вычислениями. Попробуй отправить лог еще раз через минуту."
