# app/routers/ai_helper.py — OFFLINE ONLY (без веб-поиска)
from __future__ import annotations

import asyncio
import time
import logging
import re
import difflib
import json
from pathlib import Path
from contextlib import suppress
from typing import Optional, Tuple, List, Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton

# ---- метрики / sales-интенты ----
from app.services.stats import ai_inc, ai_observe_ms
from app.services.sales_intents import detect_sales_intent, suggest_any_in_category

# ---- KB / RAG (если модуль есть — используем; иначе fallback на локальный JSON) ----
try:
    from app.services.knowledge import find_record as kb_find_record, build_caption_from_kb
except Exception:
    kb_find_record = None
    def build_caption_from_kb(_): return ""

try:
    from app.services.knowledge import retrieve as kb_retrieve
except Exception:
    kb_retrieve = None

# ---- LLM (используем ТОЛЬКО поверх локальной KB, не для веба) ----
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
    html = re.sub(r"</?(?:h[1-6]|p|ul|ol|li|div|span)>", "", html, flags=re.I)
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
# OFFLINE KB (fallback): загрузка из JSON и поиск
# =========================
_KB_CACHE: List[dict] = []
_KB_PATHS = [
    Path("data/ingested_kb.json"),         # основной инжест
    Path("data/kb/winespecialist.json"),   # дополнительные пакеты
]

def _load_kb_once(force: bool = False):
    global _KB_CACHE
    if _KB_CACHE and not force:
        return
    out: List[dict] = []
    for p in _KB_PATHS:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    out.extend(data)
            except Exception as e:
                log.warning("[KB] read fail %s: %s", p, e)
    _KB_CACHE = out
    log.info("[KB] loaded %d records", len(_KB_CACHE))

def _all_names(rec: dict) -> List[str]:
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

def _caption_from_rec(rec: dict, display_name: Optional[str] = None) -> str:
    name = display_name or rec.get("name") or rec.get("brand") or "Бренд"
    basics = rec.get("basics") or {}
    category = rec.get("category") or basics.get("category")
    country  = rec.get("country")  or basics.get("country")
    abv      = rec.get("abv")      or basics.get("abv")
    notes    = rec.get("tasting_notes") or rec.get("taste") or []
    if isinstance(notes, str):
        notes = [notes]
    facts    = rec.get("facts") or []
    sources  = rec.get("sources") or []

    lines = [f"<b>{name}</b>"]
    meta_bits = [x for x in (category, country, abv) if x]
    if meta_bits:
        lines.append("• " + " | ".join(meta_bits))
    if notes:
        joined = ", ".join([str(n) for n in notes])
        lines.append("• Профиль: " + joined[:300])
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
        return img.strip() or None
    basics = rec.get("basics") or {}
    img2 = basics.get("image_url")
    return (img2 or None) if isinstance(img2, str) else None

# ------- быстрый поиск по KB (дизамбигуация) -------
def _score(query: str, candidate: str) -> float:
    q = (query or "").lower()
    c = (candidate or "").lower()
    if not q or not c:
        return 0.0
    if q == c:               # идеальное совпадение
        return 1.0
    if q in c or c in q:     # подстрока
        return 0.95
    return difflib.SequenceMatcher(a=q, b=c).ratio()

def _search_kb_candidates(query: str, k: int = 8) -> List[Tuple[float, dict, str]]:
    _load_kb_once()
    scored: List[Tuple[float, dict, str]] = []
    q = (query or "").strip()
    if not q:
        return scored

    for rec in _KB_CACHE:
        names = _all_names(rec)
        if not names:
            continue
        s = max(_score(q, n) for n in names)
        if s >= 0.60:
            # возьмём первый видимый алиас как display
            display = names[0]
            scored.append((s, rec, display))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]

# кэш кандидатов на пользователя для inline-кнопок
_USER_CANDIDATES: Dict[int, List[Tuple[float, dict, str]]] = {}

# =========================
# Вход/выход из AI-режима
# =========================
@router.message(F.text == AI_ENTRY_TEXT)
@router.message(F.text == "/ai")
async def ai_mode_msg(m: Message):
    AI_USERS.add(m.from_user.id)
    _load_kb_once()  # «ленивый» прогрев
    kb_size = len(_KB_CACHE)
    await m.answer(
        f"AI-режим включён.\n"
        f"Источник: <b>только оффлайн-БЗ</b> (ingested_kb.json и пакеты в data/kb/).\n"
        f"Записей в KB: <b>{kb_size}</b>.\n\n"
        f"Напиши название бренда/напитка или задавай вопросы по продажам.",
        parse_mode="HTML",
        reply_markup=menu_ai_exit_kb(),
    )

@router.callback_query(F.data == "ai:enter")
async def ai_mode_cb(cb: CallbackQuery):
    AI_USERS.add(cb.from_user.id)
    _load_kb_once()
    with suppress(Exception):
        await cb.answer()
    await cb.message.answer(
        "AI-режим включён. Работаем <b>только из оффлайн-БЗ</b>.",
        parse_mode="HTML",
        reply_markup=menu_ai_exit_kb(),
    )

@router.message(F.text == AI_EXIT_TEXT)
@router.message(F.text == "/ai_off")
@router.callback_query(F.data.in_({"ai:exit", "ai_exit"}))
async def ai_mode_off(ev):
    user_id = ev.from_user.id if hasattr(ev, "from_user") else ev.message.from_user.id
    AI_USERS.discard(user_id)
    _USER_CANDIDATES.pop(user_id, None)
    if isinstance(ev, CallbackQuery):
        with suppress(Exception):
            await ev.answer()
        with suppress(Exception):
            await ev.message.answer("AI-режим выключен.")
    else:
        await ev.answer("AI-режим выключен.")

# Доп. команды статуса/перезагрузки KB
@router.message(F.text == "/ai_status")
async def ai_status(m: Message):
    on = m.from_user.id in AI_USERS
    _load_kb_once()
    await m.answer(
        f"AI-режим: {'включён' if on else 'выключен'}\n"
        f"KB записей: {len(_KB_CACHE)}",
        reply_markup=menu_ai_exit_kb() if on else None
    )

@router.message(F.text == "/ai_reload_kb")
async def ai_reload(m: Message):
    _load_kb_once(force=True)
    await m.answer(f"KB перезагружена. Записей: {len(_KB_CACHE)}", reply_markup=menu_ai_exit_kb())

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

    # ====== РАННИЕ ИНТЕНТЫ (sales) ======
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
        # Показываем быстрые кнопки брендов в категории
        try:
            kb = ReplyKeyboardBuilder()
            for n in names[:10]:
                kb.add(KeyboardButton(text=n))
            kb.add(KeyboardButton(text="Назад"))
            kb.adjust(2)
            await m.answer(f"Могу предложить бренды в категории «{display_cat}»:", reply_markup=kb.as_markup(resize_keyboard=True))
        except Exception:
            pass

    # ====== основная логика: поиск карточки в оффлайн-КБ ======
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_pulse(m, stop_typing))
    t0 = time.monotonic()
    try:
        ai_inc("ai.query", tags={"intent": "brand"})

        # 1) Точный поиск модулем KB, если он присутствует
        rec = None
        disp_name = None
        if kb_find_record:
            with suppress(Exception):
                tmp = kb_find_record(q)
                if tmp:
                    rec = tmp
                    disp_name = (tmp.get("name") or tmp.get("brand"))

        # 2) Быстрый поиск по локальной KB (дизамбигуация)
        if rec is None:
            candidates = _search_kb_candidates(q, k=8)
            if not candidates:
                # нет кандидатов вообще
                await _reply_kb_miss(m, t0, typing_task, stop_typing)
                return

            # идеальный кандидат (score ~ 1.0) — сразу показываем
            top_score, top_rec, top_disp = candidates[0]
            if top_score >= 0.95:
                await _reply_card(m, top_rec, top_disp, t0, typing_task, stop_typing)
                return

            # иначе — покажем список на выбор
            _USER_CANDIDATES[m.from_user.id] = candidates
            kb = [
                [InlineKeyboardButton(text=f"{i+1}. {d[:64]}", callback_data=f"ai:pick:{i}")]
                for i, (_, _, d) in enumerate(candidates)
            ]
            await m.answer(
                "Нашёл несколько вариантов, уточни:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
            )
            stop_typing.set()
            with suppress(Exception):
                await typing_task
            return

        # 3) Есть запись из kb_find_record — отправляем карточку
        await _reply_card(m, rec, disp_name, t0, typing_task, stop_typing)
        return

    finally:
        stop_typing.set()
        with suppress(Exception):
            await typing_task

async def _reply_card(m: Message, rec: dict, disp_name: Optional[str],
                      t0: float, typing_task: asyncio.Task, stop_typing: asyncio.Event):
    # Сначала пробуем красивый caption из KB-модуля (если есть)
    if build_caption_from_kb != (lambda _: ""):
        with suppress(Exception):
            caption = _sanitize_caption(build_caption_from_kb(rec))
    else:
        caption = ""
    if not caption:
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

async def _reply_kb_miss(m: Message, t0: float, typing_task: asyncio.Task, stop_typing: asyncio.Event):
    # Если есть ретривер + Gemini — дадим краткий ответ «только из KB-доков», иначе — инструкция по инжесту
    if kb_retrieve and generate_caption_with_gemini:
        try:
            kb = kb_retrieve(m.text, top_k=8)
        except TypeError:
            kb = kb_retrieve(m.text)
        if kb and kb.get("results"):
            with suppress(Exception):
                caption = await generate_caption_with_gemini(
                    m.text, kb,
                    system_prompt=(
                        "Ты кратко описываешь напиток строго по данным из локальной БЗ "
                        "(результаты retrieval). Никаких догадок. Если чего-то нет — пиши 'н/д'. "
                        "Дай 2–3 лаконичные фразы и 3–6 дегустационных нот списком."
                    )
                )
            caption = _sanitize_caption(caption) or "Нет фактов в оффлайн-БЗ."
            await m.answer(caption, parse_mode="HTML", reply_markup=menu_ai_exit_kb())
        else:
            await _reply_ingest_help(m)
    else:
        await _reply_ingest_help(m)

    stop_typing.set()
    with suppress(Exception):
        await typing_task
    dt_ms = (time.monotonic() - t0) * 1000
    ai_inc("ai.source", tags={"source": "kb_offline_miss"})
    ai_inc("ai.answer", tags={"intent": "brand", "source": "kb_offline_miss"})
    ai_observe_ms("ai.latency", dt_ms, tags={"intent": "brand", "source": "kb_offline_miss"})
    log.info("[AI] offline KB miss in %.2fs", dt_ms / 1000.0)

async def _reply_ingest_help(m: Message):
    help_text = (
        "<b>Не нашёл в оффлайн-базе.</b>\n"
        "Как добавить бренд без веб-поиска:\n"
        "1) Открой data/seed_urls.json и добавь точные карточки в \"exact_pages\".\n"
        "2) Запусти GitHub → Actions → <i>Ingest allowed sites</i> c параметром <b>run_all: true</b>.\n"
        "3) Проверь, что data/ingested_kb.json обновился — и спроси бренд ещё раз."
    )
    await m.answer(help_text, parse_mode="HTML", reply_markup=menu_ai_exit_kb())

# =========================
# Коллбеки: выбор кандидата из списка
# =========================
@router.callback_query(F.data.regexp(r"^ai:pick:(\d+)$"))
async def ai_pick_candidate(cb: CallbackQuery):
    uid = cb.from_user.id
    m = cb.message
    with suppress(Exception):
        await cb.answer()

    candidates = _USER_CANDIDATES.get(uid) or []
    try:
        idx = int(cb.data.split(":")[-1])
    except Exception:
        idx = -1
    if idx < 0 or idx >= len(candidates):
        with suppress(Exception):
            await m.answer("Список кандидатов устарел. Напишите запрос ещё раз.")
        _USER_CANDIDATES.pop(uid, None)
        return

    _, rec, disp = candidates[idx]
    _USER_CANDIDATES.pop(uid, None)

    # показываем карточку
    t0 = time.monotonic()
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_pulse(m, stop_typing))
    try:
        await _reply_card(m, rec, disp, t0, typing_task, stop_typing)
    finally:
        stop_typing.set()
        with suppress(Exception):
            await typing_task
