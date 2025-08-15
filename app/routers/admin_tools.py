from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services import rag

router = Router()
ADMIN_IDS = {1294415669}

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

@router.message(Command("reindex"))
async def cmd_reindex(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Только для админов."); return
    msg = rag.rebuild_index()
    await m.answer(f"Готово: {msg}")

@router.message(Command("validate_kb"))
async def validate_kb(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Только для админов."); return
    import json
    issues = []
    for fn in ("data/catalog.json","data/brands_kb.json","data/ingested_kb.json"):
        try:
            obj = json.loads(open(fn,"r",encoding="utf-8").read())
            _ = obj is not None
        except Exception as e:
            issues.append(f"{fn}: {e}")
    await m.answer("KB в порядке ✅" if not issues else "Нашёл проблемы:\n• " + "\n• ".join(issues))

@router.message(Command("reload_portfolio"))
async def reload_portfolio(m: Message):
    if not _is_admin(m.from_user.id):
        await m.answer("Только для админов."); return
    try:
        from app.services.portfolio import _names_cache, load_names
        _names_cache.clear(); load_names()
        await m.answer("Портфель перегружен ✅")
    except Exception as e:
        await m.answer(f"Не удалось перегрузить портфель: {e}")

@router.message(Command("lang"))
async def cmd_lang(m: Message):
    parts = (m.text or "").split()
    if len(parts)==1:
        await m.answer("Использование: /lang ru|kk"); return
    lang = parts[1].lower()
    if lang not in {"ru","kk"}:
        await m.answer("Поддерживаются: ru, kk"); return
    await m.answer(f"Язык сохранён: {lang}")
