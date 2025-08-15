# app/services/knowledge.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pathlib import Path
import json, re

_KB_PATH = Path("data/brands_kb.json")

try:
    _KB: List[Dict[str, Any]] = json.loads(_KB_PATH.read_text(encoding="utf-8"))
    if not isinstance(_KB, list):
        _KB = []
except Exception:
    _KB = []

def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def find_record(brand_or_query: str) -> Optional[Dict[str, Any]]:
    """Поиск записи точным названием, алиасом, затем по вхождению."""
    q = _norm(brand_or_query)
    if not q:
        return None

    # 1) точное совпадение по полю brand
    for r in _KB:
        if _norm(r.get("brand")) == q:
            return r

    # 2) точное совпадение по алиасам
    for r in _KB:
        for a in r.get("aliases", []) or []:
            if _norm(a) == q:
                return r

    # 3) по вхождению в brand/aliases/ключевые поля
    for r in _KB:
        hay = " ".join([
            r.get("brand") or "",
            " ".join(r.get("aliases", []) or []),
            r.get("category") or "",
            r.get("country") or "",
            r.get("tasting_notes") or "",
            r.get("production_facts") or "",
            r.get("serve") or "",
        ])
        if _norm(hay).find(q) != -1 or q.find(_norm(r.get("brand"))) != -1:
            return r

    return None

def build_caption_from_kb(r: Dict[str, Any]) -> str:
    """Рендер локальной карточки в HTML (валидной для Telegram)."""
    def _srcs(urls: List[str] | None) -> str:
        if not urls:
            return ""
        tags = []
        i = 1
        for u in urls:
            if u:
                tags.append(f"<a href='{u}'>[{i}]</a>")
                i += 1
            if i > 3:
                break
        return " ".join(tags)

    brand = r.get("brand", "Без названия")
    cat = r.get("category") or "нет данных"
    country = r.get("country") or "нет данных"
    abv = r.get("abv") or "нет данных"

    lines: List[str] = []
    lines.append(f"<b>{brand}</b>")
    lines.append(f"• Категория / страна / крепость: {cat} / {country} / {abv}")

    if r.get("tasting_notes"):
        lines.append(f"• Профиль вкуса/ароматики: {r['tasting_notes']}")
    if r.get("serve"):
        lines.append(f"• Подача: {r['serve']}")
    if r.get("food_pairing"):
        lines.append(f"• С чем сочетается: {r['food_pairing']}")
    if r.get("cocktails") and r["cocktails"] != "—":
        lines.append(f"• Коктейли: {r['cocktails']}")
    if r.get("production_facts"):
        lines.append(f"• Факты: {r['production_facts']}")
    if r.get("sales_script"):
        lines.append(f"• Скрипт продажи: {r['sales_script']}")

    src = _srcs(r.get("sources"))
    if src:
        lines.append(f"Источники: {src}")

    return "\n".join(lines)
