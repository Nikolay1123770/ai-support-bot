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

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "7869311061:AAGPstYpuGk7CZTHBQ-_1IL7FCXDyUfIXPY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8473513085"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://supportbothost.bothost.ru")
PORT = int(os.getenv("PORT", "3000"))

# Твой ключ SambaNova
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "01a93dcf-f427-4458-88cb-9c5da2da788e")
SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"

DB_PATH = "knowledge_base.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Модели SambaNova (Самая умная - 405B - стоит первой)
AI_MODELS = [
    {"id": "Meta-Llama-3.1-405B-Instruct", "name": "Llama 3.1 405B 🧠 (Ultra)"},
    {"id": "Meta-Llama-3.1-70B-Instruct", "name": "Llama 3.1 70B ⚡ (Fast)"},
    {"id": "Meta-Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B 🚀 (Lite)"},
]

user_context = {}
last_fixed = {}
pending_ratings = {}
stats = {"requests": 0, "users": set(), "from_cache": 0, "from_ai": 0}

# --- БАЗА ДАННЫХ ---
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

# --- ПРОМПТ И AI ---
SYSTEM_PROMPT = """# ROLE DEFINITION
Ты — Senior Technical Engineer и эксперт по отладке систем (SRE/DevOps/Backend Architect) с 20-летним опытом. Твоя специализация: анализ логов ошибок, трассировок стека (stack traces), дампов памяти и исходного кода. Ты владеешь всеми популярными языками программирования (Python, Java, Go, JS/TS, C++, Rust, SQL) и облачными инфраструктурами.

# OBJECTIVE
Твоя единственная цель — на основе предоставленных логов и кода выявить КОРНЕВУЮ ПРИЧИНУ (Root Cause) проблемы и предоставить точное, безопасное и оптимальное решение.

# ANALYSIS FRAMEWORK (Think Step-by-Step)
Прежде чем дать ответ, ты должен пройти через следующий алгоритм анализа:

1. **АНАЛИЗ КОНТЕКСТА:**
   - Определи язык программирования, фреймворк и среду выполнения.
   - Выдели критические сообщения об ошибках (Error level, Fatal) из "шума" (Info, Debug).
   - Найди конкретную строку кода, вызвавшую сбой.

2. **ВЫЯВЛЕНИЕ ПРИЧИНЫ (DIAGNOSIS):**
   - Это синтаксическая ошибка, логическая ошибка, проблема с зависимостями, утечка памяти, race condition или ошибка конфигурации среды?
   - Если предоставлен только лог, реконструируй возможный сценарий, который привел к ошибке.

3. **ФОРМУЛИРОВАНИЕ РЕШЕНИЯ:**
   - Предложи исправление, которое решает проблему в корне, а не просто "затыкает" ошибку (try-catch без обработки — запрещены, если не обоснованы).
   - Убедись, что решение безопасно (security best practices) и производительно.

# OUTPUT FORMAT (Strictly Follow This Structure)
Твой ответ должен быть структурирован следующим образом:

## 🔍 Диагноз
Краткое описание того, что произошло. (Например: "Произошло исключение NullPointerException в модуле обработки платежей из-за отсутствия валидации входящего JSON").

## 🎯 Корневая причина
Техническое объяснение. (Например: "Переменная `user_id` приходит как `undefined`, потому что API изменило формат ответа, а сервис ожидает старую структуру").

## 🛠 Решение
1. Пошаговая инструкция, что нужно сделать.
2. **ИСПРАВЛЕННЫЙ КОД** (в блоке кода с синтаксической подсветкой). Укажи, какие именно строки были изменены или добавлены. Добавь комментарии в код, объясняющие изменения.

## 💡 Рекомендации (Preventive Measures)
Как предотвратить повторение этой ошибки в будущем? (Например: добавить типизацию, внедрить валидатор схем, настроить алерты или изменить конфигурацию таймаутов).

---
# RULES & CONSTRAINTS
- Если в логах недостаточно информации для 100% решения, предложи наиболее вероятную гипотезу, но честно укажи: "Необходимо проверить также..."
- Не лей воду. Будь краток, профессионален и технически точен.
- Если код содержит уязвимости (SQL Injection, хардкод паролей), укажи на это немедленно, даже если ошибка в другом.
- Отвечай на том же языке, на котором задан вопрос (если вопрос на русском — отвечай на русском)."""

async def ask_ai(messages: list, user_id: int) -> Tuple[str, str, str]:
    user_query = messages[1]["content"]
    
    # 1. Поиск в базе (кэш)
    cached = await search_knowledge_base(user_query)
    if cached and cached["confidence"] > 0.7:
        stats["from_cache"] += 1
        error_hash = get_error_hash(user_query)
        pending_ratings[user_id] = error_hash
        answer = cached["solution"]
        if "💾" not in answer:
            answer += f"\n\n_💾 Ответ из базы знаний (уверенность: {int(cached['confidence']*100)}%)_"
        return answer, "🧠 Личная AI", "cache"
    
    # 2. SambaNova Cloud
    stats["from_ai"] += 1
    if user_id not in user_context: user_context[user_id] = []
    
    history = user_context[user_id][-4:]
    full_messages = [{"role": "system", "content": messages[0]["content"]}] + history + [{"role": "user", "content": messages[1]["content"]}]
    
    headers = {
        "Authorization": f"Bearer {SAMBANOVA_API_KEY}", 
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        # Пробуем модели по очереди, начиная с самой умной (405B)
        for model in AI_MODELS:
            try:
                # logger.info(f"Trying SambaNova model: {model['id']}")
                response = await client.post(
                    SAMBANOVA_URL,
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
                    data = response.json()
                    answer = data["choices"][0]["message"]["content"]
                    
                    # Сохраняем контекст
                    user_context[user_id].append({"role": "user", "content": messages[1]["content"][:1000]})
                    user_context[user_id].append({"role": "assistant", "content": answer[:1000]})
                    
                    # Извлекаем код
                    code_snippet = ""
                    if "```" in answer:
                        try: code_snippet = answer.split("```")[1]
                        except: pass
                    
                    await save_to_knowledge_base(user_query, answer, code_snippet)
                    error_hash = get_error_hash(user_query)
                    pending_ratings[user_id] = error_hash
                    
                    stats["requests"] += 1
                    stats["users"].add(user_id)
                    
                    return answer, model["name"], "sambanova"
                
                elif response.status_code == 429: # Rate limit
                    logger.warning(f"SambaNova 429 on {model['id']}, switching...")
                    await asyncio.sleep(1)
                    continue
                else:
                    logger.error(f"SambaNova Error {response.status_code}: {response.text}")
                    continue
                    
            except Exception as e:
                logger.error(f"AI Connection Error {model['name']}: {e}")
                continue

    return "❌ Серверы AI перегружены или недоступны. Попробуй через 30 секунд.", "Ошибка", "error"

# --- MINI APP ---
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
    <p class="text-sm text-gray-500 mb-2">Powered by SambaNova Llama 3.1 405B</p>
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
      <p class="text-lg font-medium text-green-400">AI Думает...</p>
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
        document.getElementById("source-badge").textContent = data.source === "cache" ? "💾 База" : "🌐 SambaNova";
        
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
      let html = text
        .replace(/</g, "&lt;").replace(/>/g, "&gt;") 
        .replace(/### (.*?)\\n/g, '<span class="md-heading">$1</span>') 
        .replace(/\*\*(.*?)\*\*/g, '<b class="text-white">$1</b>') 
        .replace(/`([^`]+)`/g, '<span class="md-inline-code">$1</span>') 
        .replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<div class="md-code-block">$2</div>') 
        .replace(/\\n/g, '<br>'); 
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

# --- BOT HANDLERS ---
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
    try: 
        await bot.set_chat_menu_button(
            chat_id=m.chat.id, 
            menu_button=MenuButtonWebApp(text="🚀 AI Console", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except: pass
    
    stats_text = "✨ База знаний обновляется..."
    try:
        s = await get_knowledge_stats()
        stats_text = (
            f"🧠 **AI:** `SambaNova Llama 3.1 405B`\n"
            f"⚡ **Решений в базе:** `{s['total_solutions']}`"
        )
    except: pass

    await m.answer(
        f"👋 **Привет, {m.from_user.first_name}!**\n\n"
        f"Я — **BotHost AI**, твой персональный DevOps-инженер.\n"
        f"Я использую мощнейшую модель **Llama 405B** на чипах SambaNova.\n\n"
        f"{stats_text}\n\n"
        f"🛠 **Отправь мне ошибку, и я её решу.**",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть AI Консоль", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton(text="📚 Как это работает?", callback_data="help")]
        ])
    )

@dp.message(F.text | F.document)
async def handle_msg(m: types.Message):
    if m.text and m.text.startswith("/"): return
    
    thinking = await m.answer("🧠 **SambaNova анализирует (405B)...**")
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

    msg = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text[:30000]}]
    
    ans, model, source = await ask_ai(msg, m.from_user.id)
    
    code_only = ""
    if "```" in ans:
        try: code_only = ans.split("```")[1].split("\n", 1)[1]
        except: pass
    last_fixed[m.from_user.id] = code_only if code_only else ans

    await thinking.delete()
    
    src_text = "💾 База" if source == "cache" else "🌐 SambaNova Cloud"
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


# --- SERVER SETUP ---
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
    logger.info(f"🚀 BotHost AI (SambaNova) Running on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
