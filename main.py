from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import asyncio
import io

from config import BOT_TOKEN, ADMIN_ID
from ai_engine import solve_problem
import database as db

# Инициализация
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# Кнопки
def get_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Код работает", callback_data="solved"), 
         InlineKeyboardButton(text="❌ Ошибка осталась", callback_data="not_solved")]
    ])

# --- КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "🛠 **BotHost Engineering Core**\n\n"
        "Я — специализированный ИИ для отладки кода.\n"
        "Моя цель: **Исправить твой код, чтобы он заработал.**\n\n"
        "📥 **Что мне отправить:**\n"
        "1. **Лог ошибки** (Traceback) — обязательно.\n"
        "2. **Файл с кодом** (.py, .js, .go) — желательно.\n\n"
        "🚀 _Движок: DeepSeek-R1 (Logic Optimized)_"
    )

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    stats = await db.get_global_stats()
    await message.answer(f"📊 **Stat:** Users: `{stats['users']}` | Requests: `{stats['requests']}`")

@dp.message(Command("send"))
async def send_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/send", "").strip()
    if not text: return
    users = await db.get_all_users_ids()
    count = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 **Update:**\n{text}")
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"Sent to {count} users.")

# --- ОБРАБОТЧИК (Все типы контента) ---

@dp.message(F.text | F.document | F.photo)
async def handle_engineering_task(message: types.Message):
    if message.text and message.text.startswith("/"): return

    # 1. Регистрация
    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.increment_stats(message.from_user.id)

    # 2. Визуализация "Думаю"
    status_msg = await message.answer("🔍 **Анализирую стек вызовов...**")
    await bot.send_chat_action(message.chat.id, "typing")

    user_query = message.text or message.caption or ""
    file_content = ""

    # 3. Чтение файлов (код или логи)
    if message.document:
        try:
            # Ограничение на размер файла (чтобы не упал) - 1MB
            if message.document.file_size > 1024 * 1024:
                await status_msg.edit_text("⚠️ Файл слишком большой. Пришли лог текстом или файл до 1МБ.")
                return

            file = await bot.get_file(message.document.file_id)
            f_obj = await bot.download_file(file.file_path)
            
            # Пытаемся декодировать
            content = f_obj.read().decode('utf-8', errors='ignore')
            
            # Умная обрезка: берем начало (импорты) и конец (ошибка)
            if len(content) > 20000:
                file_content = content[:5000] + "\n\n...[SKIP]...\n\n" + content[-15000:]
            else:
                file_content = content
                
            user_query += f"\n\n--- FILE CONTENT ({message.document.file_name}) ---\n{file_content}"
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка чтения файла: {e}")
            return

    # 4. Проверка на пустоту
    if len(user_query.strip()) < 5:
        await status_msg.edit_text("🤷‍♂️ Пришли мне **код** или **текст ошибки**.")
        return

    # 5. ЗАПУСК ДВИЖКА
    try:
        # DeepSeek может думать до 10-20 секунд
        answer = await solve_problem(user_query[:50000]) # Большой контекст
        
        # Удаляем сообщение "Анализирую..." и шлем ответ
        await status_msg.delete()
        
        # Защита от кривого Markdown
        try:
            await message.answer(answer, reply_markup=get_kb())
        except:
            await message.answer(answer, parse_mode=None, reply_markup=get_kb())
            
    except Exception as e:
        await status_msg.edit_text(f"💥 Критическая ошибка бота: {e}")

# --- CALLBACKS ---

@dp.callback_query(F.data == "solved")
async def cb_solved(cb: types.CallbackQuery):
    await cb.answer("Отлично!")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("✅ Тикет закрыт. Удачи с деплоем!")

@dp.callback_query(F.data == "not_solved")
async def cb_not(cb: types.CallbackQuery):
    await cb.answer()
    await cb.message.answer("Если решение не помогло — пришли мне **полный файл main.py** и **полный лог** ошибки еще раз.")

async def main():
    await db.init_db()
    print("🚀 ENGINEERING BOT STARTED (DeepSeek R1 Mode)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
