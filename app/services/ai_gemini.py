# app/services/ai_gemini.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
import os, logging, re, json, asyncio

log = logging.getLogger(__name__)

# === Библиотека Gemini (поддержка old/new SDK) ===
try:
    import google.generativeai as genai  # old SDK
    try:
        from google.genai import types   # new SDK (google-genai)
        _HAS_TYPES = True
    except Exception:
        types = None
        _HAS_TYPES = False
    _HAS_LIB = True
except Exception as e:
    log.warning("Gemini lib not installed: %s", e)
    genai = None
    types = None
    _HAS_TYPES = False
    _HAS_LIB = False

# Ключ и модель
_GEMINI_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def have_gemini() -> bool:
    return _HAS_LIB and bool(_GEMINI_KEY)

# ---------- Вспомогалки ----------
_SYSTEM_JSON = (
    "Ты — эксперт по алкогольным брендам и продажам HoReCa.\n"
    "Отвечай ТОЛЬКО на основе переданных источников (RAG). Если факта нет — пиши null.\n"
    "Не сравнивай с другими брендами.\n"
    "Выводи СТРОГО JSON без лишнего текста и без Markdown/HTML.\n"
    "Схема JSON:\n"
    "{\n"
    '  "название": string,\n'
    '  "категория": string | null,\n'
    '  "страна": string | null,\n'
    '  "крепость": string | null,\n'
    '  "дегустационные_ноты": string | null,\n'
    '  "производство": string | null,\n'
    '  "как_продавать": string | null\n'
    "}\n"
    "Требования:\n"
    "- Используй ТОЛЬКО факты из results.\n"
    "- На русском, ≤ 900 символов.\n"
    "- Если данных нет — ставь null, не выдумывай.\n"
)


_JSON_RE = re.compile(r"\{.*\}", re.S)

def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    m = _JSON_RE.search(text)
    if not m:
        return {}
    raw = m.group(0)
    try:
        return json.loads(raw)
    except Exception:
        raw = (raw
               .replace("\u201c", '"').replace("\u201d", '"')
               .replace("\u00ab", '"').replace("\u00bb", '"'))
        try:
            return json.loads(raw)
        except Exception:
            return {}

def _pack_context(results_or_chunks: Any) -> tuple[str, List[str]]:
    """
    Возвращает (текстовый блок контекста, список источников-url)
    Поддерживает:
      - KB-чанки: [{'text':..., 'url':..., 'brand':...}, ...]
      - CSE-результаты: {'results': [{'name','snippet','url'}, ...]}
    """
    urls: List[str] = []
    if isinstance(results_or_chunks, list) and results_or_chunks and isinstance(results_or_chunks[0], dict):
        # KB чанки
        lines = []
        for i, ch in enumerate(results_or_chunks[:10], 1):
            text = (ch.get("text") or "").strip()
            url = (ch.get("url") or "").strip()
            if url:
                urls.append(url)
                lines.append(f"[{i}] {url}\n{text}")
            else:
                lines.append(f"[{i}]\n{text}")
        return ("\n\n".join(lines) if lines else "нет данных"), urls

    if isinstance(results_or_chunks, dict) and "results" in results_or_chunks:
        lines = []
        for i, r in enumerate(results_or_chunks.get("results", [])[:10], 1):
            name = (r.get("name") or "").strip()
            snip = (r.get("snippet") or "").strip()
            url = (r.get("url") or "").strip()
            if url:
                urls.append(url)
            payload = snip or name or ""
            lines.append(f"[{i}] {url}\n{payload}")
        return ("\n\n".join(lines) if lines else "нет данных"), urls

    return "нет данных", urls

def _render_card_html(d: Dict[str, Any]) -> str:
    # Только безопасные для Telegram теги: <b>, <i>, <u>, <s>, <a>, <code>, <pre>, <br>
    def esc(s: str) -> str:
        return (s or "").strip()

    name = esc(d.get("name", ""))
    b = d.get("basics", {}) or {}
    basics = []
    if b.get("category"): basics.append(f"Категория: {esc(b.get('category'))}")
    if b.get("country"):  basics.append(f"Страна: {esc(b.get('country'))}")
    if b.get("abv"):      basics.append(f"Крепость: {esc(b.get('abv'))}")

    lines: List[str] = []
    if name:
        lines.append(f"<b>{name}</b>")
    if basics:
        lines.append("• " + " | ".join(basics))
    if d.get("taste"):
        lines.append("• Профиль: " + esc(d.get("taste")))
    if d.get("serve"):
        lines.append("• Подача: " + esc(d.get("serve")))
    if d.get("pairing"):
        lines.append("• Сочетания: " + esc(d.get("pairing")))

    ckt = d.get("cocktails") or []
    if isinstance(ckt, list) and ckt:
        lines.append("• Коктейли: " + ", ".join(esc(x) for x in ckt[:2]))

    facts = d.get("facts") or []
    for f in facts[:3]:
        lines.append("• " + esc(f))

    ss = d.get("sales_script") or []
    if ss:
        lines.append("<b>Скрипт продажи:</b>")
        for s in ss[:3]:
            lines.append("• " + esc(s))

    src = d.get("sources") or []
    if src:
        tail = " ".join([f"<a href='{esc(u)}'>[{i+1}]</a>" for i, u in enumerate(src[:3])])
        lines.append("Источники: " + tail)

    card = "\n".join(lines).strip()
    if len(card) > 1000:  # безопасный лимит для caption
        card = card[:1000].rstrip() + "…"
    return card

# ---------- Основной генератор карточки (JSON -> HTML) ----------
async def generate_caption_with_gemini(query: str, results_or_chunks: Optional[Any]) -> str:
    """
    Новая версия: просим у модели СТРОГО JSON по схеме, парсим и рендерим в компактный HTML.
    На вход можно давать KB-чанки или CSE-результаты.
    """
    if not have_gemini():
        return "<b>LLM не настроен.</b>"

    context_block, ctx_urls = _pack_context(results_or_chunks)
    system = _SYSTEM_JSON
    prompt = (
        system
        + "\n\nПользовательский запрос:\n" + (query or "")
        + "\n\nДОСТУПНЫЕ ИСТОЧНИКИ (используй только это):\n" + context_block
        + "\n\nВЫВЕДИ ТОЛЬКО JSON СООТВЕТСТВУЮЩИЙ СХЕМЕ, БЕЗ ЛИШНЕГО ТЕКСТА."
    )

    def _call_new():
        client = genai.Client(api_key=_GEMINI_KEY)
        return client.models.generate_content(model=_MODEL, contents=prompt)

    def _call_old():
        genai.configure(api_key=_GEMINI_KEY)
        mdl = genai.GenerativeModel(_MODEL)
        return mdl.generate_content(prompt)

    # Вызов в отдельном потоке
    try:
        if hasattr(genai, "Client"):
            resp = await asyncio.to_thread(_call_new)
        else:
            resp = await asyncio.to_thread(_call_old)
        raw = (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        log.warning("Gemini generation error: %s", e)
        raw = ""

    data = _extract_json(raw)
    if not data:
        # Фоллбек — минимальный JSON
        data = {
            "name": query,
            "basics": {"category": "", "country": "", "abv": "нет данных"},
            "taste": "",
            "serve": "",
            "pairing": "",
            "cocktails": [],
            "facts": ["нет данных по источникам."],
            "sales_script": [
                "Уточните предпочтения гостя (сладость/крепость/ароматика).",
                "Коротко обозначьте ценность: профиль вкуса, повод или сочетание."
            ],
            "sources": ctx_urls[:3]
        }
    # Если модель не вернула sources, добавим из контекста
    if not data.get("sources") and ctx_urls:
        data["sources"] = ctx_urls[:3]

    html = _render_card_html(data)
    return html

# ---------- Тренерский «playbook» (оставлен) ----------
async def generate_sales_playbook_with_gemini(query: str, outlet: str | None, brand: str | None) -> str:
    """
    Короткий тренерский разбор: как продавать указанный продукт в заданном формате точки.
    Без сравнений/конкурентов/цен. HTML совместим с Telegram.
    """
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

    def _call_new():
        client = genai.Client(api_key=_GEMINI_KEY)
        if _HAS_TYPES:
            cfg = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=0))
            return client.models.generate_content(model=_MODEL, contents=prompt, config=cfg)
        else:
            return client.models.generate_content(model=_MODEL, contents=prompt)

    def _call_old():
        genai.configure(api_key=_GEMINI_KEY)
        mdl = genai.GenerativeModel(_MODEL)
        return mdl.generate_content(prompt)

    try:
        if hasattr(genai, "Client"):
            resp = await asyncio.to_thread(_call_new)
        else:
            resp = await asyncio.to_thread(_call_old)
        return (getattr(resp, "text", "") or "Не удалось сгенерировать ответ.").strip()
    except Exception as e:
        log.warning("Gemini playbook error: %s", e)
        return "Не удалось сгенерировать ответ."
