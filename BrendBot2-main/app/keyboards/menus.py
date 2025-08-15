from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

AI_ENTRY_BUTTON_TEXT = "AI эксперт 🤖"
AI_EXIT_BUTTON_TEXT  = "Выйти из AI режима"

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=AI_ENTRY_BUTTON_TEXT)], [KeyboardButton(text='🧩 Квиз'), KeyboardButton(text='📸 Фото-анализ')], [KeyboardButton(text='👤 Профиль')]],
        resize_keyboard=True
    )

def ai_exit_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=AI_EXIT_BUTTON_TEXT, callback_data="ai:exit")]]
    )
