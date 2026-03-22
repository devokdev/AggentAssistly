"""Local runtime for desktop-first prj3bot assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zipfile import ZipFile
import json

from loguru import logger

from prj3bot.agent.loop import AgentLoop
from prj3bot.bus.events import InboundMessage
from prj3bot.bus.queue import MessageBus
from prj3bot.channels.email import EmailChannel
from prj3bot.config.loader import load_config, save_config
from prj3bot.config.schema import Config
from prj3bot.cli.commands import _gog_generate_doc_draft, _gog_parse_json_object
from prj3bot.local_app.email_assistant import EmailAssistant
from prj3bot.local_app.gmail_reader import GmailReader, GmailReaderError
from prj3bot.local_app.intent_router import IntentRouter
from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError
from prj3bot.utils.helpers import sync_workspace_templates


def _build_provider(config: Config):
    """Return an LLM provider if one is configured, otherwise None."""
    from prj3bot.providers.custom_provider import CustomProvider
    from prj3bot.providers.litellm_provider import LiteLLMProvider
    from prj3bot.providers.openai_codex_provider import OpenAICodexProvider
    from prj3bot.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    provider_cfg = config.get_provider(model)

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    if provider_name == "custom":
        return CustomProvider(
            api_key=provider_cfg.api_key if provider_cfg else "",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    spec = find_by_name(provider_name) if provider_name else None
    if not model.startswith("bedrock/") and not (provider_cfg and provider_cfg.api_key) and not (
        spec and spec.is_oauth
    ):
        return None

    return LiteLLMProvider(
        api_key=provider_cfg.api_key if provider_cfg else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=provider_cfg.extra_headers if provider_cfg else None,
        provider_name=provider_name,
    )


def _preview_email(content: str, max_len: int = 160) -> str:
    body = (content or "").split("\n\n", 1)
    text = body[1].strip() if len(body) > 1 else (content or "").strip()
    text = " ".join(text.split())
    return text[: max_len - 3] + "..." if len(text) > max_len else text


def _imap_preview_from_content(content: str) -> str:
    return _preview_email(content or "")


def _extract_normalized_emails(value: str | list[str]) -> list[str]:
    return EmailChannel.normalize_recipients(value)


def _clean_email_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_lookup_text(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _lookup_tokens(value: str) -> list[str]:
    stopwords = {
        "a", "an", "and", "any", "as", "at", "by", "email", "for", "from", "mail", "mails",
        "message", "messages", "my", "of", "on", "please", "regarding", "reply", "respond",
        "saying", "send", "that", "the", "this", "to", "with",
    }
    return [token for token in _normalize_lookup_text(value).split() if len(token) >= 3 and token not in stopwords]


def _extract_email_reference_hint(message: str) -> str:
    normalized = _normalize_lookup_text(message)
    patterns = (
        r"(?:reply|respond)\s+to\s+(.+?)\s+(?:email|mail|message)s?\b",
        r"(?:reply|respond)\s+(.+?)\s+(?:email|mail|message)s?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            hint = match.group(1).strip()
            if hint and hint not in {"this", "that"}:
                return hint
    return ""


def _attachment_context_block(attachments: list[dict[str, str]] | None, transcription: str = "") -> str:
    parts: list[str] = []
    if transcription.strip():
        parts.append(f"[Voice Transcription]\n{transcription.strip()}")
    for item in attachments or []:
        name = (item.get("name") or "attachment").strip()
        content = (item.get("content") or "").strip()
        if content:
            parts.append(f"[Attachment: {name}]\n{content}")
    return "\n\n".join(parts).strip()


def _merge_prompt_with_context(message: str, attachments: list[dict[str, str]] | None, transcription: str = "") -> str:
    context = _attachment_context_block(attachments, transcription=transcription)
    if not context:
        return message
    base = (message or "").strip() or "Please use the provided attachment context."
    return f"{base}\n\nUse this uploaded context when responding:\n\n{context}"


def _looks_like_google_doc_request(message: str) -> bool:
    normalized = _normalize_lookup_text(message)
    has_doc_word = any(term in normalized for term in ("google doc", "google docs", "document", "doc"))
    has_create_word = any(term in normalized for term in ("create", "make", "generate", "write", "draft"))
    return has_doc_word and has_create_word


def _extract_doc_title_hint(message: str) -> str:
    normalized = re.sub(r"\s+", " ", (message or "").strip())
    patterns = (
        r"\b(?:titled|called|named)\s+['\"]?([^'\"]+?)['\"]?(?:\s+(?:about|on|for|with)\b|$)",
        r"\btitle\s+['\"]?([^'\"]+?)['\"]?(?:\s+(?:about|on|for|with)\b|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _format_email_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return raw


def _email_summary(item: dict[str, Any], max_len: int = 220) -> str:
    preview = (
        str(item.get("preview", "")).strip()
        or str(item.get("snippet", "")).strip()
        or _clean_email_text(str(item.get("body", "")).split("\n\n", 1)[-1])
    )
    preview = " ".join(preview.split())
    return preview[: max_len - 3] + "..." if len(preview) > max_len else preview


def _email_body_excerpt(item: dict[str, Any], max_len: int = 420) -> str:
    body = _clean_email_text(str(item.get("body", "")))
    if body.lower().startswith("email received."):
        parts = body.split("\n\n", 1)
        body = parts[1].strip() if len(parts) > 1 else body
    body = body or str(item.get("snippet", "")).strip() or "(No preview available)"
    body = body[: max_len - 3] + "..." if len(body) > max_len else body
    return body


def format_email_list_reply(emails: list[dict[str, Any]], sender_filters: list[str] | None = None) -> str:
    if not emails:
        return "No emails found."

    lines: list[str] = []
    if sender_filters:
        joined = ", ".join(sender_filters)
        lines.append(f"Showing {len(emails)} email(s) from {joined}.")
    else:
        lines.append(f"Showing {len(emails)} email(s).")

    for idx, item in enumerate(emails, start=1):
        subject = str(item.get("subject", "")).strip() or "(No Subject)"
        sender = str(item.get("from", "")).strip() or "Unknown sender"
        date_text = _format_email_date(str(item.get("date", "")))
        summary = _email_summary(item)
        excerpt = _email_body_excerpt(item)

        lines.append(
            "\n".join(
                [
                    f"{idx}. {subject}",
                    f"From: {sender}",
                    *( [f"Date: {date_text}"] if date_text else [] ),
                    f"Summary: {summary}",
                    "Body Preview:",
                    excerpt,
                ]
            )
        )
    return "\n\n".join(lines)


@dataclass
class LocalAppRuntime:
    """Shared runtime state for the desktop app."""

    config: Config
    bus: MessageBus
    router: IntentRouter
    email_channel: EmailChannel
    gmail_reader: GmailReader
    email_assistant: EmailAssistant
    agent_loop: AgentLoop | None
    workspace: Path
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_email_by_session: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_email_list_by_session: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def create(cls, config: Config | None = None) -> "LocalAppRuntime":
        config = config or load_config()
        sync_workspace_templates(config.workspace_path)

        bus = MessageBus()
        router = IntentRouter()
        email_channel = EmailChannel(config.channels.email, bus)
        gmail_reader = GmailReader(config)
        email_assistant = EmailAssistant(config)
        agent_loop = cls._build_agent_loop(config, bus)
        return cls(
            config=config,
            bus=bus,
            router=router,
            email_channel=email_channel,
            gmail_reader=gmail_reader,
            email_assistant=email_assistant,
            agent_loop=agent_loop,
            workspace=config.workspace_path,
        )

    @staticmethod
    def _build_agent_loop(config: Config, bus: MessageBus) -> AgentLoop | None:
        provider = _build_provider(config)
        if provider is None:
            return None
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            temperature=config.agents.defaults.temperature,
            max_tokens=config.agents.defaults.max_tokens,
            max_iterations=config.agents.defaults.max_tool_iterations,
            memory_window=config.agents.defaults.memory_window,
            reasoning_effort=config.agents.defaults.reasoning_effort,
            brave_api_key=config.tools.web.search.api_key or None,
            web_proxy=config.tools.web.proxy or None,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            google_workspace_config=config.google_workspace,
        )

    def config_status(self) -> dict[str, Any]:
        google_creds_ready = bool(self.config.google_workspace.credentials_json.strip()) or Path(
            self.config.google_workspace.credentials_path
        ).expanduser().exists()
        gemini_ready = bool(self.config.providers.gemini.api_key.strip())
        return {
            "gemini_configured": gemini_ready,
            "google_configured": google_creds_ready,
            "onboarding_complete": gemini_ready and google_creds_ready,
        }

    def save_user_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        gemini_api_key = str(payload.get("geminiApiKey", "")).strip()
        google_credentials_json = str(payload.get("googleCredentialsJson", "")).strip()

        if not gemini_api_key:
            raise ValueError("Gemini API key is required.")
        if not google_credentials_json:
            raise ValueError("Google credentials JSON is required.")

        self.config.providers.gemini.api_key = gemini_api_key
        self.config.agents.defaults.model = "gemini/gemini-2.5-flash-lite"
        self.config.google_workspace.enabled = True
        self.config.google_workspace.credentials_json = google_credentials_json
        self.config.channels.email.enabled = True
        self.config.channels.email.consent_granted = True

        for field_name in (
            "imapHost",
            "imapPort",
            "imapUsername",
            "imapPassword",
            "smtpHost",
            "smtpPort",
            "smtpUsername",
            "smtpPassword",
            "fromAddress",
        ):
            value = payload.get(field_name)
            if value in (None, ""):
                continue
            snake_name = "".join([f"_{c.lower()}" if c.isupper() else c for c in field_name]).lstrip("_")
            if snake_name in {"imap_port", "smtp_port"}:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{field_name} must be a number.")
            setattr(self.config.channels.email, snake_name, value)

        save_config(self.config)
        self._reload_components()
        return self.config_status()

    def _reload_components(self) -> None:
        self.email_channel = EmailChannel(self.config.channels.email, self.bus)
        self.gmail_reader = GmailReader(self.config)
        self.email_assistant = EmailAssistant(self.config)
        self.agent_loop = self._build_agent_loop(self.config, self.bus)

    async def handle_message(
        self,
        message: str,
        session_id: str,
        email_uid: str = "",
        media_paths: list[str] | None = None,
        attachments: list[dict[str, str]] | None = None,
        transcription: str = "",
    ) -> dict[str, Any]:
        message = _merge_prompt_with_context(message, attachments, transcription=transcription)
        intent = self.router.detect_intent(message)
        logger.info("Local intent {} for session {}", intent.name, session_id)

        if _looks_like_google_doc_request(message):
            result = await self._create_google_doc_preview(message)
            return {
                **result,
                "intent": intent.to_dict(),
                "session_id": session_id,
            }

        if intent.action == "read_email":
            n = int(intent.parameters.get("count") or 3)
            unread_only = bool(intent.parameters.get("unread_only"))
            sender_filters = [str(item).strip().lower() for item in (intent.parameters.get("from") or []) if str(item).strip()]
            emails_result = self.get_latest_emails(n, unread_only=unread_only)
            emails = self._filter_emails_by_sender(emails_result.get("emails", []), sender_filters)
            self.last_email_list_by_session[session_id] = emails
            if emails:
                self.last_email_by_session[session_id] = emails[0]
            return {
                "type": "email_list",
                "emails": emails,
                "items": emails,
                "reply": format_email_list_reply(emails, sender_filters=sender_filters),
                "unread_only": unread_only,
                "sender_filters": sender_filters,
                "intent": intent.to_dict(),
                "session_id": session_id,
            }

        if intent.action in {"send_email", "reply_email"}:
            draft = await self._generate_email_preview(
                message=message,
                session_id=session_id,
                recipients=intent.parameters.get("to") or [],
                email_uid=email_uid,
                is_reply=intent.action == "reply_email",
            )
            return {
                **draft,
                "intent": intent.to_dict(),
                "session_id": session_id,
            }

        reply = await self._handle_chat(message, session_id, media_paths=media_paths or [])
        return {
            "type": "assistant_message",
            "reply": reply,
            "intent": intent.to_dict(),
            "session_id": session_id,
        }

    async def _generate_email_preview(
        self,
        message: str,
        session_id: str,
        recipients: list[str],
        email_uid: str,
        is_reply: bool,
    ) -> dict[str, Any]:
        selected_email = self._resolve_selected_email(session_id=session_id, message=message, email_id=email_uid)
        thread_context = ""
        subject = ""
        if is_reply:
            if not selected_email:
                raise ValueError("No email selected for reply. Ask me to show latest emails first.")
            thread_context = self._get_thread_for_selected_email(selected_email)
            subject = str(selected_email.get("subject", "")).strip()
            recipients = self.email_channel.normalize_recipients(selected_email.get("from", ""))
            reply_body = await self.email_assistant.generate_reply(
                thread_text=thread_context,
                user_instruction=message,
            )
            draft = {
                "to": recipients,
                "subject": self._reply_subject(subject),
                "body": reply_body,
            }
        else:
            draft = await self.email_assistant.draft_email(
                user_input=message,
                recipients=recipients,
                thread_context=None,
            )
        normalized_to = self.email_channel.normalize_recipients(draft.get("to", []))
        draft_id = str(uuid4())

        reply_message_id = ""
        reply_refs = ""
        if selected_email:
            reply_message_id = str(selected_email.get("messageId", "")).strip()
            refs = str(selected_email.get("references", "")).strip()
            ref_parts = [part for part in refs.split() if part]
            if reply_message_id and reply_message_id not in ref_parts:
                ref_parts.append(reply_message_id)
            reply_refs = " ".join(ref_parts)

        self.drafts[draft_id] = {
            "to": normalized_to,
            "subject": draft.get("subject", "").strip(),
            "body": draft.get("body", "").strip(),
            "in_reply_to": reply_message_id,
            "references": reply_refs,
            "thread_context": thread_context,
        }
        return {
            "type": "email_preview",
            "draft_id": draft_id,
            "to": normalized_to,
            "subject": draft.get("subject", "").strip(),
            "body": draft.get("body", "").strip(),
            "thread_context": thread_context,
        }

    def get_latest_emails(self, n: int, unread_only: bool = False) -> dict[str, Any]:
        """Fetch latest emails from Gmail API, falling back to IMAP when needed."""
        try:
            result = self.gmail_reader.get_latest_emails(n, unread_only=unread_only)
            result["emails"] = self._filter_received_emails(result.get("emails", []))
            if "items" in result:
                result["items"] = result["emails"]
            return result
        except GmailReaderError as exc:
            logger.info("Gmail API unavailable for local mail read; using IMAP fallback.")
            try:
                items = self.email_channel.fetch_recent_messages(
                    limit=max(1, int(n)),
                    unread_only=unread_only,
                    mark_seen=False,
                )
            except Exception as imap_exc:
                raise ValueError(str(exc)) from imap_exc
            return {
                "type": "email_list",
                "emails": self._filter_received_emails([self._imap_message_to_local_email(item) for item in items]),
                "source": "imap",
                "warning": str(exc),
            }

    def get_thread(self, thread_id: str) -> str:
        """Fetch full email thread text from Gmail API."""
        try:
            return self.gmail_reader.get_thread(thread_id)
        except GmailReaderError as exc:
            raise ValueError(str(exc)) from exc

    def _get_thread_for_selected_email(self, selected_email: dict[str, Any]) -> str:
        thread_id = str(selected_email.get("threadId", "")).strip()
        if thread_id:
            return self.get_thread(thread_id)

        uid = str(selected_email.get("uid", "") or selected_email.get("id", "")).strip()
        if not uid:
            raise ValueError("No email selected for reply. Ask me to show latest emails first.")

        try:
            items = self.email_channel.fetch_thread_by_uid(uid)
        except Exception as exc:
            raise ValueError(f"Failed to fetch email thread from IMAP: {exc}") from exc
        if not items:
            raise ValueError("Could not find the email thread for the selected message.")
        blocks = [str(item.get("content", "")).strip() for item in items if str(item.get("content", "")).strip()]
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _imap_message_to_local_email(item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.get("metadata", {}) or {}
        uid = str(metadata.get("uid", "")).strip()
        message_id = str(item.get("message_id") or metadata.get("message_id", "")).strip()
        content = str(item.get("content", "")).strip()
        return {
            "id": uid or message_id,
            "uid": uid,
            "threadId": "",
            "messageId": message_id,
            "from": item.get("sender", ""),
            "to": item.get("to", "") or metadata.get("to", ""),
            "subject": item.get("subject", ""),
            "date": item.get("date", "") or metadata.get("date", ""),
            "references": metadata.get("references", ""),
            "snippet": _imap_preview_from_content(content),
            "preview": _imap_preview_from_content(content),
            "body": content,
            "source": "imap",
        }

    @staticmethod
    def _filter_emails_by_sender(emails: list[dict[str, Any]], sender_filters: list[str]) -> list[dict[str, Any]]:
        if not sender_filters:
            return list(emails)
        normalized_filters = {value.strip().lower() for value in sender_filters if value.strip()}
        filtered: list[dict[str, Any]] = []
        for item in emails:
            sender = str(item.get("from", "")).lower()
            if any(token in sender for token in normalized_filters):
                filtered.append(item)
        return filtered

    def _filter_received_emails(self, emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
        own_addresses = {
            addr
            for addr in (
                _extract_normalized_emails(self.config.channels.email.from_address),
                _extract_normalized_emails(self.config.channels.email.smtp_username),
                _extract_normalized_emails(self.config.channels.email.imap_username),
            )
            for addr in addr
        }
        if not own_addresses:
            return list(emails)

        filtered: list[dict[str, Any]] = []
        for item in emails:
            sender_addresses = set(_extract_normalized_emails(str(item.get("from", ""))))
            if sender_addresses and sender_addresses.intersection(own_addresses):
                continue
            filtered.append(item)
        return filtered

    @staticmethod
    def _reply_subject(subject: str) -> str:
        clean = (subject or "").strip()
        if clean.lower().startswith("re:"):
            return clean
        return f"Re: {clean or 'Conversation'}"

    def _resolve_selected_email(self, session_id: str, message: str, email_id: str = "") -> dict[str, Any]:
        if email_id:
            for item in self.last_email_list_by_session.get(session_id, []):
                if str(item.get("id", "")) == email_id:
                    return item

        emails = self.last_email_list_by_session.get(session_id, [])
        if not emails:
            return self.last_email_by_session.get(session_id, {})

        match = re.search(r"\b(\d{1,2})\b", message or "")
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(emails):
                return emails[idx]

        hinted = self._resolve_selected_email_by_name(emails, message)
        if hinted:
            return hinted

        return self.last_email_by_session.get(session_id, emails[0])

    @staticmethod
    def _resolve_selected_email_by_name(emails: list[dict[str, Any]], message: str) -> dict[str, Any]:
        hint = _extract_email_reference_hint(message)
        hint_tokens = _lookup_tokens(hint)
        fallback_tokens = _lookup_tokens(message)
        best_item: dict[str, Any] = {}
        best_score = 0

        for item in emails:
            haystack = _normalize_lookup_text(
                f"{item.get('from', '')} {item.get('subject', '')}"
            )
            score = 0

            if hint and hint in haystack:
                score += 10
            if hint_tokens:
                score += sum(2 for token in hint_tokens if token in haystack)
            else:
                score += sum(1 for token in fallback_tokens if token in haystack)

            if score > best_score:
                best_score = score
                best_item = item

        threshold = 2 if hint_tokens else 1
        return best_item if best_score >= threshold else {}

    async def send_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft_id = str(payload.get("draft_id", "")).strip()
        draft = self.drafts.get(draft_id, {})
        to_value = payload.get("to", draft.get("to", []))
        recipients = self.email_channel.normalize_recipients(to_value)
        subject = str(payload.get("subject", draft.get("subject", ""))).strip()
        body = str(payload.get("body", draft.get("body", ""))).strip()
        if not recipients:
            raise ValueError("At least one recipient is required.")
        if not subject:
            raise ValueError("Subject is required.")
        if not body:
            raise ValueError("Body is required.")

        await self.email_channel.send_email(
            recipients=recipients,
            subject=subject,
            body=body,
            in_reply_to=str(draft.get("in_reply_to", "")),
            references=str(draft.get("references", "")),
        )
        if draft_id:
            self.drafts.pop(draft_id, None)
        return {
            "type": "email_sent",
            "reply": f"Email sent to {', '.join(recipients)}",
            "to": recipients,
            "subject": subject,
        }

    async def _create_google_doc_preview(self, message: str) -> dict[str, Any]:
        title, body = await self._generate_google_doc_draft(message)
        try:
            client = GoogleWorkspaceClient.from_config(self.config.google_workspace)
            created = await asyncio.to_thread(client.docs_create, title, body)
        except GoogleWorkspaceError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            raise ValueError(f"Failed to create Google Doc: {exc}") from exc

        return {
            "type": "document_preview",
            "provider": "google_docs",
            "document_id": created.get("id", ""),
            "title": created.get("title", title),
            "content": body,
            "url": created.get("url", ""),
            "reply": f"Google Doc created: {created.get('title', title)}",
        }

    async def _generate_google_doc_draft(self, message: str) -> tuple[str, str]:
        provider = _build_provider(self.config)
        title_hint = _extract_doc_title_hint(message)

        if provider is not None:
            response = await provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create complete Google Docs. Return strict JSON only with keys "
                            "\"title\" and \"body\". The body must be the full finished document, "
                            "not notes about the document. Include headings and paragraph breaks when useful. "
                            "Do not return an empty body."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Create the full document for this request:\n{message}\n\n"
                            f"Preferred title: {title_hint or 'Choose a good title based on the request.'}"
                        ),
                    },
                ],
                model=self.config.agents.defaults.model,
                max_tokens=min(max(self.config.agents.defaults.max_tokens, 1400), 8192),
                temperature=0.2,
                reasoning_effort=self.config.agents.defaults.reasoning_effort,
            )
            parsed = _gog_parse_json_object(response.content or "")
            if parsed:
                title = str(parsed.get("title", "")).strip() or title_hint or "prj3bot document"
                body = str(parsed.get("body", "")).strip()
                if body:
                    return title, body

        fallback = await _gog_generate_doc_draft(self.config, message)
        if isinstance(fallback, str):
            raise ValueError(fallback)
        title, body = fallback
        return title.strip() or title_hint or "prj3bot document", body.strip()

    def list_emails(self, limit: int = 10, unread_only: bool = False) -> list[dict[str, Any]]:
        result = self.get_latest_emails(limit, unread_only=unread_only)
        emails: list[dict[str, Any]] = []
        for item in result.get("emails", []):
            emails.append(
                {
                    "id": str(item.get("id", "")),
                    "threadId": str(item.get("threadId", "")),
                    "messageId": str(item.get("messageId", "")),
                    "from": item.get("from", ""),
                    "to": item.get("to", ""),
                    "subject": item.get("subject", ""),
                    "date": item.get("date", ""),
                    "references": item.get("references", ""),
                    "snippet": item.get("snippet", ""),
                    "preview": _preview_email(item.get("body", "")),
                    "body": item.get("body", ""),
                }
            )
        return emails

    async def _handle_chat(self, message: str, session_id: str, media_paths: list[str] | None = None) -> str:
        if not self.agent_loop:
            return "Assistant is ready, but no model is configured yet. Open Settings to finish setup."
        msg = InboundMessage(
            channel="local",
            sender_id="user",
            chat_id=session_id,
            content=message,
            media=media_paths or [],
        )
        response = await self.agent_loop._process_message(msg, session_key=f"local:{session_id}")
        return response.content if response else ""
