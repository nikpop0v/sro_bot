import asyncio
import os
import re
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, BufferedInputFile
from dotenv import load_dotenv



load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
ADMINS = {int(x) for x in os.getenv("ADMINS", "").replace(" ", "").split(",") if x}
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


@dp.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer("Привет! Пришли вопрос — отвечу по базе знаний.")


@dp.message(F.text & ~F.via_bot & ~F.text.startswith("/"))
async def handle_question(message: Message):
    text = message.text.strip()
    if not text:
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{API_BASE_URL}/ask", json={
            "question": text,
            "chat_id": str(message.chat.id)
        })
        resp.raise_for_status()
        data = resp.json()

    # 1) Санитизируем текст, чтобы не падать на <br>
    raw = data["answer"]
    safe_text = re.sub(r'(?i)<br\s*/?>', '\n', raw)

    # 2) Создаём клавиатуру (пятибалльная шкала -2…2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="-2", callback_data=f"rate:{data['log_id']}:-2"),
            InlineKeyboardButton(text="-1", callback_data=f"rate:{data['log_id']}:-1"),
            InlineKeyboardButton(text="0",  callback_data=f"rate:{data['log_id']}:0"),
            InlineKeyboardButton(text="+1", callback_data=f"rate:{data['log_id']}:1"),
            InlineKeyboardButton(text="+2", callback_data=f"rate:{data['log_id']}:2"),
        ]
    ])

    await message.answer(safe_text, reply_markup=kb, parse_mode=None)


@dp.callback_query(F.data.startswith("rate:"))
async def rate_cb(cb: CallbackQuery):
    _, log_id, rating = cb.data.split(":")
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{API_BASE_URL}/feedback", json={"log_id": int(log_id), "rating": int(rating)})
    await cb.answer("Спасибо за оценку!")

@dp.message(Command("export"), F.from_user & F.from_user.id.in_(ADMINS))
async def export_admin_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Сегодня", callback_data="export:today"),
        InlineKeyboardButton(text="Неделя",  callback_data="export:week"),
        InlineKeyboardButton(text="Месяц",   callback_data="export:month"),
    ]])
    await message.answer("Выберите период для экспорта:", reply_markup=kb)


@dp.callback_query(F.data.startswith("export:"))
async def export_period_cb(cb: CallbackQuery):
    # безопасность: проверим админа и здесь тоже
    if not (cb.from_user and cb.from_user.id in ADMINS):
        await cb.answer("Только для админов", show_alert=True)
        return

    period = cb.data.split(":", 1)[1]  # today | week | month
    await bot.send_chat_action(cb.message.chat.id, ChatAction.UPLOAD_DOCUMENT)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(f"{API_BASE_URL}/export", params={"period": period})
        resp.raise_for_status()
        csv_bytes = resp.content

    await cb.message.answer_document(
        BufferedInputFile(csv_bytes, filename=f"logs_{period}.csv"),
        caption=f"Экспорт логов: {period}"
    )
    await cb.answer("Готово")
# /export для всех остальных
@dp.message(Command("export"))
async def export_denied(message: Message):
    await message.answer("Эта команда доступна только администраторам.")

async def main():
    # Проверка, что backend есть
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.get(f"{API_BASE_URL}/health")
    except Exception:
        print("[WARN] Backend недоступен по", API_BASE_URL)

    await dp.start_polling(bot)



if __name__ == "__main__":
    asyncio.run(main())
