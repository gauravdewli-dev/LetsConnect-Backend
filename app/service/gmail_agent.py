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

MAX_TOOL_ROUNDS = 8

GMAIL_TOOL_NAMES = {t["name"] for t in GMAIL_TOOL_DEFINITIONS}
SLACK_TOOL_NAMES = {t["name"] for t in SLACK_TOOL_DEFINITIONS}


def _system_prompt(has_gmail: bool, has_slack: bool) -> str:
    parts = [
        "You are LetsConnect, a helpful AI assistant connected to the user's work tools.",
        "Keep replies concise and readable (short paragraphs, bullet lists when helpful).",
    ]
    if has_gmail:
        parts.append(
            "Gmail tools: fetch real email data before answering email questions. "
            "Use list_unread, search_messages, get_thread, get_message. "
            "Use send_email to send mail — search first to find the right recipient."
        )
    if has_slack:
        parts.append(
            "Slack rules: DMs and channel posts are sent AS THE LOGGED-IN USER (their own Slack account), "
            "not as a bot. A DM to Rohit appears in the user's normal 1:1 DM with Rohit. "
            "ALWAYS call slack_send_dm before claiming a message was sent. "
            "Use slack_get_workspace, slack_list_users, slack_read_channel, slack_send_channel_message. "
            "If slack_send_dm returns an error, report it — never claim success."
        )
    parts.append("More integrations (Jira, Teams) coming later.")
    return " ".join(parts)


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    gmail: GmailClient | None,
    slack: SlackTools | None,
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
    if not gmail_conn and not slack_conn:
        raise ValueError("Connect Gmail or Slack in the dashboard first")

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

    tool_definitions: list[dict[str, Any]] = []
    if gmail:
        tool_definitions.extend(GMAIL_TOOL_DEFINITIONS)
    if slack:
        tool_definitions.extend(SLACK_TOOL_DEFINITIONS)

    client = genai.Client(api_key=settings.gemini_api_key)
    tools_used: list[str] = []
    slack_dm_result: dict[str, Any] | None = None
    slack_dm_error: str | None = None
    config = _gemini_config(tool_definitions, _system_prompt(bool(gmail), bool(slack)))

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
                    result = _run_tool(name, args, gmail=gmail, slack=slack)
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
