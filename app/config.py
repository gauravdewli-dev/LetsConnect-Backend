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

        self.app_name = os.getenv("APP_NAME", "LetsConnect")
        # Email: brevo (recommended free tier) | smtp | resend | auto
        self.email_provider = os.getenv("EMAIL_PROVIDER", "auto").strip().lower()
        self.brevo_api_key = os.getenv("BREVO_API_KEY", "").strip()
        self.resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
        self.email_from = os.getenv("EMAIL_FROM", "").strip()
        self.smtp_host = os.getenv("SMTP_HOST", "").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        self._validate_secrets()

    @property
    def is_production(self) -> bool:
        return "localhost" not in self.backend_url and "127.0.0.1" not in self.backend_url

    def _validate_secrets(self) -> None:
        weak_jwt = self.jwt_secret in {"", "change-me-in-production", "change-me-to-a-long-random-string"}
        if self.is_production and weak_jwt:
            raise ValueError(
                "JWT_SECRET must be set to a long random string in production (BACKEND_URL is not localhost)"
            )
        if self.is_production and len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters in production")

    @property
    def gmail_oauth_callback_uri(self) -> str:
        return f"{self.backend_url}/oauth/callback"

    @property
    def slack_oauth_callback_uri(self) -> str:
        return f"{self.backend_url}/slack/oauth/callback"
