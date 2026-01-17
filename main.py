from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import asyncio
import io

from config import BOT_TOKEN, ADMIN_ID
from utils import ask_ai, transcribe_voice
import database as db

# Инициализация
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# Баннер (Киберпанк стиль)
BANNER = "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?q=80&w=2070&auto=format&fit=crop"

# Главный промпт
SYSTEM_PROMPT = """
Ты — Макс, Senior Engineer в BotHost. Ты решаешь проблемы разработчиков.
ФОРМАТ ОТВЕТА (ОБЯЗАТЕЛЬНО):

🧐 **Анализ:**
(Коротко: в чем суть ошибки)

💡 **Решение:**
(Четкая инструкция)

💻 **Код:**
Обязательно оборачивай код в тройные кавычки. Будь краток и полезен.
"""

def get_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Работает", callback_data="solved"), InlineKeyboardButton(text="👎 Нет", callback_data="not_solved")],
        [InlineKeyboardButton(text="🔥 Прожарить мой код", callback_data="roast_me")] # Кнопка для фана
    ])

# --- КОМАНДЫ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer_photo(
        photo=BANNER,
        caption=(
            "👋 **BotHost AI Support 3.0**\n\n"
            "Я твой личный AI-DevOps.\n\n"
            "🔥 **Что я умею:**\n"
            "1. 📝 **Текст/Логи:** Кидай ошибку, я починю.\n"
            "2. 🎙 **Голос:** Просто скажи проблему голосом — я пойму!\n"
            "3. 💀 **Прожарка:** Напиши `/roast` + код, если хочешь посмеяться.\n\n"
            "👇 _Кидай проблему прямо сейчас!_"
        )
    )

@dp.message(Command("roast"))
async def roast_cmd(message: types.Message):
    # Режим прожарки
    code = message.text.replace("/roast", "").strip()
    if not code and not message.reply_to_message:
        await message.answer("👺 **Режим Прожарки**\nПришли код с командой `/roast` или ответь на сообщение с кодом, и я унижу этот говнокод.")
        return
    
    target_text = code if code else (message.reply_to_message.text or message.reply_to_message.caption)
    
    await message.answer("🔥 Разжигаю мангал...")
    messages = [{"role": "system", "content": ""}, {"role": "user", "content": target_text}]
    answer = await ask_ai(messages, roast_mode=True)
    await message.answer(answer)

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    stats = await db.get_global_stats()
    text = f"📊 **BotHost Stats**\n👥 Юзеров: `{stats['users']}`\n⚡️ Запросов: `{stats['requests']}`"
    await message.answer(text)

@dp.message(Command("send"))
async def send_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/send", "").strip()
    if not text: return
    users = await db.get_all_users_ids()
    await message.answer(f"🚀 Рассылка на {len(users)} чел...")
    for uid in users:
        try:
            await bot.send_message(uid, f"🔔 **NEWS**\n\n{text}")
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("✅ Готово")

# --- ОБРАБОТКА (ТЕКСТ + ФАЙЛЫ + ГОЛОС) ---

@dp.message(F.text | F.document | F.photo | F.voice)
async def handle_content(message: types.Message):
    if message.text and message.text.startswith("/"): return

    await db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.increment_stats(message.from_user.id)

    user_query = message.text or message.caption or ""
    
    # 1. ОБРАБОТКА ГОЛОСА (WOW-эффект)
    if message.voice:
        await bot.send_chat_action(message.chat.id, "upload_voice") # Статус "записывает голосовое"
        file = await bot.get_file(message.voice.file_id)
        voice_io = await bot.download_file(file.file_path)
        voice_bytes = voice_io.read()
        
        # Распознаем текст
        transcribed_text = await transcribe_voice(voice_bytes, f"{message.voice.file_id}.ogg")
        if not transcribed_text:
            await message.reply("👂 Не расслышал, повтори.")
            return
            
        await message.reply(f"🎙 **Вы сказали:**\n_{transcribed_text}_", parse_mode="Markdown")
        user_query += f"\n\n[Текст из голосового]: {transcribed_text}"

    # 2. ОБРАБОТКА ФАЙЛОВ
    if message.document:
        try:
            file = await bot.get_file(message.document.file_id)
            f_obj = await bot.download_file(file.file_path)
            content = f_obj.read().decode('utf-8', errors='ignore')[-15000:]
            user_query += f"\n\n📎 ЛОГ:\n{content}"
        except: pass

    if len(user_query.strip()) < 2:
        await message.answer("🤷‍♂️ Пришли лог, текст ошибки или запиши голосовое!")
        return

    # 3. ОТПРАВКА В AI
    await bot.send_chat_action(message.chat.id, "typing")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_query[:35000]}]
    answer = await ask_ai(messages)

    try:
        await message.answer(answer, reply_markup=get_kb())
    except:
        await message.answer(answer, parse_mode=None, reply_markup=get_kb())

# --- КОЛБЕКИ ---

@dp.callback_query(F.data == "solved")
async def cb_solved(cb: types.CallbackQuery):
    await cb.answer("Супер!")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("🎉 Рад помочь!")

@dp.callback_query(F.data == "not_solved")
async def cb_not(cb: types.CallbackQuery):
    await cb.answer("Жаль :(")
    await cb.message.answer("Попробуй скинуть полный лог файлом.")

@dp.callback_query(F.data == "roast_me")
async def cb_roast(cb: types.CallbackQuery):
    # Берем текст из ответа бота (где был код пользователя) или просим прислать
    await cb.answer("Включаю режим токсичности...")
    await cb.message.answer("👺 Перешли мне свой код и напиши /roast, если смелый!")

async def main():
    await db.init_db()
    print("🚀 BotHost ULTIMATE запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
