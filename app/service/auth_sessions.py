import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import (
    PASSWORD_RESET_OTP_EXPIRE_MINUTES,
    PASSWORD_RESET_OTP_LENGTH,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.schema.auth_session import AuthSession
from app.schema.users import User
from app.security import create_access_token, hash_refresh_token
from app.service.users import get_user_by_id, normalize_email


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def create_session(db: Session, user: User) -> tuple[str, str]:
    refresh_token = secrets.token_urlsafe(48)
    expires_at = _utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    access_token = create_access_token(user.id, user.email)
    return access_token, refresh_token


def refresh_session(db: Session, refresh_token: str) -> tuple[str, str, User]:
    token_hash = hash_refresh_token(refresh_token)
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.refresh_token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if not session or _as_utc(session.expires_at) < _utcnow():
        raise ValueError("Invalid or expired session")

    user = get_user_by_id(db, session.user_id)
    if not user:
        raise ValueError("Invalid or expired session")

    session.revoked_at = _utcnow()
    db.commit()
    access_token, new_refresh = create_session(db, user)
    return access_token, new_refresh, user


def revoke_session(db: Session, refresh_token: str) -> bool:
    token_hash = hash_refresh_token(refresh_token)
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.refresh_token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if not session:
        return False
    session.revoked_at = _utcnow()
    db.commit()
    return True


def revoke_all_sessions(db: Session, user_id: int) -> None:
    now = _utcnow()
    db.query(AuthSession).filter(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    ).update({"revoked_at": now})
    db.commit()
