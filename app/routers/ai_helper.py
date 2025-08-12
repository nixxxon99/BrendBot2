from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
import logging

from app.services.brands import exact_lookup, get_brand
from app.services.ai_google import web_search_brand, image_search_brand, build_caption_from_results, FetchError
from app.services.ai_llm import have_llm, generate_card_with_llm  # OK, если OPENAI_API_KEY есть

router = Router()
log = logging.getLogger(__name__)

# Пользователи в AI-режиме
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

# ⚠️ Хендлер сработает ТОЛЬКО если пользователь уже в AI_USERS
@router.message(F.text & F.from_user.id.func(lambda uid: uid in AI_USERS))
async def ai_any_text(m: Message):
    q = (m.text or "").strip()
    if not q:
        return

    log.info("[AI] user=%s query=%r", m.from_user.id, q)

    # 1) если бренд есть в базе — отдаём локальную карточку
    name = exact_lookup(q)
    if name:
        item = get_brand(name)
        await m.answer_photo(photo=item["photo_file_id"], caption=item["caption"], parse_mode="HTML")
        return

    # 2) иначе — веб-поиск (+ LLM, если доступен)
    await m.answer("Ищу в интернете и готовлю карточку… (2–7 сек)")
    try:
        results = web_search_brand(q)
        if have_llm():
            caption = await generate_card_with_llm(q, results)
        else:
            caption = build_caption_from_results(q, results)

        img = image_search_brand(q + " бутылка бренд алкоголь label")
        if img:
            await m.answer_photo(photo=img["contentUrl"], caption=caption, parse_mode="HTML")
        else:
            await m.answer(caption, parse_mode="HTML")
    except FetchError as e:
        log.warning("[AI] fetch error: %s", e)
        await m.answer("Не получилось получить данные из интернета. Попробуй другой запрос.")
