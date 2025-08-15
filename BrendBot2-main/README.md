
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


---
## Smart upgrade (август 2025)
- RAG семантический поиск (`app/services/rag.py`) — использует OpenAI embeddings (если есть `OPENAI_API_KEY`) или `rapidfuzz` как резерв.
- Sales Playbook (`app/services/playbook.py`) — генерация подсказок по апсейлу/кросс‑сейлу.
- Персонализация (`app/services/personalize.py`) — роль/регион/тип ТТ (Redis либо `data/user_prefs.json`).
- Фото‑анализ (`app/services/vision.py`, `app/routers/vision.py`) — заглушка, легко подключить Google Vision / CLIP.
- Квизы (`app/routers/quiz.py`) — быстрые вопросы по брендам из `data/catalog.json`.

### Переменные окружения
- `OPENAI_API_KEY` — для семантических эмбеддингов.
- `REDIS_URL` или локальный Redis (опционально).

### Как включить Vision
Открой `app/services/vision.py` и подключи библиотеку (например, `google-cloud-vision`). Верни список брендов по распознаванию текста/этикеток — остальное уже готово.


### Семантический индекс без OpenAI
- Индекс хранится в `data/` и строится локально.
- По умолчанию используется TF‑IDF (scikit‑learn). Если установлен `sentence-transformers`, можно включить SBERT через `SBERT_MODEL`.
- Собрать индекс: `python tools/build_semantic_index.py` (TF‑IDF) или `python tools/build_semantic_index.py --sbert`.


### Админ и утилиты
- `/reindex [--sbert]` — пересборка семантического индекса (TF‑IDF или SBERT).
- `/validate_kb` — простая валидация KB файлов.
- `/lang ru|kk` — переключение языка интерфейса (персональная настройка).

### POSM-трекер
- Кнопка **📦 POSM списание** — мини-мастер заполнения.
- Экспорт CSV: команда `/posm_export`.

### Экспорт карточек в PDF
- Кнопка в карточке **Экспорт PDF** — создаёт PDF в `data/export/` и отправляет в чат.

### Быстрые действия в карточке
- **Как продавать** — локальный playbook с учётом профиля.
- **Альтернатива** — похожие бренды.

## 🚀 Развёртывание на Render (Blueprint)
1) Форкни/загрузи репозиторий с этим кодом.
2) Включи **Blueprints** в Render и нажми **New +** → **Blueprint** → укажи ссылку на репозиторий.
3) Render считает `render.yaml` и создаст **Worker**‑сервис.
4) В сервисе → **Environment** добавь:
   - `BOT_TOKEN` — токен бота из @BotFather
   - `TIMEZONE` — `Asia/Almaty` (можно оставить по умолчанию)
5) Нажми **Deploy**. Всё!

Альтернатива: создайте Worker вручную и укажи Start Command: `python main.py`.
