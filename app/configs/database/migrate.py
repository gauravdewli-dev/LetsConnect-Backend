"""Align Postgres schema with LC-Backend SQLAlchemy models."""

from sqlalchemy import inspect, text

from app.configs.database.db import Base, engine

_REQUIRED_USER_COLUMNS = {"id", "email", "hashed_password", "created_at"}
_DEPENDENT_TABLES = ("pending_actions", "gmail_connections", "slack_connections")
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


def ensure_schema() -> None:
    if _users_schema_ok():
        Base.metadata.create_all(bind=engine)
        _ensure_slack_user_token_column()
        return

    # Wrong or partial schema (often from another app sharing the same Neon DB).
    # Drop legacy auth tables; index names like `ix_users_id` are global in Postgres.
    with engine.begin() as conn:
        for table_name in _DEPENDENT_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

        for table_name in _LEGACY_TABLES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))

    Base.metadata.create_all(bind=engine)
