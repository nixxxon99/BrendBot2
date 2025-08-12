import httpx
from urllib.parse import quote


class WikiError(Exception):
    pass


def _search_title(lang: str, query: str) -> str | None:
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 1,
        "format": "json",
    }
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    hits = (data.get("query") or {}).get("search") or []
    return hits and hits[0]["title"]


def _summary_image(lang: str, title: str) -> dict | None:
    enc = quote(title.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{enc}"
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        s = r.json()
    if s.get("type") == "disambiguation":
        return None
    img = (s.get("originalimage") or {}).get("source") or (s.get("thumbnail") or {}).get("source")
    if not img:
        return None
    return {
        "contentUrl": img,
        "pageUrl": f"https://{lang}.wikipedia.org/wiki/{enc}",
        "title": s.get("title") or title,
        "lang": lang,
    }


def wiki_image_brand(query: str, langs=("ru", "en")) -> dict | None:
    for lang in langs:
        title = _search_title(lang, query)
        if not title:
            continue
        img = _summary_image(lang, title)
        if img:
            return img
    return None
