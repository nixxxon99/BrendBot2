# app/settings.py
from __future__ import annotations
import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


def _split_domains(val: str) -> List[str]:
    return [d.strip().lower().lstrip(".") for d in (val or "").split(",") if d.strip()]


class Settings(BaseSettings):
    # Бот / инфраструктура
    api_token: str = Field(alias="API_TOKEN")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    webhook_secret: str = Field(default="MARS", alias="WEBHOOK_SECRET")
    tz: str = Field(default=os.getenv("TZ", "Asia/Almaty"), alias="TZ")

    # Ключи для LLM / поисков
    bing_api_key: str | None = Field(default=None, alias="BING_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # Gemini (Google AI Studio)
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")  # фолбэк

    # Google Programmable Search (CSE)
    google_cse_key: str | None = Field(default=None, alias="GOOGLE_CSE_KEY")
    google_cse_cx: str | None = Field(default=None, alias="GOOGLE_CSE_CX")

    # Разрешённые домены для веб-поиска (через запятую)
    search_allowed_domains: str = Field(
        default="winestyle.ru,luxalcomarket.kz,decanter.ru,newxo.kz,ru.inshaker.com",
        alias="SEARCH_ALLOWED_DOMAINS",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def allowed_domains_list(self) -> List[str]:
        return _split_domains(self.search_allowed_domains)


# Инстанс настроек
settings = Settings()

# Удобные модульные экспортируемые переменные — так их импортируют другие модули
GOOGLE_CSE_KEY = settings.google_cse_key or settings.google_api_key
GOOGLE_CSE_CX = settings.google_cse_cx
SEARCH_ALLOWED_DOMAINS = settings.allowed_domains_list
GEMINI_API_KEY = settings.gemini_api_key or settings.google_api_key
