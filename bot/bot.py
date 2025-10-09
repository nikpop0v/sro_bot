import asyncio
import os

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


@dp.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer("Привет! Пришли вопрос — отвечу по базе знаний.")


@dp.message(F.text & ~F.via_bot)
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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="2", callback_data=f"rate:{data['log_id']}:2"),
            InlineKeyboardButton(text="1", callback_data=f"rate:{data['log_id']}:1"),
            InlineKeyboardButton(text="0", callback_data=f"rate:{data['log_id']}:0"),
            InlineKeyboardButton(text="-1", callback_data=f"rate:{data['log_id']}:-1"),
            InlineKeyboardButton(text="-2", callback_data=f"rate:{data['log_id']}:-2"),
        ]
    ])

    await message.answer(data["answer"], reply_markup=kb)


@dp.callback_query(F.data.startswith("rate:"))
async def rate_cb(cb: CallbackQuery):
    _, log_id, rating = cb.data.split(":")
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{API_BASE_URL}/feedback", json={"log_id": int(log_id), "rating": int(rating)})
    await cb.answer("Спасибо за оценку!")


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
