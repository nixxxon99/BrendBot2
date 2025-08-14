# app/routers/ai_helper.py
from __future__ import annotations

import asyncio
import time
import logging
import re
import difflib
from contextlib import suppress
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

# ---- метрики / sales-интенты ----
from app.services.stats import ai_inc, ai_observe_ms
from app.services.sales_intents import detect_sales_intent

# ---- локальная база (JSON) ----
from app.services.brands import exact_lookup, get_brand, fuzzy_suggest
try:
    # если добавлял "умный" поиск (возраст/финиш), используем его
    from app.services.brands import smart_lookup as _smart_lookup
except Exception:
    _smart_lookup = None

# ---- KB / RAG (необязательно — пропустим, если нет) ----
try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

try:
    from app.services.knowledge import find_record as kb_find_record, build_caption_from_kb
except Exception:
    kb_find_record = None
    def build_caption_from_kb(_): return ""

# ---- веб-поиск / картинки ----
from app.services.ai_google import web_search_brand, image_search_brand

# ---- LLM (опционально) ----
try:
    from app.services.ai_gemini import (
        generate_caption_with_gemini,
        generate_sales_playbook_with_gemini,
    )
except Exception:
    generate_caption_with_gemini = None
    generate_sales_playbook_with_gemini = None

log = logging.getLogger(__name__)
router = Router()

# =========================
# Состояние AI-режима / антиспам
# =========================
AI_USERS: set[int] = set()

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_USER_LAST: dict[int, float] = {}
_COOLDOWN = 4.0  # сек между запросами

def _user_lock(uid: int) -> asyncio.Lock:
    if uid not in _USER_LOCKS:
        _USER_LOCKS[uid] = asyncio.Lock()
    return _USER_LOCKS[uid]

def _cooldown_left(uid: int) -> float:
    last = _USER_LAST.get(uid, 0.0)
    left = _COOLDOWN - (time.time() - last)
    return max(0.0, left)

def _mark_used(uid: int):
    _USER_LAST[uid] = time.time()

# =========================
# Клавиатуры и тексты
# =========================
# В твоём UI вход — inline-кнопка с callback_data="ai:enter" и текстом "🤖 AI-помощник".
AI_ENTRY_TEXT = "🤖 AI-помощник"
AI_EXIT_TEXT  = "Выйти из AI режима"  # текст для кнопки выхода (если понадобится как message)

def ai_exit_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=AI_EXIT_TEXT, callback_data="ai:exit")]]
    )

# =========================
# Санитайзер для подписи Telegram
# =========================
_ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "br"}

def _sanitize_caption(html: str, limit: int = 1000) -> str:
    if not html:
        return ""
    # выпиливаем запретные теги
    html = re.sub(r"</?(?:h[1-6]|p|ul|ol|li)>", "", html, flags=re.I)
    # strong/em -> b/i
    html = re.sub(r"<\s*strong\s*>", "<b>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*strong\s*>", "</b>", html, flags=re.I)
    html = re.sub(r"<\s*em\s*>", "<i>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*em\s*>", "</i>", html, flags=re.I)
    # убираем любые другие теги
    def _strip_tag(m):
        tag = m.group(1).lower()
        return m.group(0) if tag in _ALLOWED_TAGS else ""
    html = re.sub(r"</?([a-z0-9]+)(?:\s+[^>]*)?>", _strip_tag, html)
    # сжать пустые строки
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
    # ограничить длину
    if len(html) > limit:
        html = html[:limit-1].rstrip() + "…"
    return html

# =========================
# «печатает…» индикация
# =========================
async def _typing_pulse(m: Message, stop_evt: asyncio.Event):
    try:
        while not stop_evt.is_set():
            with suppress(Exception):
                await m.bot.send_chat_action(m.chat.id, "typing")
            await asyncio.wait_for(stop_evt.wait(), timeout=4.0)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass

# =========================
# Утилиты: нормализация / KB-имена / угадывание бренда
# =========================
def _normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _kb_brand_names() -> list[str]:
    """Список брендов из data/brands_kb.json (если есть)."""
    try:
        from pathlib import Path
        import json
        p = Path("data/brands_kb.json")
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        names: list[str] = []
        if isinstance(data, list):
            for it in data:
                n = (it.get("brand") or "").strip()
                if n:
                    names.append(n)
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    names.append((v.get("brand") or k).strip())
        return [n for n in names if n]
    except Exception:
        return []

def _guess_brand(q: str) -> Optional[str]:
    """Пробуем сопоставить запрос бренду: JSON (точно/умно) → KB."""
    # 1) точный матч по JSON
    e = exact_lookup(q)
    if e:
        return e

    # 2) «умный» поиск (если есть) или fuzzy
    if _smart_lookup:
        try:
            s = _smart_lookup(q)
            if s:
                return s
        except Exception:
            pass
    else:
        try:
            cand = fuzzy_suggest(q, limit=1)
            if cand and cand[0][1] >= 0.72:
                return cand[0][0]
        except Exception:
            pass

    # 3) KB-имена (если файл есть)
    cand = _kb_brand_names()
    if not cand:
        return None

    norm = _normalize_text(q).lower()
    for name in cand:
        if name.lower() in norm or norm in name.lower():
            return name

    match = difflib.get_close_matches(norm, [c.lower() for c in cand], n=1, cutoff=0.72)
    if match:
        lower2real = {c.lower(): c for c in cand}
        return lower2real.get(match[0])
    return None

# =========================
# Вход/выход из AI-режима
# =========================
@router.message(F.text == AI_ENTRY_TEXT)
@router.message(F.text == "/ai")
async def ai_mode_msg(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "AI-режим включён. Напишите бренд или вопрос.\n"
        "Приоритет: <b>локальная база → KB → веб</b>.",
        parse_mode="HTML",
        reply_markup=ai_exit_inline_kb(),
    )

@router.callback_query(F.data == "ai:enter")
async def ai_mode_cb(cb: CallbackQuery):
    AI_USERS.add(cb.from_user.id)
    with suppress(Exception):
        await cb.answer()
    await cb.message.answer(
        "AI-режим включён. Напишите бренд или вопрос.\n"
        "Приоритет: <b>локальная база → KB → веб</b>.",
        parse_mode="HTML",
        reply_markup=ai_exit_inline_kb(),
    )

@router.message(F.text == AI_EXIT_TEXT)
@router.message(F.text == "/ai_off")
@router.callback_query(F.data.in_({"ai:exit", "ai_exit"}))  # поддержим старый вариант
async def ai_mode_off(ev):
    user_id = ev.from_user.id if hasattr(ev, "from_user") else ev.message.from_user.id
    AI_USERS.discard(user_id)
    if isinstance(ev, CallbackQuery):
        with suppress(Exception):
            await ev.answer()
        with suppress(Exception):
            await ev.message.answer("AI-режим выключен.")
    else:
        await ev.answer("AI-режим выключен.")

# =========================
# Главный AI-хендлер (работает только в AI-режиме)
# =========================
@router.message(lambda m: m.from_user.id in AI_USERS and m.text is not None)
async def handle_ai(m: Message):
    # антиспам: по одному запросу и кулдаун
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
        # 0) sales-интент?
        outlet = detect_sales_intent(q)
        if outlet:
            await _answer_sales(m, q, outlet, stop_typing, typing_task, t0)
            return

        # 1) локальная карточка JSON (точный/умный матч)
        name = _smart_lookup(q) if _smart_lookup else None
        if not name:
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

        # 2) KB-first: прямая карточка из brands_kb.json
        brand_guess = _guess_brand(q)
        if kb_find_record:
            try:
                rec = kb_find_record(q, brand_hint=brand_guess)
            except TypeError:
                rec = kb_find_record(q)
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

        # 2b) KB → LLM карточка
        if kb_retrieve and generate_caption_with_gemini:
            try:
                kb = kb_retrieve(q, brand=brand_guess, top_k=8)
            except TypeError:
                kb = kb_retrieve(q)
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

        # 3) Веб → (LLM или фолбэк) + картинка
        cached_key = q  # можно усложнить ключ при желании
        results = None
        with suppress(Exception):
            ai_inc("ai.query", tags={"intent": "brand"})
        try:
            results = await web_search_brand(q)
        except Exception:
            results = {}

        if generate_caption_with_gemini:
            try:
                caption = await generate_caption_with_gemini(q, results or {})
            except Exception:
                caption = ""
        else:
            caption = ""

        if not caption:
            # простой фолбэк без LLM
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

    finally:
        stop_typing.set()
        with suppress(Exception):
            await typing_task

# =========================
# Sales-интент (короткий «плейбук»)
# =========================
async def _answer_sales(
    m: Message,
    q: str,
    outlet: str,
    stop_typing: asyncio.Event,
    typing_task: asyncio.Task,
    t0: float,
):
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
            f"• Уточни вкус гостя (сладость/сухость; ваниль/фрукты/дым).\n"
            f"• Предложи хайболл или короткую классику (Old Fashioned / Sour).\n"
            f"• 1 фраза про происхождение/бочки — как история.\n"
            f"• Апселл: большая порция/премиальная версия; кросс-селл: подходящая закуска."
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
