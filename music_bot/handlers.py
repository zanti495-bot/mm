import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    User,
)

from music_bot import storage
from music_bot.download_queue import (
    DownloadJob,
    DownloadQueue,
    QueueClosedError,
    QueueFullError,
)
from music_bot.keyboards import (
    MAIN_KEYBOARD,
    admin_back_keyboard,
    admin_keyboard,
    broadcast_cancel_keyboard,
    broadcast_confirmation_keyboard,
    search_again_keyboard,
)
from music_bot.settings import ADMIN_IDS

router = Router()
logger = logging.getLogger(__name__)


class BroadcastFlow(StatesGroup):
    waiting_for_message = State()
    confirming = State()
    sending = State()


async def _register_user(user: User) -> None:
    await storage.register_user(user.id, user.username, user.full_name)


def _period_starts() -> tuple[tuple[str, float], ...]:
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    return (
        ("Сегодня", today.timestamp()),
        ("Эта неделя", week_start.timestamp()),
        ("Этот месяц", month_start.timestamp()),
    )


async def _edit_admin_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=reply_markup)
    else:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=text,
            reply_markup=reply_markup,
        )


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    user = message.from_user
    if user:
        await _register_user(user)
    greeting = f"🎧 Привет, {user.first_name}!\n\n" if user else "🎧 Привет!\n\n"
    await message.answer(
        greeting
        + "Отправь название песни или ссылку на неё — я найду трек "
        "и пришлю аудио.\n\n"
        "Для более точного поиска добавь имя исполнителя.\n"
        "⏱️ Максимальная длительность — 20 минут.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.text == "ℹ️ Помощь")
async def help_button_handler(message: Message) -> None:
    if message.from_user:
        await _register_user(message.from_user)
    await message.answer(
        "🎵 Отправь название песни и, если знаешь, исполнителя — "
        "или пришли ссылку на трек. Я найду и отправлю аудио.\n"
        "⏱️ Принимаются треки длительностью до 20 минут.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.text == "🎵 Найти песню")
async def search_button_handler(message: Message) -> None:
    if message.from_user:
        await _register_user(message.from_user)
    await message.answer(
        "Напиши название песни или пришли ссылку на трек.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("myid"))
async def myid_handler(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    await _register_user(user)
    await message.answer(
        f"Твой Telegram ID: {user.id}\n"
        f"Чтобы получить доступ администратора, добавь в .env:\n"
        f"ADMIN_IDS={user.id}"
    )


@router.message(Command("admin"))
async def admin_handler(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if not user or user.id not in ADMIN_IDS:
        await message.answer("Эта команда доступна только администратору.")
        return

    await state.clear()
    maintenance = await storage.get_maintenance()
    await message.answer(
        "Панель администратора",
        reply_markup=admin_keyboard(maintenance),
    )


def _csv_safe(value: object) -> str:
    text = str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _users_csv(users: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "telegram_id",
            "username",
            "full_name",
            "first_seen",
            "last_seen",
            "blocked",
        ]
    )
    for user in users:
        writer.writerow(
            [
                user["user_id"],
                _csv_safe(user["username"] or ""),
                _csv_safe(user["full_name"]),
                datetime.fromtimestamp(user["first_seen"]).astimezone().isoformat(),
                datetime.fromtimestamp(user["last_seen"]).astimezone().isoformat(),
                bool(user["is_blocked"]),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


async def _send_broadcast(
    bot: Bot,
    source_chat_id: int,
    source_message_id: int,
    recipients: list[int],
    progress_message: Message,
) -> tuple[int, int]:
    sent = 0
    failed = 0
    total = len(recipients)

    for index, user_id in enumerate(recipients, start=1):
        for attempt in range(5):
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                )
            except TelegramRetryAfter as error:
                if attempt == 4:
                    logger.error("Rate limit retries exhausted for user %s", user_id)
                    failed += 1
                    break
                await asyncio.sleep(error.retry_after)
            except TelegramForbiddenError:
                await storage.mark_user_blocked(user_id)
                failed += 1
                break
            except TelegramAPIError:
                logger.exception("Broadcast delivery failed for user %s", user_id)
                failed += 1
                break
            else:
                sent += 1
                break

        if index % 20 == 0 or index == total:
            try:
                await progress_message.edit_text(
                    f"Рассылка: {index}/{total}\n"
                    f"Отправлено: {sent}, ошибок: {failed}."
                )
            except TelegramAPIError:
                logger.exception("Could not update broadcast progress message")
        await asyncio.sleep(0.04)

    return sent, failed


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return

    data = callback.data or ""
    if data == "admin:menu":
        await state.clear()
        maintenance = await storage.get_maintenance()
        await _edit_admin_message(
            callback,
            "Панель администратора",
            admin_keyboard(maintenance),
        )
    elif data == "admin:stats":
        lines = ["Статистика запросов:"]
        for label, since in _period_starts():
            stats = await storage.get_stats(since)
            lines.append(
                f"\n{label}: запросов — {stats['total']}, "
                f"отправлено — {stats['sent']}, "
                f"ошибок/отказов — {stats['failed']}, "
                f"пользователей — {stats['users']}"
            )
        await _edit_admin_message(
            callback,
            "\n".join(lines),
            admin_back_keyboard(),
        )
    elif data == "admin:export_users":
        users = await storage.export_users()
        document = BufferedInputFile(
            _users_csv(users),
            filename=f"users_{datetime.now():%Y-%m-%d_%H-%M}.csv",
        )
        chat_id = (
            callback.message.chat.id
            if isinstance(callback.message, Message)
            else callback.from_user.id
        )
        await callback.answer("CSV готов.")
        await callback.bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=f"Пользователей в базе: {len(users)}.",
        )
        return
    elif data == "admin:broadcast":
        await state.clear()
        await state.set_state(BroadcastFlow.waiting_for_message)
        await _edit_admin_message(
            callback,
            "Отправь одним сообщением текст, фото, видео, аудио или документ "
            "для рассылки. Форматирование и подпись к медиа сохранятся.\n"
            "Альбомы пока не поддерживаются. Для отмены нажми кнопку "
            "или отправь /cancel.",
            broadcast_cancel_keyboard(),
        )
    elif data == "admin:broadcast:cancel":
        await state.clear()
        maintenance = await storage.get_maintenance()
        await _edit_admin_message(
            callback,
            "Рассылка отменена.",
            admin_keyboard(maintenance),
        )
    elif data == "admin:broadcast:confirm":
        if await state.get_state() != BroadcastFlow.confirming.state:
            await callback.answer("Нет подготовленной рассылки.", show_alert=True)
            return

        payload = await state.get_data()
        source_chat_id = payload.get("source_chat_id")
        source_message_id = payload.get("source_message_id")
        if not isinstance(source_chat_id, int) or not isinstance(
            source_message_id, int
        ):
            await state.clear()
            await callback.answer("Не удалось прочитать сообщение.", show_alert=True)
            return

        recipients = await storage.broadcast_user_ids()
        await state.set_state(BroadcastFlow.sending)
        await callback.answer("Рассылка запущена.")
        admin_chat_id = (
            callback.message.chat.id
            if isinstance(callback.message, Message)
            else callback.from_user.id
        )
        progress_message = await callback.bot.send_message(
            chat_id=admin_chat_id,
            text=f"Начинаю рассылку для {len(recipients)} пользователей…",
        )
        try:
            sent, failed = await _send_broadcast(
                callback.bot,
                source_chat_id,
                source_message_id,
                recipients,
                progress_message,
            )
            await progress_message.edit_text(
                f"Рассылка завершена.\n"
                f"Отправлено: {sent} из {len(recipients)}.\n"
                f"Ошибок: {failed}."
            )
        finally:
            await state.clear()
        return
    elif data == "admin:maintenance":
        maintenance = not await storage.get_maintenance()
        await storage.set_maintenance(maintenance)
        await _edit_admin_message(
            callback,
            "Панель администратора\n"
            f"Режим техработ: {'включён' if maintenance else 'выключен'}.",
            admin_keyboard(maintenance),
        )
    else:
        await callback.answer("Неизвестная команда.", show_alert=True)
        return

    await callback.answer()


@router.message(BroadcastFlow.waiting_for_message)
async def broadcast_message_handler(
    message: Message,
    state: FSMContext,
) -> None:
    user = message.from_user
    if not user or user.id not in ADMIN_IDS:
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("Создание рассылки отменено.")
        return

    if message.media_group_id:
        await message.answer(
            "Альбомы пока не поддерживаются. Отправь один файл или текст "
            "одним сообщением."
        )
        return

    supported_types = {
        "text",
        "photo",
        "video",
        "audio",
        "document",
        "animation",
        "voice",
        "video_note",
        "sticker",
    }
    if message.content_type not in supported_types:
        await message.answer(
            "Этот тип сообщения нельзя включить в рассылку. "
            "Попробуй текст, фото, видео, аудио или документ."
        )
        return

    try:
        await message.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except TelegramAPIError:
        logger.exception("Could not create broadcast preview")
        await message.answer(
            "Не удалось показать предпросмотр этого сообщения. "
            "Попробуй отправить другой файл."
        )
        return

    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(BroadcastFlow.confirming)
    recipient_count = len(await storage.broadcast_user_ids())
    await message.answer(
        "Предпросмотр выше. Рассылка отправит сообщение "
        f"{recipient_count} пользователям, которые не отписались.\n"
        "Отправить?",
        reply_markup=broadcast_confirmation_keyboard(),
    )


@router.callback_query(F.data == "user:search")
async def search_again_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    chat_id = (
        callback.message.chat.id
        if isinstance(callback.message, Message)
        else callback.from_user.id
    )
    await callback.bot.send_message(
        chat_id=chat_id,
        text="Напиши название песни или пришли ссылку на трек.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(F.text.startswith("/"))
async def unknown_command_handler(message: Message) -> None:
    await message.answer(
        "Неизвестная команда. Используй кнопки ниже или отправь название песни.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message()
async def song_handler(
    message: Message,
    download_queue: DownloadQueue,
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Отправь название песни текстовым сообщением.")
        return

    user = message.from_user
    if not user:
        return

    await _register_user(user)
    request_id = await storage.create_request(user.id)
    if await storage.get_maintenance() and user.id not in ADMIN_IDS:
        await storage.finish_request(request_id, "maintenance")
        await message.answer("Бот временно на техобслуживании. Попробуй позже.")
        return

    status = await message.answer("✅ Запрос принят.")
    job = DownloadJob(
        chat_id=message.chat.id,
        query=query,
        request_id=request_id,
        status_message_id=status.message_id,
    )
    try:
        waiting_ahead = download_queue.enqueue(job)
    except QueueFullError:
        await storage.finish_request(request_id, "queue_full")
        await status.edit_text(
            "Очередь переполнена. Попробуй отправить запрос немного позже.",
            reply_markup=search_again_keyboard(),
        )
        return
    except QueueClosedError:
        await storage.finish_request(request_id, "queue_closed")
        await status.edit_text(
            "Бот перезапускается и временно не принимает запросы. Попробуй позже."
        )
        return

    if waiting_ahead:
        await message.answer(
            f"⏳ Запрос в очереди. Перед тобой: {waiting_ahead}."
        )


