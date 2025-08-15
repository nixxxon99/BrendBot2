# app/services/playbook.py
from __future__ import annotations
from typing import Optional

SEASONS = {
    1: "зима", 2: "зима", 12: "зима",
    3: "весна", 4: "весна", 5: "весна",
    6: "лето", 7: "лето", 8: "лето",
    9: "осень", 10: "осень", 11: "осень",
}

def current_season(month: Optional[int] = None) -> str:
    import datetime as _dt
    m = month or _dt.datetime.now().month
    return SEASONS.get(m, "сезон")

def gen_playbook(brand: str, region: str|None = None, venue_type: str|None = None, month: Optional[int] = None) -> str:
    season = current_season(month)
    region_hint = ""
    if region:
        region_hint = f"Регион: {region}. "
    venue_hint = ""
    if venue_type:
        venue_hint = f"Тип ТТ: {venue_type}. "
    # простые правила
    upsell = []
    cross = []
    pitch = []
    brand_l = (brand or "").lower()

    if "monkey" in brand_l:
        upsell = ["Glenfiddich 12 как апгрейд вкуса 'зелёного яблока' и ванили."]
        cross = ["Апсейл: коктейли на виски-хайболле (лимон, сода).", "Сырная тарелка/вяленое мясо."]
        pitch = ["Подчеркни премиальность и барную историю, предложи 'первый виски-хайболл' для гостей."]
    elif "glenfiddich" in brand_l:
        upsell = ["Выше — Glenfiddich IPA/Fire & Cane (лимитка для знатоков)."]
        cross = ["Сигара-корнер, если есть; десерты с карамелью."]
        pitch = ["Упор на 'single malt №1 в мире' и мягкость вкуса."]
    elif "paulaner" in brand_l:
        upsell = ["Seasonal/Weissbier Spezial.", "Переход из lager в weiss."]
        cross = ["Прецели, сосиски, солёные снеки."]
        pitch = ["'Немецкий пшеничный №1' — расскажи про дрожжевой профиль и свежесть."]
    elif "reyka" in brand_l or "рейка" in brand_l:
        upsell = ["Finlandia как массовая альтернатива (если ценочувствительность)."]
        cross = ["Тоник, лайм; подача 'кристально холодной' как фишка Исландии."]
        pitch = ["Сделай акцент на чистоте и происхождении воды/лаве."]

    # сезонные приёмы
    if season == "лето":
        pitch.append("Летом дави на лёгкие коктейли и хайболлы, холодная подача — must.")
    elif season == "зима":
        pitch.append("Зимой продавай согревающие напитки, дополнение — десерты и пряные закуски.")

    # тип заведения
    if (venue_type or "").lower() in {"кафе","бар","паб"}:
        cross.append("Предложи сет 'напиток + закуска' — это поднимает средний чек.")
    elif (venue_type or "").lower() in {"ресторан"}:
        cross.append("Согласуй фуд-пейринги с шефом, вынеси рекомендации в карту.")

    return (
        f"<b>Sales Playbook — {brand}</b>\n"
        f"{region_hint}{venue_hint}Сезон: {season}.\n\n"
        f"• Pitch: {' '.join(pitch) or 'Сделай упор на ценность бренда и подачу.'}\n"
        f"• Upsell: {'; '.join(upsell) or 'Предложи следующий уровень бренда/лимитки.'}\n"
        f"• Cross-sell: {'; '.join(cross) or 'Подберём закуску и коктейль под вкус гостя.'}\n"
    )
