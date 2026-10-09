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
    site_label: str = ""  # e.g. "TEST SITE": a red badge in every page's top bar, so a test copy is never mistaken for the real one
    public_url: str = ""  # how people reach AT-HUB (e.g. https://hub.example.com) -- printed QR codes link here
    backup_dir: str = "./backups"
    backup_every_hours: float = 6  # automatic database backups; 0 turns them off
    backup_copies: str = ""  # more folders every backup is also copied to, ";"-separated (OneDrive, a NAS, a USB disk...)
    backup_keep: int = 30  # automatic backups kept (manual and pre-restore ones are kept until deleted)

    # Private AI for reading customer PO PDFs into draft orders. Runs on a local Ollama
    # server (https://ollama.com) -- documents never leave this machine / network.
    # The URL must be localhost or a private-network address; anything else is refused.
    ai_ollama_url: str = "http://127.0.0.1:11434"
    ai_model: str = "qwen2.5:7b"
    anthropic_api_key: str = ""  # optional "Ask Claude" (cloud) document reading; blank = feature off
    # Which AI reads documents: "local" (the Ollama model above, on this network) or "claude" (the cloud server, where
    # no local model runs: every reader uses Claude -- text PDFs only, with our / the customer's details removed first;
    # scans and photos are never sent and have to be typed in).
    ai_engine: str = "local"
    # Which AI reads documents: "local" (the Ollama model above, on this network) or "claude" (the cloud server, where
    # no local model runs: every reader uses Claude -- text PDFs only, with our / the customer's details removed first;
    # scans and photos are never sent and have to be typed in).
    ai_engine: str = "local"
    ai_vision_model: str = "qwen2.5vl:7b"  # reads photos and scanned PDFs (PODs, scanned invoices)
    ai_timeout_seconds: int = 180

    # The company's time zone (app/services/clock.py): printed documents, "today" for invoice / paid dates, and the
    # default for users who haven't picked their own zone.
    business_timezone: str = "America/Chicago"

    # Keep TEST-* products, a test customer/vendor and an open test order ready at every startup.
    test_data_enabled: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
