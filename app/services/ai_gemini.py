# app/services/ai_gemini.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
import os
import logging

log = logging.getLogger(__name__)

# библиотека Gemini
try:
    import google.generativeai as genai
    _HAS_LIB = True
except Exception as e:
    log.warning("Gemini lib not installed: %s", e)
    _HAS_LIB = False

# ключ можно назвать GEMINI_API_KEY или GOOGLE_API_KEY
_GEMINI_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
_MODEL = "gemini-1.5-flash"

_SYSTEM_STYLE = (
    "Ты — помощник для карточек алкогольных брендов. "
    "Пиши кратко и по делу, форматируй HTML-списком точек, без лишнего маркетинга. "
    "Если нет точных данных (крепость, страна, стиль), не выдумывай — опускай пункт."
)

def have_gemini() -> bool:
    return _HAS_LIB and bool(_GEMINI_KEY)

def _client():
    if not have_gemini():
        raise RuntimeError("Gemini is not configured")
    genai.configure(api_key=_GEMINI_KEY)
    return genai.GenerativeModel(_MODEL)

def _build_prompt_from_results(query: str, results: Dict[str, Any]) -> str:
    bullets: List[str] = []
    for r in results.get("results", [])[:8]:
        name = (r.get("name") or "").strip()
        snip = (r.get("snippet") or "").strip()
        url = (r.get("url") or "").strip()
        if snip:
            bullets.append(f"- {snip} (источник: {url})")
        elif name:
            bullets.append(f"- {name} (источник: {url})")
    joined = "\n".join(bullets) if bullets else "—"
    return (
        f"{_SYSTEM_STYLE}\n\n"
        f"Запрос пользователя: {query}\n"
        f"Сводка источников (рабочие заметки):\n{joined}\n\n"
        "Собери компактную карточку бренда в HTML. Структура:\n"
        "<b>Название</b>\n"
        "• Тип/категория (если ясно)\n"
        "• Страна/регион (если ясно)\n"
        "• Крепость ABV (если есть)\n"
        "• Профиль вкуса (кратко)\n"
        "• Подача/коктейли (если уместно)\n"
        "• Интересные факты (1–2 пункта, если уместно)\n"
        "Не пиши источники и ссылки, не выдумывай цены. Текст на русском."
    )

def _build_prompt_no_results(query: str) -> str:
    return (
        f"{_SYSTEM_STYLE}\n\n"
        f"Запрос пользователя: {query}\n"
        "Поиска в интернете нет. Сформируй аккуратную карточку бренда в HTML, "
        "исходя из общих знаний. Если данных нет — пиши общо и честно, без выдумок.\n"
        "Структура как выше. Русский язык."
    )

async def generate_caption_with_gemini(query: str, results: Optional[Dict[str, Any]]) -> str:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_generate, query, results)

def _sync_generate(query: str, results: Optional[Dict[str, Any]]) -> str:
    mdl = _client()
    prompt = _build_prompt_from_results(query, results) if results else _build_prompt_no_results(query)
    try:
        resp = mdl.generate_content(prompt)
        text = (resp.text or "").strip()
        if not text:
            return f"<b>{query}</b>\n• Краткая информация недоступна."
        return text
    except Exception as e:
        log.warning("Gemini generation error: %s", e)
        return f"<b>{query}</b>\n• Краткая информация недоступна."
