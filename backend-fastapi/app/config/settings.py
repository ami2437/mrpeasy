from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # MRPeasy API Configuration
    mrpeasy_api_base_url: str = "https://api.mrpeasy.com/rest/v1"
    mrpeasy_api_key: str = ""
    mrpeasy_api_secret: str = ""

    # Database Configuration
    database_url: str = "sqlite:///./mrpeasy.db"

    # Server Configuration
    port: int = 8000
    host: str = "0.0.0.0"
    debug: bool = True

    # CORS Configuration
    cors_origins: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # JWT Configuration
    secret_key: str = "your-secret-key-change-in-production-12345"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
