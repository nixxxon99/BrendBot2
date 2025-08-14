# app/routers/ai_helper.py
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatAction  # typing-индикатор
import logging
import time
import re  # для санитайзера
import difflib
import json
import asyncio
from pathlib import Path
from contextlib import suppress

from app.keyboards.menus import (
    AI_ENTRY_BUTTON_TEXT,
    AI_EXIT_BUTTON_TEXT,
    main_menu_kb,
    ai_exit_inline_kb,
)

log = logging.getLogger(__name__)

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

# =========================
# Кэш CSE и антиспам/очередь
# =========================
AI_USERS: set[int] = set()

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

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_LAST_AT: dict[int, float] = {}
_COOLDOWN_SEC = 4.0  # минимальная пауза между запросами

def _user_lock(uid: int) -> asyncio.Lock:
    lock = _USER_LOCKS.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[uid] = lock
    return lock

def _too_soon(uid: int) -> float:
    now = time.monotonic()
    last = _LAST_AT.get(uid, 0.0)
    left = _COOLDOWN_SEC - (now - last)
    return left if left > 0 else 0.0

def _mark_used(uid: int):
    _LAST_AT[uid] = time.monotonic()

async def _typing_pulse(bot, chat_id: int, stop: asyncio.Event, period: float = 4.0):
    try:
        while not stop.is_set():
            with suppress(Exception):
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            try:
                await asyncio.wait_for(stop.wait(), timeout=period)
            except asyncio.TimeoutError:
                continue
    except Exception:
        pass

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

    # антиспам: кулдаун
    left = _too_soon(m.from_user.id)
    if left > 0:
        with suppress(Exception):
            await m.answer(f"Чуть-чуть погодите {left:.0f} сек…")
        return

    async with _user_lock(m.from_user.id):
        _mark_used(m.from_user.id)
        t0 = time.monotonic()
        log.info("[AI] user=%s query=%r", m.from_user.id, q)

        # фон: «печатает…»
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(_typing_pulse(m.bot, m.chat.id, stop_typing))

        # 0) Продажный интент: “как продать …”
        sale = detect_sales_intent(q)
        if sale and have_gemini():
            try:
                html = await asyncio.wait_for(
                    generate_sales_playbook_with_gemini(q, sale.get("outlet"), _guess_brand(q)),
                    timeout=25.0,
                )
            except asyncio.TimeoutError:
                html = "<b>Долго думаем…</b>\n• Попробуйте уточнить запрос или повторить позже."
            text = _sanitize_caption(html)
            with suppress(Exception):
                await m.answer(text, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
            stop_typing.set()
            with suppress(Exception):
                await typing_task
            log.info("[AI] sales playbook in %.2fs", time.monotonic() - t0)
            return

        # 1) локальная карточка из твоей базы (если точный матч)
        name = exact_lookup(q)
        if name:
            item = get_brand(name)
            caption = _sanitize_caption(item["caption"])
            with suppress(Exception):
                await m.answer_photo(
                    photo=item["photo_file_id"],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=ai_exit_inline_kb(),
                )
            stop_typing.set()
            with suppress(Exception):
                await typing_task
            log.info("[AI] local card in %.2fs", time.monotonic() - t0)
            return

        # 2) Иначе — пробуем KB-first (если есть ретривер), потом — Google CSE
        status_msg = None
        with suppress(Exception):
            status_msg = await m.answer("Ищу в интернете и готовлю карточку…")

        try:
            brand_guess = _guess_brand(q)

            # --- KB-first (опционально) ---
            caption: str | None = None
            kb_chunks = None
            if kb_retrieve:
                try:
                    kb_chunks = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda: kb_retrieve(q, brand=brand_guess, top_k=8)
                        ),
                        timeout=5.0,
                    )
                except Exception as e:
                    log.warning("[AI] kb_retrieve error: %s", e)
                    kb_chunks = None

            if kb_chunks and have_gemini():
                try:
                    raw = await asyncio.wait_for(generate_caption_with_gemini(q, kb_chunks), timeout=30.0)
                except asyncio.TimeoutError:
                    raw = ""
                caption = _sanitize_caption(raw)

            # --- Веб-поиск, если KB не дал результата ---
            if not caption:
                results = _cache_get(q)
                if results is None:
                    try:
                        results = await asyncio.wait_for(
                            asyncio.to_thread(web_search_brand, q),
                            timeout=15.0
                        )
                    except asyncio.TimeoutError:
                        results = {"results": []}
                    _cache_set(q, results)

                if have_gemini():
                    try:
                        raw = await asyncio.wait_for(generate_caption_with_gemini(q, results), timeout=35.0)
                    except asyncio.TimeoutError:
                        raw = ""
                else:
                    raw = build_caption_from_results(q, results)
                caption = _sanitize_caption(raw or f"<b>{q}</b>\n• Короткая сводка недоступна.")

            # Картинка (если у тебя в ai_google ограничено на Wikipedia — это применится здесь автоматически)
            img = None
            try:
                img = await asyncio.wait_for(
                    asyncio.to_thread(image_search_brand, (brand_guess or q) + " бутылка бренд алкоголь label"),
                    timeout=8.0
                )
            except asyncio.TimeoutError:
                img = None

            # удалить «Ищу…» и отдать ответ
            with suppress(Exception):
                if status_msg:
                    await status_msg.delete()

            with suppress(Exception):
                if img and img.get("contentUrl"):
                    await m.answer_photo(
                        photo=img["contentUrl"],
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=ai_exit_inline_kb(),
                    )
                else:
                    await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())

        except FetchError as e:
            log.warning("[AI] fetch error: %s", e)
            with suppress(Exception):
                if status_msg:
                    await status_msg.delete()

            if have_gemini():
                try:
                    raw = await asyncio.wait_for(generate_caption_with_gemini(q, results_or_chunks=None), timeout=25.0)
                except asyncio.TimeoutError:
                    raw = ""
                caption = _sanitize_caption(raw or f"<b>{q}</b>\n• Не удалось получить данные из интернета.")
                with suppress(Exception):
                    await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
            else:
                with suppress(Exception):
                    await m.answer(
                        "Не получилось получить данные из интернета. Попробуй другой запрос.",
                        reply_markup=ai_exit_inline_kb(),
                    )
        finally:
            stop_typing.set()
            with suppress(Exception):
                await typing_task
            log.info("[AI] finished in %.2fs", time.monotonic() - t0)
