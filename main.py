import asyncio
import os
import json
import hashlib
import httpx
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, Tuple, List

# Telegram
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

# Web Server
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Database
import aiosqlite

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "7869311061:AAGPstYpuGk7CZTHBQ-_1IL7FCXDyUfIXPY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8473513085"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://bot_1768748012_8436_zavik.bothost.ru")
PORT = int(os.getenv("PORT", "3000"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")

# Путь к базе данных
DB_PATH = "knowledge_base.db"

# ============================================
# БЕСПЛАТНЫЕ AI МОДЕЛИ
# ============================================

FREE_MODELS = [
    {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B ⚡"},
    {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B 🦙"},
    {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B 🎯"},
    {"id": "gemma2-9b-it", "name": "Gemma 2 9B 💎"},
]

# Хранилище в памяти
user_context = {}
last_fixed = {}
pending_ratings = {}
stats = {"requests": 0, "users": set(), "from_cache": 0, "from_ai": 0}

# ============================================
# ЛОГИРОВАНИЕ
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ============================================
# БАЗА ДАННЫХ — МОЗГ ОБУЧЕНИЯ
# ============================================

async def init_database():
    """Создаём таблицы для обучения с индексами"""
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
            CREATE TABLE IF NOT EXISTS error_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT UNIQUE,
                error_type TEXT,
                quick_fix TEXT,
                count INTEGER DEFAULT 1
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

        # Добавляем индексы
        await db.execute("CREATE INDEX IF NOT EXISTS idx_error_hash ON solutions(error_hash)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_error_type ON solutions(error_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_confidence ON solutions(confidence)")

        await db.commit()
        logger.info("✅ База данных инициализирована с индексами")

def get_error_hash(text: str) -> str:
    """Создаём уникальный хеш для ошибки"""
    import re
    normalized = re.sub(r'/[\w/]+/', '/PATH/', text)
    normalized = re.sub(r'line \d+', 'line N', normalized)
    normalized = re.sub(r':\d+:', ':N:', normalized)
    normalized = normalized.lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()[:16]

def extract_error_type(text: str) -> str:
    """Определяем тип ошибки"""
    import re

    patterns = {
        "ModuleNotFoundError": r"ModuleNotFoundError|No module named",
        "ImportError": r"ImportError|cannot import",
        "SyntaxError": r"SyntaxError|invalid syntax",
        "TypeError": r"TypeError",
        "AttributeError": r"AttributeError|has no attribute",
        "KeyError": r"KeyError",
        "ValueError": r"ValueError",
        "ConnectionError": r"ConnectionError|Connection refused|timeout",
        "AuthError": r"401|403|Unauthorized|Forbidden|Invalid token",
        "FileError": r"FileNotFoundError|PermissionError|No such file",
    }

    for error_type, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return error_type

    return "UnknownError"

# ============================================
# ЛИЧНАЯ AI — ПОИСК В БАЗЕ ЗНАНИЙ
# ============================================

async def search_knowledge_base(error_text: str) -> Optional[dict]:
    """Ищем похожее решение в базе знаний"""
    error_hash = get_error_hash(error_text)
    error_type = extract_error_type(error_text)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Сначала ищем по точному хешу
        cursor = await db.execute(
            "SELECT * FROM solutions WHERE error_hash = ? AND confidence > 0.6",
            (error_hash,)
        )
        exact_match = await cursor.fetchone()

        if exact_match:
            logger.info(f"🎯 Точное совпадение: {error_hash}")
            return dict(exact_match)

        # Ищем по типу ошибки с высокой уверенностью
        cursor = await db.execute("""
            SELECT * FROM solutions
            WHERE error_type = ? AND confidence > 0.7
            ORDER BY confidence DESC, success_count DESC
            LIMIT 1
        """, (error_type,))
        type_match = await cursor.fetchone()

        if type_match:
            logger.info(f"📂 Совпадение по типу: {error_type}")
            return dict(type_match)

    return None

async def save_to_knowledge_base(error_text: str, solution: str, code_snippet: str = ""):
    """Сохраняем новое решение в базу"""
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
        logger.info(f"💾 Сохранено в базу: {error_hash}")

async def update_confidence(error_hash: str, is_positive: bool):
    """Обновляем уверенность на основе оценки"""
    async with aiosqlite.connect(DB_PATH) as db:
        if is_positive:
            await db.execute("""
                UPDATE solutions SET
                    success_count = success_count + 1,
                    confidence = MIN(1.0, confidence + 0.1),
                    updated_at = CURRENT_TIMESTAMP
                WHERE error_hash = ?
            """, (error_hash,))
        else:
            await db.execute("""
                UPDATE solutions SET
                    fail_count = fail_count + 1,
                    confidence = MAX(0.0, confidence - 0.15),
                    updated_at = CURRENT_TIMESTAMP
                WHERE error_hash = ?
            """, (error_hash,))

        await db.commit()

async def save_rating(user_id: int, error_hash: str, rating: str):
    """Сохраняем оценку пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ratings (user_id, error_hash, rating) VALUES (?, ?, ?)",
            (user_id, error_hash, rating)
        )
        await db.commit()

async def save_user_history(user_id: int, query: str, response: str, source: str):
    """Сохраняем историю запросов"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_history (user_id, query, response, source) VALUES (?, ?, ?, ?)",
            (user_id, query[:500], response[:2000], source)
        )
        await db.commit()

async def get_knowledge_stats() -> dict:
    """Получаем статистику базы знаний"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM solutions")
        total_solutions = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM solutions WHERE confidence > 0.7")
        reliable_solutions = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM ratings WHERE rating = 'good'")
        positive_ratings = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM ratings WHERE rating = 'bad'")
        negative_ratings = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM user_history")
        total_queries = (await cursor.fetchone())[0]

        return {
            "total_solutions": total_solutions,
            "reliable_solutions": reliable_solutions,
            "positive_ratings": positive_ratings,
            "negative_ratings": negative_ratings,
            "total_queries": total_queries
        }

# ============================================
# СИСТЕМНЫЙ ПРОМПТ
# ============================================

SYSTEM_PROMPT = """Ты — Макс, опытный DevOps-инженер с 15 годами опыта.
Ты специалист по анализу логов Telegram-ботов на Python, Node.js, Go.

ФОРМАТ ОТВЕТА:

📍 **Где ошибка:**
`[точная строка или файл]`

❌ **Что произошло:**
[Простое объяснение]

💡 **Почему:**
[Причина ошибки]

🛠 **Решение:**

**Вариант 1:**
```python
# код исправления
```

**Вариант 2:**
[альтернатива если есть]

⚡ **Команда:**
```bash
pip install something
```

📝 **Совет:**
[Как избежать в будущем]"""

# ============================================
# УМНЫЙ AI ENGINE
# ============================================

async def ask_ai(messages: list, user_id: int) -> Tuple[str, str, str]:
    """
    Умный запрос: сначала база знаний, потом Groq
    Возвращает: (ответ, модель, источник)
    """
    user_query = messages[1]["content"]

    # Поиск в базе знаний
    cached = await search_knowledge_base(user_query)

    if cached and cached["confidence"] > 0.7:
        stats["from_cache"] += 1
        answer = cached["solution"]
        answer += f"\n\n_💾 Ответ из базы знаний (уверенность: {int(cached['confidence']*100)}%)_"
        error_hash = get_error_hash(user_query)
        pending_ratings[user_id] = error_hash
        await save_user_history(user_id, user_query, answer, "cache")
        return answer, "🧠 Личная AI", "cache"

    # Запрос к Groq API
    stats["from_ai"] += 1

    if user_id not in user_context:
        user_context[user_id] = []

    history = user_context[user_id][-6:]
    full_messages = [
        {"role": "system", "content": messages[0]["content"]}
    ] + history + [
        {"role": "user", "content": messages[1]["content"]}
    ]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in FREE_MODELS:
            try:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model["id"],
                        "messages": full_messages,
                        "temperature": 0.3,
                        "max_tokens": 4000
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]

                    # Сохраняем контекст
                    user_context[user_id].append({
                        "role": "user",
                        "content": messages[1]["content"][:1500]
                    })
                    user_context[user_id].append({
                        "role": "assistant",
                        "content": answer[:1500]
                    })

                    # Сохраняем в базу знаний
                    code_snippet = ""
                    if "```" in answer:
                        try:
                            code_snippet = answer.split("```")[1]
                        except:
                            pass

                    await save_to_knowledge_base(user_query, answer, code_snippet)
                    error_hash = get_error_hash(user_query)
                    pending_ratings[user_id] = error_hash
                    await save_user_history(user_id, user_query, answer, "groq")
                    stats["requests"] += 1
                    stats["users"].add(user_id)

                    return answer, model["name"], "groq"

                elif response.status_code == 429:
                    logger.warning(f"Rate limit exceeded for model {model['id']}")
                    await asyncio.sleep(2)
                    continue

            except httpx.RequestError as e:
                logger.error(f"Ошибка запроса к Groq API: {e}")
                continue
            except Exception as e:
                logger.error(f"Неожиданная ошибка: {e}")
                continue

    return "❌ AI недоступен. Попробуй позже.", "Ошибка", "error"

def clear_context(user_id: int):
    if user_id in user_context:
        user_context[user_id] = []

# ============================================
# MINI APP HTML
# ============================================

MINI_APP_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>BotHost AI</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #00ff88;
      --primary-dark: #00cc6a;
      --bg-dark: #0a0a0f;
      --bg-card: #12121a;
      --bg-input: #1a1a24;
      --text-primary: #ffffff;
      --text-secondary: #8b8b9e;
      --border: #2a2a3e;
      --error: #ff4757;
      --success: #00ff88;
    }

    .light-theme {
      --primary: #00d4aa;
      --primary-dark: #00b894;
      --bg-dark: #f5f5f5;
      --bg-card: #ffffff;
      --bg-input: #e8e8e8;
      --text-primary: #000000;
      --text-secondary: #666666;
      --border: #d1d5db;
      --error: #ff4757;
      --success: #00d4aa;
    }

    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    body {
      font-family: 'Inter', -apple-system, sans-serif;
      background: var(--bg-dark);
      color: var(--text-primary);
      min-height: 100vh;
      overflow-x: hidden;
    }

    .bg-animated {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: radial-gradient(circle at 20% 80%, rgba(0, 255, 136, 0.08) 0%, transparent 50%),
                  radial-gradient(circle at 80% 20%, rgba(0, 204, 106, 0.06) 0%, transparent 50%),
                  radial-gradient(circle at 40% 40%, rgba(0, 255, 136, 0.04) 0%, transparent 60%),
                  var(--bg-dark);
      z-index: -1;
    }

    .bg-grid {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background-image: linear-gradient(rgba(0, 255, 136, 0.03) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(0, 255, 136, 0.03) 1px, transparent 1px);
      background-size: 50px 50px;
      z-index: -1;
    }

    .logo-glow {
      text-shadow: 0 0 10px rgba(0, 255, 136, 0.8),
                   0 0 20px rgba(0, 255, 136, 0.6),
                   0 0 40px rgba(0, 255, 136, 0.4);
      animation: pulse-glow 2s ease-in-out infinite;
    }

    @keyframes pulse-glow {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.8; }
    }

    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      backdrop-filter: blur(10px);
    }

    .card-glow {
      box-shadow: 0 0 20px rgba(0, 255, 136, 0.1),
                  inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
      color: #000;
      font-weight: 600;
      border: none;
      border-radius: 12px;
      padding: 16px 24px;
      cursor: pointer;
      transition: all 0.3s ease;
      box-shadow: 0 4px 20px rgba(0, 255, 136, 0.3);
    }

    .btn-primary:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 30px rgba(0, 255, 136, 0.4);
    }

    .btn-primary:active {
      transform: scale(0.98);
    }

    .btn-secondary {
      background: var(--bg-input);
      color: var(--text-primary);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 20px;
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .btn-secondary:hover {
      background: var(--border);
    }

    .code-editor {
      font-family: 'JetBrains Mono', monospace;
      background: var(--bg-input);
      border: 2px solid var(--border);
      border-radius: 16px;
      color: #e2e8f0;
      resize: none;
      transition: all 0.3s ease;
    }

    .code-editor:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 4px rgba(0, 255, 136, 0.1);
    }

    .code-editor::placeholder {
      color: #4a4a5e;
    }

    .message {
      animation: message-in 0.3s ease;
    }

    @keyframes message-in {
      from {
        opacity: 0;
        transform: translateY(10px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .message-ai {
      background: linear-gradient(135deg, rgba(0, 255, 136, 0.1) 0%, rgba(0, 204, 106, 0.05) 100%);
      border-left: 3px solid var(--primary);
    }

    .message-user {
      background: var(--bg-input);
      border-left: 3px solid #6366f1;
    }

    .code-block {
      font-family: 'JetBrains Mono', monospace;
      background: #0d0d14;
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
      font-size: 13px;
      line-height: 1.5;
    }

    .status-bar {
      background: rgba(0, 255, 136, 0.1);
      border: 1px solid rgba(0, 255, 136, 0.2);
      border-radius: 100px;
      padding: 6px 12px;
      font-size: 12px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--primary);
      animation: blink 1.5s infinite;
    }

    @keyframes blink {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.3; }
    }

    .loader {
      width: 60px;
      height: 60px;
      border: 3px solid var(--border);
      border-top-color: var(--primary);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .typing-indicator {
      display: flex;
      gap: 4px;
      padding: 8px 12px;
    }

    .typing-dot {
      width: 8px;
      height: 8px;
      background: var(--primary);
      border-radius: 50%;
      animation: typing 1.4s infinite;
    }

    .typing-dot:nth-child(2) { animation-delay: 0.2s; }
    .typing-dot:nth-child(3) { animation-delay: 0.4s; }

    @keyframes typing {
      0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
      30% { transform: translateY(-8px); opacity: 1; }
    }

    ::-webkit-scrollbar {
      width: 6px;
    }

    ::-webkit-scrollbar-track {
      background: var(--bg-dark);
    }

    ::-webkit-scrollbar-thumb {
      background: var(--border);
      border-radius: 3px;
    }

    ::-webkit-scrollbar-thumb:hover {
      background: #3a3a4e;
    }

    .fade-in {
      animation: fadeIn 0.4s ease;
    }

    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }

    .hl-error { color: #ff6b6b; font-weight: 600; }
    .hl-success { color: #00ff88; }
    .hl-warning { color: #ffd93d; }
    .hl-info { color: #6366f1; }
    .hl-command { color: #00d4ff; }
  </style>
</head>
<body>
  <div class="bg-animated"></div>
  <div class="bg-grid"></div>

  <div class="min-h-screen flex flex-col p-4 pb-6 max-w-2xl mx-auto">
    <!-- Хедер -->
    <header class="text-center py-6 fade-in">
      <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-green-500/20 to-emerald-500/10 mb-4">
        <span class="text-4xl">🧠</span>
      </div>
      <h1 class="text-2xl font-bold logo-glow mb-2" style="color: var(--primary);">BotHost AI</h1>
      <p class="text-sm text-gray-500 mb-3">Умный помощник по коду</p>

      <!-- Статус -->
      <div class="flex justify-center gap-3 flex-wrap">
        <div class="status-bar">
          <div class="status-dot"></div>
          <span style="color: var(--primary);">Online</span>
        </div>
        <div class="status-bar" id="stats-bar">
          <span>💾</span>
          <span id="solutions-count">—</span>
          <span class="text-gray-500">решений</span>
        </div>
      </div>
    </header>

    <!-- Контент -->
    <main class="flex-1 flex flex-col">
      <!-- Экран ввода -->
      <div id="input-screen" class="flex-1 flex flex-col fade-in">
        <!-- Быстрые подсказки -->
        <div class="grid grid-cols-2 gap-2 mb-4">
          <button onclick="insertExample('python')" class="btn-secondary text-left text-xs py-3">
            <span class="text-lg mb-1 block">🐍</span>
            Python ошибка
          </button>
          <button onclick="insertExample('node')" class="btn-secondary text-left text-xs py-3">
            <span class="text-lg mb-1 block">💚</span>
            Node.js ошибка
          </button>
        </div>

        <!-- Поле ввода -->
        <div class="flex-1 flex flex-col mb-4">
          <label class="text-xs text-gray-500 mb-2 flex items-center gap-2">
            <span>📋</span>
            Вставь лог ошибки или код
          </label>
          <textarea
            id="input-code"
            class="code-editor flex-1 min-h-[200px] p-4 text-sm"
            placeholder="Traceback (most recent call last):
  File &quot;main.py&quot;, line 42, in <module>
    bot = Bot(token=TOKEN, parse_mode='HTML')
TypeError: ...

Вставь сюда лог ошибки — я проанализирую и помогу исправить 🔍"></textarea>
        </div>

        <!-- Кнопка анализа -->
        <button id="analyze-btn" onclick="analyze()" class="btn-primary w-full text-lg">
          <span class="mr-2">🔍</span>
          Анализировать
        </button>

        <!-- Подсказка -->
        <p class="text-center text-xs text-gray-600 mt-4">
          🧠 AI учится на каждом запросе • Оценивай ответы чтобы я стал умнее
        </p>
      </div>

      <!-- Экран загрузки -->
      <div id="loading-screen" class="hidden flex-1 flex flex-col items-center justify-center fade-in">
        <div class="loader mb-6"></div>
        <p class="text-lg font-medium mb-2" style="color: var(--primary);">Анализирую...</p>
        <p class="text-sm text-gray-500 mb-4" id="loading-status">Проверяю базу знаний</p>
        <div class="typing-indicator">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
        <p class="text-xs text-gray-600 mt-6" id="timer">0 сек</p>
      </div>

      <!-- Экран результата -->
      <div id="result-screen" class="hidden flex-1 flex flex-col fade-in">
        <!-- Заголовок результата -->
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-green-500/20 to-emerald-500/10 flex items-center justify-center">
              <span class="text-xl">✨</span>
            </div>
            <div>
              <p class="font-medium">Анализ готов</p>
              <p class="text-xs text-gray-500" id="result-meta">—</p>
            </div>
          </div>
          <div class="flex gap-2">
            <span id="source-badge" class="status-bar text-xs">💾 База</span>
          </div>
        </div>

        <!-- Результат -->
        <div class="card card-glow flex-1 overflow-hidden mb-4">
          <div class="p-4 max-h-[50vh] overflow-y-auto">
            <div id="result-content" class="text-sm leading-relaxed"></div>
          </div>
        </div>

        <!-- Оценка -->
        <div class="card p-4 mb-4">
          <p class="text-xs text-gray-500 mb-3 text-center">Это помогло?</p>
          <div class="flex gap-3">
            <button onclick="rate('good')" class="flex-1 btn-secondary py-4 hover:bg-green-500/10 hover:border-green-500/30 transition-all">
              <span class="text-2xl block mb-1">👍</span>
              <span class="text-xs">Да, спасибо!</span>
            </button>
            <button onclick="rate('bad')" class="flex-1 btn-secondary py-4 hover:bg-red-500/10 hover:border-red-500/30 transition-all">
              <span class="text-2xl block mb-1">👎</span>
              <span class="text-xs">Не помогло</span>
            </button>
          </div>
        </div>

        <!-- Действия -->
        <div class="grid grid-cols-2 gap-3 mb-4">
          <button onclick="copyResult()" class="btn-secondary py-3">
            <span class="mr-2">📋</span>
            Копировать
          </button>
          <button onclick="copyCodeOnly()" class="btn-primary py-3">
            <span class="mr-2">💻</span>
            Только код
          </button>
        </div>

        <!-- Новый анализ -->
        <button onclick="reset()" class="btn-secondary w-full py-4">
          <span class="mr-2">🔄</span>
          Новый анализ
        </button>
      </div>
    </main>

    <!-- Футер -->
    <footer class="text-center pt-4 mt-auto">
      <p class="text-xs text-gray-600">
        Powered by <span style="color: var(--primary);">Llama 3.3</span> •
        <span style="color: var(--primary);">Mixtral</span> •
        <span style="color: var(--primary);">Gemma 2</span>
      </p>
    </footer>
  </div>

  <script>
    // Telegram WebApp
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();

    try {
      tg.setHeaderColor('#0a0a0f');
      tg.setBackgroundColor('#0a0a0f');
    } catch(e) {}

    // Переменные
    let resultText = "";
    let codeOnly = "";
    let timerInterval = null;
    let seconds = 0;
    let theme = "dark";

    // Загружаем статистику
    loadStats();

    // Переключение темы
    function toggleTheme() {
      theme = theme === "dark" ? "light" : "dark";
      document.body.classList.toggle("light-theme");
      localStorage.setItem("theme", theme);
    }

    // Проверяем сохранённую тему
    if (localStorage.getItem("theme") === "light") {
      toggleTheme();
    }

    async function loadStats() {
      try {
        const res = await fetch("/api/stats");
        const data = await res.json();
        document.getElementById("solutions-count").textContent = data.total_solutions || 0;
      } catch(e) {
        document.getElementById("solutions-count").textContent = "0";
      }
    }

    // Примеры для быстрого ввода
    function insertExample(type) {
      const examples = {
        python: `Traceback (most recent call last):
  File "main.py", line 10, in <module>
    from aiogram import Bot
ModuleNotFoundError: No module named 'aiogram'`,
        node: `Error: Cannot find module 'express'
    at Function.Module._resolveFilename (node:internal/modules/cjs/loader:933:15)
    at Function.Module._load (node:internal/modules/cjs/loader:778:27)`
      };

      document.getElementById("input-code").value = examples[type] || "";
      haptic("light");
    }

    // Анализ
    async function analyze() {
      const input = document.getElementById("input-code").value.trim();

      if (!input) {
        tg.showAlert("Вставь лог ошибки!");
        return;
      }

      if (input.length < 15) {
        tg.showAlert("Лог слишком короткий. Вставь полный текст ошибки.");
        return;
      }

      // Показываем загрузку
      showScreen("loading");
      startTimer();
      updateLoadingStatus("Проверяю базу знаний...");
      haptic("light");

      try {
        // Имитация этапов
        setTimeout(() => updateLoadingStatus("Анализирую ошибку..."), 1500);
        setTimeout(() => updateLoadingStatus("Готовлю решение..."), 4000);

        const response = await fetch("/api/fix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code: input,
            user_id: tg.initDataUnsafe?.user?.id || 0
          })
        });

        const data = await response.json();

        if (data.error) {
          throw new Error(data.error);
        }

        resultText = data.fixed_code || "";
        codeOnly = data.code_only || "";

        // Отображаем результат
        document.getElementById("result-content").innerHTML = formatResult(resultText);
        document.getElementById("result-meta").textContent = `${data.model || 'AI'} • ${seconds} сек`;
        document.getElementById("source-badge").innerHTML =
          data.source === "cache" ? "💾 Из базы" : "🌐 Groq";

        stopTimer();
        showScreen("result");
        haptic("success");

        // Обновляем статистику
        loadStats();

      } catch (error) {
        stopTimer();
        showScreen("input");
        tg.showAlert("Ошибка: " + (error.message || "Попробуй ещё раз"));
        haptic("error");
      }
    }

    // Форматирование результата
    function formatResult(text) {
      return text
        .replace(/(📍|❌|💡|🛠|⚡|📝|💻|💾|✅)/g, '<span style="font-size: 1.3em;">$1</span>')
        .replace(/\*\*(.*?)\*\*/g, '<strong class="text-white">$1</strong>')
        .replace(/`([^`]+)`/g, '<code class="bg-black/50 px-1.5 py-0.5 rounded text-yellow-400 text-xs">$1</code>')
        .replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
          return `<div class="code-block my-3"><div class="text-xs text-gray-500 mb-2">${lang || 'code'}</div><code class="text-green-400">${escapeHtml(code.trim())}</code></div>`;
        })
        .replace(/(Error|Exception|Failed|Traceback)/gi, '<span class="hl-error">$1</span>')
        .replace(/(pip install \S+)/g, '<span class="hl-command">$1</span>')
        .replace(/(npm install \S+)/g, '<span class="hl-command">$1</span>')
        .replace(/(Вариант \d)/g, '<span class="hl-warning">$1</span>')
        .replace(/(✅|Успех|Решено)/g, '<span class="hl-success">$1</span>')
        .replace(/\n/g, '<br>');
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    // Оценка
    async function rate(rating) {
      try {
        await fetch("/api/rate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id: tg.initDataUnsafe?.user?.id || 0,
            rating: rating
          })
        });

        if (rating === "good") {
          tg.showAlert("✅ Спасибо! AI стал умнее!");
        } else {
          tg.showAlert("📝 Учтём! Попробуй уточнить запрос.");
        }

        haptic("light");
        loadStats();

      } catch(e) {
        console.error(e);
      }
    }

    // Копирование
    function copyResult() {
      navigator.clipboard.writeText(resultText).then(() => {
        tg.showAlert("✅ Скопировано!");
        haptic("light");
      });
    }

    function copyCodeOnly() {
      if (codeOnly) {
        navigator.clipboard.writeText(codeOnly).then(() => {
          tg.showAlert("✅ Код скопирован!");
          haptic("light");
        });
      } else {
        tg.showAlert("В ответе нет блока кода");
      }
    }

    // Сброс
    function reset() {
      document.getElementById("input-code").value = "";
      showScreen("input");
      haptic("light");
    }

    // Утилиты
    function showScreen(name) {
      document.getElementById("input-screen").classList.add("hidden");
      document.getElementById("loading-screen").classList.add("hidden");
      document.getElementById("result-screen").classList.add("hidden");
      document.getElementById(name + "-screen").classList.remove("hidden");
    }

    function startTimer() {
      seconds = 0;
      timerInterval = setInterval(() => {
        seconds++;
        document.getElementById("timer").textContent = seconds + " сек";
      }, 1000);
    }

    function stopTimer() {
      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }
    }

    function updateLoadingStatus(text) {
      document.getElementById("loading-status").textContent = text;
    }

    function haptic(type) {
      try {
        if (type === "success") {
          tg.HapticFeedback.notificationOccurred("success");
        } else if (type === "error") {
          tg.HapticFeedback.notificationOccurred("error");
        } else {
          tg.HapticFeedback.impactOccurred("light");
        }
      } catch(e) {}
    }
  </script>
</body>
</html>
"""

# ============================================
# TELEGRAM BOT
# ============================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

def get_keyboard(show_rating=True):
    buttons = []

    if show_rating:
        buttons.append([
            InlineKeyboardButton(text="👍 Помогло", callback_data="rate_good"),
            InlineKeyboardButton(text="👎 Не помогло", callback_data="rate_bad")
        ])

    buttons.extend([
        [
            InlineKeyboardButton(text="📋 Копировать", callback_data="copy"),
            InlineKeyboardButton(text="📥 Скачать", callback_data="download")
        ],
        [
            InlineKeyboardButton(text="🔄 Новый", callback_data="new"),
            InlineKeyboardButton(text="👨‍💻 Человек", callback_data="human")
        ]
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🧠 Открыть BotHost AI",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )],
        [InlineKeyboardButton(text="📊 Статистика AI", callback_data="ai_stats")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
    ])

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    try:
        await bot.set_chat_menu_button(
            chat_id=m.chat.id,
            menu_button=MenuButtonWebApp(text="🧠 AI", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except:
        pass

    kb_stats = await get_knowledge_stats()

    await m.answer(
        f"🧠 **BotHost AI — Самообучающийся помощник**\n\n"
        f"Я становлюсь умнее с каждым запросом!\n\n"
        f"📊 **Моя база знаний:**\n"
        f"• 💾 Решений: `{kb_stats['total_solutions']}`\n"
        f"• ✅ Надёжных: `{kb_stats['reliable_solutions']}`\n"
        f"• 👍 Положительных оценок: `{kb_stats['positive_ratings']}`\n\n"
        f"📤 **Как использовать:**\n"
        f"→ Пришли лог ошибки\n"
        f"→ Получи анализ и решение\n"
        f"→ Оцени ответ — я запомню!\n\n"
        f"_Чем больше оценок — тем умнее я становлюсь_ 🚀",
        reply_markup=get_start_keyboard()
    )

@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    kb_stats = await get_knowledge_stats()

    cache_rate = 0
    if stats["from_cache"] + stats["from_ai"] > 0:
        cache_rate = int(stats["from_cache"] / (stats["from_cache"] + stats["from_ai"]) * 100)

    await m.answer(
        f"📊 **Статистика BotHost AI**\n\n"
        f"**🧠 База знаний:**\n"
        f"• Всего решений: `{kb_stats['total_solutions']}`\n"
        f"• Надёжных (>70%): `{kb_stats['reliable_solutions']}`\n"
        f"• Всего запросов: `{kb_stats['total_queries']}`\n\n"
        f"**📈 Оценки:**\n"
        f"• 👍 Положительных: `{kb_stats['positive_ratings']}`\n"
        f"• 👎 Отрицательных: `{kb_stats['negative_ratings']}`\n\n"
        f"**⚡ Сессия:**\n"
        f"• Из кэша: `{stats['from_cache']}`\n"
        f"• Из Groq: `{stats['from_ai']}`\n"
        f"• Эффективность кэша: `{cache_rate}%`"
    )

@dp.message(Command("brain"))
async def cmd_brain(m: types.Message):
    """Показать что знает AI"""
    if m.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT error_type, COUNT(*) as count, AVG(confidence) as avg_conf
            FROM solutions
            GROUP BY error_type
            ORDER BY count DESC
            LIMIT 10
        """)
        rows = await cursor.fetchall()

    text = "🧠 **Что я знаю:**\n\n"
    for row in rows:
        conf = int(row["avg_conf"] * 100)
        text += f"• {row['error_type']}: {row['count']} решений ({conf}% уверенность)\n"

    await m.answer(text)

@dp.message(F.text | F.document)
async def handle_message(m: types.Message):
    if m.text and m.text.startswith("/"):
        return

    thinking = await m.answer("🧠 *Думаю...*\n💾 Проверяю базу знаний...")
    await bot.send_chat_action(m.chat.id, "typing")

    text = m.text or m.caption or ""

    if m.document:
        try:
            file = await bot.get_file(m.document.file_id)
            content = (await bot.download_file(file.file_path)).read().decode('utf-8', errors='ignore')
            text += f"\n\n{content[-25000:]}"
        except:
            pass

    if len(text.strip()) < 10:
        await thinking.delete()
        await m.answer("❌ Пришли лог ошибки для анализа")
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Проанализируй:\n\n{text}"}
    ]

    answer, model_name, source = await ask_ai(messages, m.from_user.id)

    # Сохраняем для скачивания
    code_only = ""
    if "```" in answer:
        try:
            code_only = answer.split("```")[1]
            code_only = "\n".join(code_only.split("\n")[1:])
        except:
            pass

    last_fixed[m.from_user.id] = (code_only or answer, "fix.py", model_name)

    await thinking.delete()

    source_text = "💾 из базы знаний" if source == "cache" else "🌐 от Groq"
    footer = f"\n\n_⚡ {model_name} | {source_text}_"

    try:
        await m.answer(answer + footer, reply_markup=get_keyboard())
    except:
        await m.answer(answer[:4000] + footer, parse_mode=None, reply_markup=get_keyboard())

@dp.callback_query(F.data == "rate_good")
async def cb_rate_good(cb: types.CallbackQuery):
    user_id = cb.from_user.id

    if user_id in pending_ratings:
        error_hash = pending_ratings[user_id]
        await update_confidence(error_hash, True)
        await save_rating(user_id, error_hash, "good")
        del pending_ratings[user_id]

    await cb.answer("👍 Спасибо! AI стал умнее!")
    await cb.message.edit_reply_markup(reply_markup=get_keyboard(show_rating=False))

@dp.callback_query(F.data == "rate_bad")
async def cb_rate_bad(cb: types.CallbackQuery):
    user_id = cb.from_user.id

    if user_id in pending_ratings:
        error_hash = pending_ratings[user_id]
        await update_confidence(error_hash, False)
        await save_rating(user_id, error_hash, "bad")
        del pending_ratings[user_id]

    await cb.answer("📝 Учтём! Попробуй переформулировать запрос")
    await cb.message.edit_reply_markup(reply_markup=get_keyboard(show_rating=False))

@dp.callback_query(F.data == "ai_stats")
async def cb_ai_stats(cb: types.CallbackQuery):
    kb_stats = await get_knowledge_stats()
    await cb.message.answer(
        f"🧠 **База знаний AI:**\n\n"
        f"💾 Решений: `{kb_stats['total_solutions']}`\n"
        f"✅ Надёжных: `{kb_stats['reliable_solutions']}`\n"
        f"👍 Оценок: `{kb_stats['positive_ratings']}`"
    )
    await cb.answer()

@dp.callback_query(F.data == "download")
async def cb_download(cb: types.CallbackQuery):
    if cb.from_user.id not in last_fixed:
        await cb.answer("Нет данных")
        return

    content, filename, _ = last_fixed[cb.from_user.id]
    file = BufferedInputFile(file=content.encode('utf-8'), filename=filename)
    await bot.send_document(cb.message.chat.id, file)
    await cb.answer("📥 Отправлено!")

@dp.callback_query(F.data == "copy")
async def cb_copy(cb: types.CallbackQuery):
    if cb.from_user.id not in last_fixed:
        await cb.answer("Нет данных")
        return

    content, _, _ = last_fixed[cb.from_user.id]
    await cb.message.answer(f"```\n{content[:4000]}\n```", parse_mode="Markdown")
    await cb.answer()

@dp.callback_query(F.data == "new")
async def cb_new(cb: types.CallbackQuery):
    await cb.answer("Жду новый лог!")
    await cb.message.answer("📤 Отправь лог ошибки")

@dp.callback_query(F.data == "help")
async def cb_help(cb: types.CallbackQuery):
    await cb.message.answer(
        "📖 **Как я учусь:**\n\n"
        "1️⃣ Ты присылаешь лог ошибки\n"
        "2️⃣ Я ищу решение в своей базе\n"
        "3️⃣ Если не нашёл — спрашиваю Groq\n"
        "4️⃣ Сохраняю решение в базу\n"
        "5️⃣ Ты оцениваешь ответ\n"
        "6️⃣ Я запоминаю что работает!\n\n"
        "🧠 _Чем больше оценок — тем умнее я становлюсь!_"
    )
    await cb.answer()

@dp.callback_query(F.data == "human")
async def cb_human(cb: types.CallbackQuery):
    await bot.send_message(ADMIN_ID, f"🆘 @{cb.from_user.username} | ID: {cb.from_user.id}")
    await cb.answer("Админ уведомлён!")

# ============================================
# FASTAPI SERVER
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    asyncio.create_task(start_bot())
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/", response_class=HTMLResponse)
async def root():
    return MINI_APP_HTML

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/favicon.ico")
async def favicon():
    return HTMLResponse(content="", status_code=204)

@app.get("/api/stats")
async def api_stats():
    return await get_knowledge_stats()

@app.post("/api/fix")
async def api_fix(request: Request):
    try:
        data = await request.json()
        code = data.get("code", "")
        user_id = data.get("user_id", 0)

        if not code.strip():
            return JSONResponse({"error": "Пусто"}, status_code=400)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Проанализируй:\n\n{code[:30000]}"}
        ]

        answer, model, source = await ask_ai(messages, user_id)

        code_only = ""
        if "```" in answer:
            try:
                code_only = "\n".join(answer.split("```")[1].split("\n")[1:]).strip()
            except:
                pass

        return {
            "fixed_code": answer,
            "code_only": code_only,
            "model": model,
            "source": source
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/rate")
async def api_rate(request: Request):
    try:
        data = await request.json()
        user_id = data.get("user_id", 0)
        rating = data.get("rating", "good")

        if user_id in pending_ratings:
            error_hash = pending_ratings[user_id]
            await update_confidence(error_hash, rating == "good")
            await save_rating(user_id, error_hash, rating)

        return {"status": "ok"}
    except:
        return {"status": "error"}

# ============================================
# ЗАПУСК
# ============================================

async def start_bot():
    logger.info("🤖 Telegram Bot запускается...")
    await dp.start_polling(bot)

def main():
    logger.info("=" * 50)
    logger.info("🧠 BotHost AI — Самообучающаяся система")
    logger.info("=" * 50)
    logger.info(f"📡 Web: http://0.0.0.0:{PORT}")
    logger.info(f"💾 База: {DB_PATH}")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
