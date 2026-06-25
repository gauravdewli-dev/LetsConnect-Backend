from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.configs.database.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    channel_id = Column(String, nullable=False)
    draft_to = Column(String, nullable=False)
    draft_subject = Column(String, nullable=True)
    draft_body = Column(Text, nullable=False)
    status = Column(String, default="pending", nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
