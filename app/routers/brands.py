
from aiogram import Router, F
from aiogram.types import Message
from app.keyboards.common import categories_kb
from app.services.brands import categories, by_category, exact_lookup, fuzzy_suggest, get_brand
from app.services.stats import record_brand_view

router = Router()

@router.message(F.text == "🗂️ Меню брендов")
async def show_brand_menu(m: Message):
    await m.answer("Выберите категорию:", reply_markup=categories_kb())

@router.message(F.text == "Назад")
async def back(m: Message):
    await m.answer("Окей, выбери категорию снова:", reply_markup=categories_kb())

# Category handlers (simple match by emoji-name)
@router.message(F.text.in_({"🥃 Виски", "🧊 Водка", "🍺 Пиво", "🍷 Вино", "🦌 Ягермейстер"}))
async def pick_category(m: Message):
    cat_label = m.text
    mapping = {
        "🥃 Виски": "Виски",
        "🧊 Водка": "Водка",
        "🍺 Пиво": "Пиво",
        "🍷 Вино": "Вино",
        "🦌 Ягермейстер": "Ликёр",
    }
    cat = mapping.get(cat_label, "")
    names = by_category(cat)
    if not names:
        await m.answer("Пока пусто. Выбери другую категорию.", reply_markup=categories_kb())
        return
    # Build simple keyboard list
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    from aiogram.types import KeyboardButton
    kb = ReplyKeyboardBuilder()
    for n in names:
        kb.add(KeyboardButton(text=n))
    kb.add(KeyboardButton(text="Назад"))
    kb.adjust(2)
    await m.answer(f"Выбери бренд ({cat}):", reply_markup=kb.as_markup(resize_keyboard=True))

# Exact brand lookup by text (from catalog/aliases)
@router.message(lambda m: m.text is not None and exact_lookup(m.text) is not None)
async def send_brand_card(m: Message):
    name = exact_lookup(m.text)
    item = get_brand(name)
    if not item:
        await m.answer("Не нашёл бренд. Попробуй ещё раз.")
        return
    record_brand_view(m.from_user.id, name, item["category"])
    await m.answer_photo(photo=item["photo_file_id"], caption=item["caption"], parse_mode="HTML")

# Fuzzy suggestions when user types free text
@router.message(lambda m: m.text is not None and exact_lookup(m.text) is None)
async def suggest(m: Message):
    qs = m.text.strip()
    suggestions = fuzzy_suggest(qs, limit=6)
    if not suggestions:
        return
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    from aiogram.types import KeyboardButton
    kb = ReplyKeyboardBuilder()
    for name, score in suggestions:
        kb.add(KeyboardButton(text=name))
    kb.add(KeyboardButton(text="Назад"))
    kb.adjust(1)
    await m.answer("Возможно, вы искали:", reply_markup=kb.as_markup(resize_keyboard=True))
