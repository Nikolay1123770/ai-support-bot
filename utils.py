import httpx
from config import GROQ_API_KEY

async def ask_groq(messages: list) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-70b-instruct",
                    "messages": messages,
                    "temperature": 0.72,
                    "max_tokens": 3072,
                    "top_p": 0.95
                }
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"🔥 Макс сейчас на сервере чинит железо, подожди 15 секунд и пиши ещё раз.\n\n(технически: {str(e)})"
