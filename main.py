import asyncio
import logging

import static_ffmpeg
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from music_bot.download_queue import DownloadQueue
from music_bot.handlers import router
from music_bot.settings import BOT_TOKEN
from music_bot.storage import initialize


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise RuntimeError("Укажи токен бота в переменной BOT_TOKEN файла .env.")

    await asyncio.to_thread(static_ffmpeg.add_paths, weak=True)
    await initialize()
    bot = Bot(token=BOT_TOKEN)
    download_queue = DownloadQueue(bot)
    download_queue.start()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    dispatcher["download_queue"] = download_queue
    try:
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        await download_queue.stop()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
