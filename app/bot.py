
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums.parse_mode import ParseMode

from app.settings import settings
from app.middlewares.error_logging import ErrorsLoggingMiddleware
from app.routers import brands as brands_router
from app.routers import main as main_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")

bot = Bot(settings.api_token, parse_mode=ParseMode.HTML)
dp = Dispatcher()
dp.message.middleware(ErrorsLoggingMiddleware())

# Routers
dp.include_router(main_router.router)
dp.include_router(brands_router.router)
