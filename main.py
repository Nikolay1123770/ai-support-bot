
# ============================================
# BOTHOST AI SUPPORT — ПОЛНАЯ ВЕРСИЯ
# Бот + Mini App в одном файле
# ============================================

import asyncio
import os
import httpx
from datetime import datetime
from contextlib import asynccontextmanager

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

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "sk-or-v1-94c...c21")  # Твой ключ
ADMIN_ID = int(os.getenv("ADMIN_ID", "136271671"))

# Домен твоего бота на BotHost (замени на свой)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://bothostsupport.bothost.ru")

# Порт для веб-сервера (BotHost обычно даёт 8080 или 3000)
PORT = int(os.getenv("PORT", "8080"))

# ============================================
# МОДЕЛИ ИИ (OpenRouter)
# ============================================

MODELS = [
    "anthropic/claude-sonnet-4",
    "deepseek/deepseek-r1",
    "google/gemini-2.5-pro-preview",
    "anthropic/claude-3.5-sonnet",
    "openai/gpt-4o",
    "meta-llama/llama-3.3-70b-instruct",
]

# Хранилище
user_context = {}
last_fixed = {}
user_stats = {}

# ============================================
# AI ENGINE
# ============================================

async def ask_ai(messages: list, user_id: int) -> tuple[str, str]:
    if user_id not in user_context:
        user_context[user_id] = []

    history = user_context[user_id][-8:]
    full_messages = [
        {"role": "system", "content": messages[0]["content"]}
    ] + history + [
        {"role": "user", "content": messages[1]["content"]}
    ]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": WEBAPP_URL,
        "X-Title": "BotHost AI"
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in MODELS:
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": full_messages,
                        "temperature": 0.3,
                        "max_tokens": 8192
                    }
                )

                if response.status_code == 200:
                    answer = response.json()["choices"][0]["message"]["content"]
                    
                    user_context[user_id].append({"role": "user", "content": messages[1]["content"][:1500]})
                    user_context[user_id].append({"role": "assistant", "content": answer[:1500]})
                    
                    return answer, model.split("/")[-1]

                elif response.status_code in [429, 503, 529]:
                    continue
                    
            except Exception as e:
                print(f"[{model}] Error: {e}")
                continue

    return "⚠️ Серверы ИИ перегружены. Попробуй через минуту.", "none"


def clear_context(user_id: int):
    user_context.pop(user_id, None)


# ============================================
# СИСТЕМНЫЙ ПРОМПТ
# ============================================

SYSTEM_PROMPT = """Ты — Макс, легендарный Full-Stack инженер BotHost с 15 годами опыта.
Ты эксперт по Telegram-ботам: Python (aiogram, telebot, pyrogram), Node.js, Go, Bun.

ТВОЯ МИССИЯ: Получить код с ошибкой → Вернуть 100% рабочий исправленный код.

ФОРМАТ ОТВЕТА:

🔍 **Диагноз:**
(Что сломано, 1-3 пункта)

🛠 **Лечение:**
(Что именно исправил)

💻 **Готовый код:**
```python
# ПОЛНЫЙ ИСПРАВЛЕННЫЙ ФАЙЛ
# Скопируй и замени свой файл
```

⚡ **Советы:**
(Дополнительные рекомендации)

ПРАВИЛА:
1. Код ВСЕГДА в блоке ``` с указанием языка
2. Возвращай ВЕСЬ файл целиком
3. Никаких "возможно", "попробуй" — только 100% решения
4. Обновляй устаревший код до стандартов 2025
5. Добавляй комментарии к исправлениям"""

# ============================================
# TELEGRAM BOT
# ============================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()


def get_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Скачать", callback_data="download"),
            InlineKeyboardButton(text="📋 Копировать", callback_data="copy")
        ],
        [
            InlineKeyboardButton(text="🔄 Новый код", callback_data="new"),
            InlineKeyboardButton(text="🧹 Очистить", callback_data="clear")
        ],
        [
            InlineKeyboardButton(text="⭐ Огонь!", callback_data="rate"),
            InlineKeyboardButton(text="👨‍💻 Человек", callback_data="human")
        ]
    ])


def get_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть BotHost AI",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )],
        [InlineKeyboardButton(text="📖 Как пользоваться", callback_data="help")]
    ])


@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    # Устанавливаем кнопку меню с Mini App
    try:
        await bot.set_chat_menu_button(
            chat_id=m.chat.id,
            menu_button=MenuButtonWebApp(
                text="🤖 BotHost AI",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
    except:
        pass

    await m.answer(
        "🚀 **BotHost AI — Ultimate Edition**\n\n"
        "Я подключён ко всем лучшим нейросетям:\n"
        "• Claude Sonnet 4\n"
        "• DeepSeek R1\n"
        "• GPT-4o\n"
        "• Gemini 2.5 Pro\n"
        "• Llama 3.3\n\n"
        "📤 **Отправь мне:**\n"
        "→ Файл `main.py` или `index.js`\n"
        "→ Лог ошибки\n"
        "→ Описание проблемы\n\n"
        "📥 **Получишь:**\n"
        "→ Готовый исправленный файл\n"
        "→ Объяснение что было не так\n\n"
        "💡 Или открой **Mini App** — там ещё удобнее!",
        reply_markup=get_start_keyboard()
    )


@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    
    total_users = len(user_stats)
    total_requests = sum(user_stats.values())
    
    await m.answer(
        f"📊 **BotHost AI Stats**\n\n"
        f"👥 Пользователей: `{total_users}`\n"
        f"💬 Запросов: `{total_requests}`\n"
        f"🧠 Активных контекстов: `{len(user_context)}`"
    )


@dp.message(Command("webapp"))
async def cmd_webapp(m: types.Message):
    await m.answer(
        "🚀 **Открой BotHost AI Mini App**\n\n"
        "Там можно:\n"
        "• Вставлять код прямо в редактор\n"
        "• Скачивать исправленные файлы\n"
        "• Работать быстрее и удобнее",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🤖 Открыть Mini App",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )]
        ])
    )


@dp.message(F.text | F.document | F.photo)
async def handle_message(m: types.Message):
    if m.text and m.text.startswith("/"):
        return

    # Статистика
    user_stats[m.from_user.id] = user_stats.get(m.from_user.id, 0) + 1

    # Думаем
    thinking = await m.answer("🧠 *Анализирую код...*\n⏳ Это займёт 5-20 секунд")
    await bot.send_chat_action(m.chat.id, "typing")

    text = m.text or m.caption or ""
    filename = "main.py"

    # Читаем файл
    if m.document:
        try:
            file = await bot.get_file(m.document.file_id)
            content = (await bot.download_file(file.file_path)).read().decode('utf-8', errors='ignore')
            filename = m.document.file_name or "code.py"
            text += f"\n\n📎 **Файл: {filename}**\n```\n{content[-30000:]}\n```"
        except Exception as e:
            text += f"\n[Ошибка чтения: {e}]"

    if len(text.strip()) < 5:
        await thinking.delete()
        await m.answer("❌ Пришли код, лог или файл")
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text}
    ]

    # Запрос к ИИ
    answer, model_used = await ask_ai(messages, m.from_user.id)

    # Извлекаем код
    if "```" in answer:
        try:
            parts = answer.split("```")
            code_block = parts[1]
            lang = code_block.split("\n")[0].strip().lower()
            code = "\n".join(code_block.split("\n")[1:])
            
            if "python" in lang or "py" in lang:
                filename = filename if filename.endswith(".py") else "main.py"
            elif "javascript" in lang or "js" in lang:
                filename = "index.js"
            elif "go" in lang:
                filename = "main.go"
            elif "typescript" in lang or "ts" in lang:
                filename = "index.ts"
            
            last_fixed[m.from_user.id] = (code.strip(), filename, model_used)
        except:
            pass

    await thinking.delete()

    footer = f"\n\n_⚡ Модель: {model_used}_"

    try:
        await m.answer(answer + footer, reply_markup=get_keyboard())
    except:
        await m.answer(answer[:4000] + footer, parse_mode=None, reply_markup=get_keyboard())


@dp.callback_query(F.data == "download")
async def cb_download(cb: types.CallbackQuery):
    if cb.from_user.id not in last_fixed:
        await cb.answer("Сначала пришли код!")
        return

    code, filename, model = last_fixed[cb.from_user.id]
    
    file = BufferedInputFile(file=code.encode('utf-8'), filename=filename)

    await bot.send_document(
        cb.message.chat.id,
        file,
        caption=f"✅ **Файл:** `{filename}`\n_Исправлено: {model}_"
    )
    await cb.answer("📥 Отправлено!")


@dp.callback_query(F.data == "copy")
async def cb_copy(cb: types.CallbackQuery):
    if cb.from_user.id not in last_fixed:
        await cb.answer("Нет кода")
        return
    
    code, _, _ = last_fixed[cb.from_user.id]
    await cb.message.answer(f"```\n{code[:4000]}\n```", parse_mode="Markdown")
    await cb.answer("Нажми на блок кода для копирования")


@dp.callback_query(F.data == "new")
async def cb_new(cb: types.CallbackQuery):
    await cb.answer("Жду новый код!")
    await cb.message.answer("📤 Отправь файл или ошибку")


@dp.callback_query(F.data == "clear")
async def cb_clear(cb: types.CallbackQuery):
    clear_context(cb.from_user.id)
    await cb.answer("🧹 Память очищена!")


@dp.callback_query(F.data == "rate")
async def cb_rate(cb: types.CallbackQuery):
    await cb.answer("Спасибо! ⭐⭐⭐⭐⭐")


@dp.callback_query(F.data == "help")
async def cb_help(cb: types.CallbackQuery):
    await cb.message.answer(
        "📖 **Как пользоваться BotHost AI:**\n\n"
        "1️⃣ Скопируй свой код с ошибкой\n"
        "2️⃣ Отправь его мне (или файл .py/.js)\n"
        "3️⃣ Подожди 5-20 секунд\n"
        "4️⃣ Получи готовый исправленный код\n"
        "5️⃣ Нажми «Скачать» — замени файл\n\n"
        "💡 Я помню контекст разговора, так что можешь уточнять!"
    )
    await cb.answer()


@dp.callback_query(F.data == "human")
async def cb_human(cb: types.CallbackQuery):
    try:
        await bot.forward_message(ADMIN_ID, cb.message.chat.id, cb.message.message_id)
        await bot.send_message(ADMIN_ID, f"🆘 От: @{cb.from_user.username} | ID: `{cb.from_user.id}`")
    except:
        pass
    await cb.answer("Инженер уведомлён!")
    await cb.message.answer("👨‍💻 Живой человек скоро подключится")


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
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');
    * { font-family: 'JetBrains Mono', monospace; -webkit-tap-highlight-color: transparent; }
    body { background: linear-gradient(180deg, #0a0a0f 0%, #111118 100%); }
    .glass { background: rgba(255,255,255,0.03); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.05); }
    .glow { box-shadow: 0 0 40px rgba(0,255,136,0.15); }
    .glow-text { text-shadow: 0 0 20px rgba(0,255,136,0.5); }
    .code-area { background: #0d1117; border: 1px solid #21262d; caret-color: #00ff88; }
    .code-area:focus { border-color: #00ff88; outline: none; box-shadow: 0 0 0 3px rgba(0,255,136,0.1); }
    .btn-glow { background: linear-gradient(135deg, #00ff88 0%, #00cc6a 100%); box-shadow: 0 4px 20px rgba(0,255,136,0.3); }
    .btn-glow:active { transform: scale(0.98); }
    .spinner { border: 3px solid #1a1a2e; border-top-color: #00ff88; }
    .fade-in { animation: fadeIn 0.3s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    .pulse { animation: pulse 2s infinite; }
  </style>
</head>
<body class="min-h-screen text-white overflow-x-hidden">

  <!-- Header -->
  <header class="glass sticky top-0 z-50 px-4 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-green-500/20 to-emerald-500/10 flex items-center justify-center">
        <span class="text-xl">⚡</span>
      </div>
      <div>
        <h1 class="text-lg font-bold glow-text">BotHost AI</h1>
        <p class="text-[10px] text-gray-500">Claude • DeepSeek • GPT-4o</p>
      </div>
    </div>
    <div id="status" class="flex items-center gap-2">
      <div class="w-2 h-2 rounded-full bg-green-500 pulse"></div>
      <span class="text-xs text-gray-400">Online</span>
    </div>
  </header>

  <!-- Main Content -->
  <main class="p-4 pb-8">
    
    <!-- Input View -->
    <div id="input-view" class="fade-in">
      <div class="mb-3">
        <label class="block text-xs text-gray-500 mb-2 uppercase tracking-wider">Вставь код или лог ошибки</label>
        <textarea 
          id="code-input" 
          class="w-full h-72 code-area rounded-2xl p-4 text-green-400 text-sm resize-none transition-all"
          placeholder="// main.py, index.js или лог ошибки...
// Просто вставь сюда и нажми кнопку ниже"></textarea>
      </div>

      <div class="glass rounded-2xl p-3 mb-4 flex items-center gap-3">
        <span class="text-2xl">💡</span>
        <p class="text-xs text-gray-400">Я использую Claude, DeepSeek и GPT-4o одновременно, чтобы дать лучший ответ</p>
      </div>

      <button 
        id="fix-btn"
        onclick="fixCode()" 
        class="w-full btn-glow py-4 rounded-2xl font-bold text-lg text-black transition-all">
        ⚡ ИСПРАВИТЬ КОД
      </button>
    </div>

    <!-- Loading View -->
    <div id="loading-view" class="hidden fade-in">
      <div class="flex flex-col items-center justify-center py-20">
        <div class="w-16 h-16 spinner rounded-full animate-spin mb-6"></div>
        <p class="text-lg font-semibold glow-text mb-2">Анализирую код...</p>
        <p class="text-sm text-gray-500">Claude, DeepSeek и GPT-4o думают</p>
        <p id="timer" class="text-xs text-gray-600 mt-4">0 сек</p>
      </div>
    </div>

    <!-- Result View -->
    <div id="result-view" class="hidden fade-in">
      <div class="glass rounded-2xl p-3 mb-4 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-green-500">✓</span>
          <span class="text-sm text-gray-300">Исправлено</span>
        </div>
        <span id="model-badge" class="text-xs px-2 py-1 rounded-full bg-green-500/10 text-green-400">Claude</span>
      </div>

      <div class="code-area rounded-2xl p-4 mb-4 max-h-72 overflow-auto">
        <pre id="fixed-code" class="text-green-400 text-xs leading-relaxed whitespace-pre-wrap"></pre>
      </div>

      <div class="grid grid-cols-2 gap-3 mb-4">
        <button onclick="copyCode()" class="glass py-3.5 rounded-xl font-medium text-sm transition-all hover:bg-white/5 active:scale-98">
          📋 Копировать
        </button>
        <button onclick="downloadFile()" class="btn-glow py-3.5 rounded-xl font-medium text-sm text-black">
          📥 Скачать
        </button>
      </div>

      <button onclick="reset()" class="w-full glass py-3 rounded-xl text-sm text-gray-400 hover:text-white transition-all">
        🔄 Исправить другой код
      </button>
    </div>

  </main>

  <script>
    const tg = window.Telegram.WebApp;
    tg.ready();
    tg.expand();
    
    // Тема
    const bg = tg.themeParams.bg_color || '#0a0a0f';
    document.body.style.background = `linear-gradient(180deg, ${bg} 0%, #111118 100%)`;
    tg.setHeaderColor('#0a0a0f');
    tg.setBackgroundColor('#0a0a0f');

    let fixedCode = "";
    let filename = "main.py";
    let timer = null;
    let seconds = 0;

    function startTimer() {
      seconds = 0;
      timer = setInterval(() => {
        seconds++;
        document.getElementById("timer").textContent = seconds + " сек";
      }, 1000);
    }

    function stopTimer() {
      if (timer) clearInterval(timer);
    }

    async function fixCode() {
      const input = document.getElementById("code-input").value.trim();
      if (!input) {
        tg.showAlert("Вставь код или лог ошибки");
        return;
      }

      // UI
      document.getElementById("input-view").classList.add("hidden");
      document.getElementById("loading-view").classList.remove("hidden");
      startTimer();
      tg.HapticFeedback.impactOccurred("light");

      try {
        const res = await fetch("/api/fix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            code: input,
            user_id: tg.initDataUnsafe?.user?.id || 0,
            username: tg.initDataUnsafe?.user?.username || "unknown"
          })
        });

        const data = await res.json();
        
        if (data.error) {
          throw new Error(data.error);
        }

        fixedCode = data.fixed_code;
        filename = data.filename || "main.py";
        
        document.getElementById("fixed-code").textContent = fixedCode;
        document.getElementById("model-badge").textContent = data.model || "AI";
        
        stopTimer();
        document.getElementById("loading-view").classList.add("hidden");
        document.getElementById("result-view").classList.remove("hidden");

        tg.HapticFeedback.notificationOccurred("success");

      } catch (e) {
        stopTimer();
        document.getElementById("loading-view").classList.add("hidden");
        document.getElementById("input-view").classList.remove("hidden");
        tg.showAlert("Ошибка: " + (e.message || "Попробуй ещё раз"));
        tg.HapticFeedback.notificationOccurred("error");
      }
    }

    function copyCode() {
      navigator.clipboard.writeText(fixedCode).then(() => {
        tg.HapticFeedback.impactOccurred("light");
        tg.showAlert("✓ Скопировано в буфер!");
      });
    }

    function downloadFile() {
      const blob = new Blob([fixedCode], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      tg.HapticFeedback.impactOccurred("medium");
    }

    function reset() {
      document.getElementById("result-view").classList.add("hidden");
      document.getElementById("input-view").classList.remove("hidden");
      document.getElementById("code-input").value = "";
      fixedCode = "";
      tg.HapticFeedback.impactOccurred("light");
    }
  </script>
</body>
</html>
"""

# ============================================
# FASTAPI (WEB SERVER)
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск бота в фоне
    asyncio.create_task(start_bot())
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def root():
    return MINI_APP_HTML


@app.get("/health")
async def health():
    return {"status": "ok", "bot": "running"}


@app.post("/api/fix")
async def api_fix(request: Request):
    try:
        data = await request.json()
        code = data.get("code", "")
        user_id = data.get("user_id", 0)
        
        if not code.strip():
            return JSONResponse({"error": "Код пустой"}, status_code=400)

        system = """Ты — эксперт по исправлению кода. 
Верни ТОЛЬКО исправленный код в блоке ```.
Никакого текста до или после — только код.
Если это Python — ```python, если JS — ```javascript"""

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": code}
        ]

        answer, model = await ask_ai(messages, user_id)

        # Извлекаем код
        if "```" in answer:
            parts = answer.split("```")
            code_block = parts[1] if len(parts) > 1 else answer
            lang = code_block.split("\n")[0].strip().lower()
            clean_code = "\n".join(code_block.split("\n")[1:])
            
            ext = ".py"
            if "javascript" in lang or "js" in lang:
                ext = ".js"
            elif "typescript" in lang or "ts" in lang:
                ext = ".ts"
            elif "go" in lang:
                ext = ".go"
            
            return {
                "fixed_code": clean_code.strip(),
                "filename": f"fixed{ext}",
                "model": model
            }
        else:
            return {
                "fixed_code": answer,
                "filename": "fixed.txt",
                "model": model
            }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ============================================
# ЗАПУСК
# ============================================

async def start_bot():
    print("🤖 Telegram Bot запускается...")
    await dp.start_polling(bot)


def main():
    print("=" * 50)
    print("🚀 BotHost AI Ultimate Edition")
    print("=" * 50)
    print(f"📡 Web Server: http://0.0.0.0:{PORT}")
    print(f"🌐 Mini App URL: {WEBAPP_URL}")
    print(f"🤖 Bot: Starting...")
    print("=" * 50)
    
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
