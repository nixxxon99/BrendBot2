# app/services/extractors.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
import re, httpx, urllib.parse

from app.settings import settings

_ALLOWED = set(settings.allowed_domains_list)

_ABV_RE = re.compile(r'(?:крепость|alcohol|abv)[^0-9]{0,10}(\d{1,2}(?:[.,]\d)?\s*%)', re.I | re.U)
_COUNTRY_RE = re.compile(r'(?:страна(?:\s*производства)?|country)\s*[:\-–]\s*([A-Za-zА-Яа-яЁё\-\s]+)', re.I | re.U)
# простая категоризация по ключевым словам
_CATS = ["Виски","Джин","Ром","Водка","Ликёр","Ликер","Текила","Коньяк","Бренди","Вино","Пиво"]
_TASTE_HINTS = re.compile(r'(?:аромат|вкус|букет|ноты)\s*[:\-–]\s*([^.<\n]{20,400})', re.I | re.U)

def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""

def _pick_cat(text: str) -> Optional[str]:
    t = text
    for c in _CATS:
        if re.search(fr'\b{re.escape(c)}\b', t, re.I):
            return "Ликёр" if c.lower() in ("ликёр","ликер") else c
    return None

def _pick_og_image(html: str) -> Optional[str]:
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m: return m.group(1)
    m = re.search(r'<meta[^>]+name=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else None

def _clean(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = re.sub(r'\s+', ' ', s).strip()
    return s or None

def fetch_and_extract(brand: str, results: Dict[str, Any], max_pages: int = 3) -> Dict[str, Any]:
    """Возвращает структуру для карточки: name, basics{category,country,abv}, taste, facts[], sources[], image_url"""
    urls = []
    for r in (results or {}).get("results", []):
        u = (r.get("url") or "").strip()
        if not u: continue
        if _domain(u) in _ALLOWED:
            urls.append(u)
        if len(urls) >= max_pages:
            break

    out: Dict[str, Any] = {
        "name": brand,
        "basics": {"category": None, "country": None, "abv": None},
        "taste": None,
        "facts": [],
        "sources": [],
        "image_url": None,
    }
    if not urls:
        return out

    client = httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0 (bot)"})
    img_url: Optional[str] = None

    for u in urls:
        try:
            resp = client.get(u)
            if resp.status_code != 200:
                continue
            html = resp.text
            text = re.sub(r'<[^>]+>', ' ', html)  # грубо, но достаточно для regex
            # извлекаем поля
            abv = _clean((_ABV_RE.search(text) or (None,))[1] if _ABV_RE.search(text) else None)
            country = _clean((_COUNTRY_RE.search(text) or (None,))[1] if _COUNTRY_RE.search(text) else None)
            cat = _pick_cat(text)
            notes = _clean((_TASTE_HINTS.search(text) or (None,))[1] if _TASTE_HINTS.search(text) else None)

            if abv and not out["basics"]["abv"]: out["basics"]["abv"] = abv
            if country and not out["basics"]["country"]: out["basics"]["country"] = country
            if cat and not out["basics"]["category"]: out["basics"]["category"] = cat
            if notes and not out["taste"]: out["taste"] = notes

            if not img_url:
                ogi = _pick_og_image(html)
                if ogi: img_url = ogi

            out["sources"].append(u)
        except Exception:
            continue

    if img_url:
        out["image_url"] = img_url

    # мини-факт для карточки
    facts = []
    b = out["basics"]
    if b.get("category"):
        facts.append(f"Категория: {b['category']}")
    if b.get("country"):
        facts.append(f"Страна: {b['country']}")
    if b.get("abv"):
        facts.append(f"Крепость: {b['abv']}")
    out["facts"] = facts

    return out
