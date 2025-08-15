# app/services/sales_intents.py
from __future__ import annotations
from typing import Optional, Tuple, List
import re

from app.services.brands import exact_lookup, fuzzy_suggest, by_category

# Интент "как продать"
_SALES_PAT = re.compile(r'\b(как\s+продать|как\s+продавать|как\s+предложить|скрипт(?:\s+продаж)?|sales)\b', re.IGNORECASE)

# Куда продаём (канал)
_OUTLET_MAP: List[tuple[str, str]] = [
    ("horeca", r'\b(horeca|бар|паб|ресторан|кафе|караоке|отель)\b'),
    ("retail", r'\b(розниц|магазин|ритейл|супермаркет|полк|сеть)\b'),
    ("ecom",   r'\b(e-?com|онлайн|интернет|маркетплейс|доставка)\b'),
    ("dutyfree", r'\b(duty\s*-?\s*free|дьюти)\b'),
]

# Категории для "любой/неважно какой ..."
_CAT_WORDS = {
    "виски": "Виски",
    "джин": "Джин",
    "ром": "Ром",
    "водка": "Водка",
    "ликёр": "Ликёр",
    "ликер": "Ликёр",
    "текила": "Текила",
    "бренди": "Бренди",
    "коньяк": "Коньяк",
    "вино": "Вино",
    "пиво": "Пиво",
}

_ANY_PAT = re.compile(r'\b(любой|неважно|какой\s+угодно)\b', re.IGNORECASE)

def detect_sales_intent(text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Возвращает: (это «как продать»?, канал/horeca|retail|ecom|dutyfree|None, бренд-строка|None)
    Бренд ищем по exact, иначе fuzzy по тексту без ключевых слов.
    """
    if not text:
        return (False, None, None)
    if not _SALES_PAT.search(text):
        return (False, None, None)

    lower = text.lower()

    outlet = None
    for key, pat in _OUTLET_MAP:
        if re.search(pat, lower):
            outlet = key
            break

    # уберём маркерные слова, чтобы не мешали распознаванию бренда
    brand_area = _SALES_PAT.sub(" ", lower).strip()

    brand = exact_lookup(brand_area)
    if not brand:
        sugg = fuzzy_suggest(brand_area, limit=1)
        if sugg:
            brand = sugg[0][0]

    return (True, outlet, brand)

def suggest_any_in_category(text: str) -> Optional[Tuple[str, List[str]]]:
    """
    Если пользователь пишет «любой/неважно какой <категория>», вернём (категория, топ-бренды из этой категории).
    """
    if not text or not _ANY_PAT.search(text):
        return None
    lower = text.lower()
    for k, display in _CAT_WORDS.items():
        if k in lower:
            names = by_category(display, limit=12)  # возьмём до 12 имён
            if names:
                return (display, names)
    return None
