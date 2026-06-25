import json
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import ClientError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.service.gmail_tokens import get_gmail_client_for_user, get_gmail_connection
from gmail_mcp.gmail_client import GmailClient

SYSTEM_PROMPT = """You are a helpful Gmail assistant connected to the user's account via Slack.
Use the provided tools to fetch real email data before answering.
When summarizing emails, include useful details: sender, subject, date, and key content.
For unread mail use list_unread or search_messages.
For full content use get_thread or get_message.
To send email use send_message — always search first to find the right recipient.
Keep replies concise and readable in Slack (short paragraphs, bullet lists when helpful).
Be concise but thorough. If no emails match, say so clearly."""

TOOL_DEFINITIONS = [
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
        "name": "send_message",
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

MAX_TOOL_ROUNDS = 8


def _run_tool(gmail: GmailClient, name: str, args: dict[str, Any]) -> Any:
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
    if name == "send_message":
        return gmail.send_email(args["to"], args["subject"], args["body"])
    raise ValueError(f"Unknown tool: {name}")


def _gemini_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=TOOL_DEFINITIONS)],
        system_instruction=SYSTEM_PROMPT,
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


def run_agent(
    db: Session,
    user_id: int,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    if not get_gmail_connection(db, user_id):
        raise ValueError("Gmail not connected — connect Gmail in the dashboard first")

    client = genai.Client(api_key=settings.gemini_api_key)
    gmail = get_gmail_client_for_user(db, user_id)
    tools_used: list[str] = []
    config = _gemini_config()

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
                result = _run_tool(gmail, name, args)
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={"result": json.loads(json.dumps(result, default=str))},
                    )
                )

            contents.append(types.Content(role="user", parts=tool_response_parts))
            continue

        return {"reply": _extract_reply(model_content.parts), "tools_used": tools_used}

    return {
        "reply": "I couldn't finish answering — too many steps. Try a simpler question.",
        "tools_used": tools_used,
    }
