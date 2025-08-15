from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message
from app.services.personalize import set_pref, get_pref

router = Router()

@router.message(F.text == "👤 Профиль")
async def profile_home(m: Message):
    role = get_pref(m.from_user.id, "role", "ТП")
    region = get_pref(m.from_user.id, "region", "Усть‑Каменогорск")
    venue = get_pref(m.from_user.id, "venue", "бар")
    await m.answer(
        f"<b>Профиль</b>\nРоль: {role}\nРегион: {region}\nТип ТТ: {venue}\n"
        f"Команды:\n"
        f"— /role ТП|бармен|управляющий\n"
        f"— /region <город>\n"
        f"— /venue бар|кафе|ресторан|паб"
    )

@router.message(F.text.regexp(r"^/role\s+(.+)$"))
async def set_role(m: Message):
    role = m.text.split(maxsplit=1)[1].strip()
    set_pref(m.from_user.id, "role", role)
    await m.answer(f"Ок, роль: <b>{role}</b>")

@router.message(F.text.regexp(r"^/region\s+(.+)$"))
async def set_region(m: Message):
    region = m.text.split(maxsplit=1)[1].strip()
    set_pref(m.from_user.id, "region", region)
    await m.answer(f"Ок, регион: <b>{region}</b>")

@router.message(F.text.regexp(r"^/venue\s+(.+)$"))
async def set_venue(m: Message):
    venue = m.text.split(maxsplit=1)[1].strip()
    set_pref(m.from_user.id, "venue", venue)
    await m.answer(f"Ок, тип ТТ: <b>{venue}</b>")
