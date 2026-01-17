from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import aiofiles
import os
from config import BOT_TOKEN, ADMIN_ID
from utils import ask_groq

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# Загружаем промпт один раз
async def load_prompt():
    async with aiofiles.open("system_prompt.txt", "r", encoding="utf-8") as f:
        return (await f.read()).strip()

SYSTEM_PROMPT = asyncio.run(load_prompt())

# Кнопки после ответа
def get_reply_markup():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Решило за минуту!", callback_data="solved")],
        [InlineKeyboardButton("❌ Не помогло", callback_data="not_solved")],
        [InlineKeyboardButton("🔥 Позвать живого Макса", callback_data="call_max")]
    ])
    return keyboard

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я Макс — техподдержка BotHost 24/7 ⚡\n\n"
        "Кидай лог ошибки, лог сборки, скриншот — я починю твоего бота за 2 минуты.\n\n"
        "Уже починил 28 347 ботов. Твой следующий 😉",
        disable_web_page_preview=True
    )

@dp.message(F.text | F.document | F.photo)
async def handle_message(message: types.Message):
    await bot.send_chat_action(message.chat.id, "typing")

    user_text = (message.text or message.caption or "").strip()
    log_content = ""

    # Если файл
    if message.document:
        file = await bot.get_file(message.document.file_id)
        file_path = file.file_path
        downloaded = await bot.download_file(file_path)
        log_content = downloaded.read().decode("utf-8", errors="ignore")[-30000:]  # последние 30к символов

    # Если фото (скрины ошибки)
    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
        await bot.download_file(file.file_path, "temp_screenshot.jpg")
        photo = FSInputFile("temp_screenshot.jpg")
        log_content += "\n\n[Пользователь прислал скриншот ошибки]"

    full_user_message = user_text + "\n\n" + log_content if log_content else user_text

    if not full_user_message.strip():
        await message.reply("Бро, пришли хоть что-то: лог, скрин, описание ошибки...")
        return

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": full_user_message[:32000]}
    ]

    reply = await ask_groq(messages)

    await message.answer(
        reply,
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=get_reply_markup()
    )

@dp.callback_query(F.data == "call_max")
async def call_max(callback: types.CallbackQuery):
    await bot.forward_message(ADMIN_ID, callback.message.chat.id, callback.message.message_id)
    await callback.message.answer(
        "⚡ Живой Макс уже летит в чат!\n"
        "Обычно отвечает в течение 1–3 минут (сейчас онлайн)"
    )
    await callback.answer("Вызвал Макса!")

@dp.callback_query(F.data.in_({"solved", "not_solved"}))
async def feedback(callback: types.CallbackQuery):
    await callback.answer("Спасибо за обратку ❤️")

async def main():
    print("Макс запущен и готов чинить боты 24/7 ⚡")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
