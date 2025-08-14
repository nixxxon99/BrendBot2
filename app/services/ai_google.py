# app/services/ai_google.py
from __future__ import annotations
from typing import Dict, Any, Optional, List
import html
from urllib.parse import urlparse
import httpx

from app.settings import GOOGLE_CSE_KEY, GOOGLE_CSE_CX, SEARCH_ALLOWED_DOMAINS

WEB_URL = "https://www.googleapis.com/customsearch/v1"


class FetchError(Exception):
    pass


def _domain(host: str) -> str:
    h = (host or "").lower()
    return h[4:] if h.startswith("www.") else h


def _is_allowed_url(url: str, whitelist: List[str]) -> bool:
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


def _is_allowed_image_item(item: Dict[str, Any], whitelist: List[str]) -> bool:
    """Для image-поиска проверяем прежде всего домен страницы (contextLink/displayLink)."""
    ctx = (item.get("image") or {}).get("contextLink") or item.get("displayLink") or ""
    if ctx and _is_allowed_url(ctx, whitelist):
        return True
    link = item.get("link") or ""
    return _is_allowed_url(link, whitelist)


def _build_site_query(q: str, domains: List[str]) -> str:
    ds = [d.lstrip(".").lower() for d in domains if d]
    return f"({' OR '.join(f'site:{d}' for d in ds)}) {q}" if ds else q


def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        raise FetchError("GOOGLE_CSE_KEY/GOOGLE_CSE_CX are not configured")
    params = {**params, "key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_CX}
    r = httpx.get(WEB_URL, params=params, timeout=12.0)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "error" in data:
        raise FetchError(str(data["error"]))
    return data


_LANG_VARIANTS = [
    {"hl": "ru", "lr": "lang_ru"},
    {"hl": "ru"},
    {},  # без языковых ограничений — на крайний случай
]


def _parse_items(items: List[Dict[str, Any]], whitelist: List[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in items or []:
        link = it.get("link") or ""
        if whitelist and not _is_allowed_url(link, whitelist):
            continue
        title   = html.unescape(it.get("title") or it.get("htmlTitle") or "")
        snippet = html.unescape(it.get("snippet") or it.get("htmlSnippet") or "")
        out.append({"name": title, "url": link, "snippet": snippet})
    return out


def web_search_brand(query: str, limit: int = 8) -> Dict[str, Any]:
    """
    Возвращает {"results":[{"name","url","snippet"},...], "source":"web"}
    Только с доменов из SEARCH_ALLOWED_DOMAINS. Делает комбинированный запрос,
    затем — подзапросы по каждому домену до набора нужного количества результатов.
    """
    whitelist = SEARCH_ALLOWED_DOMАINS or []
    q = query.strip()
    num = min(max(limit, 1), 10)

    collected: List[Dict[str, str]] = []

    # 1) Комбинированный запрос
    combined = _build_site_query(q, whitelist)
    for langp in _LANG_VARIANTS:
        try:
            raw = _get({"q": combined, "num": num, "safe": "off", **langp})
            collected.extend(_parse_items(raw.get("items", []) or [], whitelist))
        except Exception:
            pass
        if len(collected) >= 3:
            break

    # 2) По одному домену — догребаем результатов
    if len(collected) < 3:
        for d in whitelist:
            site_q = f"site:{d} {q}"
            for langp in _LANG_VARIANTS:
                try:
                    raw = _get({"q": site_q, "num": 3, "safe": "off", **langp})
                    collected.extend(_parse_items(raw.get("items", []) or [], whitelist))
                except Exception:
                    pass
                if len(collected) >= num:
                    break
            if len(collected) >= num:
                break

    # Уникализируем и режем
    seen = set()
    uniq: List[Dict[str, str]] = []
    for r in collected:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        uniq.append(r)

    return {"results": uniq[:num], "source": "web"}


def image_search_brand(query: str) -> Optional[Dict[str, Any]]:
    """
    Ищет картинку только на разрешённых доменах (проверяем contextLink/displayLink).
    Возвращает {"contentUrl": <url>, ...} либо None.
    """
    whitelist = SEARCH_ALLOWED_DOMAINS or []
    q = query.strip()

    def _try(qstr: str) -> Optional[Dict[str, Any]]:
        try:
            raw = _get({
                "q": qstr,
                "num": 6,
                "searchType": "image",
                "imgSize": "xlarge",
                "safe": "off",
                "hl": "ru",
            })
        except Exception:
            return None
        items = raw.get("items", []) or []
        for it in items:
            if whitelist and not _is_allowed_image_item(it, whitelist):
                continue
            link = it.get("link") or ""
            return {
                "contentUrl": link,
                "contextLink": (it.get("image") or {}).get("contextLink"),
                "mime": it.get("mime"),
                "title": it.get("title"),
            }
        return None

    # 1) Комбинированный site:запрос
    res = _try(_build_site_query(q, whitelist))
    if res:
        return res

    # 2) По доменам
    for d in whitelist:
        res = _try(f"site:{d} {q}")
        if res:
            return res

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
    lines.append("• Источник: интернет (только из whitelisted сайтов)")
    return "\n".join(lines)
