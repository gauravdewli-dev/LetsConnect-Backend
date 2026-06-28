import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.configs.mongodb.client import (
    AGENT_HISTORY_LIMIT,
    UI_MESSAGE_PAGE_LIMIT,
    get_messages_collection,
)
from app.schema.conversations import Conversation
from app.service.letsconnect_agent import run_agent

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_conversation_for_user(db: Session, user_id: int, conversation_id: str) -> Conversation | None:
    return (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .first()
    )


def get_or_create_primary_conversation(db: Session, user_id: int) -> Conversation:
    conv = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.is_primary.is_(True))
        .first()
    )
    if conv:
        return conv

    now = _utcnow()
    conv = Conversation(
        id=str(uuid4()),
        user_id=user_id,
        title="LetsConnect Assistant",
        is_primary=True,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def touch_conversation(db: Session, conv: Conversation) -> None:
    conv.updated_at = _utcnow()
    db.commit()


def append_message(
    conversation_id: str,
    user_id: int,
    role: str,
    content: str,
    channel: str,
    *,
    tools_used: list[str] | None = None,
) -> str:
    doc = {
        "_id": str(uuid4()),
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "channel": channel,
        "tools_used": tools_used or [],
        "created_at": _utcnow(),
    }
    get_messages_collection().insert_one(doc)
    return doc["_id"]


def get_agent_history(conversation_id: str, limit: int = AGENT_HISTORY_LIMIT) -> list[dict[str, str]]:
    cursor = (
        get_messages_collection()
        .find({"conversation_id": conversation_id}, {"role": 1, "content": 1, "created_at": 1})
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = list(cursor)
    docs.reverse()
    return [{"role": doc["role"], "content": doc["content"]} for doc in docs]


def _serialize_message(doc: dict) -> dict:
    created_at = doc["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(timezone.utc).isoformat()
    return {
        "id": doc["_id"],
        "role": doc["role"],
        "content": doc["content"],
        "channel": doc.get("channel", "web"),
        "tools_used": doc.get("tools_used") or [],
        "created_at": created_at,
    }


def get_messages_page(
    conversation_id: str,
    *,
    limit: int = UI_MESSAGE_PAGE_LIMIT,
    before: datetime | None = None,
) -> tuple[list[dict], str | None]:
    query: dict = {"conversation_id": conversation_id}
    if before is not None:
        query["created_at"] = {"$lt": before}

    cursor = (
        get_messages_collection()
        .find(query)
        .sort("created_at", -1)
        .limit(limit + 1)
    )
    docs = list(cursor)
    has_more = len(docs) > limit
    if has_more:
        docs = docs[:limit]

    docs.reverse()
    messages = [_serialize_message(doc) for doc in docs]
    next_cursor = messages[0]["created_at"] if has_more and messages else None
    return messages, next_cursor


def handle_chat_message(
    db: Session,
    user_id: int,
    message: str,
    *,
    channel: str = "web",
    conversation_id: str | None = None,
) -> dict:
    if conversation_id:
        conv = get_conversation_for_user(db, user_id, conversation_id)
        if not conv:
            raise ValueError("Conversation not found")
    else:
        conv = get_or_create_primary_conversation(db, user_id)

    history = get_agent_history(conv.id, limit=AGENT_HISTORY_LIMIT)
    result = run_agent(db, user_id, message, history=history or None)
    reply = result["reply"]
    tools_used = result.get("tools_used", [])

    append_message(conv.id, user_id, "user", message, channel)
    append_message(conv.id, user_id, "assistant", reply, channel, tools_used=tools_used)
    touch_conversation(db, conv)

    return {
        "reply": reply,
        "tools_used": tools_used,
        "conversation_id": conv.id,
    }
