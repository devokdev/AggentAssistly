"""Natural-language intent routing for the local desktop assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _extract_emails(text: str) -> list[str]:
    seen: set[str] = set()
    recipients: list[str] = []
    for match in EMAIL_RE.findall(text or ""):
        email = match.strip().lower()
        if email and email not in seen:
            seen.add(email)
            recipients.append(email)
    return recipients


def _extract_count(text: str, default: int = 10) -> int:
    match = re.search(r"\b(\d{1,2})\b", text or "")
    if not match:
        return default
    return max(1, int(match.group(1)))


def _extract_sender_filters(text: str) -> list[str]:
    lowered = _normalize(text)
    if " from " not in f" {lowered} ":
        return []
    return _extract_emails(text)


@dataclass(slots=True)
class IntentMatch:
    """Detected user intent and runtime parameters."""

    name: str
    tool: str
    action: str | None = None
    confidence: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool": self.tool,
            "action": self.action,
            "confidence": self.confidence,
            "parameters": self.parameters,
            "clarification": self.clarification,
        }


class IntentRouter:
    """Router for local desktop assistant intents."""

    def detect_intent(self, user_input: str) -> IntentMatch:
        text = (user_input or "").strip()
        lowered = _normalize(text)
        recipients = _extract_emails(text)
        sender_filters = _extract_sender_filters(text)
        if not lowered:
            return IntentMatch(
                name="general_chat",
                tool="chat",
                confidence=1.0,
                clarification="Tell me what you want to do.",
            )

        if any(phrase in lowered for phrase in ("reply to this email", "reply to that email", "reply to email")):
            return IntentMatch(
                name="reply_email",
                tool="email",
                action="reply_email",
                confidence=0.93,
                parameters={},
            )

        if "reply" in lowered and "email" in lowered:
            return IntentMatch(
                name="reply_email",
                tool="email",
                action="reply_email",
                confidence=0.88,
                parameters={"to": recipients},
            )

        is_read_intent = bool(re.search(r"\b(read|check|show|list|get|fetch|see|open|any)\b.*\b(email|emails|mail|mails|message|messages|inbox)\b", lowered))
        is_inbox = "inbox" in lowered or "latest email" in lowered or lowered.strip() in ("emails", "email", "mail")

        if is_read_intent or is_inbox:
            return IntentMatch(
                name="read_email",
                tool="email",
                action="read_email",
                confidence=0.92,
                parameters={
                    "unread_only": "unread" in lowered,
                    "count": _extract_count(lowered, default=10),
                    "from": sender_filters,
                },
            )

        send_cues = ("send email", "draft email", "compose email", "write email", "write a mail", "send mail")
        if any(token in lowered for token in send_cues) or recipients:
            return IntentMatch(
                name="send_email",
                tool="email",
                action="send_email",
                confidence=0.91,
                parameters={"to": recipients},
            )

        google_cues = (
            "google doc",
            "google docs",
            "drive",
            "sheet",
            "spreadsheet",
            "calendar",
            "meet",
            "slides",
            "classroom",
        )
        if any(cue in lowered for cue in google_cues):
            return IntentMatch(
                name="google_tools",
                tool="google_workspace",
                action="google_tools",
                confidence=0.8,
                parameters={},
            )

        return IntentMatch(
            name="general_chat",
            tool="chat",
            action="general_chat",
            confidence=0.55,
            parameters={},
        )

    def detect(self, message: str) -> IntentMatch:
        """Compatibility alias for existing local app code/tests."""
        return self.detect_intent(message)
