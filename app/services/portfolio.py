from __future__ import annotations
from typing import List, Optional, Set
from pathlib import Path
import csv, io, re
CANDIDATES = [Path("data/portfolio.csv")]
CANDIDATES.extend(sorted(Path("data").glob("*.csv")))
def _open_any(p: Path):
    for enc in ["cp866","cp1251","utf-8-sig","utf-8","latin-1"]:
        try:
            txt = p.read_bytes().decode(enc)
            try:
                dialect = csv.Sniffer().sniff(txt[:5000], delimiters=";,|	")
                delim = dialect.delimiter
            except Exception:
                delim = ";"
            rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
            return rows
        except Exception:
            continue
    return []
def _find_name_col(rows: List[List[str]]) -> Optional[int]:
    for row in rows[:20]:
        for ci, val in enumerate(row):
            if isinstance(val,str) and "наимен" in val.lower():
                return ci
    max_ci, max_nonempty = None, 0
    for ci in range(max(len(r) for r in rows[:50])):
        nonempty = sum(1 for r in rows if ci < len(r) and (r[ci] or "").strip())
        if nonempty > max_nonempty:
            max_nonempty = nonempty; max_ci = ci
    return max_ci
def _clean_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'(?i)\b(\d+[\.,]?\d*\s*(л|l|ml|мл|cl)|\d+\s*л)\b', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()
_names_cache: Set[str] = set()
def load_names() -> Set[str]:
    global _names_cache
    if _names_cache: return _names_cache
    all_rows = []
    for c in CANDIDATES:
        if c.exists():
            try: rows = _open_any(c); 
            except Exception: rows = []
            if rows: all_rows.extend(rows)
    if not all_rows: _names_cache=set(); return _names_cache
    ci = _find_name_col(all_rows) or 0
    names = []
    header_passed = False
    for r in all_rows:
        if ci>=len(r): continue
        val = (r[ci] or "").strip()
        if not val: continue
        if not header_passed and "наимен" in val.lower():
            header_passed=True; continue
        names.append(_clean_name(val))
    _names_cache = {n for n in names if n}
    return _names_cache
def in_portfolio(query: str, threshold: int = 90) -> bool:
    from rapidfuzz import fuzz
    q = _clean_name(query).lower()
    if not q: return False
    names = load_names()
    if q in {n.lower() for n in names}: return True
    for n in names:
        if fuzz.partial_ratio(q, n.lower()) >= threshold: return True
    return False
def suggest_alternatives(query: str, maxn: int = 5) -> List[str]:
    from rapidfuzz import fuzz, process
    from app.services.brands import by_category, all_brand_names
    q = _clean_name(query).lower()
    if not load_names(): return all_brand_names()[:maxn]
    alt_map = {
        "jameson": ["Tullamore D.E.W. Original", "Grant's Triple Wood"],
        "джеймсон": ["Tullamore D.E.W. Original", "Grant's Triple Wood"],
        "bushmills": ["Tullamore D.E.W. Original"], "бушмилс": ["Tullamore D.E.W. Original"],
        "chivas": ["Grant's Triple Wood", "Monkey Shoulder Blended Malt"],
        "ballantine": ["Grant's Triple Wood"], "johnnie walker": ["Grant's Triple Wood", "Monkey Shoulder Blended Malt"],
        "absolut": ["Reyka Vodka", "Finlandia"], "абсолют": ["Reyka Vodka", "Finlandia"],
        "beluga": ["Finlandia", "Reyka Vodka"], "grey goose": ["Reyka Vodka", "Finlandia"],
        "bacardi": ["Sailor Jerry Spiced Rum"], "havana": ["Sailor Jerry Spiced Rum"],
        "beefeater": ["Hendrick's Gin"], "tanqueray": ["Hendrick's Gin"], "bombay": ["Hendrick's Gin"],
        "jägermeister": ["Jägermeister"], "ягер": ["Jägermeister"],
    }
    for k, vals in alt_map.items():
        if k in q: return vals[:maxn]
    kw_map = {"виски":"Виски","whisky":"Виски","whiskey":"Виски","ром":"Ром","джин":"Джин","водка":"Водка","tequila":"Текила","текила":"Текила","beer":"Пиво","пиво":"Пиво","liqueur":"Ликёр","ликер":"Ликёр","ликёр":"Ликёр"}
    for kw, cat in kw_map.items():
        if kw in q: 
            from app.services.brands import by_category
            return by_category(cat, limit=maxn) or all_brand_names()[:maxn]
    all_names = all_brand_names()
    best = process.extract(q, all_names, scorer=fuzz.token_sort_ratio, limit=maxn)
    return [name for name, score, _ in best]
