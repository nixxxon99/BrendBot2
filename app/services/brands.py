
import json
import unicodedata
from pathlib import Path
from typing import Dict, Any, List, Tuple

from rapidfuzz import process, fuzz

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "catalog.json"

def normalize(text: str) -> str:
    # lower, strip, remove punctuation/spaces, basic transliteration-like normalize
    text = unicodedata.normalize("NFKD", text).lower()
    return "".join(ch for ch in text if ch.isalnum())

def load_catalog() -> Dict[str, Any]:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CATALOG = load_catalog()

# Build alias map
ALIAS_MAP: Dict[str, str] = {}
for name, item in CATALOG.items():
    ALIAS_MAP[normalize(name)] = name
    for a in item.get("aliases", []):
        ALIAS_MAP[normalize(a)] = name

def categories() -> List[str]:
    cats = set()
    for item in CATALOG.values():
        cats.add(item["category"])
    return sorted(cats)

def by_category(cat: str) -> List[str]:
    return [name for name, item in CATALOG.items() if item["category"] == cat]

def exact_lookup(query: str) -> str | None:
    key = normalize(query)
    return ALIAS_MAP.get(key)

def fuzzy_suggest(query: str, limit: int = 6) -> List[Tuple[str, int]]:
    universe = list(CATALOG.keys())
    # combine names + aliases for stronger recall
    searchable = []
    for name, item in CATALOG.items():
        searchable.append(name)
        searchable.extend(item.get("aliases", []))
    # RapidFuzz returns (match, score, idx) — we keep unique brand names
    results = process.extract(query, searchable, scorer=fuzz.WRatio, limit=20)
    seen = set()
    out: List[Tuple[str, int]] = []
    for cand, score, _ in results:
        brand_name = next((name for name, item in CATALOG.items()
                           if cand == name or cand in item.get("aliases", [])), None)
        if brand_name and brand_name not in seen:
            seen.add(brand_name)
            out.append((brand_name, int(score)))
        if len(out) >= limit:
            break
    return out

def get_brand(name: str) -> Dict[str, Any] | None:
    return CATALOG.get(name)
