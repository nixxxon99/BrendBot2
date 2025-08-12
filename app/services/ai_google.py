from typing import Dict, Any, Optional, List
import os
import httpx

try:
    from app.settings import settings
    GOOGLE_CSE_KEY = getattr(settings, 'google_cse_key', None) or os.getenv('GOOGLE_CSE_KEY')
    GOOGLE_CSE_CX  = getattr(settings, 'google_cse_cx',  None) or os.getenv('GOOGLE_CSE_CX')
except Exception:
    GOOGLE_CSE_KEY = os.getenv('GOOGLE_CSE_KEY')
    GOOGLE_CSE_CX  = os.getenv('GOOGLE_CSE_CX')

WEB_URL = "https://www.googleapis.com/customsearch/v1"

class FetchError(Exception):
    pass

def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        raise FetchError("GOOGLE_CSE_KEY/GOOGLE_CSE_CX are not configured")
    params = dict(params)
    params.setdefault("key", GOOGLE_CSE_KEY)
    params.setdefault("cx", GOOGLE_CSE_CX)
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
    num = min(max(limit, 1), 10)
    data = _get({
        "q": query,
        "num": num,
        "hl": "ru",
        "safe": "active",
    })
    items = data.get("items") or []
    results: List[Dict[str, str]] = []
    for it in items:
        results.append({
            "name": it.get("title"),
            "url": it.get("link"),
            "snippet": it.get("snippet"),
        })
    if not results:
        raise FetchError("No results from Google CSE")
    return {"results": results}

def image_search_brand(query: str) -> Optional[Dict[str, Any]]:
    data = _get({
        "q": query,
        "num": 5,
        "searchType": "image",
        "imgSize": "xlarge",
        "safe": "active",
        "hl": "ru",
    })
    items = data.get("items") or []
    for it in items:
        link = it.get("link")
        if link:
            return {
                "contentUrl": link,
                "contextLink": (it.get("image") or {}).get("contextLink"),
                "mime": (it.get("mime")),
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
        snippets = ["• Краткая справка недоступна."]
    lines.extend([f"• {s}" for s in snippets])
    lines.append("• Источник: интернет (Google CSE, автоматическая сводка)")
    return "\n".join(lines)
