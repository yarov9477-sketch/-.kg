import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "http://localhost:8000")
    DB_PATH: str = os.getenv("DB_PATH", "kg.db")
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
