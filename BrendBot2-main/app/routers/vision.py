from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message
from app.services.vision import recognize_brands_from_bytes
from app.services.brands import fuzzy_suggest

router = Router()

@router.message(F.text == "📸 Фото-анализ")
async def info(m: Message):
    await m.answer("Пришли фото полки или бутылки — попробую распознать бренды и предложить аналоги.")

@router.message(F.photo)
async def handle_photo(m: Message):
    p = m.photo[-1]
    file = await m.bot.get_file(p.file_id)
    file_bytes = await m.bot.download_file(file.file_path)
    data = file_bytes.read()
    brands = recognize_brands_from_bytes(data)
    if not brands:
        await m.answer("Пока не удалось распознать бренды (модуль Vision не активирован). Я добавил инфраструктуру — подключи Google Vision/CLIP в app/services/vision.py.")
        return
    # show suggestions
    lines = []
    for b in brands[:5]:
        lines.append(f"• {b}")
        sugg = fuzzy_suggest(b)
        if sugg:
            lines.append(f"  ↳ альтернатива: {sugg[0]}")
    await m.answer("\n".join(lines) if lines else "Ничего не нашёл на фото.")
