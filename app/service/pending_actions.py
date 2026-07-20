"""Human-approval gate for side-effecting agent tools.

Threat model: tool output (email bodies, Slack messages, Jira descriptions, PR
bodies) is attacker-controlled text that lands in the model's context. A prompt
injection can therefore make the model *want* to call a write tool with
attacker-chosen arguments.

Two properties defend against that, and both live here rather than in the prompt:

1. No write tool executes without an explicit out-of-band human approval.
2. The approval prompt the human sees is rendered by ``render_summary`` from the
   stored args — not from model prose — and approval replays those same stored
   args. So the model cannot describe one action to the user and perform another.
"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.schema.pending_action import PendingAction

# Every tool that mutates state or leaves the user's account. Kept as a single
# explicit set so adding a tool to the agent without classifying it here fails
# closed (see assert_write_tools_classified).
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "send_email",
        "create_draft",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "slack_send_dm",
        "slack_send_channel_message",
        "jira_create_issue",
        "jira_update_issue",
        "jira_delete_issue",
        "github_create_pull_request",
        "github_merge_pull_request",
    }
)

# Approvals go stale — a user should not be able to rubber-stamp an action
# proposed an hour ago in a context they no longer remember.
PENDING_ACTION_TTL = timedelta(minutes=15)

# Generous cap: the user must be able to see an exfiltration payload in full to
# judge it, so we truncate only to keep the response from being unbounded.
_MAX_FIELD_CHARS = 4000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: object) -> str:
    text = "" if value is None else str(value)
    if len(text) <= _MAX_FIELD_CHARS:
        return text
    return f"{text[:_MAX_FIELD_CHARS]}\n… [truncated {len(text) - _MAX_FIELD_CHARS} chars]"


def _lines(*pairs: tuple[str, object]) -> str:
    out = []
    for label, value in pairs:
        if value in (None, "", [], False):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        out.append(f"{label}: {_clip(value)}")
    return "\n".join(out)


def render_summary(tool_name: str, args: dict) -> str:
    """Deterministic, code-generated description of what will run.

    This is the only text the human is asked to approve against. It must never
    be produced or influenced by the model.
    """
    a = args.get

    if tool_name in ("send_email", "create_draft"):
        verb = "Send an email" if tool_name == "send_email" else "Save a Gmail draft"
        return f"{verb}\n" + _lines(
            ("To", a("to")),
            ("Subject", a("subject")),
            ("Body", a("body")),
        )

    if tool_name == "calendar_create_event":
        return "Create a calendar event\n" + _lines(
            ("Title", a("summary")),
            ("Start", a("start")),
            ("End", a("end")),
            ("Location", a("location")),
            ("Attendees (will be emailed invites)", a("attendees")),
            ("Google Meet", "yes" if a("add_google_meet") else None),
            ("Description", a("description")),
        )

    if tool_name == "calendar_update_event":
        return "Modify a calendar event\n" + _lines(
            ("Event ID", a("event_id")),
            ("New title", a("summary")),
            ("New start", a("start")),
            ("New end", a("end")),
            ("New location", a("location")),
            ("Attendee list becomes", a("attendees")),
            ("New description", a("description")),
        )

    if tool_name == "calendar_delete_event":
        return "PERMANENTLY DELETE a calendar event\n" + _lines(("Event ID", a("event_id")))

    if tool_name == "slack_send_dm":
        return "Send a Slack DM (posted as you, from your own account)\n" + _lines(
            ("To", a("user")),
            ("Message", a("text")),
        )

    if tool_name == "slack_send_channel_message":
        return "Post a Slack channel message (posted as you)\n" + _lines(
            ("Channel", a("channel")),
            ("Message", a("text")),
        )

    if tool_name == "jira_create_issue":
        return "Create a Jira issue\n" + _lines(
            ("Project", a("project_key")),
            ("Type", a("issue_type")),
            ("Summary", a("summary")),
            ("Priority", a("priority")),
            ("Due", a("due_date")),
            ("Description", a("description")),
        )

    if tool_name == "jira_update_issue":
        return "Modify a Jira issue\n" + _lines(
            ("Issue", a("issue_key")),
            ("New summary", a("summary")),
            ("New description", a("description")),
            ("New priority", a("priority")),
            ("New due date", a("due_date")),
            ("New assignee", a("assignee")),
            ("New status", a("status")),
        )

    if tool_name == "jira_delete_issue":
        return "PERMANENTLY DELETE a Jira issue\n" + _lines(("Issue", a("issue_key")))

    if tool_name == "github_create_pull_request":
        return "Open a GitHub pull request\n" + _lines(
            ("Repo", f"{a('owner')}/{a('repo')}"),
            ("Title", a("title")),
            ("From branch", a("head")),
            ("Into branch", a("base")),
            ("Body", a("body")),
        )

    if tool_name == "github_merge_pull_request":
        return "MERGE a GitHub pull request (modifies the repository)\n" + _lines(
            ("Repo", f"{a('owner')}/{a('repo')}"),
            ("PR number", a("pull_number")),
            ("Merge method", a("merge_method")),
        )

    # Unknown write tool: show the raw args rather than pretending to summarize.
    return f"Run {tool_name}\n" + json.dumps(args, indent=2, default=str)


def assert_write_tools_classified(all_tool_names: set[str]) -> None:
    """Fail loudly if WRITE_TOOLS names a tool the agent no longer exposes."""
    unknown = WRITE_TOOLS - all_tool_names
    if unknown:
        raise RuntimeError(f"WRITE_TOOLS references unknown tools: {sorted(unknown)}")


def create_pending_action(
    db: Session,
    user_id: int,
    conversation_id: str | None,
    tool_name: str,
    args: dict,
) -> PendingAction:
    """Record a proposed write and supersede any earlier pending one."""
    db.query(PendingAction).filter(
        PendingAction.user_id == user_id,
        PendingAction.conversation_id == conversation_id,
        PendingAction.status == "pending",
    ).update({PendingAction.status: "superseded", PendingAction.resolved_at: _utcnow()},
             synchronize_session=False)

    action = PendingAction(
        user_id=user_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        tool_args=json.dumps(args, default=str),
        summary=render_summary(tool_name, args),
        status="pending",
        created_at=_utcnow(),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _is_expired(action: PendingAction) -> bool:
    created = action.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return _utcnow() - created > PENDING_ACTION_TTL


def get_claimable_action(db: Session, user_id: int, action_id: int) -> PendingAction:
    """Load a pending action, enforcing ownership, single-use, and TTL."""
    action = (
        db.query(PendingAction)
        .filter(PendingAction.id == action_id, PendingAction.user_id == user_id)
        .first()
    )
    if not action:
        raise ValueError("Pending action not found")
    if action.status != "pending":
        raise ValueError(f"This action was already {action.status}")
    if _is_expired(action):
        action.status = "expired"
        action.resolved_at = _utcnow()
        db.commit()
        raise ValueError("This approval request expired — ask again to get a fresh one")
    return action


def mark_resolved(
    db: Session,
    action: PendingAction,
    status: str,
    result: object = None,
) -> None:
    action.status = status
    action.resolved_at = _utcnow()
    if result is not None:
        action.result = json.dumps(result, default=str)[:8000]
    db.commit()


def serialize(action: PendingAction) -> dict:
    return {
        "id": action.id,
        "tool": action.tool_name,
        "summary": action.summary,
        "status": action.status,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "expires_in_seconds": int(PENDING_ACTION_TTL.total_seconds()),
    }
