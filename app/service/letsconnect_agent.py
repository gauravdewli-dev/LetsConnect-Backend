import json
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.service.gmail_tokens import (
    get_gmail_client_for_user,
    get_gmail_connection,
    get_slack_bot_token,
    get_slack_connection_by_user,
    get_slack_user_token,
)
from app.service.jira_tokens import get_jira_connection, get_jira_tools_for_user
from app.service.jira_tools import JiraTools
from app.service.slack_tools import SlackTools
from gmail_mcp.gmail_client import GmailClient

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
        "description": "Read recent messages from a Slack channel or DM by name (e.g. 'room_alerts') or ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name (with or without #) or channel ID"},
                "limit": {"type": "integer", "description": "Number of messages (1-50, default 10)"},
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
        "name": "jira_search_issues",
        "description": "Search Jira issues with JQL (e.g. project=PROJ AND status=Open).",
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
            "Create a new Jira issue. Show the draft (project, type, summary, description) "
            "and confirm with the user before calling unless they asked to create immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string", "description": "Project key, e.g. PROJ"},
                "summary": {"type": "string"},
                "issue_type": {"type": "string", "description": "Task, Bug, Story, etc."},
                "description": {"type": "string"},
                "priority": {"type": "string", "description": "Optional priority name"},
            },
            "required": ["project_key", "summary", "issue_type"],
        },
    },
    {
        "name": "jira_update_issue",
        "description": (
            "Update an existing Jira issue (summary, description, or priority). "
            "Confirm changes with the user before calling unless they asked to update immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
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
SLACK_TOOL_NAMES = {t["name"] for t in SLACK_TOOL_DEFINITIONS}
JIRA_TOOL_NAMES = {t["name"] for t in JIRA_TOOL_DEFINITIONS}


def _system_prompt(has_gmail: bool, has_slack: bool, has_jira: bool) -> str:
    parts = [
        "You are LetsConnect, a helpful AI assistant connected to the user's work tools.",
        "Keep replies concise and readable (short paragraphs, bullet lists when helpful).",
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
    if has_slack:
        parts.append(
            "Slack rules: DMs and channel posts are sent AS THE LOGGED-IN USER (their own Slack account), "
            "not as a bot. A DM to Rohit appears in the user's normal 1:1 DM with Rohit. "
            "ALWAYS call slack_send_dm before claiming a message was sent. "
            "Use slack_get_workspace, slack_list_users, slack_read_channel, slack_send_channel_message. "
            "Show the drafted message text for approval before sending (unless the user asked to send in one step). "
            "If slack_send_dm returns an error, report it — never claim success."
        )
    if has_jira:
        parts.append(
            "Jira rules: use jira_search_issues with JQL before answering ticket questions. "
            "Use jira_get_issue for a single ticket. Use jira_list_projects when the user "
            "doesn't know project keys. "
            "For create/update, show the draft fields and ask for confirmation before calling "
            "jira_create_issue or jira_update_issue (unless the user asked to do it in one step). "
            "For jira_delete_issue, ALWAYS get explicit confirmation — deletion is permanent. "
            "Include browse_url links when sharing issue keys."
        )
    if not has_jira:
        parts.append("More integrations (Teams) coming later.")
    return " ".join(parts)


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    gmail: GmailClient | None,
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
            return slack.read_channel(args["channel"], limit=args.get("limit", 10))
        if name == "slack_send_dm":
            return slack.send_dm(args["user"], args["text"])
        if name == "slack_send_channel_message":
            return slack.send_channel_message(args["channel"], args["text"])

    if name in JIRA_TOOL_NAMES:
        if not jira:
            raise ValueError("Jira not connected")
        if name == "jira_get_site":
            return jira.get_site()
        if name == "jira_list_projects":
            return jira.list_projects(max_results=args.get("max_results", 50))
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
            )
        if name == "jira_update_issue":
            return jira.update_issue(
                args["issue_key"],
                summary=args.get("summary"),
                description=args.get("description"),
                priority=args.get("priority"),
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
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    gmail_conn = get_gmail_connection(db, user_id)
    slack_conn = get_slack_connection_by_user(db, user_id)
    jira_conn = get_jira_connection(db, user_id)
    if not gmail_conn and not slack_conn and not jira_conn:
        raise ValueError("Connect Gmail, Slack, or Jira in the dashboard first")

    gmail = get_gmail_client_for_user(db, user_id) if gmail_conn else None
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
    if slack:
        tool_definitions.extend(SLACK_TOOL_DEFINITIONS)
    if jira:
        tool_definitions.extend(JIRA_TOOL_DEFINITIONS)

    client = genai.Client(api_key=settings.gemini_api_key)
    tools_used: list[str] = []
    slack_dm_result: dict[str, Any] | None = None
    slack_dm_error: str | None = None
    config = _gemini_config(
        tool_definitions,
        _system_prompt(bool(gmail), bool(slack), bool(jira)),
    )

    contents: list[types.Content] = _build_contents(message, history)

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except ClientError as exc:
            if exc.code == 429:
                raise ValueError(
                    "Gemini rate limit reached — wait a minute and try again, "
                    "or switch GEMINI_MODEL in .env (e.g. gemini-2.5-flash)."
                ) from exc
            raise ValueError(f"Gemini API error: {exc}") from exc

        if not response.candidates:
            raise ValueError("Gemini returned no response")

        candidate = response.candidates[0]
        model_content = candidate.content
        if not model_content or not model_content.parts:
            raise ValueError("Gemini returned empty content")

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
                    result = _run_tool(name, args, gmail=gmail, slack=slack, jira=jira)
                except (ValueError, RuntimeError) as exc:
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
