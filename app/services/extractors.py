# app/services/extractors.py
from __future__ import annotations
import contextlib

from typing import Dict, Any, List, Optional
import re
import httpx
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from app.settings import settings


# =========================
# Конфиг/разрешённые домены
# =========================
# Если список пустой – считаем, что разрешены все домены
_ALLOWED = set(settings.allowed_domains_list or [])

# Единый заголовок клиента
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 BrendBot/1.0"
}

# =========================
# Утилиты
# =========================
_BAD_IMG_SUBSTR = ("banner", "placeholder", "no-image", "stub", "dummy", "spacer")

# Нормализация категорий
_CATS_CANON = {
    "ирландский виски": "Виски",
    "шотландский виски": "Виски",
    "бурбон": "Виски",
    "виски": "Виски",
    "джин": "Джин",
    "ром": "Ром",
    "водка": "Водка",
    "ликёр": "Ликёр",
    "ликер": "Ликёр",
    "текила": "Текила",
    "коньяк": "Коньяк",
    "бренди": "Бренди",
    "вермут": "Вермут",
    "портвейн": "Портвейн",
    "биттер": "Биттер",
    "вино": "Вино",
    "пиво": "Пиво",
}

_ABV_ANY = re.compile(r"(?<!\d)(\d{1,2}(?:[.,]\d)?)\s*%", re.I)
_SEO_TAIL = re.compile(r"(купить.*$|в\s+алматы.*$|с\s+доставк.*$|отличное качество.*$)", re.I)

def _domain(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
        return h.lower().lstrip("www.")
    except Exception:
        return ""

def _is_allowed(url: str) -> bool:
    return not _ALLOWED or _domain(url) in _ALLOWED

def _clean_spaces(s: str | None) -> str:
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
    """Читает пары характеристик из таблиц/списков."""
    specs: dict[str, str] = {}

    # table -> th/td
    for tr in soup.select("table tr"):
        th = tr.select_one("th") or tr.select_one("td:nth-child(1)")
        td = tr.select_one("td:nth-child(2)") or (tr.select("td")[1] if len(tr.select("td")) >= 2 else None)
        k = _clean_spaces(th.get_text(" ", strip=True) if th else "")
        v = _clean_spaces(td.get_text(" ", strip=True) if td else "")
        if k and v:
            specs[k.lower()] = v

    # dl/dt/dd
    for dl in soup.select("dl"):
        dts = dl.select("dt")
        dds = dl.select("dd")
        for i in range(min(len(dts), len(dds))):
            k = _clean_spaces(dts[i].get_text(" ", strip=True))
            v = _clean_spaces(dds[i].get_text(" ", strip=True))
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
        ".product__gallery img",
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

def _push_fact(facts: list[str], s: str, limit: int = 8):
    s = _clean_spaces(s)
    if not s:
        return
    if s not in facts and len(facts) < limit:
        facts.append(s)


# =========================
# Парсеры по сайтам
# =========================
def _make_basics(name: str, crumbs_category: str, country: str, abv: str) -> dict:
    """
    Приоритет категории: H1/title -> хлебные крошки -> пусто.
    """
    basics: dict[str, str] = {}
    cat_from_name = _canon_cat(name)
    if cat_from_name or crumbs_category:
        basics["category"] = cat_from_name or (_canon_cat(crumbs_category) or crumbs_category)
    if country:
        basics["country"] = country
    if abv:
        basics["abv"] = abv
    return basics

def parse_luxalcomarket(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, nav.breadcrumb a, .breadcrumbs a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна производитель") or specs.get("страна-производитель") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

    desc = ""
    for sel in ["#tab-description", ".tab-description", ".product-description", ".description", ".content"]:
        el = soup.select_one(sel)
        if el:
            desc = el.get_text(" ", strip=True)
            break
    taste = _first_sentence(desc)

    img = _og_image(soup, url) or _gallery_image(soup, url)
    if _bad_img(img):
        img = ""

    facts: list[str] = []
    for key in ("выдержка", "бочки", "регион", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    basics = _make_basics(name, category, country, abv)
    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_winestyle(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    crumbs = [c.get_text(" ", strip=True) for c in soup.select(".breadcrumb a, .breadcrumbs a, nav.breadcrumb a")]
    category = crumbs[-1] if crumbs else ""

    specs = _table_specs(soup)
    country = specs.get("страна") or specs.get("страна, регион") or specs.get("страна/регион") or ""
    abv = specs.get("крепость") or specs.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))

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

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "стиль", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    basics = _make_basics(name, category, country, abv)
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

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    basics = _make_basics(name, category, country, abv)
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

    facts: list[str] = []
    for key in ("регион", "выдержка", "бочки", "сорт винограда", "тип"):
        v = specs.get(key, "")
        if v:
            _push_fact(facts, f"{key.capitalize()}: {v}")
    if not facts and desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    basics = _make_basics(name, category, country, abv)
    return {"name": name, "aliases": [], "basics": basics, "taste": taste, "facts": facts, "sources": [url], "image_url": img}

def parse_inshaker(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    name = _clean_title(
        (soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get("content", ""))
    )

    # они часто имеют блоки характеристик списком
    props: dict[str, str] = {}
    for li in soup.select("ul li"):
        strong = li.select_one("b, strong")
        k = _clean_spaces(strong.get_text(" ", strip=True)) if strong else ""
        v = _clean_spaces(li.get_text(" ", strip=True))
        if k and v:
            props[k.lower()] = v

    abv = props.get("крепость") or props.get("алкоголь") or ""
    abv = _pick_abv(abv) or _pick_abv(soup.get_text(" ", strip=True))
    country = props.get("страна") or ""

    # категории у Inshaker — «ирландский виски», «биттер» и т.п.
    text_all = soup.get_text(" ", strip=True).lower()
    category_hint = ""
    for k in _CATS_CANON.keys():
        if k in text_all:
            category_hint = _CATS_CANON[k]
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

    facts: list[str] = []
    if desc:
        _push_fact(facts, _first_sentence(desc, 160))

    if _quality_drop_name(name):
        return {}

    basics = _make_basics(name, category_hint, country, abv)
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

    # Категория – по названию, затем по всему тексту
    category = _canon_cat(name)
    if not category:
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

    basics = _make_basics(name, category, country, abv)
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
    if "inshaker.com" in host:
        return parse_inshaker(html, url)
    return parse_generic(html, url)


# =========================
# Основной API: одна «объединённая» карточка
# =========================
def fetch_and_extract(brand: str, results: Dict[str, Any], max_pages: int = 3) -> Dict[str, Any]:
    """
    Вход: brand, результаты web_search_brand (dict) с полем results[] (url, title, snippet)
    Выход: единая карточка: name, basics{category,country,abv}, taste, facts[], sources[], image_url
    """
    urls: List[str] = []
    for r in (results or {}).get("results", []):
        u = _clean_spaces((r.get("url") or "")) if isinstance(r, dict) else _clean_spaces(str(r))
        if not u:
            continue
        if _is_allowed(u):
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

    client = httpx.Client(timeout=12.0, headers=_HTTP_HEADERS)

    img_best = ""
    facts_acc: list[str] = []

    try:
        for u in urls:
            try:
                resp = client.get(u)
            except Exception:
                continue
            if resp.status_code != 200 or not resp.text:
                continue

            parsed = parse_by_host(resp.text, u)
            if not parsed:
                continue

            # имя — первое нормальное
            if parsed.get("name") and (not out["name"] or out["name"] == _clean_title(brand)):
                out["name"] = parsed["name"]

            # basics — заполняем только пустые поля
            pb = parsed.get("basics") or {}
            ob = out["basics"]
            if pb.get("category") and not ob.get("category"):
                ob["category"] = pb["category"]
            if pb.get("country") and not ob.get("country"):
                ob["country"] = pb["country"]
            if pb.get("abv") and not ob.get("abv"):
                ob["abv"] = pb["abv"]

            # taste
            if parsed.get("taste") and not out.get("taste"):
                out["taste"] = parsed["taste"]

            # facts — аккумулируем без дублей
            for f in (parsed.get("facts") or []):
                _push_fact(facts_acc, f, limit=8)

            # источники
            if u not in out["sources"]:
                out["sources"].append(u)

            # картинка — первая нормальная
            if not img_best:
                img = (parsed.get("image_url") or "").strip()
                if img and not _bad_img(img):
                    img_best = img
    finally:
        with contextlib.suppress(Exception):
            client.close()

    # финал
    out["facts"] = facts_acc

    # добавим мини-факты из basics в начало, если их ещё нет
    b = out["basics"]
    basics_facts: list[str] = []
    if b.get("category"):
        basics_facts.append(f"Категория: {b['category']}")
    if b.get("country"):
        basics_facts.append(f"Страна: {b['country']}")
    if b.get("abv"):
        basics_facts.append(f"Крепость: {b['abv']}")

    final_facts = []
    for x in basics_facts + out["facts"]:
        if x not in final_facts:
            final_facts.append(x)
    out["facts"] = final_facts[:8]

    if img_best:
        out["image_url"] = img_best

    return out


# =========================
# Многорезультатная версия (по каждому источнику)
# =========================
def fetch_and_extract_many(brand: str, results: Dict[str, Any], max_pages: int = 2, top_k: int = 6) -> List[Dict[str, Any]]:
    """
    Возвращает список отдельных извлечений (по 1 странице), чтобы потом можно было
    склеить в LLM или выбрать лучшую вручную.
    """
    urls: List[str] = []
    items: List[Dict[str, Any]] = []

    for r in (results or {}).get("results", []):
        u = _clean_spaces((r.get("url") or "")) if isinstance(r, dict) else _clean_spaces(str(r))
        if not u:
            continue
        if _is_allowed(u):
            urls.append(u)
        if len(urls) >= min(top_k, max_pages):
            break

    if not urls:
        return items

    client = httpx.Client(timeout=12.0, headers=_HTTP_HEADERS)
    try:
        for u in urls:
            try:
                resp = client.get(u)
            except Exception:
                continue
            if resp.status_code != 200 or not resp.text:
                continue
            parsed = parse_by_host(resp.text, u)
            if not parsed:
                continue
            parsed["source_url"] = u
            items.append(parsed)
    finally:
        with contextlib.suppress(Exception):
            client.close()

    return items
