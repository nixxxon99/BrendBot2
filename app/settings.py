import os
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    api_token: str = Field(alias="API_TOKEN")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    webhook_secret: str = Field(default="MARS", alias="WEBHOOK_SECRET")
    tz: str = Field(default=os.getenv("TZ", "Asia/Almaty"), alias="TZ")
    bing_api_key: str | None = Field(default=None, alias="BING_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
