# app/services/extractors.py
from __future__ import annotations

from typing import Dict, Any, List, Optional
import re
import httpx
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from app.settings import settings

# ====== конфиг / разрешённые домены ======
_ALLOWED = set(settings.allowed_domains_list or [])

# ====== утилиты ======
_BAD_IMG_SUBSTR = ("banner", "placeholder", "no-image", "stub", "dummy", "spacer")

_CATS_CANON = {
    "виски": "Виски",
    "ирландский виски": "Виски",
    "шотландский виски": "Виски",
    "бурбон": "Виски",
    "джин": "Джин",
    "ром": "Ром",
    "водка": "Водка",
    "ликер": "Ликёр",
    "ликёр": "Ликёр",
    "текила": "Текила",
    "коньяк": "Коньяк",
    "бренди": "Бренди",
    "вино": "Вино",
    "пиво": "Пиво",
    "вермут": "Вермут",
    "портвейн": "Портвейн",
    "кампари": "Биттер",
}

_ABV_ANY = re.compile(r"(?<!\d)(\d{1,2}(?:[.,]\d)?)\s*%")
_SEO_TAIL = re.compile(r"(купить.*$|в\s+алматы.*$|с\s+доставк.*$|отличное качество.*$)", re.I)

def _domain(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
        return h.lower().lstrip("www.")
    except Exception:
        return ""

def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _clean_title(t: str) -> str:
    t = _clean_spaces(t)
    t = _SEO_TAIL.sub("", t).strip(" -–—")
    return t

def _first_sentence(text: str, limit: int = 220) -> str:
    text = _clean_spaces(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[\.\!\?])\s+", text)
    s = parts[0] if parts else text
    return (s[: limit - 1] + "…") if len(s) > limit else s

def _abs_url(base: str, u: str | None) -> str:
    if not u:
        return ""
    return urljoin(base, u)

def _bad_img(u: str) -> bool:
    u = (u or "").lower()
    return not u or any(x in u for x in _BAD_IMG_SUBSTR)

def _pick_abv(text: str) -> str:
    m = _ABV_ANY.search(text or "")
    if not m:
        return ""
    val = m.group(1).replace(",", ".")
    try:
        # нормализация 40 -> 40%
        return f"{float(val):g}%"
    except Exception:
        return f"{val}%"

def _canon_cat(raw: str) -> str:
    r = (raw or "").strip().lower()
    for k, v in _CATS_CANON.items():
        if k in r:
            return v
    return ""

def _table_specs(soup: BeautifulSoup) -> dict:
    specs = {}
    for tr in soup.select("table tr"):
        th = tr.select_one("th") or tr.select_one("td:nth-child(1)")
        td = tr.select_one("td:nth-child(2)") or (tr.select("td")[1] if len(tr.select("td")) >= 2 else None)
        k = _clean_spaces(th.get_text(" ", strip=True) if th else "")
        v = _clean_spaces(td.get_text(" ", strip=True) if td else "")
        if k and v:
            specs[k.lower()] = v
    return specs

def _og_image(soup: BeautifulSoup, url: str) -> str:
    og = soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="og:image"]')
    if og and og.get("content"):
        u = _abs_url(url, og.get("content"))
        if not _bad_img(u):
            return u
    return ""

def _gallery_image(soup: BeautifulSoup, url: str) -> str:
    for sel in [
        'img[data-zoom-image]',
        ".swiper-slide img",
        ".product-gallery img",
        "img.product-image",
        'img[itemprop="image"]',
        ".gallery img",
        ".product-images img",
    ]:
        img = soup.select_one(sel)
        if img:
            u = img.get("data-zoom-image") or img.get("src") or img.get("data-src")
            u = _abs_url(url, u)
            if not _bad_img(u):
                return u
    return ""

def _quality_drop_name(name: str) -> bool:
    n = (name or "").lower()
    return not n or "купить" in n or "доставк" in n

def _push_fact(facts: list[str], s: str, limit: int = 4):
    s = _clean_spaces(s)
    if not s:
        return
    if s not in facts and len(facts) < limit:
        facts.append(s)

# ====== парсеры по сайтам ======
def parse_luxalcomarket(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    # крошки -> категория
    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, nav.breadcrumb a, .breadcrumbs a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна производитель") or specs.get("страна-производитель") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

    # описание и ноты
    desc = ""
    for sel in ["#tab-description", ".tab-description", ".product-description", ".description", ".content"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    # изображения
    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = _canon_cat(category) or category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    for key in ("выдержка", "бочки", "регион", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_winestyle(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    # категория по крошкам
    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, .breadcrumbs a, nav.breadcrumb a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна, регион") or specs.get("страна/регион") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

    # описание/ноты
    desc = ""
    for sel in [".tasting-notes", ".description", ".product-card__description", "#description"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = _canon_cat(category) or category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "стиль", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_decanter(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, .breadcrumbs a, nav.breadcrumb a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна производитель") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

    desc = ""
    for sel in [".product-description", ".description", "#tab-description", ".tab-content"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = _canon_cat(category) or category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_newxo(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )
    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, .breadcrumbs a, nav.breadcrumb a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна производитель") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

    desc = ""
    for sel in [".product-description", ".description", "#tab-description", ".tab-content"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = _canon_cat(category) or category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_inshaker(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    # они часто имеют блоки характеристик списком
    props = {}
    for li in soup.select("ul li"):
        k = _clean_spaces(li.select_one("b, strong").get_text(" ", strip=True)) if li.select_one("b, strong") else ""
        v = _clean_spaces(li.get_text(" ", strip=True))
        if k and v:
            props[k.lower()] = v

    abv = props.get("крепость") or props.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))
    country = props.get("страна") or ""

    # категории у Inshaker — «ирландский виски», «биттер» и т.п.
    text_all = soup.get_text(" ", strip=True).lower()
    category = ""
    for k in _CATS_CANON.keys():
        if k in text_all:
            category = _CATS_CANON[k]
            break

    desc = ""
    for sel in [".description", ".text", ".content", ".article"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    if desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_generic(html: str, url: str) -> dict:
    """Запасной универсальный парсер."""
    soup = BeautifulSoup(html, "html.parser")
    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )
    if _quality_drop_name(name):
        return {}

    text = soup.get_text(" ", strip=True)
    abv = _pick_abv(text)
    category = ""
    for k in _CATS_CANON.keys():
        if k in text.lower():
            category = _CATS_CANON[k]
            break
    country = ""

    desc = ""
    for sel in [".description", ".product-description", ".content", "article"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    basics = {}
    if category:
        basics["category"] = category
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv

    facts: list[str] = []
    if desc:
        _push_fact(facts, _first_sentence(desc, 160))

    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_by_host(html: str, url: str) -> dict:
    host = _domain(url)
    if "luxalcomarket.kz" in host:
        return parse_luxalcomarket(html, url)
    if "winestyle.ru" in host:
        return parse_winestyle(html, url)
    if "decanter.ru" in host:
        return parse_decanter(html, url)
    if "newxo.kz" in host:
        return parse_newxo(html, url)
    if "ru.inshaker.com" in host or "inshaker.com" in host:
        return parse_inshaker(html, url)
    return parse_generic(html, url)

# ====== основной API: одна «объединённая» карточка из нескольких страниц ======
def fetch_and_extract(brand: str, results: Dict[str, Any], max_pages: int = 3) -> Dict[str, Any]:
    """
    Вход: brand, результаты web_search_brand (dict) с полем results[] (url, title, snippet)
    Выход: единая карточка: name, basics{category,country,abv}, taste, facts[], sources[], image_url
    """
    # соберём урлы только с разрешённых доменов
    urls: List[str] = []
    for r in (results or {}).get("results", []):
        u = _clean_spaces((r.get("url") or "")) if isinstance(r, dict) else _clean_spaces(str(r))
        if not u:
            continue
        if _domain(u) in _ALLOWED:
            urls.append(u)
        if len(urls) >= max_pages:
            break

    out: Dict[str, Any] = {
        "name": _clean_title(brand),
        "basics": {"category": None, "country": None, "abv": None},
        "taste": None,
        "facts": [],
        "sources": [],
        "image_url": None,
    }
    if not urls:
        return out

    client = httpx.Client(timeout=12.0, headers={"User-Agent": "Mozilla/5.0 (BrendBot/1.0)"})

    # агрегируем лучшее из нескольких источников
    img_best = ""
    facts_acc: list[str] = []

    try:
        for u in urls:
            try:
                r = client.get(u)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            parsed = parse_by_host(r.text, u)
            if not parsed:
                continue

            # имя — первое внятное
            if parsed.get("name") and (not out["name"] or out["name"] == _clean_title(brand)):
                out["name"] = parsed["name"]

            # basics — дополняем пустые поля
            pb = parsed.get("basics") or {}
            ob = out["basics"]
            if pb.get("category") and not ob.get("category"):
                ob["category"] = pb["category"]
            if pb.get("country") and not ob.get("country"):
                ob["country"] = pb["country"]
            if pb.get("abv") and not ob.get("abv"):
                ob["abv"] = pb["abv"]

            # taste — берём первый нормальный
            if parsed.get("taste") and not out.get("taste"):
                out["taste"] = parsed["taste"]

            # facts — аккумулируем, убирая дубли
            for f in (parsed.get("facts") or []):
                _push_fact(facts_acc, f, limit=8)

            # источники
            if u not in out["sources"]:
                out["sources"].append(u)

            # картинка — первая небаннерная
            if not img_best:
                img = (parsed.get("image_url") or "").strip()
                if img and not _bad_img(img):
                    img_best = img
    finally:
        try:
            client.close()
        except Exception:
            pass

    out["facts"] = facts_acc
    if img_best:
        out["image_url"] = img_best

    return out

# ====== многорезультатная версия: отдаём список «мини-карточек» по каждому источнику ======
def fetch_and_extract_many(brand: str, results: Dict[str, Any], max_pages: int = 2, top_k: int = 6) -> List[Dict[str, Any]]:
    """
    Возвращает список отдельных извлечений (по 1 странице), чтобы затем можно было
    склеить в LLM или руками выбрать лучшую.
    """
    items: List[Dict[str, Any]] = []
    urls: List[str] = []

    for r in (results or {}).get("results", []):
        u = _clean_spaces((r.get("url") or "")) if isinstance(r, dict) else _clean_spaces(str(r))
        if not u:
            continue
        if _domain(u) in _ALLOWED:
            urls.append(u)
        if len(urls) >= min(top_k, max_pages):
            break

    if not urls:
        return items

    client = httpx.Client(timeout=12.0, headers={"User-Agent": "Mozilla/5.0 (BrendBot/1.0)"})
    try:
        for u in urls:
            try:
                r = client.get(u)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            parsed = parse_by_host(r.text, u)
            if not parsed:
                continue
            parsed["source_url"] = u
            items.append(parsed)
    finally:
        try:
            client.close()
        except Exception:
            pass

    return items
