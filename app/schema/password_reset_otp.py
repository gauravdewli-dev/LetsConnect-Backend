from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.configs.database.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class PasswordResetOtp(Base):
    __tablename__ = "password_reset_otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    purpose = Column(String, nullable=False, index=True, default="password_reset")
    otp_hash = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
