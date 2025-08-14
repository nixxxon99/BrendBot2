# app/routers/ai_helper.py
from __future__ import annotations

import asyncio
import time
import logging
from contextlib import suppress
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

# --- сервисы и утилиты ---
from app.services.stats import ai_inc, ai_observe_ms
from app.services.sales_intents import detect_sales_intent

# Локальная база (JSON)
from app.services.brands import exact_lookup, get_brand, fuzzy_suggest

# KB / RAG (опционально — не упадём, если файла нет)
try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

# Статическая KB-карточка (опционально)
try:
    from app.services.knowledge import find_record as kb_find_record, build_caption_from_kb
except Exception:
    kb_find_record = None
    def build_caption_from_kb(_): return ""

# Веб-поиск и картинки
from app.services.ai_google import web_search_brand, image_search_brand

# LLM (опционально — если ключа нет, есть фолбэк)
try:
    from app.services.ai_gemini import generate_caption_with_gemini, generate_sales_playbook_with_gemini
except Exception:
    generate_caption_with_gemini = None
    generate_sales_playbook_with_gemini = None

log = logging.getLogger(__name__)
router = Router()

# =========================
# Кэш CSE и антиспам/очередь
# =========================
AI_USERS: set[int] = set()

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60 * 30  # 30 минут

def _cache_get(key: str):
    item = _CACHE.get(key)
    if not item: return None
    ts, data = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return data

def _cache_set(key: str, data: dict):
    _CACHE[key] = (time.time(), data)

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_USER_LAST: dict[int, float] = {}
_COOLDOWN = 4.0  # секунд между запросами

def _user_lock(uid: int) -> asyncio.Lock:
    if uid not in _USER_LOCKS:
        _USER_LOCKS[uid] = asyncio.Lock()
    return _USER_LOCKS[uid]

# =========================
# Клавиатуры
# =========================
AI_ENTRY_BUTTON_TEXT = "🤖 AI режим"
AI_EXIT_BUTTON_TEXT  = "⬅️ Выйти из AI"

def ai_exit_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Выйти из AI", callback_data="ai_exit")]
    ])

# =========================
# Санитайзер подписи для Telegram
# =========================
import re
_ALLOWED_TAGS = {"b","i","u","s","a","code","pre","br"}

def _sanitize_caption(html: str, limit: int = 1000) -> str:
    if not html: return ""
    # Уберём запрещённые теги грубо
    html = re.sub(r"</?(?:h[1-6]|p|ul|ol|li)>", "", html, flags=re.I)
    # Приведём strong/em -> b/i
    html = re.sub(r"<\s*strong\s*>", "<b>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*strong\s*>", "</b>", html, flags=re.I)
    html = re.sub(r"<\s*em\s*>", "<i>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*em\s*>", "</i>", html, flags=re.I)
    # Уберём любые теги, кроме разрешённых (допустим брютально)
    def _strip_tag(m):
        tag = m.group(1).lower()
        return m.group(0) if tag in _ALLOWED_TAGS else ""
    html = re.sub(r"</?([a-z0-9]+)(?:\s+[^>]*)?>", _strip_tag, html)
    # Сжать пустые строки
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
    # Ограничить длину
    if len(html) > limit:
        html = html[:limit-1].rstrip() + "…"
    return html

# =========================
# Служебные функции
# =========================
def _normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

async def _typing_pulse(m: Message, stop_evt: asyncio.Event):
    """Фоновая «печатает…» индикация раз в 4с."""
    try:
        while not stop_evt.is_set():
            with suppress(Exception):
                await m.bot.send_chat_action(m.chat.id, "typing")
            await asyncio.wait_for(stop_evt.wait(), timeout=4.0)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass

def _cooldown_left(uid: int) -> float:
    last = _USER_LAST.get(uid, 0)
    left = _COOLDOWN - (time.time() - last)
    return max(0.0, left)

def _mark_used(uid: int):
    _USER_LAST[uid] = time.time()

# =========================
# БРЕНД: угадывание (JSON → KB)
# =========================
import difflib

def _kb_brand_names() -> list[str]:
    """Вернёт список имён брендов из статической KB (если есть)."""
    try:
        from pathlib import Path
        import json
        p = Path("data/brands_kb.json")
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        names = []
        if isinstance(data, list):
            for it in data:
                n = (it.get("brand") or "").strip()
                if n: names.append(n)
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    names.append((v.get("brand") or k).strip())
        return [n for n in names if n]
    except Exception:
        return []

def _guess_brand(q: str) -> Optional[str]:
    # 1) точный матч по локальному каталогу
    e = exact_lookup(q)
    if e:
        return e

    # 2) fuzzy по локальному каталогу (важно: даём шанс JSON первее остальных)
    try:
        cand = fuzzy_suggest(q, limit=1)
        if cand and cand[0][1] >= 0.72:
            return cand[0][0]
    except Exception:
        pass

    # 3) имена из статической KB (если есть)
    cand = _kb_brand_names()
    if not cand:
        return None

    norm = _normalize_text(q).lower()

    # contains
    for name in cand:
        if name.lower() in norm or norm in name.lower():
            return name

    # fuzzy по KB-именам
    match = difflib.get_close_matches(norm, [c.lower() for c in cand], n=1, cutoff=0.72)
    if match:
        lower2real = {c.lower(): c for c in cand}
        return lower2real.get(match[0])
    return None

# =========================
# Хэндлеры вход/выход из AI
# =========================
@router.message(F.text == AI_ENTRY_BUTTON_TEXT)
@router.message(F.text == "/ai")
async def ai_mode(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "AI-режим включён. Напиши бренд или вопрос.\n"
        "Приоритет источников: <b>локальная база → KB → веб</b>.",
        parse_mode="HTML",
        reply_markup=ai_exit_inline_kb(),
    )

@router.message(F.text == AI_EXIT_BUTTON_TEXT)
@router.message(F.text == "/ai_off")
@router.callback_query(F.data == "ai_exit")
async def ai_mode_off(ev):
    user_id = ev.from_user.id if hasattr(ev, "from_user") else ev.message.from_user.id
    AI_USERS.discard(user_id)
    with suppress(Exception):
        if hasattr(ev, "message"):
            await ev.message.answer("AI-режим выключен.")
            await ev.answer()  # callback ack
        else:
            await ev.answer("AI-режим выключен.")

# =========================
# Главный AI-хэндлер
# =========================
@router.message(lambda m: m.from_user.id in AI_USERS and m.text is not None)
async def handle_ai(m: Message):
    # Антиспам: по одному запросу и кулдаун
    lock = _user_lock(m.from_user.id)
    if lock.locked():
        await m.answer("Уже отвечаю на предыдущий запрос…")
        return

    left = _cooldown_left(m.from_user.id)
    if left > 0.1:
        await m.answer(f"Подождите {left:.0f} сек…")
        return

    async with lock:
        _mark_used(m.from_user.id)
        await _answer_ai(m, m.text.strip())

async def _answer_ai(m: Message, text: str):
    q = _normalize_text(text)
    if not q:
        await m.answer("Напишите запрос или название бренда.")
        return

    # «печатает…»
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_pulse(m, stop_typing))

    t0 = time.monotonic()
    try:
        # Sales intent?
        intent = detect_sales_intent(q)
        if intent:
            await _answer_sales(m, q, intent, stop_typing, typing_task, t0)
            return

        # Бренд/карточка
        await _answer_brand(m, q, stop_typing, typing_task, t0)

    finally:
        stop_typing.set()
        with suppress(Exception):
            await typing_task

async def _answer_brand(m: Message, q: str, stop_typing: asyncio.Event, typing_task: asyncio.Task, t0: float):
    """
    Приоритет: 1) Локальная JSON-карточка → 2) KB-first → 3) Веб
    """

    # 1) локальная карточка из твоей базы (точный или уверенный fuzzy)
    name = exact_lookup(q)
    if not name:
        try:
            cand = fuzzy_suggest(q, limit=1)
            if cand and cand[0][1] >= 0.72:
                name = cand[0][0]
        except Exception:
            name = None

    if name:
        ai_inc("ai.query", tags={"intent": "brand"})
        item = get_brand(name)
        if item:
            caption = _sanitize_caption(item["caption"])
            photo_id = item.get("photo_file_id")

            try:
                if photo_id:
                    await m.answer_photo(
                        photo=photo_id,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=ai_exit_inline_kb(),
                    )
                else:
                    await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
            except TelegramBadRequest:
                ai_inc("ai.error", tags={"stage": "tg_parse"})
                with suppress(Exception):
                    await m.answer(caption, reply_markup=ai_exit_inline_kb())

            stop_typing.set()
            with suppress(Exception):
                await typing_task
            dt_ms = (time.monotonic() - t0) * 1000
            ai_inc("ai.source", tags={"source": "local"})
            ai_inc("ai.answer", tags={"intent": "brand", "source": "local"})
            ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "local"})
            log.info("[AI] local card in %.2fs", dt_ms / 1000.0)
            return

    # 2) KB-first
    brand_guess = _guess_brand(q)

    # 2a) статическая запись (brands_kb.json) -> прямая карточка
    if kb_find_record:
        try:
            rec = kb_find_record(q, brand_hint=brand_guess)
        except TypeError:
            # старый интерфейс без brand_hint
            rec = kb_find_record(q) if kb_find_record else None

        if rec:
            caption = _sanitize_caption(build_caption_from_kb(rec))
            try:
                await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
            except TelegramBadRequest:
                await m.answer(caption, reply_markup=ai_exit_inline_kb())

            stop_typing.set()
            with suppress(Exception):
                await typing_task
            dt_ms = (time.monotonic() - t0) * 1000
            ai_inc("ai.source", tags={"source": "kb"})
            ai_inc("ai.answer", tags={"intent": "brand", "source": "kb"})
            ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb"})
            log.info("[AI] kb direct card in %.2fs", dt_ms / 1000.0)
            return

    # 2b) RAG-ретривер -> Gemini карточка
    if kb_retrieve and generate_caption_with_gemini:
        try:
            kb = kb_retrieve(q, brand=brand_guess, top_k=8)
        except TypeError:
            kb = kb_retrieve(q)  # старый интерфейс
        if kb and kb.get("results"):
            try:
                caption = await generate_caption_with_gemini(q, kb)
            except Exception:
                caption = ""
            caption = _sanitize_caption(caption) or "Нет фактов в KB."
            await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())

            stop_typing.set()
            with suppress(Exception):
                await typing_task
            dt_ms = (time.monotonic() - t0) * 1000
            ai_inc("ai.source", tags={"source": "kb"})
            ai_inc("ai.answer", tags={"intent": "brand", "source": "kb"})
            ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb"})
            log.info("[AI] kb gemini card in %.2fs", dt_ms / 1000.0)
            return

    # 3) WEB-поиск (кэш 30 мин) -> Gemini / фолбэк
    cached = _cache_get(q)
    if cached:
        results = cached
    else:
        with suppress(Exception):
            ai_inc("ai.query", tags={"intent": "brand"})
        try:
            results = await web_search_brand(q)
        except Exception:
            results = {}
        if results:
            _cache_set(q, results)

    if generate_caption_with_gemini:
        try:
            caption = await generate_caption_with_gemini(q, results or {})
        except Exception:
            caption = ""
    else:
        caption = ""

    if not caption:
        # очень короткий фолбэк без LLM
        items = (results or {}).get("results", [])
        lines = []
        if brand_guess:
            lines.append(f"<b>{brand_guess}</b>")
        for r in items[:5]:
            name = r.get("name") or r.get("title") or ""
            snip = r.get("snippet") or ""
            if name:
                lines.append(f"• {name} — {snip}")
        caption = "\n".join([l for l in lines if l]) or "Ничего не нашёл."

    caption = _sanitize_caption(caption)

    # Попробуем картинку по бренду/запросу
    photo = None
    with suppress(Exception):
        photo = await image_search_brand((brand_guess or q) + " bottle label")

    try:
        if photo:
            await m.answer_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
        else:
            await m.answer(caption, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
    except TelegramBadRequest:
        await m.answer(caption, reply_markup=ai_exit_inline_kb())

    stop_typing.set()
    with suppress(Exception):
        await typing_task
    dt_ms = (time.monotonic() - t0) * 1000
    ai_inc("ai.source", tags={"source": "web"})
    ai_inc("ai.answer", tags={"intent": "brand", "source": "web"})
    ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "web"})
    log.info("[AI] web card in %.2fs", dt_ms / 1000.0)

# =========================
# Sales-интент
# =========================
async def _answer_sales(m: Message, q: str, outlet: str, stop_typing: asyncio.Event, typing_task: asyncio.Task, t0: float):
    brand_guess = _guess_brand(q) or q
    text = ""
    if generate_sales_playbook_with_gemini:
        try:
            text = await generate_sales_playbook_with_gemini(q, outlet=outlet, brand=brand_guess)
        except Exception:
            text = ""
    if not text:
        text = (
            f"<b>Как продавать: {brand_guess}</b>\n"
            f"• Уточни вкус: сладость/сухость, ваниль/фрукты/дым.\n"
            f"• Предложи хайболл или короткий классический коктейль.\n"
            f"• Пара слов о происхождении и выдержке — без цен и сравнения с конкурентами."
        )

    text = _sanitize_caption(text, limit=1000)
    try:
        await m.answer(text, parse_mode="HTML", reply_markup=ai_exit_inline_kb())
    except TelegramBadRequest:
        await m.answer(text, reply_markup=ai_exit_inline_kb())

    stop_typing.set()
    with suppress(Exception):
        await typing_task
    dt_ms = (time.monotonic() - t0) * 1000
    ai_inc("ai.source", tags={"source": "sales"})
    ai_inc("ai.answer", tags={"intent": "sales", "source": "sales"})
    ai_observe_ms("ai.latency", dt_ms, tags={"intent": "sales", "source": "sales"})
    log.info("[AI] sales in %.2fs", dt_ms / 1000.0)
