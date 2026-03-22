from unittest.mock import AsyncMock

import pytest

from prj3bot.bus.queue import MessageBus
from prj3bot.channels.telegram import (
    TelegramChannel,
    _extract_prefixed_mail_command,
    _looks_like_bare_mail_command,
)
from prj3bot.config.schema import TelegramConfig


def _telegram_config() -> TelegramConfig:
    return TelegramConfig(enabled=True, token="token", allow_from=["*"])


def test_extract_prefixed_mail_command() -> None:
    assert _extract_prefixed_mail_command("prj3bot mail read3") == "read3"
    assert _extract_prefixed_mail_command("PRJ3BOT mail unread 5") == "unread 5"
    assert _extract_prefixed_mail_command("prj3bot mail") == "help"
    assert _extract_prefixed_mail_command("mail read3") is None


def test_looks_like_bare_mail_command() -> None:
    assert _looks_like_bare_mail_command("Read3")
    assert _looks_like_bare_mail_command("unread 5")
    assert _looks_like_bare_mail_command("show 1")
    assert _looks_like_bare_mail_command("reply 2 thanks")
    assert _looks_like_bare_mail_command("send to bob@example.com (summarize top 5 news)")
    assert not _looks_like_bare_mail_command("hello there")


@pytest.mark.asyncio
async def test_maybe_handle_mail_command_executes_and_replies() -> None:
    channel = TelegramChannel(_telegram_config(), MessageBus())
    channel._app = AsyncMock()
    channel._start_typing = lambda _chat_id: None
    channel._stop_typing = lambda _chat_id: None
    channel._execute_mail_command = AsyncMock(return_value="Found 2 email(s)")

    handled = await channel._maybe_handle_mail_command(
        sender_id="123",
        chat_id=99,
        text="prj3bot mail read3",
        message_id=10,
    )

    assert handled is True
    channel._execute_mail_command.assert_awaited_once_with("123", "read3")
    channel._app.bot.send_message.assert_awaited_once()
    sent_kwargs = channel._app.bot.send_message.await_args.kwargs
    assert sent_kwargs["chat_id"] == 99
    assert sent_kwargs["text"] == "Found 2 email(s)"


@pytest.mark.asyncio
async def test_maybe_handle_mail_command_shows_prefix_hint_for_bare_mail_text() -> None:
    channel = TelegramChannel(_telegram_config(), MessageBus())
    channel._app = AsyncMock()
    channel._start_typing = lambda _chat_id: None
    channel._stop_typing = lambda _chat_id: None
    channel._execute_mail_command = AsyncMock(return_value="unused")

    handled = await channel._maybe_handle_mail_command(
        sender_id="123",
        chat_id=99,
        text="Read3",
        message_id=10,
    )

    assert handled is True
    channel._execute_mail_command.assert_not_called()
    channel._app.bot.send_message.assert_awaited_once()
    hint = channel._app.bot.send_message.await_args.kwargs["text"]
    assert "prj3bot mail <command>" in hint


@pytest.mark.asyncio
async def test_maybe_handle_mail_command_ignores_non_prefixed_message() -> None:
    channel = TelegramChannel(_telegram_config(), MessageBus())
    channel._app = AsyncMock()
    channel._start_typing = lambda _chat_id: None
    channel._stop_typing = lambda _chat_id: None
    channel._execute_mail_command = AsyncMock(return_value="unused")

    handled = await channel._maybe_handle_mail_command(
        sender_id="123",
        chat_id=99,
        text="what is the weather today?",
        message_id=10,
    )

    assert handled is False
    channel._execute_mail_command.assert_not_called()
    channel._app.bot.send_message.assert_not_called()
