
import logging
from aiogram import BaseMiddleware
from aiogram.types import Update

class ErrorsLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data):
        try:
            return await handler(event, data)
        except Exception:
            logging.exception("Unhandled error", extra={
                "update": getattr(event, "model_dump", lambda **_: str(event))()
            })
            # Optionally: notify admins here
