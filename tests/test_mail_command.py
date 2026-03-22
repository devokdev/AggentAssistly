from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from prj3bot.cli.commands import app
from prj3bot.config.schema import Config
from prj3bot.providers.base import LLMResponse

runner = CliRunner()


def _email_ready_config() -> Config:
    cfg = Config()
    em = cfg.channels.email
    em.enabled = True
    em.consent_granted = True
    em.imap_host = "imap.gmail.com"
    em.imap_username = "bot@example.com"
    em.imap_password = "imap-pass"
    em.smtp_host = "smtp.gmail.com"
    em.smtp_username = "bot@example.com"
    em.smtp_password = "smtp-pass"
    return cfg


def test_mail_read_one_shot_lists_messages() -> None:
    cfg = _email_ready_config()
    fake_messages = [
        {
            "sender": "alice@example.com",
            "subject": "Plan",
            "message_id": "<m1>",
            "content": "Email received.\nFrom: alice@example.com\nSubject: Plan\n\nPlease send a 3-day plan.",
            "metadata": {"date": "Thu, 5 Mar 2026 10:00:00 +0000"},
        }
    ]

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.channels.email.EmailChannel.fetch_recent_messages", return_value=fake_messages
    ):
        result = runner.invoke(app, ["mail", "-m", "read 1"])

    assert result.exit_code == 0
    assert "Found 1 email(s)" in result.stdout
    assert "alice@example.com" in result.stdout
    assert "Please send a 3-day plan." in result.stdout


def test_mail_read_compact_count_without_space() -> None:
    cfg = _email_ready_config()
    fetch_mock = MagicMock(return_value=[])

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.channels.email.EmailChannel.fetch_recent_messages", fetch_mock
    ):
        result = runner.invoke(app, ["mail", "-m", "read3"])

    assert result.exit_code == 0
    fetch_mock.assert_called_once_with(limit=3, unread_only=False, mark_seen=False)
    assert "No emails found." in result.stdout


def test_mail_send_one_shot_uses_email_channel_send() -> None:
    cfg = _email_ready_config()
    send_mock = AsyncMock(return_value=None)

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.channels.email.EmailChannel.send", send_mock
    ):
        result = runner.invoke(
            app,
            ["mail", "-m", "send bob@example.com | Hello | This is a direct CLI email."],
        )

    assert result.exit_code == 0
    assert "Email sent to bob@example.com" in result.stdout
    sent = send_mock.await_args.args[0]
    assert sent.channel == "email"
    assert sent.chat_id == "bob@example.com"
    assert sent.content == "This is a direct CLI email."
    assert sent.metadata["subject"] == "Hello"
    assert sent.metadata["force_send"] is True


def test_mail_ai_send_to_generates_and_sends() -> None:
    cfg = _email_ready_config()
    send_mock = AsyncMock(return_value=None)
    provider_mock = AsyncMock()
    provider_mock.chat = AsyncMock(
        return_value=LLMResponse(
            content='{"subject":"Top 5 AI News","body":"- News 1\\n- News 2\\n- News 3\\n- News 4\\n- News 5"}'
        )
    )

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.channels.email.EmailChannel.send", send_mock
    ), patch("prj3bot.cli.commands._make_provider", return_value=provider_mock):
        result = runner.invoke(
            app,
            [
                "mail",
                "-m",
                "send to bob@example.com (do detailed research on latest AI news and send 5 bullet points)",
            ],
        )

    assert result.exit_code == 0
    assert "AI email sent to bob@example.com" in result.stdout
    sent = send_mock.await_args.args[0]
    assert sent.channel == "email"
    assert sent.chat_id == "bob@example.com"
    assert sent.metadata["subject"] == "Top 5 AI News"
    assert sent.metadata["force_send"] is True
    assert "- News 1" in sent.content


def test_mail_ai_send_rejects_invalid_recipient() -> None:
    cfg = _email_ready_config()
    with patch("prj3bot.config.loader.load_config", return_value=cfg):
        result = runner.invoke(app, ["mail", "-m", "send to to (summarize top 5 news)"])

    assert result.exit_code == 0
    assert "Invalid recipient email address: to" in result.stdout


def test_mail_send_rejects_invalid_recipient() -> None:
    cfg = _email_ready_config()
    with patch("prj3bot.config.loader.load_config", return_value=cfg):
        result = runner.invoke(app, ["mail", "-m", "send to hello world"])

    assert result.exit_code == 0
    assert "Invalid recipient email address: hello" in result.stdout


def test_mail_reply_without_read_context_prompts_user() -> None:
    cfg = _email_ready_config()

    with patch("prj3bot.config.loader.load_config", return_value=cfg):
        result = runner.invoke(app, ["mail", "-m", "reply 1 Thanks for this"])

    assert result.exit_code == 0
    assert "Run `read` first" in result.stdout


def test_mail_fails_when_email_config_missing() -> None:
    cfg = Config()
    with patch("prj3bot.config.loader.load_config", return_value=cfg):
        result = runner.invoke(app, ["mail", "-m", "read 1"])

    assert result.exit_code == 1
    assert "Email config is incomplete" in result.stdout
