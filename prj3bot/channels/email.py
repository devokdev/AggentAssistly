"""Email channel implementation using IMAP polling + SMTP replies."""

import asyncio
import html
import imaplib
import re
import smtplib
import ssl
from datetime import date
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from loguru import logger

from prj3bot.bus.events import OutboundMessage
from prj3bot.bus.queue import MessageBus
from prj3bot.channels.base import BaseChannel
from prj3bot.config.schema import EmailConfig


class EmailChannel(BaseChannel):
    """
    Email channel.

    Inbound:
    - Poll IMAP mailbox for unread messages.
    - Convert each message into an inbound event.

    Outbound:
    - Send responses via SMTP back to the sender address.
    """

    name = "email"
    _IMAP_MONTHS = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    _EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")

    def __init__(self, config: EmailConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: EmailConfig = config
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._processed_uids: set[str] = set()  # Capped to prevent unbounded growth
        self._MAX_PROCESSED_UIDS = 100000

    async def start(self) -> None:
        """Start polling IMAP for inbound emails."""
        if not self.config.consent_granted:
            logger.warning(
                "Email channel disabled: consent_granted is false. "
                "Set channels.email.consentGranted=true after explicit user permission."
            )
            return

        if not self._validate_config():
            return

        self._running = True
        logger.info("Starting Email channel (IMAP polling mode)...")

        poll_seconds = max(5, int(self.config.poll_interval_seconds))
        while self._running:
            try:
                inbound_items = await asyncio.to_thread(self._fetch_new_messages)
                for item in inbound_items:
                    sender = item["sender"]
                    subject = item.get("subject", "")
                    message_id = item.get("message_id", "")

                    if subject:
                        self._last_subject_by_chat[sender] = subject
                    if message_id:
                        self._last_message_id_by_chat[sender] = message_id

                    await self._handle_message(
                        sender_id=sender,
                        chat_id=sender,
                        content=item["content"],
                        metadata=item.get("metadata", {}),
                    )
            except Exception as e:
                logger.error("Email polling error: {}", e)

            await asyncio.sleep(poll_seconds)

    async def stop(self) -> None:
        """Stop polling loop."""
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send email via SMTP."""
        if not self.config.consent_granted:
            logger.warning("Skip email send: consent_granted is false")
            return

        if not self.config.smtp_host:
            logger.warning("Email channel SMTP host not configured")
            return

        to_addr = msg.chat_id.strip()
        if not to_addr:
            logger.warning("Email channel missing recipient address")
            return

        # Determine if this is a reply (recipient has sent us an email before)
        is_reply = to_addr in self._last_subject_by_chat
        force_send = bool((msg.metadata or {}).get("force_send"))

        # autoReplyEnabled only controls automatic replies, not proactive sends
        if is_reply and not self.config.auto_reply_enabled and not force_send:
            logger.info("Skip automatic email reply to {}: auto_reply_enabled is false", to_addr)
            return

        base_subject = self._last_subject_by_chat.get(to_addr, "prj3bot reply")
        subject = self._reply_subject(base_subject)
        if msg.metadata and isinstance(msg.metadata.get("subject"), str):
            override = msg.metadata["subject"].strip()
            if override:
                subject = override

        email_msg = EmailMessage()
        email_msg["From"] = self.config.from_address or self.config.smtp_username or self.config.imap_username
        email_msg["To"] = to_addr
        email_msg["Subject"] = subject
        email_msg.set_content(msg.content or "")

        in_reply_to = self._last_message_id_by_chat.get(to_addr)
        if in_reply_to:
            email_msg["In-Reply-To"] = in_reply_to
            email_msg["References"] = in_reply_to

        try:
            await asyncio.to_thread(self._smtp_send, email_msg)
        except Exception as e:
            logger.error("Error sending email to {}: {}", to_addr, e)
            raise

    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        in_reply_to: str = "",
        references: str = "",
    ) -> None:
        """Send an email to one or more recipients."""
        if not self.config.consent_granted:
            raise RuntimeError("Email consent is not granted in configuration.")
        if not self.config.smtp_host:
            raise RuntimeError("Email SMTP host is not configured.")

        cleaned_recipients = self.normalize_recipients(recipients)
        if not cleaned_recipients:
            raise RuntimeError("At least one valid recipient is required.")

        clean_subject = (subject or "").strip()
        clean_body = (body or "").strip()
        if not clean_subject:
            raise RuntimeError("Email subject cannot be empty.")
        if not clean_body:
            raise RuntimeError("Email body cannot be empty.")

        email_msg = EmailMessage()
        email_msg["From"] = self.config.from_address or self.config.smtp_username or self.config.imap_username
        email_msg["To"] = ", ".join(cleaned_recipients)
        email_msg["Subject"] = clean_subject
        if in_reply_to:
            email_msg["In-Reply-To"] = in_reply_to
        if references:
            email_msg["References"] = references
        email_msg.set_content(clean_body)
        await asyncio.to_thread(self._smtp_send, email_msg)

    @classmethod
    def normalize_recipients(cls, recipients: list[str] | str) -> list[str]:
        if isinstance(recipients, str):
            raw = recipients
        else:
            raw = ",".join(recipients)
        seen: set[str] = set()
        output: list[str] = []
        for match in cls._EMAIL_RE.findall(raw):
            email = match.strip().lower()
            if email and email not in seen:
                seen.add(email)
                output.append(email)
        return output

    def _validate_config(self) -> bool:
        missing = []
        if not self.config.imap_host:
            missing.append("imap_host")
        if not self.config.imap_username:
            missing.append("imap_username")
        if not self.config.imap_password:
            missing.append("imap_password")
        if not self.config.smtp_host:
            missing.append("smtp_host")
        if not self.config.smtp_username:
            missing.append("smtp_username")
        if not self.config.smtp_password:
            missing.append("smtp_password")

        if missing:
            logger.error("Email channel not configured, missing: {}", ', '.join(missing))
            return False
        return True

    def _smtp_send(self, msg: EmailMessage) -> None:
        timeout = 30
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=timeout,
            ) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(msg)

    def _fetch_new_messages(self) -> list[dict[str, Any]]:
        """Poll IMAP and return parsed unread messages."""
        return self._fetch_messages(
            search_criteria=("UNSEEN",),
            mark_seen=self.config.mark_seen,
            dedupe=True,
            limit=0,
        )

    def fetch_recent_messages(
        self,
        limit: int = 20,
        unread_only: bool = False,
        mark_seen: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent emails for CLI/manual workflows.

        Args:
            limit: Max emails to return.
            unread_only: If True, fetch only UNSEEN messages.
            mark_seen: If True, mark fetched messages as seen.
        """
        criteria = ("UNSEEN",) if unread_only else ("ALL",)
        return self._fetch_messages(
            search_criteria=criteria,
            mark_seen=mark_seen,
            dedupe=False,
            limit=max(1, int(limit)),
        )

    def fetch_message_by_uid(self, uid: str) -> dict[str, Any] | None:
        target = (uid or "").strip()
        if not target:
            return None
        items = self._fetch_messages(
            search_criteria=("ALL",),
            mark_seen=False,
            dedupe=False,
            limit=200,
        )
        for item in items:
            if str(item.get("metadata", {}).get("uid", "")) == target:
                return item
        return None

    def fetch_thread_by_uid(self, uid: str, limit: int = 200) -> list[dict[str, Any]]:
        """Fetch a thread-like message collection based on headers and subject."""
        target = self.fetch_message_by_uid(uid)
        if not target:
            return []

        all_items = self._fetch_messages(
            search_criteria=("ALL",),
            mark_seen=False,
            dedupe=False,
            limit=max(20, int(limit)),
        )
        target_meta = target.get("metadata", {})
        root_subject = self._normalize_subject(target_meta.get("subject", ""))
        target_msg_id = (target_meta.get("message_id") or "").strip()
        target_refs = self._split_references(target_meta.get("references", ""))
        chain = {target_msg_id, *target_refs}
        chain.discard("")

        thread: list[dict[str, Any]] = []
        for item in all_items:
            meta = item.get("metadata", {})
            subject_match = self._normalize_subject(meta.get("subject", "")) == root_subject
            refs = set(self._split_references(meta.get("references", "")))
            msg_id = (meta.get("message_id") or "").strip()
            in_reply_to = (meta.get("in_reply_to") or "").strip()
            linked = bool(chain.intersection(refs)) or msg_id in chain or in_reply_to in chain
            if subject_match or linked:
                thread.append(item)

        thread.sort(key=lambda item: self._parsed_email_date(item.get("metadata", {}).get("date", "")))
        return thread

    @staticmethod
    def _split_references(raw: str) -> list[str]:
        return [part.strip() for part in (raw or "").split() if part.strip()]

    @staticmethod
    def _normalize_subject(subject: str) -> str:
        text = (subject or "").strip().lower()
        while text.startswith("re:") or text.startswith("fwd:") or text.startswith("fw:"):
            text = text.split(":", 1)[1].strip() if ":" in text else text
        return text

    @staticmethod
    def _parsed_email_date(raw: str):
        try:
            return parsedate_to_datetime(raw or "")
        except Exception:
            return parsedate_to_datetime("Thu, 01 Jan 1970 00:00:00 +0000")

    def fetch_messages_between_dates(
        self,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetch messages in [start_date, end_date) by IMAP date search.

        This is used for historical summarization tasks (e.g. "yesterday").
        """
        if end_date <= start_date:
            return []

        return self._fetch_messages(
            search_criteria=(
                "SINCE",
                self._format_imap_date(start_date),
                "BEFORE",
                self._format_imap_date(end_date),
            ),
            mark_seen=False,
            dedupe=False,
            limit=max(1, int(limit)),
        )

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fetch messages by arbitrary IMAP search criteria."""
        messages: list[dict[str, Any]] = []
        mailbox = self.config.imap_mailbox or "INBOX"

        if self.config.imap_use_ssl:
            client = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        else:
            client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)

        try:
            client.login(self.config.imap_username, self.config.imap_password)
            status, _ = client.select(mailbox)
            if status != "OK":
                return messages

            status, data = client.search(None, *search_criteria)
            if status != "OK" or not data:
                return messages

            ids = data[0].split()
            if limit > 0 and len(ids) > limit:
                ids = ids[-limit:]
            for imap_id in ids:
                status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                if status != "OK" or not fetched:
                    continue

                raw_bytes = self._extract_message_bytes(fetched)
                if raw_bytes is None:
                    continue

                uid = self._extract_uid(fetched)
                if dedupe and uid and uid in self._processed_uids:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
                sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                if not sender:
                    continue

                subject = self._decode_header_value(parsed.get("Subject", ""))
                date_value = parsed.get("Date", "")
                message_id = parsed.get("Message-ID", "").strip()
                in_reply_to = parsed.get("In-Reply-To", "").strip()
                references = parsed.get("References", "").strip()
                to_address = parsed.get("To", "").strip()
                body = self._extract_text_body(parsed)

                if not body:
                    body = "(empty email body)"

                body = body[: self.config.max_body_chars]
                content = (
                    f"Email received.\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date_value}\n\n"
                    f"{body}"
                )

                metadata = {
                    "message_id": message_id,
                    "subject": subject,
                    "date": date_value,
                    "sender_email": sender,
                    "to": to_address,
                    "in_reply_to": in_reply_to,
                    "references": references,
                    "uid": uid,
                }
                messages.append(
                    {
                        "sender": sender,
                        "subject": subject,
                        "to": to_address,
                        "date": date_value,
                        "message_id": message_id,
                        "content": content,
                        "metadata": metadata,
                    }
                )

                if dedupe and uid:
                    self._processed_uids.add(uid)
                    # mark_seen is the primary dedup; this set is a safety net
                    if len(self._processed_uids) > self._MAX_PROCESSED_UIDS:
                        # Evict a random half to cap memory; mark_seen is the primary dedup
                        self._processed_uids = set(list(self._processed_uids)[len(self._processed_uids) // 2:])

                if mark_seen:
                    client.store(imap_id, "+FLAGS", "\\Seen")
        finally:
            try:
                client.logout()
            except Exception:
                pass

        return messages

    @classmethod
    def _format_imap_date(cls, value: date) -> str:
        """Format date for IMAP search (always English month abbreviations)."""
        month = cls._IMAP_MONTHS[value.month - 1]
        return f"{value.day:02d}-{month}-{value.year}"

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes | None:
        for item in fetched:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return None

    @staticmethod
    def _extract_uid(fetched: list[Any]) -> str:
        for item in fetched:
            if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
                head = bytes(item[0]).decode("utf-8", errors="ignore")
                m = re.search(r"UID\s+(\d+)", head)
                if m:
                    return m.group(1)
        return ""

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        """Best-effort extraction of readable body text."""
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            if plain_parts:
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _reply_subject(self, base_subject: str) -> str:
        subject = (base_subject or "").strip() or "prj3bot reply"
        prefix = self.config.subject_prefix or "Re: "
        if subject.lower().startswith("re:"):
            return subject
        return f"{prefix}{subject}"
