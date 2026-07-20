from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.configs.database.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class PendingAction(Base):
    """A side-effecting tool call held for explicit human approval.

    The agent never executes a write tool directly. It records the call here and
    stops; the user approves or rejects out-of-band. On approval the *stored*
    args are replayed verbatim, so text injected into the model's context cannot
    change what actually runs after the user has seen it.
    """

    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String, nullable=True, index=True)

    tool_name = Column(String, nullable=False)
    # JSON-encoded args, replayed as-is on approval.
    tool_args = Column(Text, nullable=False)
    # Human-readable summary rendered by our code from tool_args — never by the model.
    summary = Column(Text, nullable=False)

    # pending | approved | rejected | executed | failed | superseded
    status = Column(String, default="pending", nullable=False, index=True)
    result = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
