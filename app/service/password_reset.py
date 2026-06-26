from sqlalchemy.orm import Session

from app.security import hash_password
from app.service.email_service import send_otp_email
from app.service.otp_service import (
    OTP_PURPOSE_PASSWORD_RESET,
    create_otp,
    delete_otp_record_on_send_failure,
    verify_otp,
)
from app.service.users import get_user_by_email, normalize_email


def request_password_reset(db: Session, email: str) -> None:
    normalized = normalize_email(email)
    user = get_user_by_email(db, normalized)
    if not user:
        return

    otp = create_otp(db, normalized, OTP_PURPOSE_PASSWORD_RESET)
    try:
        send_otp_email(to_email=normalized, otp=otp)
    except Exception:
        delete_otp_record_on_send_failure(db, normalized, OTP_PURPOSE_PASSWORD_RESET)
        raise


def reset_password_with_otp(db: Session, email: str, otp: str, new_password: str) -> None:
    normalized = normalize_email(email)
    user = get_user_by_email(db, normalized)
    if not user:
        raise ValueError("Invalid email or code")

    if not verify_otp(db, normalized, OTP_PURPOSE_PASSWORD_RESET, otp):
        raise ValueError("Invalid or expired code")

    user.hashed_password = hash_password(new_password)
    db.commit()
