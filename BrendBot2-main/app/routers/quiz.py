from __future__ import annotations
import random, json
from aiogram import Router, F
from aiogram.types import Message
from app.services.stats import inc_points, inc_tests

router = Router()

# Load catalog
try:
    _CAT = json.load(open("data/catalog.json", "r", encoding="utf-8"))
except Exception:
    _CAT = {}

def _pick_brand() -> tuple[str, dict]:
    items = list(_CAT.items())
    if not items:
        return ("", {})
    return random.choice(items)

@router.message(F.text == "🧩 Квиз")
async def start_quiz(m: Message):
    name, card = _pick_brand()
    if not card:
        await m.answer("Пока нет данных для квиза. Добавь бренды в data/catalog.json")
        return
    qtype = random.choice(["country", "abv", "category"])
    if qtype == "country":
        await m.answer(f"Страна происхождения бренда <b>{name}</b>?")
    elif qtype == "abv":
        await m.answer(f"Крепость (ABV) у <b>{name}</b>? (например, 40%)")
    else:
        await m.answer(f"Категория бренда <b>{name}</b>? (виски/водка/пиво/вино/…)")
    # store the expected answer in message thread state memory (minimalistic)
    m.bot.data.setdefault("quiz_state", {})[m.from_user.id] = (qtype, name, card)

@router.message(F.text.regexp(".+"))
async def answer_quiz(m: Message):
    state = m.bot.data.get("quiz_state", {}).get(m.from_user.id)
    if not state:
        return
    qtype, name, card = state
    user = (m.text or "").strip().lower()
    truth = ""
    if qtype == "country":
        truth = (card.get("country") or "").lower()
    elif qtype == "abv":
        truth = (card.get("abv") or "").lower()
    else:
        truth = (card.get("category") or "").lower()
    if not truth:
        await m.answer("Нет правильного ответа в базе — обнови data/catalog.json")
        return
    # naive check
    ok = truth.split("%")[0] in user
    if ok:
        inc_points(m.from_user.id, 1)
        inc_tests(m.from_user.id, 1)
        await m.answer(f"Верно ✅
Правильный ответ: <b>{truth}</b>
Ещё раз — нажми «🧩 Квиз».")
    else:
        await m.answer(f"Не совсем. Правильный ответ: <b>{truth}</b>
Попробуй ещё: «🧩 Квиз».")
    # clear state
    m.bot.data.get("quiz_state", {}).pop(m.from_user.id, None)
