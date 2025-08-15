from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from contextlib import suppress
import json

from app.services import rag
from app.services.personalize import set_pref, get_pref

ADMIN_IDS = {1294415669}  # расширь по необходимости
router = Router()

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

@router.message(Command("reindex"))
async def cmd_reindex(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Только для админов.")
        return
    args = (m.text or "").split()[1:]
    use_sbert = ("--sbert" in args or "-s" in args)
    msg = rag.rebuild_index(prefer_sbert=True if use_sbert else None)
    await m.answer(f"Готово: {msg}")

@router.message(Command("validate_kb"))
async def cmd_validate_kb(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Только для админов.")
        return
    # простая валидация
    issues = []
    def _check_dict(name: str, obj: dict):
        for k, v in obj.items():
            if not isinstance(v, dict):
                issues.append(f"{name}: {k} — не dict")
                continue
            for req in ("category",):
                if not v.get(req):
                    issues.append(f"{name}: {k} — поле '{req}' пустое")
    def _check_list(name: str, arr: list):
        for i, v in enumerate(arr):
            if not isinstance(v, dict):
                issues.append(f"{name}[{i}] — не dict")
                continue
            if not (v.get("brand") or v.get("name")):
                issues.append(f"{name}[{i}] — нет brand/name")
    try:
        cat = json.loads(open("data/catalog.json","r",encoding="utf-8").read())
        if isinstance(cat, dict):
            _check_dict("catalog", cat)
        else:
            _check_list("catalog", cat)
    except Exception as e:
        issues.append(f"catalog.json: ошибка чтения: {e}")
    for fn in ("data/brands_kb.json", "data/ingested_kb.json"):
        try:
            kb = json.loads(open(fn,"r",encoding="utf-8").read())
            if isinstance(kb, list):
                _check_list(fn, kb)
            else:
                _check_dict(fn, kb)
        except Exception as e:
            issues.append(f"{fn}: ошибка чтения: {e}")
    if not issues:
        await m.answer("KB в порядке ✅")
    else:
        text = "Нашёл проблемы:\n• " + "\n• ".join(issues[:80])
        await m.answer(text)

@router.message(Command("lang"))
async def cmd_lang(m: Message):
    parts = (m.text or "").split()
    if len(parts) == 1:
        cur = get_pref(m.from_user.id, "lang", "ru")
        await m.answer(f"Текущий язык интерфейса: <b>{cur}</b>.\nСменить: /lang ru или /lang kk", parse_mode="HTML")
        return
    lang = parts[1].strip().lower()
    if lang not in {"ru","kk"}:
        await m.answer("Поддерживаются: ru, kk")
        return
    set_pref(m.from_user.id, "lang", lang)
    await m.answer(f"Язык сохранён: <b>{lang}</b>", parse_mode="HTML")
