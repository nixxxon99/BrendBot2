from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from app.services.brands import exact_lookup, get_brand
from app.services.ai_duck import web_search_brand, image_search_brand, build_caption_from_results, FetchError
from app.services.ai_llm import have_llm, generate_card_with_llm

router = Router()

# Пользователи в режиме AI (память процесса)
AI_USERS: set[int] = set()

@router.callback_query(F.data == "ai:enter")
async def enter_ai_cb(c: CallbackQuery):
    AI_USERS.add(c.from_user.id)
    await c.message.answer(
        "⚡ ИИ-режим включён.\n"
        "Пиши название бренда или вопрос про алкоголь.\n"
        "Чтобы выйти — напиши: Выйти из AI или /ai_off"
    )
    await c.answer()

@router.message(Command("ai"))
async def enter_ai_cmd(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "⚡ ИИ-режим включён.\n"
        "Пиши название бренда или вопрос.\n"
        "Чтобы выйти — напиши: Выйти из AI или /ai_off"
    )

@router.message(F.text == "Выйти из AI")
@router.message(Command("ai_off"))
async def exit_ai(m: Message):
    AI_USERS.discard(m.from_user.id)
    await m.answer("ИИ-режим выключен. Используй меню брендов или /ai чтобы включить снова.")

@router.message(F.text)
async def ai_any_text(m: Message):
    # Обрабатываем только если пользователь в AI-режиме
    if m.from_user.id not in AI_USERS:
        return

    q = (m.text or "").strip()
    if not q:
        return

    # 1) если есть локальная карточка — отдаем быстро
    name = exact_lookup(q)
    if name:
        item = get_brand(name)
        await m.answer_photo(photo=item["photo_file_id"], caption=item["caption"], parse_mode="HTML")
        return

    # 2) иначе — веб-поиск и генерация карточки
    await m.answer("Ищу в интернете и готовлю карточку… (2–7 сек)")
    try:
        results = web_search_brand(q)
        # LLM (если ключ есть), иначе fallback на просто склейку сниппетов
        if have_llm():
            caption = await generate_card_with_llm(q, results)
        else:
            caption = build_caption_from_results(q, results)

        img = image_search_brand(q + " бутылка бренд алкоголь label")
        if img:
            await m.answer_photo(photo=img["contentUrl"], caption=caption, parse_mode="HTML")
        else:
            await m.answer(caption, parse_mode="HTML")
    except FetchError:
        await m.answer("Не получилось получить данные из интернета. Попробуй другой запрос.")
