# app/services/portfolio.py
from __future__ import annotations
from typing import List, Tuple, Dict, Optional, Set
from pathlib import Path
import csv, io

# Лучше иметь конкретное имя файла, но поддержим авто-поиск
CANDIDATES = [
    Path("data/portfolio.csv"),
]

# Попробуем найти любые .csv с колонкой "Наименование"
CANDIDATES.extend(sorted(Path("data").glob("*.csv")))

def _open_any(p: Path):
    # пробуем разные кодировки и разделители
    encodings = ["cp866", "cp1251", "utf-8-sig", "utf-8", "latin-1"]
    for enc in encodings:
        try:
            txt = p.read_bytes().decode(enc)
            # определим разделитель
            try:
                dialect = csv.Sniffer().sniff(txt[:5000], delimiters=";,|\t")
                delim = dialect.delimiter
            except Exception:
                delim = ";"
            rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
            return rows
        except Exception:
            continue
    return []

def _find_name_col(rows: List[List[str]]) -> Optional[int]:
    for ri, row in enumerate(rows[:20]):
        for ci, val in enumerate(row):
            if isinstance(val, str) and "наимен" in val.lower():
                return ci
    # иногда "Наименование товара" на строке 5
    # если не нашли — попробуем эмпирически выбрать «самую полную» колонку
    max_ci, max_nonempty = None, 0
    for ci in range(max(len(r) for r in rows[:50])):
        nonempty = sum(1 for r in rows if ci < len(r) and (r[ci] or "").strip())
        if nonempty > max_nonempty:
            max_nonempty = nonempty
            max_ci = ci
    return max_ci

def _clean_name(s: str) -> str:
    s = (s or "").strip()
    # убираем объёмы и лишние символы
    import re
    s = re.sub(r'(?i)\\b(\\d+[\\.,]?\\d*\\s*(л|l|ml|мл|cl)|\\d+\\s*л)\\b', '', s)
    s = re.sub(r'\\s+', ' ', s)
    return s.strip()

_names_cache: Set[str] = set()

def load_names() -> Set[str]:
    global _names_cache
    if _names_cache:
        return _names_cache
    all_rows: List[List[str]] = []
    for cand in CANDIDATES:
        if cand.exists():
            try:
                rows = _open_any(cand)
                if rows:
                    all_rows.extend(rows)
            except Exception:
                pass
    if not all_rows:
        _names_cache = set()
        return _names_cache
    ci = _find_name_col(all_rows) or 0
    names = []
    header_passed = False
    for r in all_rows:
        if ci >= len(r):
            continue
        val = (r[ci] or "").strip()
        if not val:
            continue
        # пропускаем строку заголовков
        if not header_passed and "наимен" in val.lower():
            header_passed = True
            continue
        names.append(_clean_name(val))
    _names_cache = {n for n in names if n}
    return _names_cache

# === проверка принадлежности бренда нашему прайсу ===
def in_portfolio(query: str, threshold: int = 90) -> bool:
    from rapidfuzz import fuzz
    q = _clean_name(query).lower()
    if not q:
        return False
    names = load_names()
    # точное и частичное
    if q in {n.lower() for n in names}:
        return True
    # частичное совпадение
    for n in names:
        if fuzz.partial_ratio(q, n.lower()) >= threshold:
            return True
    return False

# === подсказка альтернатив из нашего портфеля ===
def suggest_alternatives(query: str, maxn: int = 5) -> List[str]:
    """
    Если brand не наш — возвращаем список наших позиций-альтернатив.
    Сначала проверяем словарь сопоставлений, затем — ближайшие по категории/лексике (через catalog.json).
    """
    from rapidfuzz import fuzz, process
    from app.services.brands import by_category, all_brand_names
    q = _clean_name(query).lower()
    names_portfolio = list(load_names())
    if not names_portfolio:
        # fallback на brands из каталога
        return all_brand_names()[:maxn]

    # 1) Хардкод популярных «вражеских» брендов → наши альтернативы
    alt_map = {
        "jameson": ["Tullamore D.E.W. Original", "Grant's Triple Wood"],
        "джеймсон": ["Tullamore D.E.W. Original", "Grant's Triple Wood"],
        "bushmills": ["Tullamore D.E.W. Original"],
        "бушмилс": ["Tullamore D.E.W. Original"],
        "chivas": ["Grant's Triple Wood", "Monkey Shoulder Blended Malt"],
        "ballantine": ["Grant's Triple Wood"],
        "johnnie walker": ["Grant's Triple Wood", "Monkey Shoulder Blended Malt"],
        "absolut": ["Reyka Vodka", "Finlandia"],
        "абсолют": ["Reyka Vodka", "Finlandia"],
        "beluga": ["Finlandia", "Reyka Vodka"],
        "grey goose": ["Reyka Vodka", "Finlandia"],
        "bacardi": ["Sailor Jerry Spiced Rum"],
        "havanna": ["Sailor Jerry Spiced Rum"],
        "havana": ["Sailor Jerry Spiced Rum"],
        "beefeater": ["Hendrick's Gin"],
        "tanqueray": ["Hendrick's Gin"],
        "bombay": ["Hendrick's Gin"],
        "jägermeister": ["Jägermeister"],
        "ягер": ["Jägermeister"],
        "jäger": ["Jägermeister"],
    }
    for k, vals in alt_map.items():
        if k in q:
            return vals[:maxn]

    # 2) эвристика по ключевым словам категории
    kw_map = {
        "виски": "Виски",
        "whisky": "Виски",
        "whiskey": "Виски",
        "ирланд": "Ирландский",
        "ирландский": "Ирландский",
        "скотч": "Виски",
        "ром": "Ром",
        "джин": "Джин",
        "tequila": "Текила",
        "текила": "Текила",
        "водка": "Водка",
        "liqueur": "Ликёр",
        "ликер": "Ликёр",
        "ликёр": "Ликёр",
        "пиво": "Пиво",
        "beer": "Пиво",
    }
    for kw, cat in kw_map.items():
        if kw in q:
            opts = by_category(cat, limit=maxn) or all_brand_names()[:maxn]
            return opts

    # 3) Фоллбек — ближайшие по лексике из нашего каталога (не из CSV)
    all_names = all_brand_names()
    best = process.extract(q, all_names, scorer=fuzz.token_sort_ratio, limit=maxn)
    return [name for name, score, _ in best]
