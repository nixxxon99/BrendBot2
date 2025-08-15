# app/routers/ai_helper.py — OFFLINE ONLY (без веба и без local JSON)
from __future__ import annotations

import asyncio
import time
import logging
import re
import difflib
import json
from pathlib import Path
from contextlib import suppress
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton

# ---- метрики / sales-интенты (оставим как было) ----
from app.services.stats import ai_inc, ai_observe_ms
from app.services.sales_intents import detect_sales_intent, suggest_any_in_category

# ---- KB / RAG (опционально; если модуля нет, используем локальный загрузчик) ----
try:
    from app.services.knowledge import find_record as kb_find_record, build_caption_from_kb
except Exception:
    kb_find_record = None
    def build_caption_from_kb(_): return ""

try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

# ---- LLM (по желанию; используется ТОЛЬКО поверх KB, а не веба) ----
try:
    from app.services.ai_gemini import generate_caption_with_gemini, generate_sales_playbook_with_gemini
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
# OFFLINE KB: простой загрузчик ingested_kb.json и поиск по алиасам
# =========================
_KB_CACHE: list[dict] = []
_KB_PATHS = [
    Path("data/ingested_kb.json"),
    Path("data/kb/winespecialist.json"),  # если появятся site-packs
]

def _load_kb_once():
    global _KB_CACHE
    if _KB_CACHE:
        return
    out = []
    for p in _KB_PATHS:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    out.extend(data)
            except Exception as e:
                log.warning("[KB] read fail %s: %s", p, e)
    _KB_CACHE = out

def _all_names(rec: dict) -> list[str]:
    names = set()
    for k in ("name", "brand", "title"):
        v = (rec.get(k) or "").strip()
        if v:
            names.add(v)
    for a in rec.get("aliases") or []:
        vv = (a or "").strip()
        if vv:
            names.add(vv)
    return list(names)

def _kb_find_local(query: str) -> Tuple[Optional[dict], Optional[str]]:
    """ищем лучшую запись по точному совпадению или ближайшему алиасу"""
    _load_kb_once()
    q = (query or "").strip().lower()
    if not q:
        return None, None
    best = None
    best_name = None
    best_score = 0.0

    for rec in _KB_CACHE:
        names = _all_names(rec)
        # точное/вхождение
        for n in names:
            nlow = n.lower()
            if nlow == q or q in nlow or nlow in q:
                return rec, n  # мгновенно
        # близость
        ratio = max([difflib.SequenceMatcher(a=q, b=n.lower()).ratio() for n in names] + [0.0])
        if ratio > best_score:
            best_score = ratio
            best = rec
            best_name = names[0] if names else None

    if best and best_score >= 0.72:
        return best, best_name
    return None, None

def _caption_from_rec(rec: dict, display_name: Optional[str] = None) -> str:
    name = display_name or rec.get("name") or rec.get("brand") or "Бренд"
    category = rec.get("category")
    country  = rec.get("country")
    abv      = rec.get("abv")
    notes    = rec.get("tasting_notes") or []
    facts    = rec.get("facts") or []
    sources  = rec.get("sources") or []

    lines = [f"<b>{name}</b>"]
    meta_bits = [category, country, abv]
    meta = " | ".join([x for x in meta_bits if x])
    if meta:
        lines.append("• " + meta)
    if notes:
        lines.append("• Профиль: " + ", ".join([str(n) for n in notes])[:300])
    for f in facts[:4]:
        lines.append("• " + str(f))
    if sources:
        refs = " ".join([f"<a href='{u}'>[{i+1}]</a>" for i, u in enumerate(sources[:5])])
        lines.append("Источники: " + refs)
    return "\n".join(lines)

def _photo_from_rec(rec: dict) -> Optional[str]:
    img = rec.get("image_url")
    if isinstance(img, list):
        return next((u for u in img if isinstance(u, str) and u.strip()), None)
    if isinstance(img, str):
        return img
    return None

# =========================
# Вход/выход из AI-режима
# =========================
@router.message(F.text == AI_ENTRY_TEXT)
@router.message(F.text == "/ai")
async def ai_mode_msg(m: Message):
    AI_USERS.add(m.from_user.id)
    await m.answer(
        "AI-режим включён. Работаем <b>только из оффлайн-базы</b> (ingested_kb.json). "
        "Чтобы добавить данные — пополни seed_urls.json и запусти GitHub Actions → Ingest.",
        parse_mode="HTML",
        reply_markup=menu_ai_exit_kb(),
    )

@router.callback_query(F.data == "ai:enter")
async def ai_mode_cb(cb: CallbackQuery):
    AI_USERS.add(cb.from_user.id)
    with suppress(Exception):
        await cb.answer()
    await cb.message.answer(
        "AI-режим включён. Работаем <b>только из оффлайн-базы</b> (ingested_kb.json).",
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
# Главный AI-хендлер (OFFLINE ONLY)
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
    q = (text or "").strip()
    if not q:
        await m.answer("Напишите запрос или название бренда.")
        return

    # ====== РАННИЕ ИНТЕНТЫ (оставляем) ======
    is_sales, outlet, brand_for_sales = detect_sales_intent(q)
    if is_sales:
        html = ""
        if generate_sales_playbook_with_gemini:
            with suppress(Exception):
                html = await generate_sales_playbook_with_gemini(q, outlet, brand_for_sales)
        if not html:
            brand_hint = brand_for_sales or q
            html = (
                f"<b>Как продавать: {brand_hint}</b>\n"
                f"• Уточни вкус покупателя (сладость/сухость; ваниль/фрукты/дым).\n"
                f"• Предложи хайболл или классику (Old Fashioned / Sour), без цен.\n"
                f"• 1 фраза про происхождение/бочки как «историю бренда».\n"
                f"• Апселл: премиальная версия; кросс-селл: подходящая закуска."
            )
        await m.answer(_sanitize_caption(html), parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        return

    any_res = suggest_any_in_category(q)
    if any_res:
        display_cat, names = any_res
        first = names[0]
        # даже тут — ищем только в KB
        rec, disp = _kb_find_local(first)
        caption = _sanitize_caption(_caption_from_rec(rec, disp)) if rec else f"<b>{first}</b>\n• Нет записи в оффлайн-БЗ."
        photo = _photo_from_rec(rec) if rec else None

        if photo:
            await m.answer_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        else:
            await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())

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
        ai_inc("ai.query", tags={"intent": "brand"})

        # 1) Ищем прямо запись в KB модулем (если есть)
        rec = None
        disp_name = None
        if kb_find_record:
            try:
                tmp = kb_find_record(q)
                if tmp:
                    rec = tmp
                    disp_name = (tmp.get("name") or tmp.get("brand"))
            except Exception:
                rec = None

        # 2) Наш локальный поиск по ingested_kb.json
        if rec is None:
            rec, disp_name = _kb_find_local(q)

        # 3) Если есть — формируем карточку без веба
        if rec:
            # сначала красивый caption из KB-модуля, если доступен
            if build_caption_from_kb != (lambda _: ""):
                with suppress(Exception):
                    caption = _sanitize_caption(build_caption_from_kb(rec))
                if not caption:
                    caption = _sanitize_caption(_caption_from_rec(rec, disp_name))
            else:
                caption = _sanitize_caption(_caption_from_rec(rec, disp_name))

            photo = _photo_from_rec(rec)

            try:
                if photo:
                    await m.answer_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
                else:
                    await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
            except TelegramBadRequest:
                await m.answer(caption, reply_markup=menu_ai_exit_kb())

            stop_typing.set()
            with suppress(Exception):
                await typing_task
            dt_ms = (time.monotonic() - t0) * 1000
            ai_inc("ai.source", tags={"source": "kb_offline"})
            ai_inc("ai.answer", tags={"intent": "brand", "source": "kb_offline"})
            ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb_offline"})
            log.info("[AI] offline KB card in %.2fs", dt_ms / 1000.0)
            return

        # 4) KB → LLM (если нужно «оживить» формулировку, но только из KB)
        if kb_retrieve and generate_caption_with_gemini:
            try:
                kb = kb_retrieve(q, top_k=8)
            except TypeError:
                kb = kb_retrieve(q)
            if kb and kb.get("results"):
                try:
                    caption = await generate_caption_with_gemini(
                        q, kb,
                        system_prompt=(
                            "Ты кратко описываешь напиток строго по данным из локальной БЗ "
                            "(результаты retrieval). Никаких догадок. Если чего-то нет — пиши 'н/д'. "
                            "Дай 2–3 лаконичные фразы и 3–6 дегустационных нот списком."
                        )
                    )
                except Exception:
                    caption = ""
                caption = _sanitize_caption(caption) or "Нет фактов в оффлайн-БЗ."
                await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())

                stop_typing.set()
                with suppress(Exception):
                    await typing_task
                dt_ms = (time.monotonic() - t0) * 1000
                ai_inc("ai.source", tags={"source": "kb_offline"})
                ai_inc("ai.answer", tags={"intent": "brand", "source": "kb_offline"})
                ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb_offline"})
                log.info("[AI] offline KB + Gemini in %.2fs", dt_ms / 1000.0)
                return

        # 5) Ничего не нашли в оффлайн-БЗ — подсказываем, как «накормить»
        help_text = (
            "<b>Не нашёл в оффлайн-базе.</b>\n"
            "Чтобы добавить бренд без веб-поиска:\n"
            "1) Открой data/seed_urls.json и добавь точные карточки в \"exact_pages\".\n"
            "2) Запусти GitHub → Actions → <i>Ingest allowed sites</i> с <b>run_all: true</b>.\n"
            "3) Проверь, что data/ingested_kb.json обновился — и спроси бренд ещё раз."
        )
        await m.answer(help_text, parse_mode="HTML", reply_markup=menu_ai_exit_kb())

        stop_typing.set()
        with suppress(Exception):
            await typing_task
        dt_ms = (time.monotonic() - t0) * 1000
        ai_inc("ai.source", tags={"source": "kb_offline_miss"})
        ai_inc("ai.answer", tags={"intent": "brand", "source": "kb_offline_miss"})
        ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb_offline_miss"})
        log.info("[AI] offline KB miss in %.2fs", dt_ms / 1000.0)

    finally:
        stop_typing.set()
        with suppress(Exception):
            await typing_task
