from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🎵 Найти песню"),
            KeyboardButton(text="ℹ️ Помощь"),
        ]
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Название песни или ссылка",
)


def admin_keyboard(maintenance: bool) -> InlineKeyboardMarkup:
    maintenance_text = (
        "Выключить техработы" if maintenance else "Включить техработы"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Статистика", callback_data="admin:stats")],
            [
                InlineKeyboardButton(
                    text="Экспорт пользователей CSV",
                    callback_data="admin:export_users",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Создать рассылку",
                    callback_data="admin:broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text=maintenance_text,
                    callback_data="admin:maintenance",
                )
            ],
        ]
    )


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="admin:menu")]
        ]
    )


def broadcast_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить рассылку",
                    callback_data="admin:broadcast:confirm",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="admin:broadcast:cancel",
                ),
            ]
        ]
    )


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="admin:broadcast:cancel",
                )
            ]
        ]
    )


def search_again_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Попробовать ещё",
                    callback_data="user:search",
                )
            ]
        ]
    )


def audio_actions_keyboard(source_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Источник", url=source_url),
                InlineKeyboardButton(
                    text="Найти ещё",
                    callback_data="user:search",
                ),
            ]
        ]
    )
