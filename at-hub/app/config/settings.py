from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    database_url: str = "sqlite:///./at_hub.db"
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    cors_origins: List[str] = ["http://localhost:8010", "http://127.0.0.1:8010"]

    admin_username: str = "admin"
    admin_password: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
