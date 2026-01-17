from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import asyncio
import os

# Импортируем наши модули
from config import BOT_TOKEN, ADMIN_ID
from ai_engine import ask_ai
import database as db

# Настройка бота
bot = Bot(
    token=BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

# Системный промпт
SYSTEM_PROMPT = """
Ты — Макс, Senior DevOps инженер хостинга BotHost.
Твоя задача: анализировать логи и ошибки Telegram-ботов.
1. Сначала проанализируй ошибку.
2. Дай точное решение. Используй жирный шрифт для путей и файлов.
3. Код пиши в блоках ```язык ... ```.
4. Будь вежлив, но краток.
"""

# Клавиатура
def get_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Решено", callback_data="solved")],
        [InlineKeyboardButton(text="👨‍💻 Позвать админа", callback_data="call_admin")]
    ])

# --- СТАРТ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    # Сохраняем пользователя в БД
    await db.add_user(message.from_user.id, message.from_user.username)
    
    await message.answer(
        "👋 **Привет! Я ИИ-техподдержка BotHost.**\n\n"
        "Я умею анализировать логи Python, Node.js, Go и Java.\n"
        "Просто перешли мне сообщение с ошибкой или отправь файл лога.",
        parse_mode="Markdown"
    )

# --- АДМИНКА: СТАТИСТИКА ---
@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return # Игнорируем не админов

    data = await db.get_stats()
    text = (
        f"📊 **Статистика BotHost AI**\n\n"
        f"👤 Пользователей: `{data['users']}`\n"
        f"💬 Запросов решено: `{data['requests']}`\n\n"
        f"🏆 **Топ активных:**\n"
    )
    for u in data['top']:
        text += f"- @{u.username or u.telegram_id}: {u.request_count} запросов\n"
    
    await message.answer(text, parse_mode="Markdown")

# --- АДМИНКА: РАССЫЛКА ---
# Пример: /send Внимание! Завтра техработы.
@dp.message(Command("send"))
async def broadcast_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    text = message.text.replace("/send", "").strip()
    if not text:
        await message.answer("Введи текст рассылки: `/send Текст`")
        return

    await message.answer("🚀 Начинаю рассылку...")
    users = await db.get_all_users()
    count = 0
    
    for user_id in users:
        try:
            await bot.send_message(user_id, f"📢 **Новости BotHost**\n\n{text}")
            count += 1
            await asyncio.sleep(0.05) # Чтобы не словить бан телеграма
        except:
            pass # Бот заблокирован пользователем
            
    await message.answer(f"✅ Рассылка завершена. Доставлено: {count}")

# --- ОБРАБОТКА ВОПРОСОВ ---
@dp.message(F.text | F.document | F.photo)
async def handle_ai(message: types.Message):
    # Проверяем, не команда ли это (чтобы не триггерить ИИ на /stats)
    if message.text and message.text.startswith("/"):
        return

    # Записываем активность юзера
    await db.add_user(message.from_user.id, message.from_user.username)
    await db.increment_stats(message.from_user.id)

    await bot.send_chat_action(message.chat.id, "typing")
    
    user_input = message.text or message.caption or ""
    
    # Читаем файл логов
    if message.document:
        try:
            file = await bot.get_file(message.document.file_id)
            f = await bot.download_file(file.file_path)
            content = f.read().decode('utf-8', errors='ignore')[-15000:] # 15к символов
            user_input += f"\n\n📎 ЛОГ ФАЙЛА:\n{content}"
        except Exception as e:
            user_input += f"\n(Ошибка чтения файла: {e})"

    if len(user_input) < 2:
        await message.answer("Пришли текст ошибки или файл.")
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input[:40000]} # DeepSeek хавает много
    ]

    answer = await ask_ai(messages)
    
    # Безопасная отправка (если Markdown сломан)
    try:
        await message.answer(answer, reply_markup=get_kb())
    except:
        await message.answer(answer, parse_mode=None, reply_markup=get_kb())

@dp.callback_query(F.data == "solved")
async def solved_handler(callback: types.CallbackQuery):
    await callback.answer("Супер! Рад был помочь.")
    await callback.message.edit_text(
        callback.message.text + "\n\n✅ **Проблема решена**",
        parse_mode=None
    )

@dp.callback_query(F.data == "call_admin")
async def admin_handler(callback: types.CallbackQuery):
    await bot.send_message(
        ADMIN_ID, 
        f"🆘 **Вызов поддержки!**\nЮзер: @{callback.from_user.username}\nID: `{callback.from_user.id}`"
    )
    await bot.forward_message(ADMIN_ID, callback.message.chat.id, callback.message.message_id)
    await callback.answer("Админ получил уведомление!")
    await callback.message.answer("Администратор уведомлен и скоро ответит.")

# --- ЗАПУСК ---
async def main():
    # Инициализация базы данных
    await db.init_db()
    print("✅ База данных подключена")
    print("🤖 Бот BotHost Pro v2.0 запущен")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
