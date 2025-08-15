
import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from app.keyboards.common import main_kb
from app.keyboards.menus import main_menu_kb
from app.services.stats import get_stats, format_activity

ADMIN_IDS = {1294415669}
router = Router()

USER_INFO_PATH = "user_info.json"
try:
    with open(USER_INFO_PATH, "r", encoding="utf-8") as f:
        USER_INFO = json.load(f)
except FileNotFoundError:
    USER_INFO = {}

def save_info() -> None:
    with open(USER_INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(USER_INFO, f, ensure_ascii=False, indent=2)

def ensure_user(u) -> None:
    uid = str(u.id)
    info = USER_INFO.setdefault(uid, {})
    changed = False
    if info.get("username") != u.username:
        info["username"] = u.username
        changed = True
    if info.get("first_name") != u.first_name:
        info["first_name"] = u.first_name
        changed = True
    if info.get("last_name") != u.last_name:
        info["last_name"] = u.last_name
        changed = True
    if changed:
        save_info()

def display_name(uid: int) -> str:
    info = USER_INFO.get(str(uid), {})
    name = (info.get("first_name", "") + " " + info.get("last_name", "")).strip()
    username = info.get("username")
    if username:
        username = f"@{username}"
    else:
        username = ""
    return " ".join(part for part in [name, username] if part).strip() or f"id {uid}"

def format_stats(uid: int) -> str:
    st = get_stats(uid)
    info = USER_INFO.get(str(uid), {})
    phone = info.get("phone", "—")
    header = f"Имя: {display_name(uid)} (id: {uid}, телефон: {phone})"
    categories = ["Виски", "Водка", "Пиво", "Вино", "Ликёр"]
    counts = {c: 0 for c in categories}
    for cat in st["brands"].values():
        counts[cat] = counts.get(cat, 0) + 1
    brand_lines = "\n".join(f"  — {c}: {counts.get(c, 0)}" for c in categories)
    return (
        f"{header}\n"
        f"Лучший результат в Блице: {st['best_blitz']}\n"
        f"Завершено тестов: {st['tests']}\n"
        f"Правильных ответов: {st['points']}\n"
        f"Просмотренные бренды:\n{brand_lines}"
    )

def contact_kb():
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardBuilder().add(
        KeyboardButton(text="Отправить номер", request_contact=True)
    ).as_markup(resize_keyboard=True)

@router.message(Command("start"))
async def start(m: Message):
    ensure_user(m.from_user)
    await m.answer("Главное меню", reply_markup=main_menu_kb())

@router.message(F.text == "📊 Моя статистика")
async def show_stats(m: Message):
    st = get_stats(m.from_user.id)
    last = st["last"] or "—"
    categories = ["Виски", "Водка", "Пиво", "Вино", "Ликёр"]
    counts = {c: 0 for c in categories}
    for cat in st["brands"].values():
        counts[cat] = counts.get(cat, 0) + 1
    brand_lines = "\n".join(f"— {c}: {counts.get(c, 0)}" for c in categories)
    await m.answer(
        f"Пройдено тестов: {st['tests']}\n"
        f"Набрано баллов: {st['points']}\n"
        f"Рекорд в игре \"Верю — не верю\": {st['best_truth']}\n"
        f"Рекорд в игре \"Ассоциации\": {st['best_assoc']}\n"
        f"Рекорд в игре \"Блиц\": {st['best_blitz']}\n"
        "Просмотренные бренды:\n"
        f"{brand_lines}\n"
        f"Последняя активность: {last}",
        reply_markup=main_kb(m.from_user.id in ADMIN_IDS)
    )
