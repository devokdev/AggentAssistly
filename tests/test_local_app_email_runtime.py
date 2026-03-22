from types import SimpleNamespace

import pytest

from prj3bot.channels.email import EmailChannel
from prj3bot.config.schema import Config
from prj3bot.local_app.gmail_reader import GmailReaderError, _clean_references
from prj3bot.local_app.intent_router import IntentRouter
from prj3bot.local_app.runtime import LocalAppRuntime, format_email_list_reply


def _make_runtime() -> LocalAppRuntime:
    config = Config()
    return LocalAppRuntime(
        config=config,
        bus=SimpleNamespace(),
        router=SimpleNamespace(),
        email_channel=SimpleNamespace(normalize_recipients=EmailChannel.normalize_recipients),
        gmail_reader=SimpleNamespace(),
        email_assistant=SimpleNamespace(),
        agent_loop=None,
        workspace=config.workspace_path,
    )


def test_clean_references_normalizes_whitespace() -> None:
    assert _clean_references(" <a@example.com>\n  <b@example.com>   ") == "<a@example.com> <b@example.com>"


def test_get_latest_emails_falls_back_to_imap_when_gmail_api_fails() -> None:
    runtime = _make_runtime()

    def _raise_gmail(_n: int, unread_only: bool = False):
        raise GmailReaderError("Failed to read latest emails: Gmail API disabled")

    runtime.gmail_reader = SimpleNamespace(get_latest_emails=_raise_gmail)
    runtime.email_channel = SimpleNamespace(
        normalize_recipients=EmailChannel.normalize_recipients,
        fetch_recent_messages=lambda limit, unread_only, mark_seen: [
            {
                "sender": "alice@example.com",
                "subject": "Fallback",
                "to": "bot@example.com",
                "date": "Sat, 21 Mar 2026 10:00:00 +0000",
                "message_id": "<m1@example.com>",
                "content": "Email received.\nFrom: alice@example.com\nSubject: Fallback\n\nHello from IMAP.",
                "metadata": {"uid": "123", "references": "<root@example.com>"},
            }
        ],
    )

    result = runtime.get_latest_emails(10)

    assert result["source"] == "imap"
    assert "Gmail API disabled" in result["warning"]
    assert result["emails"][0]["id"] == "123"
    assert result["emails"][0]["messageId"] == "<m1@example.com>"
    assert result["emails"][0]["source"] == "imap"


@pytest.mark.asyncio
async def test_handle_message_filters_read_results_by_sender() -> None:
    runtime = _make_runtime()
    runtime.router = IntentRouter()
    runtime.gmail_reader = SimpleNamespace(
        get_latest_emails=lambda n, unread_only=False: {
            "type": "email_list",
            "emails": [
                {"id": "1", "from": "kartavyadev3@gmail.com", "subject": "Wanted", "body": "first"},
                {"id": "2", "from": "other@example.com", "subject": "Skip", "body": "second"},
            ],
        }
    )

    result = await runtime.handle_message("read mail from kartavyadev3@gmail.com", session_id="s1")

    assert result["type"] == "email_list"
    assert len(result["emails"]) == 1
    assert result["emails"][0]["from"] == "kartavyadev3@gmail.com"
    assert "Showing 1 email(s) from kartavyadev3@gmail.com." in result["reply"]


@pytest.mark.asyncio
async def test_handle_message_reads_three_emails_by_default() -> None:
    runtime = _make_runtime()
    runtime.router = IntentRouter()
    seen: dict[str, int] = {}

    def _get_latest_emails(n: int, unread_only: bool = False):
        seen["count"] = n
        return {"type": "email_list", "emails": []}

    runtime.gmail_reader = SimpleNamespace(get_latest_emails=_get_latest_emails)

    await runtime.handle_message("Read my email", session_id="s1")

    assert seen["count"] == 3


def test_get_latest_emails_filters_out_sent_mail() -> None:
    runtime = _make_runtime()
    runtime.config.channels.email.imap_username = "me@example.com"
    runtime.gmail_reader = SimpleNamespace(
        get_latest_emails=lambda n, unread_only=False: {
            "type": "email_list",
            "emails": [
                {"id": "1", "from": "Alice <alice@example.com>", "subject": "Wanted", "body": "first"},
                {"id": "2", "from": "me@example.com", "subject": "Sent copy", "body": "second"},
            ],
        }
    )

    result = runtime.get_latest_emails(3)

    assert [item["id"] for item in result["emails"]] == ["1"]


def test_format_email_list_reply_is_clean_and_readable() -> None:
    reply = format_email_list_reply(
        [
            {
                "subject": "Project Update",
                "from": "Alice <alice@example.com>",
                "date": "Sat, 21 Mar 2026 10:00:00 +0000",
                "preview": "Quick update on the launch plan.",
                "body": "Hello team,\n\nHere is the full update with clear next steps.",
            }
        ]
    )

    assert "Showing 1 email(s)." in reply
    assert "1. Project Update" in reply
    assert "Summary: Quick update on the launch plan." in reply
    assert "Body Preview:" in reply


@pytest.mark.asyncio
async def test_generate_reply_preview_preserves_reply_chain() -> None:
    runtime = _make_runtime()
    runtime.last_email_list_by_session["s1"] = [
        {
            "id": "email-1",
            "threadId": "thread-1",
            "from": "Alice <alice@example.com>",
            "subject": "Project Update",
            "messageId": "<m2@example.com>",
            "references": "<m0@example.com> <m1@example.com>",
        }
    ]
    runtime.last_email_by_session["s1"] = runtime.last_email_list_by_session["s1"][0]
    runtime.get_thread = lambda thread_id: f"Thread for {thread_id}"
    
    async def _generate_reply(thread_text: str, user_instruction: str) -> str:
        return "Thanks, I reviewed the update."

    runtime.email_assistant = SimpleNamespace(
        generate_reply=_generate_reply
    )

    preview = await runtime._generate_email_preview(
        message="Reply to email 1 and confirm I reviewed it",
        session_id="s1",
        recipients=[],
        email_uid="",
        is_reply=True,
    )

    stored = runtime.drafts[preview["draft_id"]]
    assert preview["to"] == ["alice@example.com"]
    assert preview["subject"] == "Re: Project Update"
    assert preview["thread_context"] == "Thread for thread-1"
    assert stored["in_reply_to"] == "<m2@example.com>"
    assert stored["references"] == "<m0@example.com> <m1@example.com> <m2@example.com>"


@pytest.mark.asyncio
async def test_generate_reply_preview_uses_imap_thread_when_gmail_thread_missing() -> None:
    runtime = _make_runtime()
    runtime.last_email_list_by_session["s1"] = [
        {
            "id": "123",
            "uid": "123",
            "threadId": "",
            "from": "Alice <alice@example.com>",
            "subject": "IMAP Thread",
            "messageId": "<m2@example.com>",
            "references": "<m0@example.com> <m1@example.com>",
        }
    ]
    runtime.last_email_by_session["s1"] = runtime.last_email_list_by_session["s1"][0]

    async def _generate_reply(thread_text: str, user_instruction: str) -> str:
        return "Reply using IMAP context."

    runtime.email_assistant = SimpleNamespace(generate_reply=_generate_reply)
    runtime.email_channel = SimpleNamespace(
        normalize_recipients=EmailChannel.normalize_recipients,
        fetch_thread_by_uid=lambda uid: [
            {"content": "Email received.\nFrom: alice@example.com\nSubject: IMAP Thread\n\nFirst message."},
            {"content": "Email received.\nFrom: me@example.com\nSubject: Re: IMAP Thread\n\nSecond message."},
        ],
    )

    preview = await runtime._generate_email_preview(
        message="Reply to email 1",
        session_id="s1",
        recipients=[],
        email_uid="",
        is_reply=True,
    )

    assert "First message." in preview["thread_context"]
    assert "Second message." in preview["thread_context"]
    assert preview["subject"] == "Re: IMAP Thread"


@pytest.mark.asyncio
async def test_generate_reply_preview_resolves_email_by_name_hint() -> None:
    runtime = _make_runtime()
    runtime.last_email_list_by_session["s1"] = [
        {
            "id": "1",
            "threadId": "thread-1",
            "from": "Thapar University <news@alerts.sarvgyan.com>",
            "subject": "Thapar Institute of Engineering & Technology 2026 application released",
            "messageId": "<m1@example.com>",
            "references": "",
        },
        {
            "id": "2",
            "threadId": "thread-2",
            "from": "SNU Chennai <news@alerts.sarvgyan.com>",
            "subject": "Shiv Nadar University Chennai application closing soon",
            "messageId": "<m2@example.com>",
            "references": "",
        },
    ]
    runtime.last_email_by_session["s1"] = runtime.last_email_list_by_session["s1"][0]
    runtime.get_thread = lambda thread_id: f"Thread for {thread_id}"

    async def _generate_reply(thread_text: str, user_instruction: str) -> str:
        return "Thank you for the opportunity."

    runtime.email_assistant = SimpleNamespace(generate_reply=_generate_reply)

    preview = await runtime._generate_email_preview(
        message="reply to the shiv nadar mail saying thank you for opportunity",
        session_id="s1",
        recipients=[],
        email_uid="",
        is_reply=True,
    )

    assert preview["subject"] == "Re: Shiv Nadar University Chennai application closing soon"
    assert preview["to"] == ["news@alerts.sarvgyan.com"]
    assert preview["thread_context"] == "Thread for thread-2"


@pytest.mark.asyncio
async def test_handle_message_creates_google_doc_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _make_runtime()
    runtime.router = IntentRouter()
    runtime.config.google_workspace.enabled = True

    async def _fake_generate_doc(_config: Config, _instruction: str):
        return "Project Notes", "Agenda\n\n1. Intro\n2. Next steps"

    monkeypatch.setattr("prj3bot.local_app.runtime._gog_generate_doc_draft", _fake_generate_doc)

    class _FakeClient:
        def docs_create(self, title: str, content: str):
            return {
                "id": "doc123",
                "title": title,
                "url": "https://docs.google.com/document/d/doc123/edit",
            }

    monkeypatch.setattr(
        "prj3bot.local_app.runtime.GoogleWorkspaceClient.from_config",
        lambda _cfg: _FakeClient(),
    )

    result = await runtime.handle_message(
        "Create a Google Doc titled Project Notes with agenda items and next steps.",
        session_id="s1",
    )

    assert result["type"] == "document_preview"
    assert result["document_id"] == "doc123"
    assert result["title"] == "Project Notes"
    assert "Agenda" in result["content"]
    assert result["url"] == "https://docs.google.com/document/d/doc123/edit"
