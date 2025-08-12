from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from app.services.brands import exact_lookup, get_brand
from app.services.ai_duck import web_search_brand, image_search_brand, build_caption_from_results, FetchError

router = Router()

@router.callback_query(F.data == "ai:enter")
async def enter_ai(c: CallbackQuery):
    await c.message.answer("Напиши название бренда или вопрос про алкоголь. Если бренда нет в базе — найду в сети и соберу карточку.")
    await c.answer()

@router.message(F.text)
async def ai_any_text(m: Message):
    q = m.text.strip()
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
