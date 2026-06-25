import base64
import mimetypes
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from gmail_mcp.auth import load_credentials


def _header_value(headers: list[dict[str, str]], name: str) -> str | None:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def _decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _extract_body(payload: dict[str, Any]) -> dict[str, str | None]:
    body: dict[str, str | None] = {"plain": None, "html": None}

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data:
        decoded = _decode_body(body_data)
        if mime_type == "text/plain":
            body["plain"] = decoded
        elif mime_type == "text/html":
            body["html"] = decoded

    for part in payload.get("parts", []):
        part_body = _extract_body(part)
        if part_body["plain"] and not body["plain"]:
            body["plain"] = part_body["plain"]
        if part_body["html"] and not body["html"]:
            body["html"] = part_body["html"]

    return body


def _format_message(message: dict[str, Any], include_body: bool = True) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    body = _extract_body(payload) if include_body else {"plain": None, "html": None}

    attachments = []
    for part in payload.get("parts", []):
        filename = part.get("filename")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType"),
                    "attachment_id": part.get("body", {}).get("attachmentId"),
                    "size": part.get("body", {}).get("size"),
                }
            )

    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "label_ids": message.get("labelIds", []),
        "snippet": message.get("snippet"),
        "subject": _header_value(headers, "Subject"),
        "from": _header_value(headers, "From"),
        "to": _header_value(headers, "To"),
        "cc": _header_value(headers, "Cc"),
        "date": _header_value(headers, "Date"),
        "body_plain": body["plain"],
        "body_html": body["html"],
        "attachments": attachments,
    }


def _build_mime_message(
    to: str,
    subject: str,
    body: str,
    *,
    cc: str | None = None,
    bcc: str | None = None,
    html_body: str | None = None,
    attachments: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    if html_body:
        msg: MIMEMultipart = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        outer: MIMEMultipart = MIMEMultipart()
        outer.attach(msg)
    else:
        outer = MIMEMultipart()
        outer.attach(MIMEText(body, "plain"))

    outer["to"] = to
    outer["subject"] = subject
    if cc:
        outer["cc"] = cc
    if bcc:
        outer["bcc"] = bcc
    if in_reply_to:
        outer["In-Reply-To"] = in_reply_to
    if references:
        outer["References"] = references

    for attachment_path in attachments or []:
        path = Path(attachment_path)
        if not path.exists():
            raise FileNotFoundError(f"Attachment not found: {attachment_path}")

        mime_type, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        part = MIMEBase(maintype, subtype)
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        outer.attach(part)

    return outer


def _encode_message(msg: MIMEMultipart) -> dict[str, str]:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


class GmailClient:
    def __init__(self, credentials=None) -> None:
        creds = credentials if credentials is not None else load_credentials()
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def get_profile(self) -> dict[str, Any]:
        profile = self._service.users().getProfile(userId="me").execute()
        return {
            "email": profile.get("emailAddress"),
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
            "history_id": profile.get("historyId"),
        }

    def list_labels(self) -> list[dict[str, Any]]:
        result = self._service.users().labels().list(userId="me").execute()
        labels = result.get("labels", [])
        return [
            {
                "id": label.get("id"),
                "name": label.get("name"),
                "type": label.get("type"),
            }
            for label in labels
        ]

    def search_threads(
        self,
        query: str,
        *,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        max_results = max(1, min(max_results, 50))
        result = (
            self._service.users()
            .threads()
            .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
            .execute()
        )
        threads = []
        for thread_meta in result.get("threads", []):
            thread = (
                self._service.users()
                .threads()
                .get(userId="me", id=thread_meta["id"], format="metadata")
                .execute()
            )
            messages = thread.get("messages", [])
            first = messages[0] if messages else {}
            headers = first.get("payload", {}).get("headers", [])
            threads.append(
                {
                    "id": thread.get("id"),
                    "snippet": thread.get("snippet"),
                    "message_count": len(messages),
                    "subject": _header_value(headers, "Subject"),
                    "from": _header_value(headers, "From"),
                    "date": _header_value(headers, "Date"),
                }
            )
        return {
            "threads": threads,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate"),
        }

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = [
            _format_message(message, include_body=True)
            for message in thread.get("messages", [])
        ]
        return {
            "id": thread.get("id"),
            "snippet": thread.get("snippet"),
            "messages": messages,
        }

    def get_message(self, message_id: str) -> dict[str, Any]:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return _format_message(message, include_body=True)

    def list_unread(self, *, max_results: int = 10, page_token: str | None = None) -> dict[str, Any]:
        return self.search_threads(
            "is:unread in:inbox",
            max_results=max_results,
            page_token=page_token,
        )

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        cc: str | None = None,
        bcc: str | None = None,
        html_body: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        msg = _build_mime_message(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            attachments=attachments,
        )
        draft = (
            self._service.users()
            .drafts()
            .create(userId="me", body={"message": _encode_message(msg)})
            .execute()
        )
        return {
            "draft_id": draft.get("id"),
            "message_id": draft.get("message", {}).get("id"),
            "thread_id": draft.get("message", {}).get("threadId"),
        }

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        cc: str | None = None,
        bcc: str | None = None,
        html_body: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        msg = _build_mime_message(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            html_body=html_body,
            attachments=attachments,
        )
        sent = (
            self._service.users()
            .messages()
            .send(userId="me", body=_encode_message(msg))
            .execute()
        )
        return {
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "label_ids": sent.get("labelIds", []),
        }

    def reply_to_thread(
        self,
        thread_id: str,
        body: str,
        *,
        html_body: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = thread.get("messages", [])
        if not messages:
            raise ValueError(f"Thread {thread_id} has no messages")

        last_message = messages[-1]
        headers = last_message.get("payload", {}).get("headers", [])
        subject = _header_value(headers, "Subject") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message_id_header = _header_value(headers, "Message-ID") or _header_value(
            headers, "Message-Id"
        )
        from_header = _header_value(headers, "From") or ""
        to_header = _header_value(headers, "To") or ""
        _, reply_to = parseaddr(from_header)
        if not reply_to:
            raise ValueError("Could not determine reply recipient")

        references = _header_value(headers, "References") or ""
        if message_id_header:
            references = f"{references} {message_id_header}".strip()

        msg = _build_mime_message(
            reply_to,
            subject,
            body,
            html_body=html_body,
            attachments=attachments,
            in_reply_to=message_id_header,
            references=references or message_id_header,
        )
        sent = (
            self._service.users()
            .messages()
            .send(
                userId="me",
                body={"raw": _encode_message(msg)["raw"], "threadId": thread_id},
            )
            .execute()
        )
        return {
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": reply_to,
            "subject": subject,
        }

    def apply_labels(
        self,
        *,
        message_id: str | None = None,
        thread_id: str | None = None,
        label_ids: list[str],
    ) -> dict[str, Any]:
        if not label_ids:
            raise ValueError("label_ids must not be empty")
        if message_id:
            result = (
                self._service.users()
                .messages()
                .modify(userId="me", id=message_id, body={"addLabelIds": label_ids})
                .execute()
            )
            return {"id": result.get("id"), "label_ids": result.get("labelIds", [])}
        if thread_id:
            result = (
                self._service.users()
                .threads()
                .modify(userId="me", id=thread_id, body={"addLabelIds": label_ids})
                .execute()
            )
            return {"id": result.get("id"), "label_ids": result.get("labelIds", [])}
        raise ValueError("Either message_id or thread_id is required")

    def remove_labels(
        self,
        *,
        message_id: str | None = None,
        thread_id: str | None = None,
        label_ids: list[str],
    ) -> dict[str, Any]:
        if not label_ids:
            raise ValueError("label_ids must not be empty")
        if message_id:
            result = (
                self._service.users()
                .messages()
                .modify(userId="me", id=message_id, body={"removeLabelIds": label_ids})
                .execute()
            )
            return {"id": result.get("id"), "label_ids": result.get("labelIds", [])}
        if thread_id:
            result = (
                self._service.users()
                .threads()
                .modify(userId="me", id=thread_id, body={"removeLabelIds": label_ids})
                .execute()
            )
            return {"id": result.get("id"), "label_ids": result.get("labelIds", [])}
        raise ValueError("Either message_id or thread_id is required")

    def trash_message(self, message_id: str) -> dict[str, Any]:
        result = self._service.users().messages().trash(userId="me", id=message_id).execute()
        return {"id": result.get("id"), "label_ids": result.get("labelIds", [])}

    def archive_thread(self, thread_id: str) -> dict[str, Any]:
        return self.remove_labels(thread_id=thread_id, label_ids=["INBOX"])
