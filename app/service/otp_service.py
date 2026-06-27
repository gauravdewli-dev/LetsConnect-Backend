import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import (
    OTP_LOCKOUT_MINUTES,
    OTP_MAX_VERIFY_ATTEMPTS,
    PASSWORD_RESET_OTP_EXPIRE_MINUTES,
    PASSWORD_RESET_OTP_LENGTH,
)
from app.schema.password_reset_otp import PasswordResetOtp

OTP_PURPOSE_PASSWORD_RESET = "password_reset"
OTP_PURPOSE_EMAIL_VERIFY = "email_verify"


@dataclass
class _OtpAttemptState:
    failures: int = 0
    locked_until: datetime | None = None


_otp_attempts: dict[tuple[str, str], _OtpAttemptState] = {}
_otp_attempts_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Normalize DB datetimes (often naive UTC) for comparison with aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def generate_otp() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PASSWORD_RESET_OTP_LENGTH))


def invalidate_active_otps(db: Session, email: str, purpose: str) -> None:
    db.query(PasswordResetOtp).filter(
        PasswordResetOtp.email == email,
        PasswordResetOtp.purpose == purpose,
        PasswordResetOtp.used_at.is_(None),
    ).update({"used_at": _utcnow()})


def create_otp(db: Session, email: str, purpose: str) -> str:
    _clear_otp_attempts(email, purpose)
    otp = generate_otp()
    expires_at = _utcnow() + timedelta(minutes=PASSWORD_RESET_OTP_EXPIRE_MINUTES)
    invalidate_active_otps(db, email, purpose)
    record = PasswordResetOtp(
        email=email,
        purpose=purpose,
        otp_hash=_hash_otp(otp),
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return otp


def _attempt_key(email: str, purpose: str) -> tuple[str, str]:
    return (email.strip().lower(), purpose)


def _clear_otp_attempts(email: str, purpose: str) -> None:
    with _otp_attempts_lock:
        _otp_attempts.pop(_attempt_key(email, purpose), None)


def _is_otp_locked(email: str, purpose: str) -> bool:
    key = _attempt_key(email, purpose)
    with _otp_attempts_lock:
        state = _otp_attempts.get(key)
        if not state or not state.locked_until:
            return False
        if state.locked_until <= _utcnow():
            _otp_attempts.pop(key, None)
            return False
        return True


def _record_otp_failure(email: str, purpose: str) -> None:
    key = _attempt_key(email, purpose)
    with _otp_attempts_lock:
        state = _otp_attempts.setdefault(key, _OtpAttemptState())
        state.failures += 1
        if state.failures >= OTP_MAX_VERIFY_ATTEMPTS:
            state.locked_until = _utcnow() + timedelta(minutes=OTP_LOCKOUT_MINUTES)


def verify_otp(db: Session, email: str, purpose: str, otp: str) -> bool:
    if _is_otp_locked(email, purpose):
        return False
    record = (
        db.query(PasswordResetOtp)
        .filter(
            PasswordResetOtp.email == email,
            PasswordResetOtp.purpose == purpose,
            PasswordResetOtp.used_at.is_(None),
        )
        .order_by(PasswordResetOtp.created_at.desc())
        .first()
    )
    if not record or _as_utc(record.expires_at) < _utcnow():
        return False
    if record.otp_hash != _hash_otp(otp.strip()):
        _record_otp_failure(email, purpose)
        return False
    _clear_otp_attempts(email, purpose)
    record.used_at = _utcnow()
    db.commit()
    return True


def delete_otp_record_on_send_failure(db: Session, email: str, purpose: str) -> None:
    record = (
        db.query(PasswordResetOtp)
        .filter(
            PasswordResetOtp.email == email,
            PasswordResetOtp.purpose == purpose,
            PasswordResetOtp.used_at.is_(None),
        )
        .order_by(PasswordResetOtp.created_at.desc())
        .first()
    )
    if record:
        db.delete(record)
        db.commit()
