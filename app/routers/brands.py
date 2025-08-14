# app/routers/brands.py
from aiogram import Router, F
from aiogram.types import Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton

from app.keyboards.common import categories_kb
from app.services.brands import by_category, exact_lookup, fuzzy_suggest, get_brand
from app.services.stats import record_brand_view
from app.routers.ai_helper import AI_USERS  # важно

router = Router()

@router.message(F.text == "🗂️ Меню брендов")
async def show_brand_menu(m: Message):
    await m.answer("Выберите категорию:", reply_markup=categories_kb())

@router.message(F.text == "Назад")
async def back(m: Message):
    await m.answer("Окей, выбери категорию снова:", reply_markup=categories_kb())

@router.message(F.text.in_({"🥃 Виски", "🧊 Водка", "🍺 Пиво", "🍷 Вино", "🦌 Ягермейстер"}))
async def pick_category(m: Message):
    if m.from_user.id in AI_USERS:
        return
    mapping = {
        "🥃 Виски": "Виски",
        "🧊 Водка": "Водка",
        "🍺 Пиво": "Пиво",
        "🍷 Вино": "Вино",
        "🦌 Ягермейстер": "Ликёр",  # подстрока покроет "Ликёр на основе виски"
    }
    cat = mapping.get(m.text, "")
    names = by_category(cat)
    if not names:
        await m.answer("Пока пусто. Выбери другую категорию.", reply_markup=categories_kb()); 
        return

    kb = ReplyKeyboardBuilder()
    for n in names:
        kb.add(KeyboardButton(text=n))
    kb.add(KeyboardButton(text="Назад"))
    kb.adjust(2)
    await m.answer(f"Выбери бренд ({cat}):", reply_markup=kb.as_markup(resize_keyboard=True))

@router.message(lambda m: m.text is not None and exact_lookup(m.text) is not None and m.from_user.id not in AI_USERS)
async def send_brand_card(m: Message):
    name = exact_lookup(m.text)
    item = get_brand(name)
    if not item:
        await m.answer("Не нашёл бренд. Попробуй ещё раз."); 
        return

    record_brand_view(m.from_user.id, item["name"], item.get("category", ""))

    photo_id = item.get("photo_file_id")
    if photo_id:
        await m.answer_photo(photo=photo_id, caption=item["caption"], parse_mode="HTML")
    else:
        await m.answer(item["caption"], parse_mode="HTML")

@router.message(lambda m: m.text is not None and exact_lookup(m.text) is None and m.from_user.id not in AI_USERS)
async def suggest(m: Message):
    qs = m.text.strip()
    suggestions = fuzzy_suggest(qs, limit=6)
    if not suggestions:
        return
    kb = ReplyKeyboardBuilder()
    for name, _ in suggestions:
        kb.add(KeyboardButton(text=name))
    kb.add(KeyboardButton(text="Назад"))
    kb.adjust(1)
    await m.answer("Возможно, вы искали:", reply_markup=kb.as_markup(resize_keyboard=True))
