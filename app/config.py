import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@lru_cache
def get_settings():
    return Settings()


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required (PostgreSQL connection string)")
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-in-production")
        self.encryption_key = os.getenv("ENCRYPTION_KEY", "")
        self.frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        self.backend_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.gmail_credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials.json")
        self.slack_client_id = os.getenv("SLACK_CLIENT_ID", "")
        self.slack_client_secret = os.getenv("SLACK_CLIENT_SECRET", "")
        self.slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
        # App ID from Slack app Basic Information (for "Open in Slack" links)
        self.slack_app_id = os.getenv("SLACK_APP_ID", "").strip()

    @property
    def gmail_oauth_callback_uri(self) -> str:
        return f"{self.backend_url}/oauth/callback"

    @property
    def slack_oauth_callback_uri(self) -> str:
        return f"{self.backend_url}/slack/oauth/callback"
