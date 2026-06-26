from sqlalchemy.orm import Session

from app.security import hash_password
from app.service.email_service import send_signup_verification_email
from app.service.otp_service import (
    OTP_PURPOSE_EMAIL_VERIFY,
    create_otp,
    delete_otp_record_on_send_failure,
    verify_otp,
)
from app.service.users import get_user_by_email, normalize_email


def send_signup_verification(db: Session, email: str) -> None:
    normalized = normalize_email(email)
    otp = create_otp(db, normalized, OTP_PURPOSE_EMAIL_VERIFY)
    try:
        send_signup_verification_email(to_email=normalized, otp=otp)
    except Exception:
        delete_otp_record_on_send_failure(db, normalized, OTP_PURPOSE_EMAIL_VERIFY)
        raise


def verify_signup_email(db: Session, email: str, otp: str) -> None:
    normalized = normalize_email(email)
    user = get_user_by_email(db, normalized)
    if not user:
        raise ValueError("Invalid email or code")
    if user.email_verified:
        return
    if not verify_otp(db, normalized, OTP_PURPOSE_EMAIL_VERIFY, otp):
        raise ValueError("Invalid or expired code")
    user.email_verified = True
    db.commit()
