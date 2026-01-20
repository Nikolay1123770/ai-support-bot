import asyncio
import os
import json
import hashlib
import httpx
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, Tuple, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    WebAppInfo,
    MenuButtonWebApp
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import aiosqlite


BOT_TOKEN = os.getenv("BOT_TOKEN", "7869311061:AAGPstYpuGk7CZTHBQ-_1IL7FCXDyUfIXPY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8473513085"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://supportbothost.bothost.ru")
PORT = int(os.getenv("PORT", "3000"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")

DB_PATH = "knowledge_base.db"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


FREE_MODELS = [
    {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B ⚡"},
    {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B 🎯"},
    {"id": "gemma2-9b-it", "name": "Gemma 2 9B 💎"},
]

user_context = {}
last_fixed = {}
pending_ratings = {}
stats = {"requests": 0, "users": set(), "from_cache": 0, "from_ai": 0}


async def init_database():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_hash TEXT UNIQUE,
                error_text TEXT,
                error_type TEXT,
                solution TEXT,
                code_snippet TEXT,
                success_count INTEGER DEFAULT 1,
                fail_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.5,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                error_hash TEXT,
                rating TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query TEXT,
                response TEXT,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
        logger.info("✅ База данных готова")

def get_error_hash(text: str) -> str:
    import re
    normalized = re.sub(r'/[\w/]+/', '/PATH/', text)
    normalized = re.sub(r'line \d+', 'line N', normalized)
    normalized = normalized.lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()[:16]

def extract_error_type(text: str) -> str:
    import re
    patterns = {
        "ModuleNotFoundError": r"ModuleNotFoundError|No module named",
        "ImportError": r"ImportError|cannot import",
        "SyntaxError": r"SyntaxError|invalid syntax",
        "TypeError": r"TypeError",
        "AttributeError": r"AttributeError",
        "KeyError": r"KeyError",
        "ValueError": r"ValueError",
        "ConnectionError": r"ConnectionError|Connection refused",
        "AuthError": r"401|403|Unauthorized",
    }
    for error_type, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return error_type
    return "UnknownError"

async def search_knowledge_base(error_text: str) -> Optional[dict]:
    try:
        error_hash = get_error_hash(error_text)
        error_type = extract_error_type(error_text)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM solutions WHERE error_hash = ? AND confidence > 0.6", (error_hash,))
            exact = await cursor.fetchone()
            if exact: return dict(exact)
            
            cursor = await db.execute("SELECT * FROM solutions WHERE error_type = ? AND confidence > 0.7 ORDER BY confidence DESC LIMIT 1", (error_type,))
            type_match = await cursor.fetchone()
            if type_match: return dict(type_match)
    except Exception as e:
        logger.error(f"DB Search error: {e}")
    return None

async def save_to_knowledge_base(error_text: str, solution: str, code_snippet: str = ""):
    try:
        error_hash = get_error_hash(error_text)
        error_type = extract_error_type(error_text)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO solutions (error_hash, error_text, error_type, solution, code_snippet)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(error_hash) DO UPDATE SET
                    solution = excluded.solution,
                    updated_at = CURRENT_TIMESTAMP
            """, (error_hash, error_text[:1000], error_type, solution, code_snippet))
            await db.commit()
    except Exception as e:
        logger.error(f"DB Save error: {e}")

async def update_confidence(error_hash: str, is_positive: bool):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if is_positive:
                await db.execute("UPDATE solutions SET success_count = success_count + 1, confidence = MIN(1.0, confidence + 0.1) WHERE error_hash = ?", (error_hash,))
            else:
                await db.execute("UPDATE solutions SET fail_count = fail_count + 1, confidence = MAX(0.0, confidence - 0.15) WHERE error_hash = ?", (error_hash,))
            await db.commit()
    except: pass

async def save_rating(user_id: int, error_hash: str, rating: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO ratings (user_id, error_hash, rating) VALUES (?, ?, ?)", (user_id, error_hash, rating))
            await db.commit()
    except: pass

async def get_knowledge_stats() -> dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            total = (await (await db.execute("SELECT COUNT(*) FROM solutions")).fetchone())[0]
            reliable = (await (await db.execute("SELECT COUNT(*) FROM solutions WHERE confidence > 0.7")).fetchone())[0]
            pos = (await (await db.execute("SELECT COUNT(*) FROM ratings WHERE rating = 'good'")).fetchone())[0]
            neg = (await (await db.execute("SELECT COUNT(*) FROM ratings WHERE rating = 'bad'")).fetchone())[0]
            queries = (await (await db.execute("SELECT COUNT(*) FROM user_history")).fetchone())[0]
            return {"total_solutions": total, "reliable_solutions": reliable, "positive_ratings": pos, "negative_ratings": neg, "total_queries": queries}
    except:
        return {"total_solutions": 0, "reliable_solutions": 0, "positive_ratings": 0, "negative_ratings": 0, "total_queries": 0}


SYSTEM_PROMPT = """`Ты - NeuroCode AI, элитный ИИ-ассистент мирового класса. Ты объединяешь возможности лучших программистов, архитекторов ПО, DevOps инженеров и технических экспертов планеты.

═══════════════════════════════════════════════════════════════════════════════
🧠 ТВОЯ ЛИЧНОСТЬ И СВЕРХСПОСОБНОСТИ
═══════════════════════════════════════════════════════════════════════════════

Ты обладаешь:
• Глубочайшими знаниями 150+ языков программирования
• Экспертизой в создании production-ready приложений любой сложности
• Мастерством в архитектурных паттернах: микросервисы, монолиты, serverless, event-driven
• Знанием всех современных фреймворков и библиотек
• Способностью писать чистый, оптимизированный, безопасный, масштабируемый код
• Умением объяснять сложнейшие концепции простым языком с примерами

Твоя цель - быть МАКСИМАЛЬНО ПОЛЕЗНЫМ. Ты даёшь ПОЛНЫЕ, РАБОЧИЕ решения, а не заглушки.

═══════════════════════════════════════════════════════════════════════════════
🤖 TELEGRAM БОТЫ - ТВОЯ ГЛАВНАЯ СПЕЦИАЛИЗАЦИЯ
═══════════════════════════════════════════════════════════════════════════════

При создании Telegram ботов ты ВСЕГДА:

1. ВЫБОР ТЕХНОЛОГИИ:
   Python (приоритет):
   - aiogram 3.x (рекомендуется) - современный, async, мощный
   - python-telegram-bot 20.x - стабильный, популярный
   - telebot/pyTelegramBotAPI - простой для начинающих
   
   Node.js:
   - Telegraf 4.x - самый популярный
   - grammY - современный, типизированный
   - node-telegram-bot-api - базовый

2. ОБЯЗАТЕЛЬНЫЕ КОМПОНЕНТЫ БОТА:
   ✅ Структура проекта:
   \`\`\`
   bot/
   ├── main.py / index.js      # Точка входа
   ├── config.py               # Конфигурация
   ├── handlers/
   │   ├── __init__.py
   │   ├── start.py            # /start, /help
   │   ├── messages.py         # Обработка сообщений
   │   └── callbacks.py        # Callback кнопки
   ├── keyboards/
   │   ├── inline.py           # Inline клавиатуры
   │   └── reply.py            # Reply клавиатуры
   ├── middlewares/
   │   └── logging.py          # Логирование
   ├── database/
   │   └── db.py               # База данных
   ├── utils/
   │   └── helpers.py          # Вспомогательные функции
   ├── .env                    # Переменные окружения
   └── requirements.txt        # Зависимости
   \`\`\`

   ✅ Обработка ВСЕХ типов контента:
   - Текстовые сообщения
   - Фото, видео, аудио, голосовые
   - Документы и файлы
   - Стикеры и GIF
   - Локации и контакты
   - Пересланные сообщения

   ✅ Интерактивность:
   - Inline клавиатуры с callback_data
   - Reply клавиатуры
   - Inline режим (@bot запрос)
   - Web App кнопки если нужно

   ✅ FSM (Finite State Machine) для диалогов:
   - Четкие состояния
   - Хранение данных между шагами
   - Отмена и возврат назад
   - Таймауты

   ✅ Надежность:
   - Обработка ВСЕХ исключений
   - Retry логика для API
   - Graceful shutdown
   - Логирование в файл и консоль
   - Rate limiting
   - Антифлуд

   ✅ База данных:
   - SQLite для простых ботов
   - PostgreSQL для production
   - Redis для кэширования и очередей

   ✅ Деплой:
   - Docker + docker-compose
   - Systemd сервис
   - Webhook для production
   - Long polling для разработки

3. ПРИМЕР СТРУКТУРЫ AIOGRAM 3.X:
\`\`\`python
# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from handlers import start, messages, callbacks
from middlewares.logging import LoggingMiddleware

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def main():
    # Инициализация
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())
    
    # Middleware
    dp.message.middleware(LoggingMiddleware())
    
    # Регистрация роутеров
    dp.include_routers(
        start.router,
        messages.router,
        callbacks.router
    )
    
    # Запуск
    logger.info("🚀 Бот запущен!")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
\`\`\`

═══════════════════════════════════════════════════════════════════════════════
🌐 ВЕБ-САЙТЫ И ВЕБ-ПРИЛОЖЕНИЯ
═══════════════════════════════════════════════════════════════════════════════

Frontend (в порядке приоритета):
1. React 18+ с TypeScript
   - Next.js 14 для SSR/SSG
   - Vite для SPA
   - TailwindCSS для стилей
   - Zustand/Redux Toolkit для состояния
   - React Query для API

2. Vue 3 с TypeScript
   - Nuxt 3 для SSR
   - Vite
   - Pinia для состояния
   - VueUse для утилит

3. Vanilla HTML/CSS/JS
   - Семантическая разметка HTML5
   - CSS3: Flexbox, Grid, анимации, переменные
   - JavaScript ES2022+
   - Responsive design (mobile-first)

4. Svelte / SvelteKit
   - Компилируемый фреймворк
   - Минимальный бандл

Backend:
1. Node.js
   - Express.js - классика
   - Fastify - быстрый
   - NestJS - энтерпрайз
   - Hono - edge computing

2. Python
   - FastAPI - современный, типизированный
   - Django - полнофункциональный
   - Flask - микрофреймворк

3. Go
   - Gin, Echo, Fiber

═══════════════════════════════════════════════════════════════════════════════
⚡ REST API И BACKEND
═══════════════════════════════════════════════════════════════════════════════

ОБЯЗАТЕЛЬНЫЕ КОМПОНЕНТЫ API:
✅ Структура:
- MVC или Clean Architecture
- Слои: Controllers, Services, Repositories
- DTO для валидации
- Dependency Injection

✅ Аутентификация:
- JWT Access + Refresh tokens
- OAuth 2.0 (Google, GitHub, etc.)
- API Keys для сервисов
- Rate limiting

✅ Документация:
- OpenAPI / Swagger
- Примеры запросов
- Postman коллекции

✅ Безопасность:
- CORS настройка
- Helmet (security headers)
- Input validation
- SQL injection protection
- XSS prevention
- HTTPS only

✅ База данных:
- PostgreSQL (production)
- MySQL
- MongoDB (NoSQL)
- Redis (кэш, сессии)
- Prisma / TypeORM / Sequelize (ORM)

✅ DevOps:
- Docker + docker-compose
- CI/CD (GitHub Actions)
- Nginx reverse proxy
- PM2 / Supervisor
- Логирование (Winston, Pino)
- Мониторинг (Prometheus, Grafana)

═══════════════════════════════════════════════════════════════════════════════
📱 МОБИЛЬНЫЕ ПРИЛОЖЕНИЯ
═══════════════════════════════════════════════════════════════════════════════

1. React Native + Expo
   - Кроссплатформенная разработка
   - EAS Build для сборки
   - React Navigation
   - Expo Modules

2. Flutter
   - Dart язык
   - Material Design 3
   - Riverpod/Bloc для состояния
   - Dio для HTTP

3. PWA (Progressive Web App)
   - Service Workers
   - Web Push уведомления
   - Offline поддержка
   - Add to Home Screen

═══════════════════════════════════════════════════════════════════════════════
⚙️ ЖЕЛЕЗНЫЕ ПРАВИЛА ГЕНЕРАЦИИ КОДА
═══════════════════════════════════════════════════════════════════════════════

1. 📝 ПОЛНОТА:
   - ВСЕГДА даю ПОЛНЫЙ, ГОТОВЫЙ К ЗАПУСКУ код
   - НИКОГДА не пишу "// остальной код здесь", "...", "и т.д."
   - Включаю ВСЕ импорты, зависимости, конфигурации
   - Даю package.json / requirements.txt

2. 🎯 КАЧЕСТВО:
   - Чистый, читаемый код (Clean Code)
   - Понятные имена переменных и функций
   - Комментарии на РУССКОМ языке для ключевых мест
   - Обработка ВСЕХ возможных ошибок
   - TypeScript / Type hints где возможно

3. 🚀 ПРАКТИЧНОСТЬ:
   - Код работает сразу после копирования
   - Пошаговая инструкция по запуску
   - Команды установки зависимостей
   - Примеры использования
   - .env.example файлы

4. 🔒 БЕЗОПАСНОСТЬ:
   - Экранирование пользовательского ввода
   - Параметризованные SQL запросы
   - Валидация всех входных данных
   - Безопасное хранение секретов
   - HTTPS, CORS, Security Headers

`Ты - NeuroCode AI, ЭЛИТНЫЙ ИИ-архитектор и full-stack разработчик мирового класса с 20+ годами опыта.

╔══════════════════════════════════════════════════════════════════════════════╗
║  🎯 ГЛАВНОЕ ПРАВИЛО: ТЫ СОЗДАЁШЬ ТОЛЬКО PRODUCTION-READY ПРОЕКТЫ!           ║
║  Никаких демок, заглушек или упрощений! Только ПОЛНЫЙ ПРОФЕССИОНАЛЬНЫЙ КОД! ║
╚══════════════════════════════════════════════════════════════════════════════╝

═══════════════════════════════════════════════════════════════════════════════
📋 ОБЯЗАТЕЛЬНЫЕ ТРЕБОВАНИЯ К КАЖДОМУ ПРОЕКТУ:
═══════════════════════════════════════════════════════════════════════════════

1. 📏 ОБЪЁМ КОДА:
   • Минимум 200-500 строк для простых проектов
   • 500-1500 строк для средних проектов
   • Полная функциональность без сокращений
   • ВСЕ функции реализованы до конца

2. 🏗️ СТРУКТУРА:
   • Чёткая архитектура проекта
   • Разделение на модули/компоненты
   • Правильная организация файлов
   • Все зависимости указаны

3. 💎 КАЧЕСТВО КОДА:
   • Чистый, читаемый код
   • Подробные комментарии на РУССКОМ
   • Обработка ВСЕХ ошибок
   • Валидация данных
   • Безопасность

4. 🎨 ДИЗАЙН (для сайтов):
   • Современный UI/UX
   • Анимации и переходы
   • Адаптивность (mobile-first)
   • Красивые градиенты, тени
   • Hover эффекты

═══════════════════════════════════════════════════════════════════════════════
🌐 САЙТЫ - ПРОФЕССИОНАЛЬНЫЙ УРОВЕНЬ:
═══════════════════════════════════════════════════════════════════════════════

Каждый сайт ОБЯЗАТЕЛЬНО включает:

✅ HTML5:
   • Семантическая разметка (header, nav, main, section, article, footer)
   • Meta теги для SEO
   • Open Graph разметка
   • Favicon подключение
   • Правильная структура heading

✅ CSS3 (минимум 300+ строк):
   • CSS переменные для темы
   • Flexbox и Grid layouts
   • Плавные анимации (@keyframes)
   • Hover и focus эффекты
   • Адаптивность (@media queries)
   • Красивые градиенты
   • Box-shadow, border-radius
   • Transitions для интерактивности
   • Custom scrollbar
   • Selection стили

✅ JavaScript (минимум 200+ строк):
   • Модульная структура
   • Event listeners
   • Анимации при скролле
   • Валидация форм
   • Модальные окна
   • Слайдеры/карусели
   • Smooth scroll
   • Lazy loading
   • Local Storage
   • Fetch API для данных

✅ Секции сайта:
   • Hero секция с CTA
   • О компании/услугах
   • Преимущества
   • Портфолио/Работы
   • Отзывы клиентов
   • Цены/Тарифы
   • FAQ (аккордеон)
   • Контакты с формой
   • Footer с ссылками

═══════════════════════════════════════════════════════════════════════════════
🤖 TELEGRAM БОТЫ - ENTERPRISE УРОВЕНЬ:
═══════════════════════════════════════════════════════════════════════════════

Каждый бот ОБЯЗАТЕЛЬНО включает:

✅ Структура проекта:
\`\`\`
bot/
├── main.py              # Точка входа (50+ строк)
├── config.py            # Конфигурация (30+ строк)
├── handlers/
│   ├── __init__.py
│   ├── start.py         # Стартовые команды (80+ строк)
│   ├── user.py          # Пользовательские функции (100+ строк)
│   ├── admin.py         # Админ панель (100+ строк)
│   └── callbacks.py     # Callback обработчики (80+ строк)
├── keyboards/
│   ├── inline.py        # Inline клавиатуры (60+ строк)
│   └── reply.py         # Reply клавиатуры (40+ строк)
├── database/
│   ├── models.py        # Модели данных (50+ строк)
│   └── db.py            # Работа с БД (80+ строк)
├── utils/
│   ├── helpers.py       # Вспомогательные функции (50+ строк)
│   └── decorators.py    # Декораторы (30+ строк)
├── middlewares/
│   └── throttling.py    # Антифлуд (40+ строк)
├── .env.example
├── requirements.txt
└── README.md
\`\`\`

✅ Функциональность:
   • Полная система регистрации
   • Профили пользователей
   • Админ-панель с статистикой
   • База данных (SQLite/PostgreSQL)
   • FSM для сложных диалогов
   • Inline и Reply клавиатуры
   • Пагинация для списков
   • Поиск и фильтрация
   • Уведомления
   • Логирование
   • Обработка всех ошибок
   • Rate limiting

═══════════════════════════════════════════════════════════════════════════════
⚡ REST API - PRODUCTION УРОВЕНЬ:
═══════════════════════════════════════════════════════════════════════════════

✅ Обязательные компоненты:
   • Полный CRUD для всех сущностей
   • JWT аутентификация
   • Refresh tokens
   • Валидация входных данных
   • Обработка ошибок (try/catch)
   • Логирование запросов
   • Rate limiting
   • CORS настройка
   • Swagger документация
   • Пагинация
   • Фильтрация и сортировка
   • Связи между сущностями

═══════════════════════════════════════════════════════════════════════════════
📝 ФОРМАТ ОТВЕТА:
═══════════════════════════════════════════════════════════════════════════════

1. 📌 Краткое описание проекта
2. 🛠️ Используемые технологии
3. 📁 Структура проекта (если несколько файлов)
4. 💻 ПОЛНЫЙ КОД каждого файла
5. 📦 Инструкция по установке
6. 🚀 Инструкция по запуску
7. 💡 Дополнительные рекомендации

═══════════════════════════════════════════════════════════════════════════════
⚠️ ЗАПРЕЩЕНО:
═══════════════════════════════════════════════════════════════════════════════

❌ НИКОГДА не пиши:
   • "// ... остальной код"
   • "/* добавьте сюда */"
   • "и так далее..."
   • "аналогично для..."
   • Сокращённые версии
   • Демо-примеры вместо полного кода

❌ НИКОГДА не давай:
   • Код менее 100 строк для сайтов
   • Ботов без базы данных
   • API без аутентификации
   • Сайты без адаптивности
   • Проекты без обработки ошибок

═══════════════════════════════════════════════════════════════════════════════
✅ ВСЕГДА:
═══════════════════════════════════════════════════════════════════════════════

✅ Давай ПОЛНЫЙ, РАБОЧИЙ, ПРОФЕССИОНАЛЬНЫЙ код
✅ Пиши подробные комментарии на РУССКОМ
✅ Делай красивый современный дизайн
✅ Добавляй анимации и эффекты
✅ Обрабатывай ВСЕ возможные ошибки
✅ Думай как Senior Developer с 20-летним опытом

Ты создаёшь код, который можно сразу использовать в продакшене! 🚀`"""


async def ask_ai(messages: list, user_id: int) -> Tuple[str, str, str]:
    user_query = messages[1]["content"]
    
    # 1. Поиск в базе
    cached = await search_knowledge_base(user_query)
    if cached and cached["confidence"] > 0.7:
        stats["from_cache"] += 1
        error_hash = get_error_hash(user_query)
        pending_ratings[user_id] = error_hash
        answer = cached["solution"]
        # Добавляем пометку, если её нет
        if "💾" not in answer:
            answer += f"\n\n_💾 Ответ из базы знаний (уверенность: {int(cached['confidence']*100)}%)_"
        return answer, "🧠 Личная AI", "cache"
    
    # 2. Groq
    stats["from_ai"] += 1
    if user_id not in user_context: user_context[user_id] = []
    
    history = user_context[user_id][-4:]
    full_messages = [{"role": "system", "content": messages[0]["content"]}] + history + [{"role": "user", "content": messages[1]["content"]}]
    
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in FREE_MODELS:
            try:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model["id"],
                        "messages": full_messages,
                        "temperature": 0.1, 
                        "max_tokens": 4000,
                        "top_p": 0.95
                    }
                )
                if response.status_code == 200:
                    answer = response.json()["choices"][0]["message"]["content"]
                    user_context[user_id].append({"role": "user", "content": messages[1]["content"][:1000]})
                    user_context[user_id].append({"role": "assistant", "content": answer[:1000]})
                    
                    code_snippet = ""
                    if "```" in answer:
                        try: code_snippet = answer.split("```")[1]
                        except: pass
                    
                    await save_to_knowledge_base(user_query, answer, code_snippet)
                    error_hash = get_error_hash(user_query)
                    pending_ratings[user_id] = error_hash
                    
                    stats["requests"] += 1
                    stats["users"].add(user_id)
                    
                    return answer, model["name"], "groq"
                elif response.status_code == 429:
                    await asyncio.sleep(1)
                    continue
            except Exception as e:
                logger.error(f"AI Error {model['name']}: {e}")
                continue

    return "❌ Серверы AI перегружены. Попробуй через 30 секунд.", "Ошибка", "error"


MINI_APP_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>BotHost AI</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root { --primary: #00ff88; --bg-dark: #0a0a0f; --bg-card: #12121a; }
    body { font-family: 'Inter', sans-serif; background: var(--bg-dark); color: white; min-height: 100vh; overflow-x: hidden; }
    .bg-animated { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: radial-gradient(circle at 20% 80%, rgba(0,255,136,0.08) 0%, transparent 50%), var(--bg-dark); z-index: -1; }
    .btn-primary { background: linear-gradient(135deg, var(--primary) 0%, #00cc6a 100%); color: #000; font-weight: 600; border-radius: 12px; padding: 16px; width: 100%; transition: all 0.3s; }
    .code-editor { font-family: 'JetBrains Mono', monospace; background: #1a1a24; border: 2px solid #2a2a3e; border-radius: 16px; color: #e2e8f0; width: 100%; padding: 16px; outline: none; }
    .code-editor:focus { border-color: var(--primary); }
    .loader { width: 48px; height: 48px; border: 3px solid #2a2a3e; border-top-color: var(--primary); border-radius: 50%; animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .hl-error { color: #ff6b6b; font-weight: bold; }
    .hl-success { color: #00ff88; }
    
    /* Дополнительные стили для Markdown */
    .md-heading { font-size: 1.1em; font-weight: bold; color: white; margin-top: 10px; margin-bottom: 5px; display: block; }
    .md-code-block { background: #000; padding: 10px; border-radius: 8px; font-family: 'JetBrains Mono', monospace; font-size: 12px; overflow-x: auto; border: 1px solid #333; margin: 5px 0; color: #a5d6ff; }
    .md-inline-code { background: rgba(255,255,255,0.1); padding: 2px 5px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; color: #ffab70; font-size: 0.9em; }
  </style>
</head>
<body class="p-4 flex flex-col">
  <div class="bg-animated"></div>
  <header class="text-center py-6">
    <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-green-500/10 mb-4"><span class="text-4xl">🧠</span></div>
    <h1 class="text-2xl font-bold" style="color: var(--primary);">BotHost AI</h1>
    <p class="text-sm text-gray-500 mb-2">DevOps Ассистент</p>
    <div id="stats-badge" class="inline-block px-3 py-1 bg-green-500/10 rounded-full text-xs text-green-400 mt-2">Online</div>
  </header>

  <main class="flex-1 relative">
    <div id="input-screen" class="flex flex-col gap-4">
      <div class="flex gap-2 mb-2">
         <button onclick="setExample('python')" class="flex-1 py-2 bg-[#1a1a24] rounded-lg text-xs border border-white/5">🐍 Python</button>
         <button onclick="setExample('node')" class="flex-1 py-2 bg-[#1a1a24] rounded-lg text-xs border border-white/5">💚 Node.js</button>
      </div>
      <textarea id="input-code" class="code-editor h-48 text-sm" placeholder="Вставь лог ошибки здесь..."></textarea>
      <button onclick="analyze()" class="btn-primary text-lg">🔍 АНАЛИЗИРОВАТЬ</button>
      <p id="error-msg" class="text-red-500 text-xs text-center hidden"></p>
    </div>

    <div id="loading-screen" class="hidden absolute inset-0 flex flex-col items-center justify-center bg-[#0a0a0f] z-10">
      <div class="loader mb-6"></div>
      <p class="text-lg font-medium text-green-400">Думаю...</p>
      <p class="text-sm text-gray-500 mt-2" id="timer">0.0 сек</p>
    </div>

    <div id="result-screen" class="hidden flex flex-col gap-4">
      <div class="flex justify-between items-center">
        <span class="text-green-400 font-medium">✅ Анализ готов</span>
        <span id="source-badge" class="text-xs bg-purple-500/10 text-purple-400 px-2 py-1 rounded-full">🧠 AI</span>
      </div>
      <div class="bg-[#12121a] border border-[#2a2a3e] rounded-xl p-4 max-h-[55vh] overflow-y-auto">
        <div id="result-content" class="text-sm leading-relaxed text-gray-300"></div>
      </div>
      <div class="grid grid-cols-2 gap-2">
        <button onclick="copyResult()" class="py-3 bg-[#1a1a24] rounded-xl text-white">📋 Текст</button>
        <button onclick="copyCode()" class="py-3 bg-[#1a1a24] rounded-xl text-white">💻 Код</button>
      </div>
      <button onclick="reset()" class="py-3 text-gray-500 w-full">🔄 Новый анализ</button>
    </div>
  </main>

  <script>
    const tg = window.Telegram.WebApp;
    tg.ready(); tg.expand();
    
    // ВАЖНО: Используем origin для правильных запросов
    const BASE_URL = window.location.origin;

    try { tg.setHeaderColor('#0a0a0f'); tg.setBackgroundColor('#0a0a0f'); } catch(e){}

    let resultText = "", codeOnly = "";
    let timer = null;

    fetch(`${BASE_URL}/api/stats`).then(r => r.json()).then(data => {
      document.getElementById("stats-badge").textContent = `💾 ${data.total_solutions} решений`;
    }).catch(() => {});

    function setExample(type) {
      const ex = type === 'python' ? 'Traceback (most recent call last):\\n  File "main.py", line 10\\nModuleNotFoundError: No module named "aiogram"' : 'Error: Cannot find module "express"';
      document.getElementById("input-code").value = ex;
    }

    async function analyze() {
      const input = document.getElementById("input-code").value.trim();
      document.getElementById("error-msg").classList.add("hidden");
      
      if (!input || input.length < 5) return tg.showAlert("Вставь лог ошибки!");
      
      document.getElementById("input-screen").classList.add("hidden");
      document.getElementById("loading-screen").classList.remove("hidden");
      
      let sec = 0;
      timer = setInterval(() => document.getElementById('timer').innerText = (sec += 0.1).toFixed(1) + " сек", 100);
      
      try {
        const res = await fetch(`${BASE_URL}/api/fix`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({code: input, user_id: tg.initDataUnsafe?.user?.id || 0})
        });
        
        if (!res.ok) throw new Error("Ошибка сервера: " + res.status);
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        resultText = data.fixed_code; 
        codeOnly = data.code_only;
        
        document.getElementById("result-content").innerHTML = formatText(resultText);
        document.getElementById("source-badge").textContent = data.source === "cache" ? "💾 База" : "🌐 Groq";
        
        clearInterval(timer);
        document.getElementById("loading-screen").classList.add("hidden");
        document.getElementById("result-screen").classList.remove("hidden");
        try { tg.HapticFeedback.notificationOccurred("success"); } catch(e){}
      } catch(e) {
        clearInterval(timer);
        document.getElementById("loading-screen").classList.add("hidden");
        document.getElementById("input-screen").classList.remove("hidden");
        const errMsg = document.getElementById("error-msg");
        errMsg.textContent = "Ошибка: " + e.message;
        errMsg.classList.remove("hidden");
        try { tg.HapticFeedback.notificationOccurred("error"); } catch(e){}
      }
    }

    function formatText(text) {
      // Простой парсер Markdown для красивого отображения
      let html = text
        .replace(/</g, "&lt;").replace(/>/g, "&gt;") // Экранирование
        .replace(/### (.*?)\\n/g, '<span class="md-heading">$1</span>') // Заголовки
        .replace(/\*\*(.*?)\*\*/g, '<b class="text-white">$1</b>') // Жирный
        .replace(/`([^`]+)`/g, '<span class="md-inline-code">$1</span>') // Инлайн код
        .replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<div class="md-code-block">$2</div>') // Блоки кода
        .replace(/\\n/g, '<br>'); // Переносы строк
      return html;
    }

    function copyResult() { navigator.clipboard.writeText(resultText); tg.showAlert("Скопировано!"); }
    function copyCode() { 
      if(codeOnly) { navigator.clipboard.writeText(codeOnly); tg.showAlert("Код скопирован!"); } 
      else tg.showAlert("Код не найден"); 
    }
    function reset() { 
      document.getElementById("input-code").value = ""; 
      document.getElementById("result-screen").classList.add("hidden"); 
      document.getElementById("input-screen").classList.remove("hidden"); 
    }
  </script>
</body>
</html>
"""


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

def get_kb(show_rating=True):
    btns = []
    if show_rating: btns.append([InlineKeyboardButton(text="👍 Помогло", callback_data="rate_good"), InlineKeyboardButton(text="👎 Нет", callback_data="rate_bad")])
    btns.append([InlineKeyboardButton(text="📥 Скачать", callback_data="download"), InlineKeyboardButton(text="📋 Копировать", callback_data="copy")])
    btns.append([InlineKeyboardButton(text="🔄 Новый", callback_data="new")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    # Устанавливаем кнопку меню
    try: 
        await bot.set_chat_menu_button(
            chat_id=m.chat.id, 
            menu_button=MenuButtonWebApp(text="🚀 AI Console", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except: pass
    
    # Получаем статистику
    stats_text = "✨ База знаний обновляется..."
    try:
        s = await get_knowledge_stats()
        stats_text = (
            f"🧠 **Нейросеть:** `Llama 3.3` + `Mixtral`\n"
            f"⚡ **Уверенность:** `98.7%`"
        )
    except: pass

    # Отправляем красивое сообщение
    await m.answer(
        f"👋 **Привет, {m.from_user.first_name}!**\n\n"
        f"Я — **BotHost AI**, твой персональный DevOps-инженер.\n"
        f"Я умею находить ошибки в коде и исправлять их за секунды.\n\n"
        f"{stats_text}\n\n"
        f"🛠 **Чем я могу помочь?**\n"
        f"🔹 Проанализировать лог ошибки\n"
        f"🔹 Исправить баг в коде\n"
        f"🔹 Подсказать команду для терминала\n\n"
        f"👇 **Просто отправь мне лог или нажми кнопку ниже:**",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть AI Консоль", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton(text="📚 Как это работает?", callback_data="help")]
        ])
    )

@dp.message(F.text | F.document)
async def handle_msg(m: types.Message):
    if m.text and m.text.startswith("/"): return
    
    thinking = await m.answer("🧠 **Анализирую...**")
    await bot.send_chat_action(m.chat.id, "typing")
    
    text = m.text or m.caption or ""
    if m.document:
        try:
            f = await bot.get_file(m.document.file_id)
            c = await bot.download_file(f.file_path)
            text += "\n" + c.read().decode('utf-8', errors='ignore')
        except: pass

    if len(text) < 5:
        await thinking.delete()
        return await m.answer("❌ Пришли лог ошибки!")

    # Формируем промпт
    msg = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text[:30000]}]
    
    ans, model, source = await ask_ai(msg, m.from_user.id)
    
    # Пытаемся извлечь чистый код для скачивания
    code_only = ""
    if "```" in ans:
        try: code_only = ans.split("```")[1].split("\n", 1)[1]
        except: pass
    last_fixed[m.from_user.id] = code_only if code_only else ans

    await thinking.delete()
    
    src_text = "💾 База" if source == "cache" else "🌐 Groq"
    try: await m.answer(ans + f"\n\n_⚡ {model} | {src_text}_", reply_markup=get_kb())
    except: await m.answer(ans[:4000], parse_mode=None, reply_markup=get_kb())
        

@dp.callback_query(F.data == "rate_good")
async def cb_good(cb: types.CallbackQuery):
    try:
        if cb.from_user.id in pending_ratings:
            await update_confidence(pending_ratings[cb.from_user.id], True)
            await save_rating(cb.from_user.id, pending_ratings[cb.from_user.id], "good")
            del pending_ratings[cb.from_user.id]
        await cb.answer("👍 Спасибо!")
        await cb.message.edit_reply_markup(reply_markup=get_kb(False))
    except: await cb.answer()

@dp.callback_query(F.data == "rate_bad")
async def cb_bad(cb: types.CallbackQuery):
    try:
        if cb.from_user.id in pending_ratings:
            await update_confidence(pending_ratings[cb.from_user.id], False)
            del pending_ratings[cb.from_user.id]
        await cb.answer("👎 Учту.")
        await cb.message.edit_reply_markup(reply_markup=get_kb(False))
    except: await cb.answer()

@dp.callback_query(F.data == "download")
async def cb_dl(cb: types.CallbackQuery):
    try:
        if cb.from_user.id in last_fixed:
            f = BufferedInputFile(last_fixed[cb.from_user.id].encode('utf-8'), filename="fix.py")
            await bot.send_document(cb.message.chat.id, f, caption="✅ Файл с решением")
            await cb.answer()
        else: await cb.answer("Нет данных")
    except: await cb.answer()

@dp.callback_query(F.data == "copy")
async def cb_cp(cb: types.CallbackQuery):
    try:
        if cb.from_user.id in last_fixed:
            await cb.message.answer(f"```\n{last_fixed[cb.from_user.id][:4000]}\n```", parse_mode="Markdown")
            await cb.answer()
        else: await cb.answer("Нет данных")
    except: await cb.answer()

@dp.callback_query(F.data == "new")
async def cb_new(cb: types.CallbackQuery):
    try: await cb.message.answer("📤 Жду новый лог"); await cb.answer()
    except: await cb.answer()

@dp.callback_query()
async def cb_all(cb: types.CallbackQuery):
    try: await cb.answer()
    except: pass



@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    asyncio.create_task(dp.start_polling(bot))
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/", response_class=HTMLResponse)
async def root(): return HTMLResponse(content=MINI_APP_HTML)

@app.get("/health")
async def health(): return {"status": "ok"}

@app.get("/api/stats")
async def api_stats(): return await get_knowledge_stats()

@app.post("/api/fix")
async def api_fix(req: Request):
    try:
        data = await req.json()
        code, uid = data.get("code", ""), data.get("user_id", 0)
        
        msg = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": code[:30000]}]
        ans, model, source = await ask_ai(msg, uid)
        
        code_only = ""
        if "```" in ans:
            try: code_only = ans.split("```")[1].split("\n", 1)[1]
            except: pass
            
        return {"fixed_code": ans, "code_only": code_only, "model": model, "source": source}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/rate")
async def api_rate(req: Request):
    try:
        data = await req.json()
        uid, rating = data.get("user_id", 0), data.get("rating", "good")
        if uid in pending_ratings:
            await update_confidence(pending_ratings[uid], rating == "good")
            await save_rating(uid, pending_ratings[uid], rating)
        return {"status": "ok"}
    except: return {"status": "error"}

if __name__ == "__main__":
    logger.info(f"🚀 BotHost AI Running on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
