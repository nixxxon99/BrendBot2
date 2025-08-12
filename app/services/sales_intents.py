import re
from typing import Optional, Dict

# Триггеры «как продавать»
_PATTERNS = [
    r"как\s+продать", r"как\s+предложить", r"что\s+говорить",
    r"скрипт\s+продаж", r"аргумент(ы)?\s+для\s+продаж",
    r"возражен(ие|ия|ий)", r"апс(е|э)лл|upsell|допродаж"
]

# Простейшее определение канала/формата точки
_OUTLETS: Dict[str, str] = {
    "тт": "budget_tt", "дискаунтер": "discount", "магазин": "retail",
    "супермаркет": "supermarket", "бар": "bar", "ресторан": "restaurant",
    "кафе": "cafe", "клуб": "club"
}

def detect_sales_intent(text: str) -> Optional[dict]:
    t = (text or "").lower()
    if not t:
        return None
    if not any(re.search(p, t) for p in _PATTERNS):
        return None
    outlet = None
    for k, v in _OUTLETS.items():
        if k in t:
            outlet = v
            break
    return {"outlet": outlet}
