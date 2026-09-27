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
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours

    # Deliberate opt-in switch for creating real invoices in MRPeasy.
    # Independent of RBAC -- lets the whole feature be built/tested before the
    # real POST path is switched on for a controlled go-live.
    allow_mrp_invoice_creation: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

if not settings.secret_key or settings.secret_key == "your-secret-key-change-in-production-12345":
    raise RuntimeError("SECRET_KEY must be configured with a strong, private value")
