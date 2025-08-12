from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from app.services.brands import exact_lookup, get_brand
from app.services.ai_duck import web_search_brand, image_search_brand, build_caption_from_results, FetchError

router = Router()

# Простая "сессия" AI-режима в памяти процесса
AI_USERS: set[int] = set()

@router.callback_query(F.data == "ai:enter")
async def enter_ai(c: CallbackQuery):
    AI_USERS.add(c.from_user.id)
    await c.message.answer(
        "⚡ Включен режим ИИ-помощника.\n"
        "Напиши название бренда или вопрос про алкоголь.\n"
        "Чтобы выйти — напиши: Выйти из AI"
    )
    await c.answer()

@router.message(F.text == "Выйти из AI")
async def exit_ai(m: Message):
    AI_USERS.discard(m.from_user.id)
    await m.answer("Режим ИИ-помощника выключен. Можешь снова пользоваться меню брендов.")

# Обрабатываем текст ТОЛЬКО если пользователь в AI-режиме
@router.message(F.text.func(lambda _: True))
async def ai_any_text(m: Message):
    if m.from_user.id not in AI_USERS:
        return  # не наш режим — пусть другие роутеры (бренды/подсказки) обработают

    q = (m.text or "").strip()
    if not q:
        return

    # 1) сначала пробуем локальный каталог
    name = exact_lookup(q)
    if name:
        item = get_brand(name)
        await m.answer_photo(photo=item["photo_file_id"], caption=item["caption"], parse_mode="HTML")
        return

    # 2) веб-поиск
    await m.answer("Ищу в интернете… (2–5 сек)")
    try:
        results = web_search_brand(q)
        caption = build_caption_from_results(q, results)
        img = image_search_brand(q + " бутылка бренд алкоголь label")
        if img:
            await m.answer_photo(photo=img["contentUrl"], caption=caption, parse_mode="HTML")
        else:
            await m.answer(caption, parse_mode="HTML")
    except FetchError:
        await m.answer("Не получилось получить данные из интернета. Попробуй другой запрос.")

