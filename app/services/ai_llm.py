import asyncio
from typing import Dict, Any, List
from openai import OpenAI
from app.settings import settings

def have_llm() -> bool:
    return bool(settings.openai_api_key)

def _trim_results(results: Dict[str, Any], limit: int = 5) -> List[Dict[str, str]]:
    out = []
    for r in (results.get("results") or [])[:limit]:
        out.append({
            "title": (r.get("name") or "")[:120],
            "snippet": (r.get("snippet") or "")[:500],
            "url": r.get("url") or ""
        })
    return out

def _build_messages(query: str, web_results: Dict[str, Any], locale: str = "ru") -> list:
    trimmed = _trim_results(web_results, limit=5)
    system_ru = (
        "Ты алкогольный консультант-эксперт. Отвечай кратко и по делу, на русском, "
        "в формате HTML: заголовок <b>Название</b> и 6–8 маркеров (•), максимум 600 символов. "
        "Если запрос — бренд алкоголя, дай: тип/категорию, страну/стиль, крепость (если встречается), "
        "ароматы/вкус, подачу/с чем пить, 1–2 интересных факта. "
        "Если данных мало — честно укажи, что информация ограничена."
    )
    return [
        {"role": "system", "content": system_ru},
        {"role": "user", "content": f"Запрос пользователя: {query}\nВеб-результаты: {trimmed}"}
    ]

def _call_openai(messages: list, model: str = "gpt-4o-mini") -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=450,
    )
    return resp.choices[0].message.content or "Данных недостаточно."

async def generate_card_with_llm(query: str, web_results: Dict[str, Any]) -> str:
    messages = _build_messages(query, web_results)
    # Вынесем блокирующий вызов в поток, чтобы не блокировать event loop
    return await asyncio.to_thread(_call_openai, messages)
