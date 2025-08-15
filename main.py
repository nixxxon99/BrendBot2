
import asyncio
from flask import Flask, request, Response
from aiogram.types import Update

from app.settings import settings
from app.bot import bot, dp

WEBHOOK_PATH = f"/webhook/{settings.webhook_secret}"
WEBHOOK_URL = settings.webhook_url + WEBHOOK_PATH if settings.webhook_url else ""

app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

@app.post(WEBHOOK_PATH)
def handle_webhook():
    update = Update.model_validate(request.json)
    loop.create_task(dp.feed_update(bot, update))
    return Response()

@app.get("/")
def hello():
    return "Bot is alive"

async def main():
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        print("⚠️ WEBHOOK_URL не задан. Укажи WEBHOOK_URL в .env, чтобы включить вебхук.")
    import hypercorn.asyncio
    import hypercorn.config
    config = hypercorn.config.Config()
    config.bind = ["0.0.0.0:10000"]
    await hypercorn.asyncio.serve(app, config)

if __name__ == "__main__":
    loop.run_until_complete(main())
