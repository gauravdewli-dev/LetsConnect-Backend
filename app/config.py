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
        self.mongodb_uri = os.getenv("MONGODB_URI", "").strip()
        if not self.mongodb_uri:
            raise ValueError("MONGODB_URI is required (MongoDB Atlas connection string)")
        self.mongodb_db_name = os.getenv("MONGODB_DB_NAME", "letsconnect").strip() or "letsconnect"
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-in-production")
        self.encryption_key = os.getenv("ENCRYPTION_KEY", "")
        self.frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        self.backend_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
        self.gemini_api_keys = self._collect_gemini_api_keys()
        self.gemini_api_key = self.gemini_api_keys[0] if self.gemini_api_keys else ""
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.skip_startup_migrations = os.getenv("SKIP_STARTUP_MIGRATIONS", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.gmail_credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials.json")
        self.slack_client_id = os.getenv("SLACK_CLIENT_ID", "")
        self.slack_client_secret = os.getenv("SLACK_CLIENT_SECRET", "")
        self.slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
        # App ID from Slack app Basic Information (for "Open in Slack" links)
        self.slack_app_id = os.getenv("SLACK_APP_ID", "").strip()
        self.jira_client_id = os.getenv("JIRA_CLIENT_ID", "").strip()
        self.jira_client_secret = os.getenv("JIRA_CLIENT_SECRET", "").strip()
        # Optional override if BACKEND_URL differs from the URL registered in Atlassian
        self.jira_oauth_callback_uri_override = os.getenv("JIRA_OAUTH_CALLBACK_URI", "").strip().rstrip("/")
        self.github_client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
        self.github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
        self.github_oauth_callback_uri_override = os.getenv(
            "GITHUB_OAUTH_CALLBACK_URI", ""
        ).strip().rstrip("/")

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

    def _collect_gemini_api_keys(self) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for env_name in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_ONE",
            "GEMINI_API_KEY_TWO",
            "GEMINI_API_KEY_THREE",
        ):
            value = os.getenv(env_name, "").strip()
            if value and value not in seen:
                seen.add(value)
                keys.append(value)
        return keys

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

    @property
    def jira_oauth_callback_uri(self) -> str:
        if self.jira_oauth_callback_uri_override:
            return self.jira_oauth_callback_uri_override
        return f"{self.backend_url}/jira/oauth/callback"

    @property
    def github_oauth_callback_uri(self) -> str:
        if self.github_oauth_callback_uri_override:
            return self.github_oauth_callback_uri_override
        return f"{self.backend_url}/github/oauth/callback"
