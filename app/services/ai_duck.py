from typing import Dict, Any, Optional, List
from duckduckgo_search import DDGS

class FetchError(Exception):
    pass

def web_search_brand(query: str, limit: int = 8) -> Dict[str, Any]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="ru-ru", safesearch="moderate", max_results=limit))
        out = []
        for r in results:
            out.append({
                "name": r.get("title"),
                "url": r.get("href") or r.get("url"),
                "snippet": r.get("body") or r.get("snippet"),
            })
        return {"results": out}
    except Exception as e:
        raise FetchError(str(e))

def image_search_brand(query: str) -> Optional[Dict[str, Any]]:
    try:
        with DDGS() as ddgs:
            imgs = list(ddgs.images(query, region="ru-ru", safesearch="moderate", size="Large", max_results=10))
        for it in imgs:
            if it.get("image"):
                return {
                    "contentUrl": it.get("image"),
                    "source": it.get("source"),
                    "title": it.get("title"),
                }
        return None
    except Exception:
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
    lines.append("• Источник: интернет (автоматическая сводка)")
    return "\n".join(lines)
