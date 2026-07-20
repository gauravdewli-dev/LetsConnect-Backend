import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.constants import AGENT_HISTORY_LIMIT, UI_MESSAGE_PAGE_LIMIT
from app.configs.mongodb.client import get_messages_collection
from app.schema.conversations import Conversation

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


def clear_user_chat_history(db: Session, user_id: int) -> None:
    """Delete all conversations and Mongo messages for a user (e.g. on Slack disconnect)."""
    convs = db.query(Conversation).filter(Conversation.user_id == user_id).all()
    conv_ids = [c.id for c in convs]

    if conv_ids:
        get_messages_collection().delete_many(
            {"user_id": user_id, "conversation_id": {"$in": conv_ids}}
        )
    else:
        get_messages_collection().delete_many({"user_id": user_id})

    for conv in convs:
        db.delete(conv)
    db.commit()


def clear_slack_channel_for_user(db: Session, user_id: int) -> None:
    db.query(Conversation).filter(
        Conversation.user_id == user_id,
        Conversation.slack_channel_id.isnot(None),
    ).update({Conversation.slack_channel_id: None}, synchronize_session=False)
    db.commit()


def get_agent_history(
    conversation_id: str,
    user_id: int,
    limit: int = AGENT_HISTORY_LIMIT,
) -> list[dict[str, str]]:
    cursor = (
        get_messages_collection()
        .find(
            {"conversation_id": conversation_id, "user_id": user_id},
            {"role": 1, "content": 1, "created_at": 1},
        )
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
    user_id: int,
    *,
    limit: int = UI_MESSAGE_PAGE_LIMIT,
    before: datetime | None = None,
) -> tuple[list[dict], str | None]:
    query: dict = {"conversation_id": conversation_id, "user_id": user_id}
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


# Exact-match whitelists. Deliberately narrow: a false positive here would
# execute a write the user did not intend, so anything not listed falls through
# to a normal agent turn and leaves the action pending.
_AFFIRMATIVES = frozenset(
    {
        "y", "yes", "yep", "yeah", "yup", "ok", "okay", "approve", "approved",
        "confirm", "confirmed", "send", "send it", "go ahead", "do it",
        "proceed", "yes please", "approve it", "looks good", "lgtm",
    }
)
_NEGATIVES = frozenset(
    {
        "n", "no", "nope", "reject", "rejected", "cancel", "cancelled", "stop",
        "don't", "dont", "do not", "abort", "nevermind", "never mind", "no thanks",
    }
)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().strip(".!,").split())


def _resolve_pending_by_text(
    db: Session,
    user_id: int,
    conversation_id: str,
    message: str,
    channel: str,
) -> dict | None:
    """Resolve a held action when the human types a bare yes/no. None otherwise."""
    from app.schema.pending_action import PendingAction

    normalized = _normalize(message)
    if normalized not in _AFFIRMATIVES and normalized not in _NEGATIVES:
        return None

    action = (
        db.query(PendingAction)
        .filter(
            PendingAction.user_id == user_id,
            PendingAction.conversation_id == conversation_id,
            PendingAction.status == "pending",
        )
        .order_by(PendingAction.created_at.desc())
        .first()
    )
    if not action:
        return None

    from app.service.letsconnect_agent import execute_approved_action, reject_pending_action

    append_message(conversation_id, user_id, "user", message, channel)

    if normalized in _NEGATIVES:
        reject_pending_action(db, user_id, action.id)
        reply = "Cancelled — I did not run that action."
        tools_used: list[str] = []
    else:
        try:
            outcome = execute_approved_action(db, user_id, action.id)
            reply = f"Done — I ran the approved action.\n\n{outcome['action']['summary']}"
            tools_used = [action.tool_name]
        except ValueError as exc:
            reply = str(exc)
            tools_used = []
        except Exception as exc:
            logger.exception("Approved action %s failed: %s", action.id, exc)
            reply = f"That action failed: {exc}"
            tools_used = []

    append_message(conversation_id, user_id, "assistant", reply, channel, tools_used=tools_used)
    conv = get_conversation_for_user(db, user_id, conversation_id)
    if conv:
        touch_conversation(db, conv)

    return {
        "reply": reply,
        "tools_used": tools_used,
        "conversation_id": conversation_id,
        "pending_action": None,
    }


def handle_chat_message(
    db: Session,
    user_id: int,
    message: str,
    *,
    channel: str = "web",
    conversation_id: str | None = None,
    slack_channel_id: str | None = None,
) -> dict:
    if conversation_id:
        conv = get_conversation_for_user(db, user_id, conversation_id)
        if not conv:
            raise ValueError("Conversation not found")
    else:
        conv = get_or_create_primary_conversation(db, user_id)

    if channel == "slack" and slack_channel_id:
        conv.slack_channel_id = slack_channel_id

    # Typed approve/reject. Surfaces are not all button-capable (Slack has no
    # approval UI), so an unambiguous affirmative typed BY THE HUMAN resolves a
    # held action. Safe because the injected content cannot type into this box —
    # and the matching is exact-whitelist, so it never fires on prose that merely
    # contains "yes".
    resolution = _resolve_pending_by_text(db, user_id, conv.id, message, channel)
    if resolution:
        return resolution

    history = get_agent_history(conv.id, user_id, limit=AGENT_HISTORY_LIMIT)
    from app.service.letsconnect_agent import run_agent

    result = run_agent(
        db, user_id, message, history=history or None, conversation_id=conv.id
    )
    reply = result["reply"]
    tools_used = result.get("tools_used", [])

    append_message(conv.id, user_id, "user", message, channel)
    append_message(conv.id, user_id, "assistant", reply, channel, tools_used=tools_used)
    touch_conversation(db, conv)

    return {
        "reply": reply,
        "tools_used": tools_used,
        "conversation_id": conv.id,
        "pending_action": result.get("pending_action"),
    }


def record_action_outcome(
    db: Session,
    user_id: int,
    conversation_id: str,
    text: str,
    *,
    channel: str = "web",
) -> None:
    """Append the outcome of an approve/reject to the visible transcript."""
    append_message(conversation_id, user_id, "assistant", text, channel)
    conv = get_conversation_for_user(db, user_id, conversation_id)
    if conv:
        touch_conversation(db, conv)
