import json
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from gmail_mcp.gmail_client import GmailClient

mcp = FastMCP(
    "gmail",
    instructions=(
        "Gmail MCP server. Use get_profile for account info. "
        "Use search_threads with Gmail query syntax (from:, subject:, is:unread, etc.). "
        "Use get_thread or get_message to read full content. "
        "Use list_unread for unread inbox emails. "
        "Use create_draft or send_email to compose. "
        "Use reply_to_thread to reply. "
        "Use apply_labels/remove_labels, trash_message, archive_thread to manage mail."
    ),
)


@lru_cache(maxsize=1)
def _client() -> GmailClient:
    return GmailClient()


def _json(data: object) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def get_profile() -> str:
    """Get Gmail account profile: email address, message and thread counts."""
    return _json(_client().get_profile())


@mcp.tool()
def list_labels() -> str:
    """List all Gmail labels with their IDs."""
    return _json(_client().list_labels())


@mcp.tool()
def search_threads(
    query: str,
    max_results: int = 10,
    page_token: str | None = None,
) -> str:
    """Search email threads using Gmail query syntax.

    Args:
        query: Gmail search query (e.g. 'from:alice@example.com', 'is:unread', 'subject:invoice')
        max_results: Maximum threads to return (1-50, default 10)
        page_token: Pagination token from a previous search_threads response
    """
    return _json(
        _client().search_threads(query, max_results=max_results, page_token=page_token)
    )


@mcp.tool()
def get_thread(thread_id: str) -> str:
    """Get a full email thread with all messages and bodies.

    Args:
        thread_id: Gmail thread ID
    """
    return _json(_client().get_thread(thread_id))


@mcp.tool()
def get_message(message_id: str) -> str:
    """Get a single email message by ID.

    Args:
        message_id: Gmail message ID
    """
    return _json(_client().get_message(message_id))


@mcp.tool()
def list_unread(max_results: int = 10, page_token: str | None = None) -> str:
    """List unread emails in the inbox.

    Args:
        max_results: Maximum threads to return (1-50, default 10)
        page_token: Pagination token from a previous list_unread response
    """
    return _json(
        _client().list_unread(max_results=max_results, page_token=page_token)
    )


@mcp.tool()
def create_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Create a draft email.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        body: Plain text body
        cc: CC recipients, comma-separated
        bcc: BCC recipients, comma-separated
        html_body: Optional HTML body
        attachments: Optional list of absolute file paths to attach
    """
    return _json(
        _client().create_draft(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            attachments=attachments,
        )
    )


@mcp.tool()
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Send a new email.

    Args:
        to: Recipient email address(es), comma-separated
        subject: Email subject
        body: Plain text body
        cc: CC recipients, comma-separated
        bcc: BCC recipients, comma-separated
        html_body: Optional HTML body
        attachments: Optional list of absolute file paths to attach
    """
    return _json(
        _client().send_email(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            attachments=attachments,
        )
    )


@mcp.tool()
def reply_to_thread(
    thread_id: str,
    body: str,
    html_body: str | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Reply to the latest message in a thread.

    Args:
        thread_id: Gmail thread ID to reply to
        body: Plain text reply body
        html_body: Optional HTML reply body
        attachments: Optional list of absolute file paths to attach
    """
    return _json(
        _client().reply_to_thread(
            thread_id,
            body,
            html_body=html_body,
            attachments=attachments,
        )
    )


@mcp.tool()
def apply_labels(
    label_ids: list[str],
    message_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Add labels to a message or thread.

    Args:
        label_ids: Label IDs to add (use list_labels to find IDs)
        message_id: Gmail message ID (provide this or thread_id)
        thread_id: Gmail thread ID (provide this or message_id)
    """
    return _json(
        _client().apply_labels(
            message_id=message_id,
            thread_id=thread_id,
            label_ids=label_ids,
        )
    )


@mcp.tool()
def remove_labels(
    label_ids: list[str],
    message_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Remove labels from a message or thread.

    Args:
        label_ids: Label IDs to remove
        message_id: Gmail message ID (provide this or thread_id)
        thread_id: Gmail thread ID (provide this or message_id)
    """
    return _json(
        _client().remove_labels(
            message_id=message_id,
            thread_id=thread_id,
            label_ids=label_ids,
        )
    )


@mcp.tool()
def trash_message(message_id: str) -> str:
    """Move a message to trash.

    Args:
        message_id: Gmail message ID
    """
    return _json(_client().trash_message(message_id))


@mcp.tool()
def archive_thread(thread_id: str) -> str:
    """Archive a thread by removing it from the inbox.

    Args:
        thread_id: Gmail thread ID
    """
    return _json(_client().archive_thread(thread_id))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
