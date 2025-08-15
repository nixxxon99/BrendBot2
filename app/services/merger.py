from collections import Counter, defaultdict
from urllib.parse import urlparse

ALLOWED_IMG_DOMAINS_ORDER = [
    "newxo.kz", "luxalcomarket.kz", "winestyle.ru", "decanter.ru", "ru.inshaker.com"
]

def _norm(s):
    return (s or "").strip()

def _parse_abv(abv):
    # принимает "40%" / "40 % об." / "40" -> "40%"
    if not abv:
        return None
    import re
    m = re.search(r"(\d{1,2}(?:[\.,]\d{1,2})?)\s*%?", str(abv))
    if not m:
        return None
    val = m.group(1).replace(",", ".")
    try:
        f = float(val)
        # 35..65 — здравый диапазон виски
        if 10 <= f <= 90:
            # без .0
            return f"{int(f) if f.is_integer() else f}%"
    except Exception:
        pass
    return None

def pick_majority(values):
    vals = [_norm(v) for v in values if _norm(v)]
    if not vals:
        return None
    c = Counter(vals)
    return c.most_common(1)[0][0]

def merge_notes(list_of_lists, limit=6):
    # Частотная выборка дегустационных нот
    c = Counter()
    for arr in list_of_lists:
        for note in (arr or []):
            n = _norm(note).lower()
            if n:
                c[n] += 1
    top = [k for k, _ in c.most_common(limit)]
    # Приведём к «человеческому» виду (первая заглавная)
    return [t.capitalize() for t in top]

def dedup_facts(facts_lists, limit=4):
    seen = set()
    out = []
    for arr in facts_lists:
        for f in (arr or []):
            norm = _norm(f)
            if norm and norm.lower() not in seen:
                seen.add(norm.lower())
                out.append(norm)
                if len(out) >= limit:
                    return out
    return out

def pick_best_image(urls):
    if not urls:
        return None
    def domain_rank(u):
        try:
            host = urlparse(u).hostname or ""
        except Exception:
            host = ""
        for i, d in enumerate(ALLOWED_IMG_DOMAINS_ORDER):
            if d in host:
                return i
        return 999
    # сортируем по приоритету домена и длине строки (часто длиннее = более конкретный asset)
    return sorted([u for u in urls if _norm(u)], key=lambda u: (domain_rank(u), -len(u)))[0]

def merge_enriched(extractions: list) -> dict:
    """
    На вход: список экстракций вида {category,country,abv,tasting_notes,facts,image_url,source_url}
    На выход: объединённый объект + перечисление источников.
    """
    if not extractions:
        return {}

    fields = defaultdict(list)
    sources = []
    for e in extractions:
        for k in ("category","country","abv","tasting_notes","facts","image_url"):
            v = e.get(k)
            if v:
                fields[k].append(v)
        src = e.get("source_url") or e.get("source") or e.get("url")
        if src:
            sources.append(src)

    merged = {}

    # category/country — по большинству
    merged["category"] = pick_majority(fields["category"])
    merged["country"]  = pick_majority(fields["country"])

    # abv — нормализуем и берём большинство
    abv_norms = list(filter(None, (_parse_abv(v if isinstance(v,str) else (v[0] if isinstance(v,list) else v)) for v in fields["abv"])))
    merged["abv"] = pick_majority(abv_norms) or (abv_norms[0] if abv_norms else None)

    # notes — топ по частоте
    merged["tasting_notes"] = merge_notes(fields["tasting_notes"], limit=6)

    # facts — первые уникальные до лимита
    merged["facts"] = dedup_facts(fields["facts"], limit=4)

    # image — лучший по доменному приоритету
    merged["image_url"] = pick_best_image(fields["image_url"])

    # источники (уникальные, до 5)
    uniq = []
    seen = set()
    for s in sources:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
        if len(uniq) >= 5:
            break
    merged["sources"] = uniq
    return merged
