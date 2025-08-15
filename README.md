
# BrendBot (refactored)

**Что нового:**
- Модульная структура (routers / services / keyboards / middlewares / data)
- Каталог брендов в `data/catalog.json` вместо хардкода
- Фаззи-поиск по брендам и синонимам (`rapidfuzz`)
- Безопасные настройки через `.env` и `pydantic-settings`
- Запасной in-memory Redis, если Redis недоступен

## Запуск

1. Создай `.env` на основе `.env.example` и заполни токены:
```
cp .env.example .env
# затем отредактируй .env
```

2. Установи зависимости:
```
pip install -r requirements.txt
```

3. Запусти (webhook):
```
python main.py
```

> По умолчанию сервер слушает `0.0.0.0:10000`. Укажи `WEBHOOK_URL` (например, с Render/NGROK), чтобы бот установил вебхук.

## Как добавить бренд
Добавь объект в `data/catalog.json`:
```json
"New Brand": {
  "category": "Виски",
  "photo_file_id": "FILE_ID",
  "caption": "<b>New Brand</b>\n• Пункты описания",
  "aliases": ["псевдоним1", "alias2"]
}
```
Категории: `Виски`, `Водка`, `Пиво`, `Вино`, `Ликёр`.

## Примечание
Игры/тесты и AI-помощник пока не перенесены — их можно вынести отдельными роутерами по аналогии.
