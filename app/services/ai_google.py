# app/services/ai_google.py
from __future__ import annotations
from typing import Dict, Any, Optional, List
import html
from urllib.parse import urlparse

import httpx

# Берём ключи и вайтлист из настроек
from app.settings import GOOGLE_CSE_KEY, GOOGLE_CSE_CX, SEARCH_ALLOWED_DOMAINS

WEB_URL = "https://www.googleapis.com/customsearch/v1"


class FetchError(Exception):
    pass


def _domain(host: str) -> str:
    h = (host or "").lower()
    return h[4:] if h.startswith("www.") else h


def _is_allowed(url: str, whitelist: List[str]) -> bool:
    try:
        host = _domain(urlparse(url).netloc)
    except Exception:
        return False
    if not host:
        return False
    for d in whitelist:
        d = d.lstrip(".").lower()
        if host == d or host.endswith("." + d):
            return True
    return False


def _build_site_query(q: str, domains: List[str]) -> str:
    ds = [d.lstrip(".").lower() for d in domains if d]
    return f"({' OR '.join(f'site:{d}' for d in ds)}) {q}" if ds else q


def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        raise FetchError("GOOGLE_CSE_KEY/GOOGLE_CSE_CX are not configured")
    params = {**params, "key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_CX}
    try:
        r = httpx.get(WEB_URL, params=params, timeout=12.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else "HTTP"
        raise FetchError(f"Google CSE error {code}") from e
    except Exception as e:
        raise FetchError(str(e)) from e


def web_search_brand(query: str, limit: int = 8) -> Dict[str, Any]:
    """
    Возвращает {"results":[{"name","url","snippet"},...], "source":"web"}
    Только с доменов из SEARCH_ALLOWED_DOMAINS.
    """
    whitelist = SEARCH_ALLOWED_DOMАINS or []
    num = min(max(limit, 1), 10)

    data = _get({
        "q": _build_site_query(query.strip(), whitelist),
        "num": num,
        "hl": "ru",
        "lr": "lang_ru",
        "safe": "off",  # или "active" если нужно
    })

    items = data.get("items") or []
    results: List[Dict[str, str]] = []
    for it in items:
        link = it.get("link") or ""
        if whitelist and not _is_allowed(link, whitelist):
            continue
        title   = html.unescape(it.get("title") or it.get("htmlTitle") or "")
        snippet = html.unescape(it.get("snippet") or it.get("htmlSnippet") or "")
        results.append({"name": title, "url": link, "snippet": snippet})

    return {"results": results, "source": "web"}


def image_search_brand(query: str) -> Optional[Dict[str, Any]]:
    """
    Ищет картинку только на разрешённых доменах.
    Возвращает {"contentUrl": <url>, ...} либо None.
    """
    whitelist = SEARCH_ALLOWED_DOMAINS or []

    data = _get({
        "q": _build_site_query(query.strip(), whitelist),
        "num": 5,
        "searchType": "image",
        "imgSize": "xlarge",
        "hl": "ru",
        "safe": "off",
    })

    items = data.get("items") or []
    for it in items:
        link = it.get("link") or ""
        if whitelist and not _is_allowed(link, whitelist):
            continue
        return {
            "contentUrl": link,
            "contextLink": (it.get("image") or {}).get("contextLink"),
            "mime": it.get("mime"),
            "title": it.get("title"),
        }
    return None


def build_caption_from_results(brand: str, results: Dict[str, Any]) -> str:
    lines = [f"<b>{brand}</b>"]
    snippets: List[str] = []
    for r in results.get("results", [])[:6]:
        sn = (r.get("snippet") or "").strip()
        if sn and sn not in snippets:
            if len(sn) > 180:
                sn = sn[:177] + "…"
            snippets.append(sn)
        if len(snippets) >= 6:
            break
    if not snippets:
        snippets = ["• На разрешённых сайтах данных не найдено."]
    lines.extend([f"• {s}" for s in snippets])
    lines.append("• Источник: интернет (только whitelisted сайты)")
    return "\n".join(lines)
