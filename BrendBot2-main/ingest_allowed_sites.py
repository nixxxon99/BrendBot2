# tools/ingest_allowed_sites.py
from __future__ import annotations
import os, re, json, time, argparse, urllib.parse as up
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SEED_PATH = DATA_DIR / "seed_urls.json"
OUT_PATH  = DATA_DIR / "ingested_kb.json"

# ====== Настройки доменов ======
def _split_domains(val: str) -> List[str]:
    return [d.strip().lower().lstrip(".") for d in (val or "").split(",") if d.strip()]

# 1) пробуем взять из app.settings (если проект установлен)
try:
    from app.settings import SEARCH_ALLOWED_DOMAINS as _DOMAINS_FROM_APP
    ALLOWED_DOMAINS = [d.lower() for d in _DOMAINS_FROM_APP]
except Exception:
    # 2) иначе берём из переменной окружения
    ALLOWED_DOMAINS = _split_domains(os.getenv("SEARCH_ALLOWED_DOMAINS", ""))

# 3) безопасный дефолт (только твои домены)
if not ALLOWED_DOMAINS:
    ALLOWED_DOMAINS = [
        "winestyle.ru",
        "luxalcomarket.kz",
        "decanter.ru",
        "newxo.kz",
        "ru.inshaker.com",
    ]

# ====== HTTP ======
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BrandBotIngest/1.0; +https://example.local)",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

def _same_or_subdomain(url: str, allowed: List[str]) -> bool:
    try:
        h = up.urlparse(url).hostname or ""
        h = h.lower()
        for d in allowed:
            if h == d or h.endswith("." + d):
                return True
        return False
    except Exception:
        return False

def _fetch(url: str, timeout: float = 12.0) -> Optional[str]:
    if not _same_or_subdomain(url, ALLOWED_DOMAINS):
        return None
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as c:
            r = c.get(url)
            r.raise_for_status()
            ct = r.headers.get("content-type","").lower()
            if "text/html" not in ct and "xml" not in ct:
                return None
            return r.text
    except Exception:
        return None

# ====== Разбор страницы ======
COUNTRIES = [
    "Шотландия","Ирландия","США","Великобритания","Англия","Франция","Испания","Италия",
    "Мексика","Исландия","Япония","Канада","Россия"
]
CATEGORIES = [
    "виски","ром","джин","водка","текила","мезкаль","ликёр","бренди","коньяк","арманьяк"
]

def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _extract_text_nodes(soup: BeautifulSoup) -> str:
    # основной текст страницы без скриптов/стилей
    for bad in soup(["script","style","noscript"]):
        bad.decompose()
    text = soup.get_text(" ", strip=True)
    return _clean_text(text)

def _find_meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for n in names:
        t = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
        if t and t.get("content"):
            return _clean_text(t["content"])
    return None

def _extract_image(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    # приоритет: og:image → twitter:image → image_src → product image
    for sel in [
        ("meta", {"property": "og:image"}),
        ("meta", {"name": "og:image"}),
        ("meta", {"name": "twitter:image"}),
        ("link", {"rel": "image_src"}),
    ]:
        tag = soup.find(*sel)
        if tag:
            url = tag.get("content") or tag.get("href")
            if url:
                return up.urljoin(base_url, url)

    # запасной: первая картинка, похожая на фото товара
    img = soup.find("img", attrs={"src": True})
    if img:
        url = img.get("src")
        if url:
            return up.urljoin(base_url, url)
    return None

def _guess_abv(text: str) -> Optional[str]:
    # ищем крепость: 40%, 43 %, 35–37.5% и т.п.
    m = re.search(r"(\d{2}(?:[.,]\d)?)\s*%\s*", text)
    if m:
        return m.group(1).replace(",", ".") + "%"
    return None

def _guess_country(text: str) -> Optional[str]:
    for c in COUNTRIES:
        if re.search(rf"\b{re.escape(c)}\b", text, re.I):
            return c
    return None

def _guess_category(text: str) -> Optional[str]:
    for c in CATEGORIES:
        if re.search(rf"\b{c}\b", text, re.I):
            # нормализуем первую букву
            return c.capitalize()
    return None

def _extract_taste(text: str) -> Optional[str]:
    # берём 1–2 предложения вокруг слов типа "вкус", "аромат", "ноты"
    m = re.search(r"(?:(?:вкус|аромат|ноты)\S*[:\-–]\s*)([^.]{30,180})", text, re.I)
    if m:
        return _clean_text(m.group(1))
    # fallback — мета description
    return None

def parse_product_page(url: str, html: str, brand_hint: Optional[str] = None, category_hint: Optional[str] = None, aliases: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    title = _find_meta(soup, "og:title", "twitter:title") or (soup.title.string if soup.title else "")
    title = _clean_text(title)
    desc  = _find_meta(soup, "description") or ""
    text  = _extract_text_nodes(soup)
    img   = _extract_image(soup, url)

    name = brand_hint or title or ""
    if not name:
        return None

    abv = _guess_abv(text) or _guess_abv(desc)
    country = _guess_country(text) or _guess_country(desc)
    category = category_hint or _guess_category(text) or _guess_category(desc)

    taste = _extract_taste(text) or _extract_taste(desc)
    facts: List[str] = []

    # Подхватим небольшие факты: выдержка, тип бочек, finish/rum/sherry/ipa и т.д.
    for pat in [
        r"выдержк[аи][^.,]{0,40}\d{1,2}\s*лет",
        r"финиш[^.,]{0,60}",
        r"бочк[аи][^.,]{0,60}",
        r"солод[^.,]{0,60}",
        r"торф[^.,]{0,60}",
        r"бурбон[^.,]{0,60}",
        r"херес[^.,]{0,60}",
        r"ром[^.,]{0,60}",
        r"каскад[^.,]{0,60}",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            facts.append(_clean_text(m.group(0)))

    rec: Dict[str, Any] = {
        "name": name,
        "aliases": aliases or [],
        "basics": {
            "category": category or "",
            "country": country or "",
            "abv": abv or "",
        },
        "taste": taste or "",
        "facts": facts[:5],
        "sources": [url],
        "image_url": img or "",
    }
    return rec

# ====== Обход seed-ов ======
def load_seeds() -> List[Dict[str, Any]]:
    if not SEED_PATH.exists():
        print(f"[ingest] seed file not found: {SEED_PATH}")
        return []
    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception as e:
        print("[ingest] seed read error:", e)
        return []

def is_allowed(url: str) -> bool:
    return _same_or_subdomain(url, ALLOWED_DOMAINS)

def crawl_category(seed: Dict[str, Any]) -> List[str]:
    """
    Простой сбор ссылок со страницы категории по include_patterns.
    """
    url = seed.get("url", "")
    inc = seed.get("include_patterns") or []
    max_pages = int(seed.get("max_pages") or 30)

    html = _fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    hrefs: Set[str] = set()
    for a in soup.find_all("a", href=True):
        link = up.urljoin(url, a["href"])
        if not is_allowed(link):
            continue
        if inc:
            ok = any(re.search(p, link) for p in inc)
            if not ok:
                continue
        hrefs.add(link)
        if len(hrefs) >= max_pages:
            break
    return list(hrefs)

def crawl_sitemap(seed: Dict[str, Any]) -> List[str]:
    url = seed.get("url","")
    inc = seed.get("include_patterns") or []
    max_pages = int(seed.get("max_pages") or 100)

    xml = _fetch(url)
    if not xml:
        return []
    links: List[str] = []
    # простое извлечение <loc>... ссылок
    for m in re.finditer(r"<loc>(.*?)</loc>", xml, re.I | re.S):
        link = _clean_text(m.group(1))
        if not is_allowed(link):
            continue
        if inc:
            ok = any(re.search(p, link) for p in inc)
            if not ok:
                continue
        links.append(link)
        if len(links) >= max_pages:
            break
    return links

# ====== Главная процедура ======
def main():
    ap = argparse.ArgumentParser(description="Ingest allowed alcohol sites into local KB")
    ap.add_argument("--brands", type=str, default="", help="подсказать бренды через запятую (для seed type=page)")
    args = ap.parse_args()

    brand_hints = [b.strip() for b in (args.brands or "").split(",") if b.strip()]

    seeds = load_seeds()
    if not seeds:
        print("[ingest] no seeds; create data/seed_urls.json")
        return

    out: Dict[str, Dict[str, Any]] = {}  # key by normalized name
    def key(n: str) -> str:
        return _clean_text(n).lower()

    total_pages = 0

    for seed in seeds:
        st = seed.get("type","page").lower()

        if st == "page":
            url = seed.get("url","")
            if not url or not is_allowed(url):
                continue
            html = _fetch(url)
            if not html:
                continue
            rec = parse_product_page(
                url, html,
                brand_hint=seed.get("brand"),
                category_hint=seed.get("category"),
                aliases=seed.get("aliases"),
            )
            if rec:
                out[key(rec["name"])] = rec
                total_pages += 1
                time.sleep(0.8)  # вежливая пауза

        elif st == "category":
            links = crawl_category(seed)
            for link in links:
                html = _fetch(link)
                if not html:
                    continue
                rec = parse_product_page(link, html)
                if rec:
                    out[key(rec["name"])] = rec
                    total_pages += 1
                    time.sleep(0.8)

        elif st == "sitemap":
            links = crawl_sitemap(seed)
            for link in links:
                html = _fetch(link)
                if not html:
                    continue
                rec = parse_product_page(link, html)
                if rec:
                    out[key(rec["name"])] = rec
                    total_pages += 1
                    time.sleep(0.6)

        else:
            # неизвестный тип — пропустим
            continue

    # Сохраняем как список
    items = list(out.values())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ingest] saved {len(items)} records from {total_pages} pages → {OUT_PATH}")

if __name__ == "__main__":
    main()
