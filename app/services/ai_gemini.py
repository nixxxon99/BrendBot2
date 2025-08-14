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

# ---------- СИСТЕМНЫЙ ПРОМПТ (строго про JSON по нужной схеме) ----------
_SYSTEM_JSON = (
    "Ты — эксперт по алкогольным брендам и продажам HoReCa.\n"
    "Опирайся ТОЛЬКО на переданные источники (RAG). Если факта нет в источниках — ставь null.\n"
    "Верни СТРОГО один JSON-объект без префиксов/суффиксов/Markdown/HTML.\n"
    "Схема JSON строго такая:\n"
    "{\n"
    '  "name": string,                      \n'
    '  "basics": {                          \n'
    '    "category": string | null,         \n'
    '    "country":  string | null,         \n'
    '    "abv":      string | null          \n'
    '  },                                    \n'
    '  "taste":        string | null,       \n'
    '  "serve":        string | null,       \n'
    '  "pairing":      string | null,       \n'
    '  "cocktails":    string[] | null,     \n'
    '  "facts":        string[] | null,     \n'
    '  "sales_script": string[] | null,     \n'
    '  "sources":      string[] | null      \n'
    "}\n"
    "Требования: на русском; никаких оценочных эпитетов и сравнений; не выдумывай данные; "
    "кратко, по делу. 'sources' — список URL из переданных источников, если они были.\n"
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
        # иногда кавычки «ёлочки» / типографские
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

# ---------- НОРМАЛИЗАТОР (если модель вдруг вернула русские ключи) ----------
def _normalize_schema(d: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    # уже правильная схема?
    if "name" in d and "basics" in d:
        return d

    # возможные русские ключи → англ. схема
    name = d.get("name") or d.get("название") or d.get("бренд") or ""
    category = None
    country  = None
    abv      = None

    # basics может быть как объект, так и россыпь полей
    basics_ru = d.get("basics") or {}
    if isinstance(basics_ru, dict):
        category = basics_ru.get("category") or basics_ru.get("категория")
        country  = basics_ru.get("country")  or basics_ru.get("страна")
        abv      = basics_ru.get("abv")      or basics_ru.get("крепость")

    category = category or d.get("категория")
    country  = country  or d.get("страна")
    abv      = abv      or d.get("крепость")

    taste    = d.get("taste") or d.get("дегустационные_ноты") or d.get("ноты")
    serve    = d.get("serve") or d.get("подача")
    pairing  = d.get("pairing") or d.get("гастросочетания") or d.get("сочетания")

    cocktails = d.get("cocktails") or d.get("коктейли")
    if isinstance(cocktails, str):
        cocktails = [cocktails]

    facts = d.get("facts") or d.get("производство") or d.get("факты")
    if isinstance(facts, str):
        facts = [facts]

    sales = d.get("sales_script") or d.get("как_продавать") or d.get("скрипт")
    if isinstance(sales, str):
        sales = [sales]

    sources = d.get("sources") or d.get("источники")

    return {
        "name": name,
        "basics": {"category": category, "country": country, "abv": abv},
        "taste": taste,
        "serve": serve,
        "pairing": pairing,
        "cocktails": cocktails if isinstance(cocktails, list) else None,
        "facts": facts if isinstance(facts, list) else (None if facts is None else [str(facts)]),
        "sales_script": sales if isinstance(sales, list) else (None if sales is None else [str(sales)]),
        "sources": sources if isinstance(sources, list) else (None if sources is None else [str(sources)]),
    }

# ---------- РЕНДЕР КОМПАКТНОГО HTML ----------
def _render_card_html(d: Dict[str, Any]) -> str:
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
    if isinstance(facts, list):
        for f in facts[:3]:
            if f:
                lines.append("• " + esc(f))

    ss = d.get("sales_script") or []
    if ss:
        lines.append("<b>Скрипт продажи:</b>")
        for s in ss[:3]:
            if s:
                lines.append("• " + esc(s))

    src = d.get("sources") or []
    if src:
        tail = " ".join([f"<a href='{esc(u)}'>[{i+1}]</a>" for i, u in enumerate(src[:3])])
        lines.append("Источники: " + tail)

    card = "\n".join(lines).strip()
    if len(card) > 1000:  # лимит подписи для Telegram
        card = card[:1000].rstrip() + "…"
    return card

# ---------- Основной генератор карточки (JSON -> HTML) ----------
async def generate_caption_with_gemini(query: str, results_or_chunks: Optional[Any]) -> str:
    """
    Просим у модели СТРОГО JSON по нужной схеме, парсим, нормализуем и рендерим HTML.
    На вход можно давать KB-чанки или CSE-результаты.
    """
    if not have_gemini():
        return "<b>LLM не настроен.</b>"

    context_block, ctx_urls = _pack_context(results_or_chunks)
    prompt = (
        _SYSTEM_JSON
        + "\n\nПользовательский запрос:\n" + (query or "")
        + "\n\nДОСТУПНЫЕ ИСТОЧНИКИ (используй только это, придумывать запрещено):\n" + context_block
        + "\n\nВЫВЕДИ ТОЛЬКО ОДИН JSON-ОБЪЕКТ СО СХЕМОЙ ВЫШЕ."
    )

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
        raw = (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        log.warning("Gemini generation error: %s", e)
        raw = ""

    data = _extract_json(raw)
    if not data:
        # минимальный фолбэк (если вдруг ответ не JSON)
        data = {
            "name": query,
            "basics": {"category": None, "country": None, "abv": None},
            "taste": None,
            "serve": None,
            "pairing": None,
            "cocktails": None,
            "facts": ["нет данных по предоставленным источникам."],
            "sales_script": [
                "Уточните предпочтения гостя (сладость/ароматика/крепость).",
                "Предложите короткую классику (Old Fashioned / Sour) или хайболл.",
            ],
            "sources": ctx_urls[:3] if ctx_urls else None,
        }

    # Нормализуем схему (если пришли русские ключи и т.п.)
    data = _normalize_schema(data)

    # Если модель не вернула sources — добавим из контекста
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
        "Ты — тренер по продажам алкоголя для HoReCa и розницы.\n"
        "Отвечай кратко и структурно в HTML для Telegram. Разрешены теги: <b>, <i>, <u>, <s>, <a>, <code>, <pre>, <br>.\n"
        "Запрещены сравнения с другими брендами и цены. Не выдумывай факты.\n"
        "Структура:\n"
        "<b>Цель</b>: что продаём и где (1 строка)\n"
        "<b>Кому</b>: портрет покупателя/гостя (2–3 пункта)\n"
        "<b>Аргументы</b>: 4–6 коротких буллитов\n"
        "<b>Как предложить</b>: 2–3 реплики продавца\n"
        "<b>Возражения и ответы</b>: 3–4 пары\n"
        "<b>Доп. продажи</b>: 2–3 идеи\n"
        "<b>Юридически</b>: 18+ и ответственное потребление\n"
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
