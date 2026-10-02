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

    # Outgoing email (invoices). Leave smtp_host empty to disable sending.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # defaults to smtp_username
    smtp_security: str = "starttls"  # starttls (port 587) | ssl (port 465) | none

    # Where uploaded attachments (PDFs, photos) are stored.
    upload_dir: str = "./uploads"

    # Private AI for reading customer PO PDFs into draft orders. Runs on a local Ollama
    # server (https://ollama.com) -- documents never leave this machine / network.
    # The URL must be localhost or a private-network address; anything else is refused.
    ai_ollama_url: str = "http://127.0.0.1:11434"
    ai_model: str = "qwen2.5:7b"
    anthropic_api_key: str = ""  # optional "Ask Claude" (cloud) document reading; blank = feature off
    ai_vision_model: str = "qwen2.5vl:7b"  # reads photos and scanned PDFs (PODs, scanned invoices)
    ai_timeout_seconds: int = 180

    # Keep TEST-* products, a test customer/vendor and an open test order ready at every startup.
    test_data_enabled: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
