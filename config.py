import os

# --- НАСТРОЙКИ БОТА ---

# Твой токен от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "7869311061:AAGPstYpuGk7CZTHBQ-_1IL7FCXDyUfIXPY") # <--- ЗАМЕНИ НА СВОЙ ТОКЕН ЕСЛИ ЭТОТ ПРИМЕР

# Твой ключ Groq (Вставлен твой рабочий ключ)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")

# ID Админа (чтобы работали команды /stats и /send)
# Замени на свой цифровой ID (можно узнать у @userinfobot)
ADMIN_ID = int(os.getenv("ADMIN_ID", "8473513085")) 
