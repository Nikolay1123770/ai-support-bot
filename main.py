from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import asyncio
import os
from config import BOT_TOKEN
from utils import ask_groq

# Настройка бота (v3.x compatible)
bot = Bot(
    token=BOT_TOKEN, 
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

# Системный промпт
SYSTEM_PROMPT = """
Ты — Макс, эксперт техподдержки хостинга BotHost.
Твоя цель: помочь пользователю запустить его Telegram-бота.
1. Если прислали ошибку — найди причину и дай решение (код или команду).
2. Будь краток и вежлив.
3. Используй Markdown для выделения кода.
4. Если не знаешь — предложи написать админу.
"""

def get_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Спасибо, помогло", callback_data="solved")],
        [InlineKeyboardButton(text="🆘 Позвать человека", callback_data="call_admin")]
    ])

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет! Я ИИ-техподдержка BotHost.\n"
        "Скинь мне **лог ошибки**, **скриншот** или файл `main.py`, и я скажу, почему бот не работает."
    )

@dp.message(F.text | F.document | F.photo)
async def handle_request(message: types.Message):
    # Показываем, что бот печатает
    await bot.send_chat_action(message.chat.id, "typing")
    
    user_text = message.text or message.caption or ""
    file_content = ""

    # Если есть документ — читаем его
    if message.document:
        try:
            file = await bot.get_file(message.document.file_id)
            f_io = await bot.download_file(file.file_path)
            file_content = f_io.read().decode('utf-8', errors='ignore')[-10000:] # Читаем последние 10к символов
            user_text += "\n\n[СОДЕРЖИМОЕ ФАЙЛА ЛОГОВ]:\n" + file_content
        except:
            pass
            
    if len(user_text) < 3:
        await message.answer("Пришли пожалуйста описание проблемы или лог ошибки.")
        return

    # Формируем запрос
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text[:30000]} # Обрезаем, чтобы влезло в контекст
    ]

    # Получаем ответ (функция сама переберет модели)
    answer = await ask_groq(messages)

    # Отправляем
    try:
        await message.answer(answer, reply_markup=get_keyboard())
    except:
        # Если Markdown сломался, шлем чистым текстом
        await message.answer(answer, parse_mode=None, reply_markup=get_keyboard())

@dp.callback_query()
async def callbacks(callback: types.CallbackQuery):
    if callback.data == "solved":
        await callback.answer("Рад был помочь! 🚀")
        await callback.message.edit_reply_markup(reply_markup=None)
    elif callback.data == "call_admin":
        await callback.answer("Админ уведомлен!")
        await callback.message.answer("Администратор скоро подключится.")

async def main():
    print("Бот BotHost Support запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
