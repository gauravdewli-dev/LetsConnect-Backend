import json
from datetime import datetime, timezone
from typing import Any

from google.genai import types
from sqlalchemy.orm import Session

from app.config import get_settings
from app.service.gemini_keys import GeminiKeyPool, generate_content as gemini_generate_content
from app.service.gmail_tokens import (
    get_calendar_client_for_user,
    get_gmail_client_for_user,
    get_gmail_connection,
    get_google_credentials,
    get_slack_bot_token,
    get_slack_connection_by_user,
    get_slack_user_token,
    has_calendar_access,
)
from app.service.jira_tokens import get_jira_connection, get_jira_tools_for_user
from app.service.jira_tools import JiraTools
from app.service.slack_tools import SlackTools
from app.service.calendar import CalendarClient
from app.service.gmail import GmailClient

GMAIL_TOOL_DEFINITIONS = [
    {
        "name": "get_profile",
        "description": "Get Gmail account email and message/thread counts.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_labels",
        "description": "List all Gmail labels with IDs.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_unread",
        "description": "List unread emails in the inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max threads (1-50, default 10)"},
            },
        },
    },
    {
        "name": "search_messages",
        "description": "Search email threads using Gmail query syntax (from:, subject:, is:unread, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Max threads (1-50, default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_thread",
        "description": "Get a full email thread with all messages and bodies.",
        "parameters": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_message",
        "description": "Get a single email message by ID with full body.",
        "parameters": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "create_draft",
        "description": (
            "Save an email as a draft in the user's Gmail Drafts folder WITHOUT sending it. "
            "Use this when the user wants to draft/compose an email to review or send later in Gmail."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_email",
        "description": "Send a new email to a recipient.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]

CALENDAR_TOOL_DEFINITIONS = [
    {
        "name": "calendar_list_events",
        "description": (
            "List Google Calendar events. Prefer on_date (YYYY-MM-DD) for 'today', 'tomorrow', "
            "or a specific day. Resolve relative dates yourself from the system clock — never ask "
            "the user for a calendar date when they already said today/tomorrow/this week."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "description": "ISO 8601 start of range (default: now)",
                },
                "time_max": {
                    "type": "string",
                    "description": "ISO 8601 end of range (optional)",
                },
                "on_date": {
                    "type": "string",
                    "description": (
                        "Calendar day YYYY-MM-DD. Use for 'meetings today/tomorrow' or any single day."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search (title, attendees, etc.)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max events (1-50, default 10)",
                },
            },
        },
    },
    {
        "name": "calendar_get_event",
        "description": "Get full details for a calendar event by ID.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Create a Google Calendar event and optionally invite attendees. "
            "Sends calendar invites when attendees are included. "
            "Set add_google_meet=true to add a Google Meet video link."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time ISO 8601 or YYYY-MM-DD for all-day"},
                "end": {"type": "string", "description": "End time ISO 8601 or YYYY-MM-DD for all-day"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendee email addresses",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone, e.g. America/New_York (default: user's calendar timezone)",
                },
                "all_day": {"type": "boolean", "description": "All-day event (default false)"},
                "add_google_meet": {
                    "type": "boolean",
                    "description": "Add a Google Meet link (default false)",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Update an existing calendar event. Sends updates to attendees when changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace attendee list with these emails",
                },
                "timezone": {"type": "string"},
                "all_day": {"type": "boolean"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": (
            "Delete a calendar event permanently. ALWAYS confirm with the user before calling."
        ),
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
]

SLACK_TOOL_DEFINITIONS = [
    {
        "name": "slack_list_users",
        "description": "List Slack workspace users (id, name, real_name) to find who to message.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max users (1-200, default 50)"},
            },
        },
    },
    {
        "name": "slack_list_channels",
        "description": "List Slack channels the bot can access (public and private).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max channels (1-100, default 50)"},
            },
        },
    },
    {
        "name": "slack_get_workspace",
        "description": "Get the connected Slack workspace name and team ID.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "slack_read_channel",
        "description": (
            "Read recent messages from a Slack channel or DM by name (e.g. 'general', 'eng') or ID. "
            "Use immediately when the user asks for status, updates, or what's new in a channel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name (with or without #) or channel ID"},
                "limit": {"type": "integer", "description": "Number of messages (1-50, default 15)"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "slack_send_dm",
        "description": "Send a direct message to a Slack user. MUST be called before claiming a DM was sent.",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Slack username, display name, or user ID"},
                "text": {"type": "string", "description": "Message text to send"},
            },
            "required": ["user", "text"],
        },
    },
    {
        "name": "slack_send_channel_message",
        "description": "Post a message to a Slack channel by name (e.g. 'general') or channel ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name or ID"},
                "text": {"type": "string", "description": "Message text to post"},
            },
            "required": ["channel", "text"],
        },
    },
]

JIRA_TOOL_DEFINITIONS = [
    {
        "name": "jira_get_site",
        "description": "Get the connected Jira Cloud site URL and cloud ID.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "jira_list_projects",
        "description": "List Jira projects the user can access (key, name).",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max projects (1-100, default 50)"},
            },
        },
    },
    {
        "name": "jira_get_me",
        "description": (
            "Get the connected Jira user's identity (display name, account id). "
            "Use when you need to confirm who 'my tickets' refers to."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "jira_list_my_issues",
        "description": (
            "List Jira issues assigned to the connected user (assignee = currentUser()). "
            "PREFER this when the user asks about their tickets, my issues, my tasks, "
            "what is assigned to me, or open work on their board — unless they explicitly "
            "ask for all team tickets or another person's tickets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter, e.g. 'In Progress', 'Open', 'Done'",
                },
                "project_key": {
                    "type": "string",
                    "description": "Optional project key filter, e.g. PROJ",
                },
                "additional_jql": {
                    "type": "string",
                    "description": "Optional extra JQL AND clause, e.g. priority = High",
                },
                "max_results": {"type": "integer", "description": "Max issues (1-50, default 20)"},
            },
        },
    },
    {
        "name": "jira_search_issues",
        "description": (
            "Search all Jira issues with JQL (team-wide or custom filters). "
            "Use when the user asks for all tickets, project board, unassigned issues, "
            "or tickets assigned to someone else — NOT for 'my tickets' (use jira_list_my_issues)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jql": {"type": "string", "description": "JQL query"},
                "max_results": {"type": "integer", "description": "Max issues (1-50, default 20)"},
            },
            "required": ["jql"],
        },
    },
    {
        "name": "jira_get_issue",
        "description": "Get full details for a Jira issue by key (e.g. PROJ-123).",
        "parameters": {
            "type": "object",
            "properties": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
        },
    },
    {
        "name": "jira_create_issue",
        "description": (
            "Create a new Jira issue. Show the draft (project, type, summary, description, "
            "priority, due date, estimate) and confirm before calling unless the user asked to "
            "create immediately. Resolve relative due dates (Friday, in 2 days) from the system clock."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string", "description": "Project key, e.g. PROJ"},
                "summary": {"type": "string"},
                "issue_type": {"type": "string", "description": "Task, Bug, Story, etc."},
                "description": {"type": "string"},
                "priority": {"type": "string", "description": "Optional priority name"},
                "due_date": {
                    "type": "string",
                    "description": "Due date as YYYY-MM-DD (resolve relative phrases yourself)",
                },
                "original_estimate": {
                    "type": "string",
                    "description": "Time estimate in Jira format, e.g. 2h, 1d, 30m",
                },
            },
            "required": ["project_key", "summary", "issue_type"],
        },
    },
    {
        "name": "jira_update_issue",
        "description": (
            "Update an existing Jira issue (summary, description, priority, due date, or estimate). "
            "Confirm changes with the user before calling unless they asked to update immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "due_date": {
                    "type": "string",
                    "description": "Due date as YYYY-MM-DD",
                },
                "original_estimate": {
                    "type": "string",
                    "description": "Time estimate in Jira format, e.g. 2h, 1d, 30m",
                },
            },
            "required": ["issue_key"],
        },
    },
    {
        "name": "jira_delete_issue",
        "description": (
            "Permanently delete a Jira issue. ALWAYS confirm with the user before calling — "
            "deletion cannot be undone."
        ),
        "parameters": {
            "type": "object",
            "properties": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
        },
    },
]

MAX_TOOL_ROUNDS = 8

GMAIL_TOOL_NAMES = {t["name"] for t in GMAIL_TOOL_DEFINITIONS}
CALENDAR_TOOL_NAMES = {t["name"] for t in CALENDAR_TOOL_DEFINITIONS}
SLACK_TOOL_NAMES = {t["name"] for t in SLACK_TOOL_DEFINITIONS}
JIRA_TOOL_NAMES = {t["name"] for t in JIRA_TOOL_DEFINITIONS}


def _clock_context() -> str:
    now = datetime.now(timezone.utc)
    return (
        f"Current date/time (UTC): {now.strftime('%Y-%m-%dT%H:%M:%SZ')}. "
        f"Today's calendar date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}). "
        "Resolve relative dates yourself (today, tomorrow, this week, next Monday, Friday, in 2 days) "
        "from this clock. Never ask the user for a calendar date when they already used relative "
        "language. When you state a plan, always show the resolved absolute date "
        "(e.g. 'tomorrow → 2026-07-13 (Monday)')."
    )


def _system_prompt(has_gmail: bool, has_calendar: bool, has_slack: bool, has_jira: bool) -> str:
    parts = [
        "You are LetsConnect, a helpful AI assistant connected to the user's work tools.",
        "Be proactive and smart: call tools when you already have enough to act; only ask for "
        "details that are truly missing.",
        "Keep replies concise and readable (short paragraphs, bullet lists when helpful).",
        _clock_context(),
        "DRAFTING: When the user asks you to write, draft, compose, or reply to a message or email, "
        "FIRST produce the full draft as text and show it to them — do NOT send yet. "
        "Render it clearly (for email, include a 'Subject:' line and the body; for chat, the message text). "
        "Then ask the user to confirm, edit, or send. "
        "Only call a send tool (send_email, slack_send_dm, slack_send_channel_message) AFTER the user "
        "explicitly approves (e.g. 'send it', 'yes', 'go ahead'). "
        "EXCEPTION: if the user clearly asks to send immediately in one step "
        "(e.g. 'email Alex saying I'll be late'), draft and send in the same turn. "
        "Match the tone the user requests (formal, friendly, brief); when unspecified, keep it professional and warm. "
        "If you lack details needed for a good draft (recipient, key facts), ask before drafting.",
    ]
    if has_gmail:
        parts.append(
            "Gmail tools: fetch real email data before answering email questions. "
            "Use list_unread, search_messages, get_thread, get_message. "
            "To send mail, use send_email — search first to find the right recipient address if you "
            "only have a name. When replying to an existing thread, read it first with get_thread so "
            "your draft quotes the right context and keeps the subject line. "
            "Always show the drafted subject and body for approval before calling send_email "
            "(unless the user asked to send in one step). "
            "If the user wants to keep editing in Gmail or save for later instead of sending, use "
            "create_draft to save it in their Gmail Drafts folder, then tell them it's saved as a draft."
        )
    if has_calendar:
        parts.append(
            "Google Calendar tools: schedule and manage meetings on the user's primary calendar. "
            "DATE RESOLUTION: For 'today', 'tomorrow', 'this week', or similar, compute YYYY-MM-DD "
            "from the system clock and call calendar_list_events immediately (prefer on_date for a "
            "single day, or time_min/time_max for a range). Do NOT ask which date they mean. "
            "SCHEDULING FLOW: When the user wants to book a meeting and gives a relative day "
            "(e.g. 'schedule a meeting tomorrow'): "
            "(1) Resolve the absolute date and say it clearly in your reply. "
            "(2) Ask only for missing essentials — agenda/title, start time (or propose one), "
            "teammates to invite (emails or names), and whether to add Google Meet. "
            "(3) If time is missing, propose a sensible default (e.g. 30 minutes mid-morning or "
            "early afternoon in their calendar timezone) and confirm once. "
            "(4) Show the full proposal (title, resolved date/time, attendees, Meet yes/no), then "
            "ask for confirmation before calendar_create_event — unless they asked to book in one step. "
            "Use ISO 8601 datetimes; use the timezone from calendar_list_events when available. "
            "Check conflicts with calendar_list_events before booking when helpful. "
            "When the user wants a video call, set add_google_meet=true. "
            "After creating, include the resolved date/time, html_link, and meet_link. "
            "For calendar_update_event, confirm changes first unless they asked to update immediately. "
            "For calendar_delete_event, ALWAYS get explicit confirmation — deletion is permanent."
        )
    if has_slack:
        parts.append(
            "Slack rules: DMs and channel posts are sent AS THE LOGGED-IN USER (their own Slack account), "
            "not as a bot. A DM to Rohit appears in the user's normal 1:1 DM with Rohit. "
            "STATUS / UPDATES: When the user asks what's new, any updates, status in a channel, or "
            "recent messages — if a channel is named, call slack_read_channel immediately and "
            "summarize (who said what, key points). If no channel is named, call slack_list_channels "
            "and ask which one, or read an obvious match if the name is clear from context. "
            "Prefer a short human summary over dumping raw message lists. "
            "ALWAYS call slack_send_dm before claiming a message was sent. "
            "Use slack_get_workspace, slack_list_users, slack_read_channel, slack_send_channel_message. "
            "Show the drafted message text for approval before sending (unless the user asked to send in one step). "
            "If slack_send_dm returns an error, report it — never claim success."
        )
    if has_jira:
        parts.append(
            "Jira rules: DEFAULT to the connected user's own tickets. When they ask about "
            "'my tickets', 'my issues', 'my tasks', 'assigned to me', or open work without "
            "naming someone else, call jira_list_my_issues (assignee = currentUser()). "
            "Only use jira_search_issues for all team tickets, project-wide boards, "
            "unassigned issues, or another person's tickets when explicitly requested. "
            "Use jira_get_me if you need the user's Jira display name. "
            "Use jira_get_issue for a single ticket by key. Use jira_list_projects when the user "
            "doesn't know project keys. "
            "CREATE FLOW: When creating a ticket, gather what's useful — project, type, summary, "
            "description, priority, due date / timeline, and estimate. Resolve relative due dates "
            "(Friday, next week, in 2 days) from the system clock and state the YYYY-MM-DD. "
            "Pass due_date and original_estimate (e.g. 2h, 1d) when the user gives them. "
            "If project or type is missing, ask or list projects first. "
            "Show the draft fields and ask for confirmation before jira_create_issue or "
            "jira_update_issue (unless the user asked to do it in one step). "
            "For jira_delete_issue, ALWAYS get explicit confirmation — deletion is permanent. "
            "Include browse_url links when sharing issue keys. Mention assignee, due date, and "
            "estimate when listing or confirming tickets."
        )
    if not has_jira:
        parts.append("More integrations (Teams) coming later.")
    return " ".join(parts)


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    gmail: GmailClient | None,
    calendar: CalendarClient | None,
    slack: SlackTools | None,
    jira: JiraTools | None,
) -> Any:
    if name in GMAIL_TOOL_NAMES:
        if not gmail:
            raise ValueError("Gmail not connected")
        if name == "get_profile":
            return gmail.get_profile()
        if name == "list_labels":
            return gmail.list_labels()
        if name == "list_unread":
            return gmail.list_unread(max_results=args.get("max_results", 10))
        if name == "search_messages":
            return gmail.search_threads(args["query"], max_results=args.get("max_results", 10))
        if name == "get_thread":
            return gmail.get_thread(args["thread_id"])
        if name == "get_message":
            return gmail.get_message(args["message_id"])
        if name == "create_draft":
            return gmail.create_draft(args["to"], args["subject"], args["body"])
        if name == "send_email":
            return gmail.send_email(args["to"], args["subject"], args["body"])

    if name in CALENDAR_TOOL_NAMES:
        if not calendar:
            raise ValueError("Google Calendar not connected — reconnect Gmail to grant calendar access")
        if name == "calendar_list_events":
            return calendar.list_events(
                time_min=args.get("time_min"),
                time_max=args.get("time_max"),
                on_date=args.get("on_date"),
                max_results=args.get("max_results", 10),
                query=args.get("query"),
            )
        if name == "calendar_get_event":
            return calendar.get_event(args["event_id"])
        if name == "calendar_create_event":
            return calendar.create_event(
                args["summary"],
                args["start"],
                args["end"],
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
                timezone_name=args.get("timezone"),
                all_day=args.get("all_day", False),
                add_google_meet=args.get("add_google_meet", False),
            )
        if name == "calendar_update_event":
            return calendar.update_event(
                args["event_id"],
                summary=args.get("summary"),
                start=args.get("start"),
                end=args.get("end"),
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
                timezone_name=args.get("timezone"),
                all_day=args.get("all_day", False),
            )
        if name == "calendar_delete_event":
            return calendar.delete_event(args["event_id"])

    if name in SLACK_TOOL_NAMES:
        if not slack:
            raise ValueError("Slack not connected")
        if name == "slack_list_users":
            return slack.list_users(limit=args.get("limit", 50))
        if name == "slack_list_channels":
            return slack.list_channels(limit=args.get("limit", 50))
        if name == "slack_get_workspace":
            return slack.get_workspace()
        if name == "slack_read_channel":
            return slack.read_channel(args["channel"], limit=args.get("limit", 15))
        if name == "slack_send_dm":
            return slack.send_dm(args["user"], args["text"])
        if name == "slack_send_channel_message":
            return slack.send_channel_message(args["channel"], args["text"])

    if name in JIRA_TOOL_NAMES:
        if not jira:
            raise ValueError("Jira not connected")
        if name == "jira_get_site":
            return jira.get_site()
        if name == "jira_get_me":
            return jira.get_me()
        if name == "jira_list_projects":
            return jira.list_projects(max_results=args.get("max_results", 50))
        if name == "jira_list_my_issues":
            return jira.list_my_issues(
                status=args.get("status"),
                project_key=args.get("project_key"),
                additional_jql=args.get("additional_jql"),
                max_results=args.get("max_results", 20),
            )
        if name == "jira_search_issues":
            return jira.search_issues(args["jql"], max_results=args.get("max_results", 20))
        if name == "jira_get_issue":
            return jira.get_issue(args["issue_key"])
        if name == "jira_create_issue":
            return jira.create_issue(
                args["project_key"],
                args["summary"],
                args["issue_type"],
                description=args.get("description"),
                priority=args.get("priority"),
                due_date=args.get("due_date"),
                original_estimate=args.get("original_estimate"),
            )
        if name == "jira_update_issue":
            return jira.update_issue(
                args["issue_key"],
                summary=args.get("summary"),
                description=args.get("description"),
                priority=args.get("priority"),
                due_date=args.get("due_date"),
                original_estimate=args.get("original_estimate"),
            )
        if name == "jira_delete_issue":
            return jira.delete_issue(args["issue_key"])

    raise ValueError(f"Unknown tool: {name}")


def _gemini_config(tool_definitions: list[dict[str, Any]], system_instruction: str) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=tool_definitions)],
        system_instruction=system_instruction,
    )


def _extract_reply(parts: list[types.Part]) -> str:
    return "".join(part.text for part in parts if part.text)


def _gemini_finish_reason(candidate: Any) -> str | None:
    reason = getattr(candidate, "finish_reason", None)
    if reason is None:
        return None
    return str(getattr(reason, "name", reason))


def _empty_candidate_message(candidate: Any) -> str:
    reason = _gemini_finish_reason(candidate)
    if reason and reason not in {"STOP", "FinishReason.STOP"}:
        return f"Gemini blocked the response ({reason}). Try rephrasing your question."
    return "Gemini returned an empty response. Please try again."


def _build_contents(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> list[types.Content]:
    contents: list[types.Content] = []
    for item in history or []:
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=item["content"])])
        )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    return contents


def _finalize_reply(
    reply: str,
    tools_used: list[str],
    *,
    slack_dm_result: dict[str, Any] | None,
    slack_dm_error: str | None,
) -> str:
    reply_lower = reply.lower()
    claims_slack_sent = any(
        phrase in reply_lower
        for phrase in ("sent a dm", "i sent", "message sent", "saying '", "saying \"")
    )

    if claims_slack_sent and "slack_send_dm" not in tools_used:
        return (
            "I could not confirm that Slack message was sent — the send API was not called. "
            "Please try again."
        )

    if "slack_send_dm" in tools_used and slack_dm_error and not slack_dm_result:
        return f"Failed to send Slack DM: {slack_dm_error}"

    if slack_dm_result and slack_dm_result.get("ts"):
        recipient = slack_dm_result.get("user_name", "the recipient")
        if slack_dm_result.get("sent_as") == "user":
            note = (
                f"\n\n**Delivered:** Sent as you in your normal DM with {recipient}. "
                "Open that conversation in Slack to see it."
            )
        else:
            note = (
                f"\n\n**Where to find it:** Sent by the **LetsConnect bot** to {recipient}. "
                "They see it under **Apps → LetsConnect** in Slack."
            )
        if note.strip() not in reply:
            reply += note

    return reply


def run_agent(
    db: Session,
    user_id: int,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_keys:
        raise ValueError("GEMINI_API_KEY is not set")

    gmail_conn = get_gmail_connection(db, user_id)
    slack_conn = get_slack_connection_by_user(db, user_id)
    jira_conn = get_jira_connection(db, user_id)
    if not gmail_conn and not slack_conn and not jira_conn:
        raise ValueError("Connect Gmail, Slack, or Jira in the dashboard first")

    google_creds = None
    gmail = None
    calendar = None
    if gmail_conn:
        try:
            gmail_conn, google_creds = get_google_credentials(db, user_id)
            gmail = GmailClient(credentials=google_creds)
            if has_calendar_access(gmail_conn, creds=google_creds):
                calendar = CalendarClient(credentials=google_creds)
        except ValueError:
            gmail = None
            calendar = None
    slack = (
        SlackTools(
            get_slack_bot_token(slack_conn),
            user_token=get_slack_user_token(slack_conn),
            team_id=slack_conn.slack_team_id,
        )
        if slack_conn
        else None
    )
    jira = get_jira_tools_for_user(db, user_id) if jira_conn else None

    tool_definitions: list[dict[str, Any]] = []
    if gmail:
        tool_definitions.extend(GMAIL_TOOL_DEFINITIONS)
    if calendar:
        tool_definitions.extend(CALENDAR_TOOL_DEFINITIONS)
    if slack:
        tool_definitions.extend(SLACK_TOOL_DEFINITIONS)
    if jira:
        tool_definitions.extend(JIRA_TOOL_DEFINITIONS)

    system_instruction = _system_prompt(bool(gmail), bool(calendar), bool(slack), bool(jira))
    if gmail_conn and google_creds and not has_calendar_access(gmail_conn, creds=google_creds):
        system_instruction += (
            " Google Calendar is NOT connected yet — if the user asks about meetings or scheduling, "
            "tell them to click Gmail on the dashboard integration graph (or 'Enable Google Calendar') "
            "to reconnect, then allow the Calendar permission on the Google consent screen. "
            "Do NOT call calendar tools."
        )
    if jira_conn and jira_conn.jira_display_name:
        system_instruction += (
            f" The connected Jira user is {jira_conn.jira_display_name!r} — "
            "'my tickets' means issues assigned to this user."
        )

    gemini_pool = GeminiKeyPool(settings.gemini_api_keys)
    tools_used: list[str] = []
    slack_dm_result: dict[str, Any] | None = None
    slack_dm_error: str | None = None
    config = _gemini_config(
        tool_definitions,
        system_instruction,
    )

    contents: list[types.Content] = _build_contents(message, history)
    empty_retries = 0

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = gemini_generate_content(
                gemini_pool,
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except ValueError:
            raise

        if not response.candidates:
            raise ValueError("Gemini returned no response")

        candidate = response.candidates[0]
        model_content = candidate.content
        if not model_content or not model_content.parts:
            if empty_retries < 1:
                empty_retries += 1
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="Please answer in plain text.")],
                    )
                )
                continue
            raise ValueError(_empty_candidate_message(candidate))

        function_calls = [part for part in model_content.parts if part.function_call]
        if function_calls:
            contents.append(model_content)
            tool_response_parts: list[types.Part] = []

            for part in function_calls:
                function_call = part.function_call
                if not function_call or not function_call.name:
                    continue

                name = function_call.name
                args = dict(function_call.args) if function_call.args else {}
                tools_used.append(name)
                try:
                    result = _run_tool(name, args, gmail=gmail, calendar=calendar, slack=slack, jira=jira)
                except Exception as exc:
                    result = {"error": str(exc)}

                if name == "slack_send_dm":
                    if isinstance(result, dict) and result.get("error"):
                        slack_dm_error = str(result["error"])
                    elif isinstance(result, dict) and result.get("ok") and result.get("ts"):
                        slack_dm_result = result
                        slack_dm_error = None

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={"result": json.loads(json.dumps(result, default=str))},
                    )
                )

            if not tool_response_parts:
                raise ValueError("Gemini issued invalid tool calls — please try again.")

            contents.append(types.Content(role="user", parts=tool_response_parts))
            continue

        reply = _finalize_reply(
            _extract_reply(model_content.parts),
            tools_used,
            slack_dm_result=slack_dm_result,
            slack_dm_error=slack_dm_error,
        )
        return {"reply": reply, "tools_used": tools_used}

    return {
        "reply": "I couldn't finish answering — too many steps. Try a simpler question.",
        "tools_used": tools_used,
    }
