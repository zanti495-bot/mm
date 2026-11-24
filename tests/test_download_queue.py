import asyncio
import unittest
from unittest.mock import MagicMock

from aiogram import Bot

from music_bot.download_queue import (
    DownloadJob,
    DownloadQueue,
    QueueFullError,
)


class ControlledQueue(DownloadQueue):
    def __init__(self, worker_count: int, max_pending: int) -> None:
        super().__init__(
            bot=MagicMock(spec=Bot),
            worker_count=worker_count,
            max_pending=max_pending,
        )
        self.started: list[int] = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()
        self.expected_started = asyncio.Event()

    async def _process(self, job: DownloadJob) -> None:
        self.started.append(job.request_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if len(self.started) >= self.worker_count:
            self.expected_started.set()
        await self.release.wait()
        self.active -= 1


def job(request_id: int) -> DownloadJob:
    return DownloadJob(
        chat_id=1,
        query=f"track {request_id}",
        request_id=request_id,
        status_message_id=request_id,
    )


class DownloadQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_processes_jobs_concurrently_in_fifo_start_order(self) -> None:
        queue = ControlledQueue(worker_count=2, max_pending=2)
        queue.start()
        try:
            self.assertEqual(queue.enqueue(job(1)), 0)
            self.assertEqual(queue.enqueue(job(2)), 1)
            await asyncio.wait_for(queue.expected_started.wait(), timeout=1)

            self.assertEqual(queue.enqueue(job(3)), 2)
            self.assertEqual(queue.started, [1, 2])
            self.assertEqual(queue.max_active, 2)
        finally:
            queue.release.set()
            await queue.stop()

        self.assertEqual(queue.started, [1, 2, 3])

    async def test_rejects_jobs_after_pending_capacity_is_reached(self) -> None:
        queue = ControlledQueue(worker_count=1, max_pending=1)
        queue.start()
        try:
            self.assertEqual(queue.enqueue(job(1)), 0)
            await asyncio.wait_for(queue.expected_started.wait(), timeout=1)

            self.assertEqual(queue.enqueue(job(2)), 1)
            with self.assertRaises(QueueFullError):
                queue.enqueue(job(3))
        finally:
            queue.release.set()
            await queue.stop()

        self.assertEqual(queue.started, [1, 2])


if __name__ == "__main__":
    unittest.main()
