# app/routers/ai_helper.py
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
import logging
import time
import re  # для санитайзера
import difflib   # NEW: для лёгкого фзи-матча бренда
import json      # NEW
from pathlib import Path  # NEW

from app.keyboards.menus import (
    AI_ENTRY_BUTTON_TEXT,
    AI_EXIT_BUTTON_TEXT,
    main_menu_kb,
    ai_exit_inline_kb,
)

# === Санитайзер HTML под Telegram ===
def _sanitize_caption(html: str) -> str:
    if not html:
        return ""
    t = html

    # заголовки h1..h6 -> убрать
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

    # оставить только разрешённые: b, i, u, s, a, code, pre, br
    t = re.sub(r'<(?!/?(b|i|u|s|a|code|pre|br)\b)[^>]+>', '', t, flags=re.I)

    # сжать лишние пустые строки и ограничить длину подписи (у фото ~1024)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    if len(t) > 1000:
        t = t[:1000].rstrip() + "…"
    return t

from app.services.brands import exact_lookup, get_brand
# Поиск и картинки — через Google CSE
from app.services.ai_google import (
    web_search_brand, image_search_brand, build_caption_from_results, FetchError
)
# Текст карточки — через Gemini (структурный вывод JSON->HTML)
from app.services.ai_gemini import have_gemini, generate_caption_with_gemini
# Детектор "как продать ..."
from app.services.sales_intents import detect_sales_intent
from app.services.ai_gemini import generate_sales_playbook_with_gemini

# NEW (опционально): локальный RAG-ретривер. Если нет — просто игнорируется.
try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

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

# === BRAND GUESS (безопасно, работает даже если KB отсутствует) ===
def _kb_brand_names() -> list[str]:
    """
    Читает data/brands_kb.json и вытаскивает список brand.
    Если файла нет — вернёт [] и ничего не сломает.
    """
    try:
        p = Path("data/brands_kb.json")
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        names = {(row.get("brand") or "").strip() for row in data if isinstance(row, dict)}
        return sorted([n for n in names if n])
    except Exception:
        return []

_VOL_RE = re.compile(r"\b\d+[.,]?\d*\s*(л|l|ml|мл)\b", re.I)

def _normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = _VOL_RE.sub(" ", s)        # выкинуть литраж
    s = re.sub(r"\s{2,}", " ", s)  # сжать пробелы
    return s.strip()

def _guess_brand(q: str) -> str | None:
    # 1) точный матч по твоей базе
    e = exact_lookup(q)
    if e:
        return e

    # 2) матч по именам из KB (если есть)
    cand = _kb_brand_names()
    if not cand:
        return None

    norm = _normalize_text(q).lower()
    # contains
    for name in cand:
        if name.lower() in norm or norm in name.lower():
            return name

    # fuzzy
    match = difflib.get_close_matches(norm, [c.lower() for c in cand], n=1, cutoff=0.72)
    if match:
        lower2real = {c.lower(): c for c in cand}
        return lower2real.get(match[0])
    return None

# === Вход/выход в AI ===
@router.message(F.text == AI_ENTRY_BUTTON_TEXT)
async def enter_ai_by_button(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "AI-режим включён. Напишите название бренда или вопрос.",
        reply_markup=None,
    )

@router.message(Command("ai"))
async def enter_ai_cmd(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "AI-режим включён. Напишите название бренда или вопрос.",
        reply_markup=None,
    )

@router.callback_query(F.data == "ai:exit")
async def exit_ai_cb(c: CallbackQuery):
    AI_USERS.discard(c.from_user.id)
    await c.message.answer(
        "AI-режим выключен. Вы в главном меню.",
        reply_markup=main_menu_kb(),
    )
    await c.answer()

@router.message(F.text == AI_EXIT_BUTTON_TEXT)
@router.message(Command("ai_off"))
async def exit_ai_cmd(m: Message):
    AI_USERS.discard(m.from_user.id)
    await m.answer(
        "AI-режим выключен. Вы в главном меню.",
        reply_markup=main_menu_kb(),
    )

# === Основной AI-хендлер (только для тех, кто в AI_USERS) ===
@router.message(F.text & F.from_user.id.func(lambda uid: uid in AI_USERS))
async def ai_any_text(m: Message):
    q = (m.text or "").strip()
    if not q:
        return

    log.info("[AI] user=%s query=%r", m.from_user.id, q)

    # 0) Продажный интент: “как продать …”
    sale = detect_sales_intent(q)
    if sale and have_gemini():
        brand_guess = _guess_brand(q)  # лучше угадывать с опечатками
        raw = await generate_sales_playbook_with_gemini(q, sale.get("outlet"), brand_guess)
        text = _sanitize_caption(raw)
        try:
            await m.answer(text, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
        except Exception:
            await m.answer(text, reply_markup=ai_exit_inline_kb())
        return

    # 1) локальная карточка из твоей базы (если точный матч)
    name = exact_lookup(q)
    if name:
        item = get_brand(name)
        caption = _sanitize_caption(item["caption"])
        try:
            await m.answer_photo(
                photo=item["photo_file_id"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=ai_exit_inline_kb(),
            )
        except Exception:
            await m.answer_photo(
                photo=item["photo_file_id"],
                caption=caption,
                reply_markup=ai_exit_inline_kb(),
            )
        return

    # 2) Иначе — пробуем KB-first (если есть ретривер), потом — Google CSE
    await m.answer("Ищу данные и готовлю карточку…")
    try:
        brand_guess = _guess_brand(q)

        # --- KB-first (опционально) ---
        caption: str | None = None
        if kb_retrieve:
            try:
                kb_chunks = kb_retrieve(q, brand=brand_guess, top_k=8) if brand_guess else kb_retrieve(q, brand=None, top_k=8)
            except Exception as e:
                log.warning("[AI] kb_retrieve error: %s", e)
                kb_chunks = None
            if kb_chunks and have_gemini():
                raw = await generate_caption_with_gemini(q, kb_chunks)
                caption = _sanitize_caption(raw)

        # --- Веб-поиск, если KB не дал результата ---
        if not caption:
            results = _cache_get(q)
            if results is None:
                results = web_search_brand(q)  # внутри можешь ограничить белый список доменов
                _cache_set(q, results)

            if have_gemini():
                raw = await generate_caption_with_gemini(q, results)
            else:
                raw = build_caption_from_results(q, results)
            caption = _sanitize_caption(raw)

        # Картинка (если у тебя в ai_google ограничено на Wikipedia — это применится здесь автоматически)
        search_image_query = (brand_guess or q) + " бутылка бренд алкоголь label"
        img = image_search_brand(search_image_query)

        try:
            if img and img.get("contentUrl"):
                await m.answer_photo(
                    photo=img["contentUrl"],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=ai_exit_inline_kb(),
                )
            else:
                await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
        except Exception:
            # fallback без parse_mode на случай «unsupported start tag»
            if img and img.get("contentUrl"):
                await m.answer_photo(
                    photo=img["contentUrl"],
                    caption=caption,
                    reply_markup=ai_exit_inline_kb(),
                )
            else:
                await m.answer(caption, reply_markup=ai_exit_inline_kb())

    except FetchError as e:
        log.warning("[AI] fetch error: %s", e)
        # Если поиск упал, но Gemini есть — сгенерируем карточку без источников (минимальный JSON в ai_gemini)
        if have_gemini():
            raw = await generate_caption_with_gemini(q, results_or_chunks=None)
            caption = _sanitize_caption(raw)
            try:
                await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
            except Exception:
                await m.answer(caption, reply_markup=ai_exit_inline_kb())
        else:
            await m.answer(
                "Не получилось получить данные из интернета. Попробуй другой запрос.",
                reply_markup=ai_exit_inline_kb(),
            )
