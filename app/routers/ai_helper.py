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
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton

# ---- метрики / sales-интенты ----
from app.services.stats import ai_inc, ai_observe_ms
from app.services.sales_intents import detect_sales_intent, suggest_any_in_category

# ---- локальная база (JSON) ----
from app.services.brands import exact_lookup, get_brand, fuzzy_suggest
try:
    from app.services.brands import smart_lookup as _smart_lookup
except Exception:
    _smart_lookup = None

# ---- KB / RAG (опционально) ----
try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

try:
    from app.services.knowledge import find_record as kb_find_record, build_caption_from_kb
except Exception:
    kb_find_record = None
    def build_caption_from_kb(_): return ""

# ---- веб-поиск / картинки (синхронные функции!) ----
# Если у тебя есть агрегатор web_search, можно поменять импорт на него.
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
# 4) ВЕБ → (LLM или фолбэк) + картинка
with suppress(Exception):
    ai_inc("ai.query", tags={"intent": "brand"})

try:
    results = web_search_brand(q)   # без await
except Exception:
    results = {}

# === НОВОЕ: пробуем достать факты со страниц разрешённых доменов ===
try:
    from app.services.extractors import fetch_and_extract
    enriched = fetch_and_extract(brand_guess or q, results, max_pages=3)
except Exception:
    enriched = {}

caption = ""
photo_url = None

# если что-то вытащили — соберём карточку без LLM
if enriched and (enriched.get("basics") or enriched.get("taste")):
    name = enriched.get("name") or (brand_guess or q)
    b = enriched.get("basics") or {}
    lines = [f"<b>{name}</b>"]
    meta = " | ".join([x for x in [b.get("category"), b.get("country"), b.get("abv")] if x])
    if meta: lines.append("• " + meta)
    if enriched.get("taste"): lines.append("• Профиль: " + enriched["taste"])
    for fct in (enriched.get("facts") or [])[:3]:
        if not fct.lower().startswith("категория") and not fct.lower().startswith("страна") and not fct.lower().startswith("крепость"):
            lines.append("• " + fct)
    srcs = enriched.get("sources") or []
    if srcs:
        refs = " ".join([f"<a href='{u}'>[{i+1}]</a>" for i, u in enumerate(srcs[:3])])
        lines.append("Источники: " + refs)
    caption = _sanitize_caption("\n".join(lines))
    photo_url = enriched.get("image_url")

# если не хватило фактов — пробуем LLM по результатам
if not caption and generate_caption_with_gemini:
    try:
        caption = await generate_caption_with_gemini(q, results or {})
    except Exception:
        caption = ""

if not caption:
    items = (results or {}).get("results", [])
    lines = []
    if brand_guess:
        lines.append(f"<b>{brand_guess}</b>")
    for r in items[:5]:
        name_ = r.get("name") or r.get("title") or ""
        snip = r.get("snippet") or ""
        if name_:
            lines.append(f"• {name_} — {snip}")
    caption = "\n".join([l for l in lines if l]) or "Ничего не нашёл в вебе."
caption = _sanitize_caption(caption)

# Картинка — если экстрактор не нашёл, доберём через image_search
if not photo_url:
    with suppress(Exception):
        img = image_search_brand((brand_guess or q) + " bottle label")
        if isinstance(img, dict):
            photo_url = img.get("contentUrl") or img.get("contextLink")
if photo_url:
    try:
        from app.services.brands import set_image_url_for_brand
        set_image_url_for_brand(brand_guess or q, photo_url)
    except Exception:
        pass

try:
    if photo_url:
        await m.answer_photo(photo=photo_url, caption=caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
    else:
        await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
except TelegramBadRequest:
    await m.answer(caption, reply_markup=menu_ai_exit_kb())


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
# Клавиатуры и тексты (берём из menus.py)
# =========================
try:
    from app.keyboards.menus import (
        AI_ENTRY_BUTTON_TEXT as MENU_AI_ENTRY,
        AI_EXIT_BUTTON_TEXT as MENU_AI_EXIT,
        ai_exit_inline_kb as menu_ai_exit_kb,
    )
except Exception:
    MENU_AI_ENTRY = "AI эксперт 🤖"
    MENU_AI_EXIT  = "Выйти из AI режима"
    def menu_ai_exit_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=MENU_AI_EXIT, callback_data="ai:exit")]]
        )

AI_ENTRY_TEXT = MENU_AI_ENTRY
AI_EXIT_TEXT  = MENU_AI_EXIT

# =========================
# Санитайзер для подписи Telegram
# =========================
_ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "br"}

def _sanitize_caption(html: str, limit: int = 1000) -> str:
    if not html:
        return ""
    html = re.sub(r"</?(?:h[1-6]|p|ul|ol|li)>", "", html, flags=re.I)
    html = re.sub(r"<\s*strong\s*>", "<b>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*strong\s*>", "</b>", html, flags=re.I)
    html = re.sub(r"<\s*em\s*>", "<i>", html, flags=re.I)
    html = re.sub(r"<\s*/\s*em\s*>", "</i>", html, flags=re.I)
    def _strip_tag(m):
        tag = m.group(1).lower()
        return m.group(0) if tag in _ALLOWED_TAGS else ""
    html = re.sub(r"</?([a-z0-9]+)(?:\s+[^>]*)?>", _strip_tag, html)
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
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
    e = exact_lookup(q)
    if e:
        return e
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
        reply_markup=menu_ai_exit_kb(),
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
        reply_markup=menu_ai_exit_kb(),
    )

@router.message(F.text == AI_EXIT_TEXT)
@router.message(F.text == "/ai_off")
@router.callback_query(F.data.in_({"ai:exit", "ai_exit"}))
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
# Главный AI-хендлер
# =========================
@router.message(lambda m: m.from_user.id in AI_USERS and m.text is not None)
async def handle_ai(m: Message):
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

    # ====== РАННИЕ ИНТЕНТЫ ======
    # 1) «Как продать …»
    is_sales, outlet, brand_for_sales = detect_sales_intent(q)
    if is_sales:
        html = ""
        if generate_sales_playbook_with_gemini:
            with suppress(Exception):
                html = await generate_sales_playbook_with_gemini(q, outlet, brand_for_sales)
        if not html:
            # простой фолбэк, если LLM недоступен
            brand_hint = brand_for_sales or _guess_brand(q) or q
            html = (
                f"<b>Как продавать: {brand_hint}</b>\n"
                f"• Уточни вкус покупателя (сладость/сухость; ваниль/фрукты/дым).\n"
                f"• Предложи хайболл или классику (Old Fashioned / Sour), без цен.\n"
                f"• 1 фраза про происхождение/бочки как «историю бренда».\n"
                f"• Апселл: премиальная версия; кросс-селл: подходящая закуска."
            )
        await m.answer(_sanitize_caption(html), parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        return

    # 2) «Любой/неважно какой <категория>»
    any_res = suggest_any_in_category(q)
    if any_res:
        display_cat, names = any_res
        first = names[0]
        item = get_brand(first)
        caption = item["caption"] if item else f"<b>{first}</b>\n• Нет локальной карточки."
        photo_id = (item or {}).get("photo_file_id") or (item or {}).get("image_url")

        if not photo_id:
            with suppress(Exception):
                img = image_search_brand(first + " бутылка этикетка")
                if isinstance(img, dict):
                    photo_id = img.get("contentUrl") or img.get("contextLink")

        if photo_id:
            await m.answer_photo(photo=photo_id, caption=_sanitize_caption(caption), parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        else:
            await m.answer(_sanitize_caption(caption), parse_mode="HTML", reply_markup=menu_ai_exit_kb())

        # клавиатура с альтернативами
        try:
            kb = ReplyKeyboardBuilder()
            for n in names[:10]:
                kb.add(KeyboardButton(text=n))
            kb.add(KeyboardButton(text="Назад"))
            kb.adjust(2)
            await m.answer(f"Могу предложить бренды в категории «{display_cat}»:",
                           reply_markup=kb.as_markup(resize_keyboard=True))
        except Exception:
            pass
        return
    # ====== КОНЕЦ РАННИХ ИНТЕНТОВ ======

    # индикация "печатает…"
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_pulse(m, stop_typing))
    t0 = time.monotonic()

    try:
        # 1) Локальная JSON карточка
        name = _smart_lookup(q) if _smart_lookup else None
        if not name:
            name = exact_lookup(q)
        if not name:
            with suppress(Exception):
                cand = fuzzy_suggest(q, limit=1)
                if cand and cand[0][1] >= 0.72:
                    name = cand[0][0]

        if name:
            ai_inc("ai.query", tags={"intent": "brand"})
            item = get_brand(name)
            if item:
                caption = _sanitize_caption(item["caption"])
                photo_id = item.get("photo_file_id") or item.get("image_url")

                try:
                    if photo_id:
                        await m.answer_photo(
                            photo=photo_id,
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=menu_ai_exit_kb(),
                        )
                    else:
                        await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
                except TelegramBadRequest:
                    ai_inc("ai.error", tags={"stage": "tg_parse"})
                    with suppress(Exception):
                        await m.answer(caption, reply_markup=menu_ai_exit_kb())

                stop_typing.set()
                with suppress(Exception):
                    await typing_task
                dt_ms = (time.monotonic() - t0) * 1000
                ai_inc("ai.source", tags={"source": "local"})
                ai_inc("ai.answer", tags={"intent": "brand", "source": "local"})
                ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "local"})
                log.info("[AI] local card in %.2fs", dt_ms / 1000.0)
                return

        # 2) KB-first (прямая карточка)
        brand_guess = _guess_brand(q)
        if kb_find_record:
            try:
                rec = kb_find_record(q, brand_hint=brand_guess)
            except TypeError:
                rec = kb_find_record(q)
            if rec:
                caption = _sanitize_caption(build_caption_from_kb(rec))
                try:
                    await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
                except TelegramBadRequest:
                    await m.answer(caption, reply_markup=menu_ai_exit_kb())

                stop_typing.set()
                with suppress(Exception):
                    await typing_task
                dt_ms = (time.monotonic() - t0) * 1000
                ai_inc("ai.source", tags={"source": "kb"})
                ai_inc("ai.answer", tags={"intent": "brand", "source": "kb"})
                ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb"})
                log.info("[AI] kb direct card in %.2fs", dt_ms / 1000.0)
                return

        # 3) KB → LLM (если есть)
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
                await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())

                stop_typing.set()
                with suppress(Exception):
                    await typing_task
                dt_ms = (time.monotonic() - t0) * 1000
                ai_inc("ai.source", tags={"source": "kb"})
                ai_inc("ai.answer", tags={"intent": "brand", "source": "kb"})
                ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb"})
                log.info("[AI] kb gemini card in %.2fs", dt_ms / 1000.0)
                return

        # 4) ВЕБ → (LLM или фолбэк) + картинка
        with suppress(Exception):
            ai_inc("ai.query", tags={"intent": "brand"})

        # ВАЖНО: web_search_brand и image_search_brand — синхронные!
        try:
            results = web_search_brand(q)   # без await
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
            items = (results or {}).get("results", [])
            lines = []
            if brand_guess:
                lines.append(f"<b>{brand_guess}</b>")
            for r in items[:5]:
                name_ = r.get("name") or r.get("title") or ""
                snip = r.get("snippet") or ""
                if name_:
                    lines.append(f"• {name_} — {snip}")
            caption = "\n".join([l for l in lines if l]) or "Ничего не нашёл в вебе."
        caption = _sanitize_caption(caption)

        # Картинка
        photo_url = None
        with suppress(Exception):
            img = image_search_brand((brand_guess or q) + " bottle label")
            if isinstance(img, dict):
                photo_url = img.get("contentUrl") or img.get("contextLink")

        try:
            if photo_url:
                await m.answer_photo(photo=photo_url, caption=caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
            else:
                await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        except TelegramBadRequest:
            await m.answer(caption, reply_markup=menu_ai_exit_kb())

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
# Sales-интент (вспомогательная, если где-то пригодится)
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
        await m.answer(text, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
    except TelegramBadRequest:
        await m.answer(text, reply_markup=menu_ai_exit_kb())

    stop_typing.set()
    with suppress(Exception):
        await typing_task
    dt_ms = (time.monotonic() - t0) * 1000
    ai_inc("ai.source", tags={"source": "sales"})
    ai_inc("ai.answer", tags={"intent": "sales", "source": "sales"})
    ai_observe_ms("ai.latency", dt_ms, tags={"intent": "sales", "source": "sales"})
    log.info("[AI] sales in %.2fs", dt_ms / 1000.0)
