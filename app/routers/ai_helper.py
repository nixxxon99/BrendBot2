from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
import logging
import time
import re  # +++

def _sanitize_caption(html: str) -> str:
    if not html:
        return ""
    t = html

    # заголовки h1..h6 -> убираем (можно заменить на <b>, но проще убрать)
    t = re.sub(r'</?(h[1-6])[^>]*>', '', t, flags=re.I)

    # списки -> маркеры
    t = re.sub(r'</?ul[^>]*>|</?ol[^>]*>', '', t, flags=re.I)
    t = re.sub(r'<li[^>]*>', '• ', t, flags=re.I)
    t = re.sub(r'</li>', '\n', t, flags=re.I)

    # параграфы/брейки -> переносы строк
    t = re.sub(r'<p[^>]*>', '', t, flags=re.I)
    t = re.sub(r'</p>', '\n', t, flags=re.I)
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)

    # strong/em -> поддерживаемые b/i
    t = re.sub(r'<strong[^>]*>', '<b>', t, flags=re.I)
    t = re.sub(r'</strong>', '</b>', t, flags=re.I)
    t = re.sub(r'<em[^>]*>', '<i>', t, flags=re.I)
    t = re.sub(r'</em>', '</i>', t, flags=re.I)

    # вычищаем любые другие теги, кроме допустимых Telegram:
    # b, i, u, s, a, code, pre, br
    t = re.sub(r'<(?!/?(b|i|u|s|a|code|pre|br)\b)[^>]+>', '', t, flags=re.I)

    # сжимаем множественные переносы
    t = re.sub(r'\n{3,}', '\n\n', t).strip()

    # лимит для подписи к фото (~1024), берём запас
    if len(t) > 1000:
        t = t[:1000].rstrip() + "…"
    return t

from app.services.brands import exact_lookup, get_brand
# Поиск и картинки — через Google CSE
from app.services.ai_google import (
    web_search_brand, image_search_brand, build_caption_from_results, FetchError
)
# Текст карточки — через Gemini (если есть ключ), иначе fallback на простую сводку
from app.services.ai_gemini import have_gemini, generate_caption_with_gemini

router = Router()
log = logging.getLogger(__name__)

# Пользователи в AI-режиме
AI_USERS: set[int] = set()

# Простой кэш результатов поиска, чтобы экономить квоту CSE
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60 * 30  # 30 минут

def _cache_get(key: str):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, data = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return data

def _cache_set(key: str, data: dict) -> None:
    _CACHE[key] = (time.time(), data)

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

    # 2) иначе — веб-поиск (Google CSE) + генерация текста (Gemini при наличии)
    await m.answer("Ищу в интернете и готовлю карточку…")
    try:
        results = _cache_get(q)
        if results is None:
            results = web_search_brand(q)
            _cache_set(q, results)

        if have_gemini():
            caption = await generate_caption_with_gemini(q, results)
        else:
            caption = build_caption_from_results(q, results)

        img = image_search_brand(q + " бутылка бренд алкоголь label")
        if img and img.get("contentUrl"):
            await m.answer_photo(photo=img["contentUrl"], caption=caption, parse_mode="HTML")
        else:
            await m.answer(caption, parse_mode="HTML")

    except FetchError as e:
        log.warning("[AI] fetch error: %s", e)
        # Если поиск упал, но Gemini есть — сгенерируем карточку без поиска
        if have_gemini():
            caption = await generate_caption_with_gemini(q, results=None)
            await m.answer(caption, parse_mode="HTML")
        else:
            await m.answer("Не получилось получить данные из интернета. Попробуй другой запрос.")
