# app/services/ai_gemini.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
import os
import logging

log = logging.getLogger(__name__)

# библиотека Gemini
try:
    import google.generativeai as genai
    from google.genai import types
    _HAS_LIB = True
except Exception as e:
    log.warning("Gemini lib not installed: %s", e)
    _HAS_LIB = False

# ключ можно назвать GEMINI_API_KEY или GOOGLE_API_KEY
_GEMINI_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
_MODEL = "gemini-1.5-flash"

_SYSTEM = (
    "Ты — эксперт по алкогольным брендам и продажам HoReCa.\n"
    "Собери КОРОТКУЮ, полезную карточку бренда в HTML для Telegram.\n\n"
    "Жёсткие правила:\n"
    "• Используй ТОЛЬКО факты из блока «Результаты поиска» ниже (никаких домыслов).\n"
    "• Если факта нет в источниках — честно напиши «нет данных» и иди дальше.\n"
    "• Разрешённые теги: <b>, <i>, <u>, <s>, <a href='...'>, <code>, <pre>, <br>.\n"
    "• НЕ используй <h1..h6>, <ul>, <ol>, <li>, <p>, <div> и пр. (Телеграм их не принимает).\n"
    "• Карточку делай компактной (≈1500–1800 символов), пунктов не больше 5–6.\n"
    "• В разделе «Скрипт продажи» СТРОГО ЗАПРЕЩЕНО сравнение с другими брендами и упоминание конкурентов. "
    "Не пиши слова «аналог», «альтернатива», «вместо», «как [бренд]», «конкурент» и т.п.; "
    "фокусируйся только на ценности самого продукта, сценариях употребления и выгодах для гостя.\n\n"
    "Структура карточки (строго в этом порядке):\n"
    "<b>Название (оригинал)</b>\n"
    "• Категория / страна / крепость: {данные или «нет данных»}\n"
    "• Профиль вкуса/ароматики: {кратко, по источникам}\n"
    "• Подача: {бокал, температура/со льдом, гарнир — только если в источниках}\n"
    "• С чем сочетается: {если есть данные в источниках}\n"
    "• Коктейли: {если в источниках упомянуты 1–2 классики}\n"
    "• 2–3 ключевых факта о производстве/выдержке/истории (по источникам)\n"
    "• Скрипт продажи: 2–3 нейтральные фразы без упоминания других брендов "
    "(какая ценность для гостя, для какого настроения/повода, как предложить в баре).\n\n"
    "В конце добавь строку с источниками (до 3 ссылок):\n"
    "Источники: <a href='URL1'>[1]</a> <a href='URL2'>[2]</a> <a href='URL3'>[3]</a>\n"
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
    joined = "\n".join(bullets) if bullets else "нет данных"
    return (
        f"{_SYSTEM}\n\n"
        f"Запрос пользователя: {query}\n\n"
        f"Результаты поиска:\n{joined}\n"
    )

def _build_prompt_no_results(query: str) -> str:
    return (
        f"{_SYSTEM}\n\n"
        f"Запрос пользователя: {query}\n\n"
        "Результаты поиска: нет данных\n"
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


async def generate_sales_playbook_with_gemini(query: str, outlet: str | None, brand: str | None) -> str:
    """
    Короткий тренерский разбор: как продавать указанный продукт в заданном формате точки.
    Без сравнений/конкурентов/цен. HTML совместим с Telegram.
    """
    import asyncio

    if not have_gemini():
        return "LLM не настроен."
    system = (
        "Ты — тренер по продажам алкоголя для HoReCa и розницы в Казахстане.\n"
        "Отвечай кратко и структурно в HTML для Telegram. Разрешены теги: <b>, <i>, <u>, <s>, <a>, <code>, <pre>, <br>.\n"
        "Запрещены сравнения с другими брендами, упоминания конкурентов и любые цены.\n"
        "Не утверждай технические факты (крепость/выдержка и т.п.), если они не были явно предоставлены ранее.\n"
        "Дай чек-лист для продавца.\n"
        "Структура:\n"
        "<b>Цель</b>: что продаём и где (1 строка)\n"
        "<b>Кому</b>: портрет покупателя/гостя (2–3 пункта)\n"
        "<b>Аргументы</b>: 4–6 коротких буллитов (вкус/сценарий/повод/сезон/миксология — без брендов)\n"
        "<b>Как предложить</b>: 2–3 реплики продавца (мини-скрипт)\n"
        "<b>Возражения и ответы</b>: 3–4 пары\n"
        "<b>Доп. продажи</b>: 2–3 идеи (закуска, посуда, миксеры — без брендов)\n"
        "<b>Юридически</b>: напоминание про 18+ и ответственное потребление\n"
    )
    topic = f"Запрос: {query}\nМесто: {outlet or 'не указано'}\nБренд: {brand or 'не указан'}"
    prompt = system + "\n\n" + topic + "\nОтвет дай строго в HTML без лишних вступлений."

    cfg = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    )

    def _call():
        client = genai.Client(api_key=_GEMINI_KEY)
        return client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=cfg,
        )

    resp = await asyncio.to_thread(_call)
    return (getattr(resp, "text", "") or "Не удалось сгенерировать ответ.").strip()
