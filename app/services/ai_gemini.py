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

# ---------- СИСТЕМНЫЕ ПРОМПТЫ ----------
# 1) Для карточек брендов (СТРОГО JSON по схеме → потом рендерим в HTML)
_SYSTEM_JSON = (
    "Ты — эксперт по алкогольным брендам и продукту.\n"
    "Опирайся ТОЛЬКО на переданные источники (RAG). Если факта нет — ставь null.\n"
    "Верни СТРОГО ОДИН JSON-объект без префиксов/суффиксов/Markdown/HTML.\n"
    "Схема JSON:\n"
    "{\n"
    '  "name": string,\n'
    '  "basics": {"category": string|null, "country": string|null, "abv": string|null},\n'
    '  "taste": string|null,\n'
    '  "serve": string|null,\n'
    '  "pairing": string|null,\n'
    '  "cocktails": string[]|null,\n'
    '  "facts": string[]|null,\n'
    '  "sales_script": string[]|null,\n'
    '  "sources": string[]|null\n'
    "}\n"
    "Требования: русский язык, никаких сравнений/оценок, не выдумывай.\n"
)

# 2) Для playbook “торгового представителя” (НЕ JSON, сразу HTML)
_SYSTEM_TRADE = (
    "Ты — торговый представитель алкогольной компании.\n"
    "Отвечай кратко и структурно в HTML (для Telegram). Разрешены теги: <b>, <i>, <u>, <s>, <a>, <code>, <pre>, <br>.\n"
    "Решай задачу для всех каналов (HoReCa, розница, e-commerce, duty-free). "
    "Если канал указан — адаптируй под него; если нет — сначала универсально, затем по 1–2 пунктам для каждого канала.\n"
    "Не сравнивай бренды и не указывай цены. Не выдумывай факты.\n"
    "Структура:\n"
    "<b>Цель</b>: что продвигаем и где (1 строка)\n"
    "<b>Дистрибуция и листинг</b>: 2–3 пункта (SKU/категория/полка)\n"
    "<b>Мерчандайзинг</b>: 3–5 пунктов (полка, фейсинги, вторичные точки, холод)\n"
    "<b>Промо и POSM</b>: 2–4 пункта (механики, доп.материалы)\n"
    "<b>Обучение персонала</b>: 2–3 пункта (скрипты/знания о продукте)\n"
    "<b>Возражения и ответы</b>: 3–4 пары\n"
    "<b>Кросс-продажи</b>: 2–3 идеи (сочетания, миксеры, наборы)\n"
    "<b>Юридически</b>: 18+ и ответственное потребление\n"
)

# ---------- Утилиты ----------
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
        # типографские кавычки → обычные
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

def _normalize_schema(d: Dict[str, Any]) -> Dict[str, Any]:
    """Приводим возможные русские ключи к ожидаемой схеме."""
    if not isinstance(d, dict):
        return {}
    if "name" in d and "basics" in d:
        return d

    name = d.get("name") or d.get("название") or d.get("бренд") or ""
    basics = d.get("basics") if isinstance(d.get("basics"), dict) else {}
    category = (basics.get("category") if basics else None) or d.get("категория")
    country  = (basics.get("country")  if basics else None) or d.get("страна")
    abv      = (basics.get("abv")      if basics else None) or d.get("крепость")

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

def _is_sparse(d: Dict[str, Any]) -> bool:
    """Почти пустой ответ?"""
    if not isinstance(d, dict):
        return True
    b = d.get("basics") or {}
    has_basics = any([b.get("category"), b.get("country"), b.get("abv")])
    has_content = any([d.get("taste"), d.get("facts"), d.get("serve"), d.get("pairing")])
    return not (has_basics or has_content)

def _smart_trim(text: str, limit: int) -> str:
    """Аккуратное укорачивание под лимит: режем по ближайшему разделителю."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # ищем удобную границу
    for sep in ("\n•", ".</", ". ", "\n", "; ", "— ", ", "):
        i = cut.rfind(sep)
        if i >= 0 and i >= limit * 0.6:
            cut = cut[:i + (0 if sep in ("\n•", "\n") else len(sep.strip()))]
            break
    cut = cut.rstrip()
    if not cut.endswith("…"):
        cut += "…"
    return cut

# ---------- РЕНДЕР КОМПАКТНОГО HTML ----------
def _render_card_html(d: Dict[str, Any], limit: int = 950) -> str:
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
    return _smart_trim(card, limit)

# ---------- Основной генератор карточки ----------
async def generate_caption_with_gemini(query: str, results_or_chunks: Optional[Any]) -> str:
    """
    Просим у модели СТРОГО JSON по нужной схеме; если ответ «скудный» — фолбэк из веб-результатов.
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
    data = _normalize_schema(data) if data else {}

    # если JSON пустой/скудный — фолбэк из веб-результатов (тот же формат карточки)
    if _is_sparse(data):
        try:
            # лёгкий фолбэк: соберём короткую сводку из сниппетов CSE
            from app.services.ai_google import build_caption_from_results
            if isinstance(results_or_chunks, dict) and results_or_chunks.get("results"):
                return _smart_trim(build_caption_from_results(query, results_or_chunks), 950)
        except Exception as e:
            log.warning("Google fallback failed: %s", e)
        # минимальная заглушка
        data = {
            "name": query,
            "basics": {"category": None, "country": None, "abv": None},
            "facts": ["нет данных по предоставленным источникам."],
            "sources": ctx_urls[:3] if ctx_urls else None,
        }

    if not data.get("sources") and ctx_urls:
        data["sources"] = ctx_urls[:3]

    return _render_card_html(data, limit=950)

# ---------- Тренерский «playbook» (Торговый представитель) ----------
async def generate_sales_playbook_with_gemini(query: str, outlet: str | None, brand: str | None) -> str:
    """
    Короткий разбор “как продвигать” — от лица торгового представителя (все каналы).
    Возвращает HTML для Telegram. Ограничиваем длину, чтобы не резалось.
    """
    if not have_gemini():
        return "LLM не настроен."

    topic = f"Запрос: {query}\nКанал/место: {outlet or 'не указано'}\nБренд/категория: {brand or 'не указан'}"
    prompt = _SYSTEM_TRADE + "\n\n" + topic + "\nОтвет дай строго в HTML без лишних вступлений."

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
        html = (getattr(resp, "text", "") or "Не удалось сгенерировать ответ.").strip()
        return _smart_trim(html, 950)
    except Exception as e:
        log.warning("Gemini playbook error: %s", e)
        return "Не удалось сгенерировать ответ."
