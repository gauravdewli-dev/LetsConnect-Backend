import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import PASSWORD_RESET_OTP_EXPIRE_MINUTES, PASSWORD_RESET_OTP_LENGTH
from app.schema.password_reset_otp import PasswordResetOtp

OTP_PURPOSE_PASSWORD_RESET = "password_reset"
OTP_PURPOSE_EMAIL_VERIFY = "email_verify"


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


def verify_otp(db: Session, email: str, purpose: str, otp: str) -> bool:
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
        return False
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
