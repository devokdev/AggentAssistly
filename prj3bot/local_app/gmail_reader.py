"""Gmail API reader for inbox and thread context."""

from __future__ import annotations

import base64
import re
from typing import Any

from prj3bot.config.schema import Config
from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError


class GmailReaderError(RuntimeError):
    """Raised for Gmail read/thread failures."""


def _header(headers: list[dict[str, str]], name: str) -> str:
    needle = name.lower()
    for item in headers or []:
        if str(item.get("name", "")).lower() == needle:
            return str(item.get("value", "")).strip()
    return ""


def _clean_references(value: str) -> str:
    return " ".join(part.strip() for part in (value or "").split() if part.strip())


def _decode_base64url(raw: str) -> str:
    if not raw:
        return ""
    padded = raw + "=" * (-len(raw) % 4)
    data = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return data.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(payload: dict[str, Any]) -> str:
    if not payload:
        return ""

    mime_type = str(payload.get("mimeType", ""))
    body_data = (payload.get("body") or {}).get("data", "")
    parts = payload.get("parts") or []

    if mime_type == "text/plain" and body_data:
        return _decode_base64url(body_data).strip()
    if mime_type == "text/html" and body_data:
        return _html_to_text(_decode_base64url(body_data))

    plain_candidates: list[str] = []
    html_candidates: list[str] = []
    for part in parts:
        extracted = _extract_body(part)
        part_mime = str(part.get("mimeType", ""))
        if extracted:
            if part_mime == "text/plain":
                plain_candidates.append(extracted)
            elif part_mime == "text/html":
                html_candidates.append(extracted)
            else:
                plain_candidates.append(extracted)

    if plain_candidates:
        return "\n\n".join(plain_candidates).strip()
    if html_candidates:
        return "\n\n".join(html_candidates).strip()
    return ""


class GmailReader:
    """Read emails and threads from Gmail API."""

    def __init__(self, config: Config):
        self.config = config
        self._gw_client = GoogleWorkspaceClient.from_config(config.google_workspace)

    def _gmail_service(self):
        if not self.config.google_workspace.enabled:
            raise GmailReaderError("Google Workspace is not enabled.")
        try:
            return self._gw_client._service("gmail", "v1")
        except GoogleWorkspaceError as exc:
            raise GmailReaderError(str(exc)) from exc
        except Exception as exc:
            raise GmailReaderError(f"Gmail API initialization failed: {exc}") from exc

    def get_latest_emails(self, n: int, unread_only: bool = False) -> dict[str, Any]:
        service = self._gmail_service()
        try:
            query = "is:unread" if unread_only else None
            listing = (
                service.users()
                .messages()
                .list(userId="me", maxResults=max(1, int(n)), q=query, labelIds=["INBOX"])
                .execute()
            )
            refs = listing.get("messages", []) or []
            emails: list[dict[str, str]] = []
            for ref in refs:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
                payload = msg.get("payload", {}) or {}
                headers = payload.get("headers", []) or []
                body = _extract_body(payload) or "(No readable body)"
                emails.append(
                    {
                        "id": str(msg.get("id", "")),
                        "threadId": str(msg.get("threadId", "")),
                        "subject": _header(headers, "Subject") or "(No Subject)",
                        "from": _header(headers, "From"),
                        "to": _header(headers, "To"),
                        "date": _header(headers, "Date"),
                        "messageId": _header(headers, "Message-ID"),
                        "inReplyTo": _header(headers, "In-Reply-To"),
                        "references": _clean_references(_header(headers, "References")),
                        "snippet": str(msg.get("snippet", "")).strip(),
                        "body": body,
                    }
                )
            return {"type": "email_list", "emails": emails}
        except Exception as exc:
            raise GmailReaderError(f"Failed to read latest emails: {exc}") from exc

    def get_thread(self, thread_id: str) -> str:
        if not thread_id:
            raise GmailReaderError("Invalid threadId.")
        service = self._gmail_service()
        try:
            thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
            messages = thread.get("messages", []) or []
            if not messages:
                raise GmailReaderError("No messages found in thread.")
            blocks: list[str] = []
            for message in messages:
                payload = message.get("payload", {}) or {}
                headers = payload.get("headers", []) or []
                sender = _header(headers, "From") or "Unknown sender"
                subject = _header(headers, "Subject") or "(No Subject)"
                date_value = _header(headers, "Date")
                body = _extract_body(payload) or "(No readable body)"
                prefix = [f"From: {sender}", f"Subject: {subject}"]
                if date_value:
                    prefix.append(f"Date: {date_value}")
                blocks.append("\n".join(prefix) + f"\n\n{body}")
            return "\n\n".join(blocks).strip()
        except GmailReaderError:
            raise
        except Exception as exc:
            raise GmailReaderError(f"Failed to fetch thread: {exc}") from exc
