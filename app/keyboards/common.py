
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def kb(*labels: str, width: int = 2) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for text in labels:
        builder.add(KeyboardButton(text=text))
    builder.adjust(width)
    return builder.as_markup(resize_keyboard=True)

MAIN_KB = kb("🗂️ Меню брендов", "🧠 Тренажёр знаний", "📊 Моя статистика", width=2)
ADMIN_MAIN_KB = kb("🗂️ Меню брендов", "🧠 Тренажёр знаний", "📊 Моя статистика", "👑 Админ-панель", width=2)

def main_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    return ADMIN_MAIN_KB if is_admin else MAIN_KB

def ai_entry_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 AI-помощник", callback_data="ai:enter")
    return kb.as_markup()

def categories_kb():
    return kb("🍷 Вино", "🧊 Водка", "🥃 Виски", "🍺 Пиво", "🦌 Ягермейстер", "Назад", width=2)
