"""Align Postgres schema with LC-Backend SQLAlchemy models."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.configs.database.db import Base, engine

_REQUIRED_USER_COLUMNS = {"id", "email", "hashed_password", "created_at"}
_DEPENDENT_TABLES = (
    "pending_actions",
    "gmail_connections",
    "slack_connections",
    "jira_connections",
    "github_connections",
    "auth_sessions",
    "password_reset_otps",
    "conversations",
)
_LEGACY_TABLES = ("users", "users_legacy")


class _SchemaState:
    """Cached table/column metadata from a single information_schema query."""

    def __init__(self, db_engine: Engine) -> None:
        self._table_names: set[str] = set()
        self._columns: dict[str, set[str]] = {}
        with db_engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                    """
                )
            )
            for table_name, column_name in rows:
                self._table_names.add(table_name)
                self._columns.setdefault(table_name, set()).add(column_name)

    @property
    def table_names(self) -> set[str]:
        return self._table_names

    def columns(self, table_name: str) -> set[str]:
        return self._columns.get(table_name, set())


def _users_schema_ok(state: _SchemaState) -> bool:
    return _REQUIRED_USER_COLUMNS.issubset(state.columns("users"))


def _ensure_slack_user_token_column(state: _SchemaState) -> None:
    if "slack_connections" not in state.table_names:
        return
    if "user_token_enc" in state.columns("slack_connections"):
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE slack_connections ADD COLUMN user_token_enc VARCHAR NULL"))


def _ensure_email_verified_column(state: _SchemaState) -> None:
    if "users" not in state.table_names:
        return
    if "email_verified" in state.columns("users"):
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT TRUE")
        )


def _ensure_otp_purpose_column(state: _SchemaState) -> None:
    if "password_reset_otps" not in state.table_names:
        return
    if "purpose" in state.columns("password_reset_otps"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE password_reset_otps "
                "ADD COLUMN purpose VARCHAR NOT NULL DEFAULT 'password_reset'"
            )
        )


def _ensure_jira_user_columns(state: _SchemaState) -> None:
    if "jira_connections" not in state.table_names:
        return
    columns = state.columns("jira_connections")
    additions = {
        "jira_account_id": "VARCHAR NULL",
        "jira_display_name": "VARCHAR NULL",
        "jira_email": "VARCHAR NULL",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in columns:
                conn.execute(text(f"ALTER TABLE jira_connections ADD COLUMN {column} {ddl}"))


def _ensure_github_connection_columns(state: _SchemaState) -> None:
    if "github_connections" not in state.table_names:
        return
    columns = state.columns("github_connections")
    additions = {
        "github_display_name": "VARCHAR NULL",
        "github_avatar_url": "VARCHAR NULL",
        "refresh_token_enc": "VARCHAR NULL",
        "expires_at": "TIMESTAMP NULL",
        "granted_scopes": "VARCHAR NULL",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in columns:
                conn.execute(text(f"ALTER TABLE github_connections ADD COLUMN {column} {ddl}"))


def _ensure_connection_identity_columns(state: _SchemaState) -> None:
    with engine.begin() as conn:
        if "gmail_connections" in state.table_names:
            columns = state.columns("gmail_connections")
            if "gmail_display_name" not in columns:
                conn.execute(
                    text("ALTER TABLE gmail_connections ADD COLUMN gmail_display_name VARCHAR NULL")
                )
            if "granted_scopes" not in columns:
                conn.execute(
                    text("ALTER TABLE gmail_connections ADD COLUMN granted_scopes VARCHAR NULL")
                )
        if "slack_connections" in state.table_names:
            columns = state.columns("slack_connections")
            if "slack_display_name" not in columns:
                conn.execute(
                    text("ALTER TABLE slack_connections ADD COLUMN slack_display_name VARCHAR NULL")
                )
            if "slack_team_name" not in columns:
                conn.execute(
                    text("ALTER TABLE slack_connections ADD COLUMN slack_team_name VARCHAR NULL")
                )


def ensure_schema() -> None:
    state = _SchemaState(engine)
    if _users_schema_ok(state):
        Base.metadata.create_all(bind=engine)
        _ensure_slack_user_token_column(state)
        _ensure_email_verified_column(state)
        _ensure_otp_purpose_column(state)
        _ensure_jira_user_columns(state)
        _ensure_github_connection_columns(state)
        _ensure_connection_identity_columns(state)
        return

    # Wrong or partial schema (often from another app sharing the same Neon DB).
    with engine.begin() as conn:
        for table_name in _DEPENDENT_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

        for table_name in _LEGACY_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

    Base.metadata.create_all(bind=engine)
