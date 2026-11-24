import asyncio
import logging
import tempfile
from dataclasses import dataclass

import yt_dlp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from music_bot import storage
from music_bot.keyboards import audio_actions_keyboard
from music_bot.music import (
    AudioFileNotCreatedError,
    TrackNotFoundError,
    download_track,
    search_track,
)
from music_bot.settings import (
    DOWNLOAD_WORKERS,
    MAX_AUDIO_SIZE_BYTES,
    MAX_DURATION_SECONDS,
    MAX_PENDING_DOWNLOADS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadJob:
    chat_id: int
    query: str
    request_id: int
    status_message_id: int


class QueueFullError(Exception):
    pass


class QueueClosedError(Exception):
    pass


class DownloadQueue:
    def __init__(
        self,
        bot: Bot,
        worker_count: int = DOWNLOAD_WORKERS,
        max_pending: int = MAX_PENDING_DOWNLOADS,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least one")
        if max_pending < 1:
            raise ValueError("max_pending must be at least one")

        self.bot = bot
        self.worker_count = worker_count
        self._queue: asyncio.Queue[DownloadJob | None] = asyncio.Queue(
            maxsize=max_pending
        )
        self._workers: list[asyncio.Task[None]] = []
        self._active_workers = 0
        self._closing = False

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("Download queue has already been started.")
        self._workers = [
            asyncio.create_task(self._worker(), name=f"download-worker-{index}")
            for index in range(1, self.worker_count + 1)
        ]

    def enqueue(self, job: DownloadJob) -> int:
        if self._closing or not self._workers:
            raise QueueClosedError
        if self._queue.full():
            raise QueueFullError

        jobs_ahead = self._active_workers + self._queue.qsize()
        self._queue.put_nowait(job)
        return jobs_ahead

    async def stop(self) -> None:
        if not self._workers:
            return

        self._closing = True
        await self._queue.join()
        for _ in self._workers:
            self._queue.put_nowait(None)
        await asyncio.gather(*self._workers)
        self._workers.clear()

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return

                self._active_workers += 1
                try:
                    await self._process(job)
                except Exception:
                    logger.exception(
                        "Unexpected failure processing request %s",
                        job.request_id,
                    )
                    await self._fail(
                        job,
                        "worker_error",
                        "Не удалось обработать запрос. Попробуй ещё раз позже.",
                    )
                finally:
                    self._active_workers -= 1
            finally:
                self._queue.task_done()

    async def _process(self, job: DownloadJob) -> None:
        await self._set_status(job, "🔎 Ищу трек...")
        try:
            track = await asyncio.to_thread(search_track, job.query)
        except TrackNotFoundError:
            await self._fail(
                job,
                "not_found",
                "Не удалось найти трек. Попробуй уточнить запрос.",
            )
            return
        except yt_dlp.utils.DownloadError:
            logger.exception("Search failed for request %s", job.request_id)
            await self._fail(
                job,
                "search_error",
                "Ошибка поиска. Попробуй ещё раз позже.",
            )
            return

        if track.duration is None:
            await self._fail(
                job,
                "duration_unknown",
                "Не удалось определить длительность трека.",
            )
            return
        if track.duration > MAX_DURATION_SECONDS:
            await self._fail(
                job,
                "too_long",
                "Трек длиннее 20 минут — скачивание отменено.",
            )
            return

        await self._set_status(job, "⬇️ Скачиваю аудио...")
        try:
            with tempfile.TemporaryDirectory(prefix="telegram_music_") as temp_dir:
                audio_path = await asyncio.to_thread(download_track, track, temp_dir)
                if audio_path.stat().st_size > MAX_AUDIO_SIZE_BYTES:
                    await self._fail(
                        job,
                        "too_large",
                        "Файл больше 49 МиБ — Telegram не сможет его принять.",
                    )
                    return

                await self._set_status(job, "📤 Отправляю аудио...")
                await self.bot.send_audio(
                    chat_id=job.chat_id,
                    audio=FSInputFile(audio_path),
                    title=track.title,
                    performer=track.artist,
                    caption=(
                        f"{track.title} — {track.artist}"
                        if track.artist
                        else track.title
                    ),
                    reply_markup=audio_actions_keyboard(track.url),
                )
        except AudioFileNotCreatedError:
            logger.exception("No audio file created for request %s", job.request_id)
            await self._fail(
                job,
                "audio_error",
                "Не удалось подготовить аудиофайл.",
            )
            return
        except yt_dlp.utils.DownloadError:
            logger.exception("Download failed for request %s", job.request_id)
            await self._fail(
                job,
                "download_error",
                "Ошибка скачивания или конвертации. Проверь FFmpeg и попробуй позже.",
            )
            return
        except OSError:
            logger.exception("Temporary audio file failed for request %s", job.request_id)
            await self._fail(
                job,
                "file_error",
                "Не удалось подготовить временный аудиофайл.",
            )
            return
        except TelegramAPIError:
            logger.exception("Telegram send failed for request %s", job.request_id)
            await self._fail(
                job,
                "telegram_error",
                "Не удалось отправить аудио. Возможно, Telegram временно недоступен.",
            )
            return

        await storage.finish_request(job.request_id, "success")
        await self._set_status(job, "✅ Готово!")

    async def _fail(self, job: DownloadJob, status: str, message: str) -> None:
        await storage.finish_request(job.request_id, status)
        await self._set_status(job, message)

    async def _set_status(self, job: DownloadJob, text: str) -> None:
        try:
            await self.bot.edit_message_text(
                chat_id=job.chat_id,
                message_id=job.status_message_id,
                text=text,
            )
        except TelegramAPIError:
            logger.warning(
                "Could not update status for request %s",
                job.request_id,
                exc_info=True,
            )
