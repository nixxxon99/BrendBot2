# app/services/ai_google.py
from typing import Dict, Any, Optional, List
import os
import httpx

from app.settings import settings

WEB_URL = "https://www.googleapis.com/customsearch/v1"

class FetchError(Exception):
    pass

def _get(params: Dict[str, Any]) -> Dict[str, Any]:
    key = settings.google_cse_key or os.getenv("GOOGLE_CSE_KEY")
    cx  = settings.google_cse_cx  or os.getenv("GOOGLE_CSE_CX")
    if not key or not cx:
        raise FetchError("GOOGLE_CSE_KEY/GOOGLE_CSE_CX are not configured")

    q = dict(params)
    q.setdefault("key", key)
    q.setdefault("cx", cx)

    try:
        r = httpx.get(WEB_URL, params=q, timeout=12.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else "HTTP"
        raise FetchError(f"Google CSE error {code}") from e
    except Exception as e:
        raise FetchError(str(e)) from e

def _with_site_filter(query: str) -> str:
    # жёстко ограничим домены через site:
    doms = [d for d in settings.allowed_domains_list if d]
    if not doms:
        return query
    sites = " OR ".join([f"site:{d}" for d in doms])
    return f"{query} ({sites})"

def web_search_brand(query: str, limit: int = 8) -> Dict[str, Any]:
    num = min(max(limit, 1), 10)
    data = _get({
        "q": _with_site_filter(query),
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
        "q": _with_site_filter(query),
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
