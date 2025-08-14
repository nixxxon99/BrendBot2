# app/services/brands.py
# Поддержка JSON в виде СПИСКА карточек [{...}, {...}] или словаря {name: {...}}
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from difflib import SequenceMatcher

# Где искать базу
SOURCE_FILES = [Path("data/catalog.json"), Path("data/brands_kb.json")]

# ---------- загрузка базы ----------
def _load_raw() -> List[Dict[str, Any]]:
    for p in SOURCE_FILES:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                items: List[Dict[str, Any]] = []
                for k, v in data.items():
                    if isinstance(v, dict):
                        d = dict(v)
                        d.setdefault("brand", k)
                        items.append(d)
                print(f"[brands] Loaded {len(items)} items from {p} (dict)")
                return items
            elif isinstance(data, list):
                items = [x for x in data if isinstance(x, dict)]
                print(f"[brands] Loaded {len(items)} items from {p} (list)")
                return items
            else:
                print(f"[brands] Unsupported JSON root in {p}: {type(data)}")
                return []
    print("[brands] No data file found")
    return []

RAW: List[Dict[str, Any]] = _load_raw()

# ---------- нормализация и индексация ----------
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    # убрать литражи/объёмы
    s = re.sub(r"\b(\d+[.,]?\d*)\s*(l|л|литр(а|ов)?|ml|мл)\b", " ", s)
    # убрать «голые» числа (0.7, 12 и т.д.)
    s = re.sub(r"\b(0\.\d+|[1-9]\d*)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

NAME_INDEX: Dict[str, Dict[str, Any]] = {}  # norm(бренд) -> запись
ALIASES: Dict[str, str] = {}                # norm(алиас) -> канон. имя бренда
ALL_CANON: List[str] = []                   # список каноничных имён

def _build_indexes() -> None:
    NAME_INDEX.clear(); ALIASES.clear(); ALL_CANON.clear()
    for entry in RAW:
        brand = (entry.get("brand") or "").strip()
        if not brand:
            continue
        key = _norm(brand)
        NAME_INDEX[key] = entry
        ALL_CANON.append(brand)
        for alias in entry.get("aliases", []) or []:
            akey = _norm(alias)
            if akey and akey not in NAME_INDEX:
                ALIASES[akey] = brand

_build_indexes()

# ---------- помощники ----------
def _build_caption(entry: Dict[str, Any]) -> str:
    brand   = entry.get("brand", "")
    cat     = entry.get("category", "")
    country = entry.get("country", "")
    abv     = entry.get("abv", "")
    notes   = entry.get("tasting_notes", "")
    facts   = entry.get("production_facts", "")
    sell    = entry.get("sales_script", "")

    head = f"<b>{brand}</b>"
    meta = " · ".join([x for x in [cat, country, abv] if x])
    if meta: head += f"\n<i>{meta}</i>"

    parts = [head]
    if notes: parts.append(notes)
    if facts: parts.append(facts)
    if sell:  parts.append(f"<b>Как продавать:</b> {sell}")

    caption = "\n".join(parts)
    caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    if len(caption) > 1000:
        caption = caption[:997] + "…"
    return caption

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# ---------- ПУБЛИЧНОЕ API (совместимо со старым кодом) ----------
def exact_lookup(text: str) -> Optional[str]:
    key = _norm(text)
    if key in NAME_INDEX:
        return NAME_INDEX[key].get("brand")
    if key in ALIASES:
        return ALIASES[key]
    return None

def get_brand(name: str) -> Optional[Dict[str, Any]]:
    canon = exact_lookup(name) or name
    entry = NAME_INDEX.get(_norm(canon))
    if not entry:
        return None
    return {
        "name": entry.get("brand", canon),
        "caption": _build_caption(entry),
        "photo_file_id": entry.get("photo_file_id"),  # может быть None
        "category": entry.get("category", "")
    }

def by_category(cat_query: str, limit: int = 50) -> List[str]:
    q = _norm(cat_query)
    out: List[str] = []
    for entry in NAME_INDEX.values():
        cat = _norm(entry.get("category", ""))
        if q and q in cat:
            b = entry.get("brand")
            if b:
                out.append(b)
                if len(out) >= limit:
                    break
    return sorted(set(out))

def fuzzy_suggest(text: str, limit: int = 10) -> List[Tuple[str, float]]:
    t = (text or "").strip()
    if not t:
        return []
    t_norm = _norm(t)
    candidates = set(ALL_CANON)
    for _, canon in ALIASES.items():
        candidates.add(canon)

    # быстрые подстрочные попадания
    hits = [(c, 1.0) for c in candidates if t_norm and t_norm in _norm(c)]

    # похожесть
    scored: List[Tuple[str, float]] = []
    for c in candidates:
        s = _similar(t.lower(), c.lower())
        if s >= 0.6:
            scored.append((c, s))

    by_name: Dict[str, float] = {n: s for n, s in scored}
    for n, s in hits:
        by_name[n] = max(by_name.get(n, 0.0), s)

    return sorted(by_name.items(), key=lambda x: x[1], reverse=True)[:limit]

# ---------- РУССКИЕ СИНОНИМЫ (если где-то импорт русскими именами) ----------
по_категории = by_category
точный_поиск = exact_lookup
нечеткий_подсказка = fuzzy_suggest
получить_бренд = get_brand
