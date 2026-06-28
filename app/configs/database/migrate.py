"""Align Postgres schema with LC-Backend SQLAlchemy models."""

from sqlalchemy import inspect, text

from app.configs.database.db import Base, engine

_REQUIRED_USER_COLUMNS = {"id", "email", "hashed_password", "created_at"}
_DEPENDENT_TABLES = (
    "pending_actions",
    "gmail_connections",
    "slack_connections",
    "jira_connections",
    "auth_sessions",
    "password_reset_otps",
    "conversations",
)
_LEGACY_TABLES = ("users", "users_legacy")


def _table_columns(table_name: str) -> set[str]:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _users_schema_ok() -> bool:
    return _REQUIRED_USER_COLUMNS.issubset(_table_columns("users"))


def _ensure_slack_user_token_column() -> None:
    if "slack_connections" not in inspect(engine).get_table_names():
        return
    if "user_token_enc" in _table_columns("slack_connections"):
        return
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE slack_connections ADD COLUMN user_token_enc VARCHAR NULL'))


def _ensure_email_verified_column() -> None:
    if "users" not in inspect(engine).get_table_names():
        return
    if "email_verified" in _table_columns("users"):
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT TRUE")
        )


def _ensure_otp_purpose_column() -> None:
    if "password_reset_otps" not in inspect(engine).get_table_names():
        return
    if "purpose" in _table_columns("password_reset_otps"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE password_reset_otps "
                "ADD COLUMN purpose VARCHAR NOT NULL DEFAULT 'password_reset'"
            )
        )


def _ensure_jira_user_columns() -> None:
    if "jira_connections" not in inspect(engine).get_table_names():
        return
    columns = _table_columns("jira_connections")
    additions = {
        "jira_account_id": "VARCHAR NULL",
        "jira_display_name": "VARCHAR NULL",
        "jira_email": "VARCHAR NULL",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in columns:
                conn.execute(text(f"ALTER TABLE jira_connections ADD COLUMN {column} {ddl}"))


def _ensure_connection_identity_columns() -> None:
    with engine.begin() as conn:
        if "gmail_connections" in inspect(engine).get_table_names():
            columns = _table_columns("gmail_connections")
            if "gmail_display_name" not in columns:
                conn.execute(text("ALTER TABLE gmail_connections ADD COLUMN gmail_display_name VARCHAR NULL"))
        if "slack_connections" in inspect(engine).get_table_names():
            columns = _table_columns("slack_connections")
            if "slack_display_name" not in columns:
                conn.execute(text("ALTER TABLE slack_connections ADD COLUMN slack_display_name VARCHAR NULL"))
            if "slack_team_name" not in columns:
                conn.execute(text("ALTER TABLE slack_connections ADD COLUMN slack_team_name VARCHAR NULL"))


def ensure_schema() -> None:
    if _users_schema_ok():
        Base.metadata.create_all(bind=engine)
        _ensure_slack_user_token_column()
        _ensure_email_verified_column()
        _ensure_otp_purpose_column()
        _ensure_jira_user_columns()
        _ensure_connection_identity_columns()
        return

    # Wrong or partial schema (often from another app sharing the same Neon DB).
    # Drop legacy auth tables; index names like `ix_users_id` are global in Postgres.
    with engine.begin() as conn:
        for table_name in _DEPENDENT_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

        for table_name in _LEGACY_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

    Base.metadata.create_all(bind=engine)
