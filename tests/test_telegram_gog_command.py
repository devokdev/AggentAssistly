from unittest.mock import AsyncMock

import pytest

from prj3bot.bus.queue import MessageBus
from prj3bot.channels.telegram import TelegramChannel, _extract_prefixed_gog_command
from prj3bot.config.schema import TelegramConfig


def _telegram_config() -> TelegramConfig:
    return TelegramConfig(enabled=True, token="token", allow_from=["*"])


def test_extract_prefixed_gog_command() -> None:
    assert (
        _extract_prefixed_gog_command(
            "prj3bot doc do create a 200 words article on transformer and give me that link"
        )
        == "doc do create a 200 words article on transformer and give me that link"
    )
    assert _extract_prefixed_gog_command("prj3bot gog drive list 5") == "drive list 5"
    assert _extract_prefixed_gog_command("prj3bot sheets list 3") == "sheet list 3"
    assert _extract_prefixed_gog_command("prj3bot calendar list 3") == "calendar list 3"
    assert _extract_prefixed_gog_command("prj3bot meet create") == "meet create"
    assert _extract_prefixed_gog_command("hello") is None


@pytest.mark.asyncio
async def test_maybe_handle_gog_command_executes_and_replies() -> None:
    channel = TelegramChannel(_telegram_config(), MessageBus())
    channel._app = AsyncMock()
    channel._start_typing = lambda _chat_id: None
    channel._stop_typing = lambda _chat_id: None
    channel._execute_gog_command = AsyncMock(
        return_value="Google Doc created: Transformer Overview\nhttps://docs.google.com/document/d/doc123/edit"
    )

    handled = await channel._maybe_handle_gog_command(
        sender_id="123",
        chat_id=99,
        text="prj3bot doc do create a 200 words article on transformer and give me that link",
        message_id=10,
    )

    assert handled is True
    channel._execute_gog_command.assert_awaited_once()
    channel._app.bot.send_message.assert_awaited_once()
    sent_kwargs = channel._app.bot.send_message.await_args.kwargs
    assert sent_kwargs["chat_id"] == 99
    assert "Google Doc created" in sent_kwargs["text"]


@pytest.mark.asyncio
async def test_maybe_handle_gog_command_ignores_non_prefixed_message() -> None:
    channel = TelegramChannel(_telegram_config(), MessageBus())
    channel._app = AsyncMock()
    channel._start_typing = lambda _chat_id: None
    channel._stop_typing = lambda _chat_id: None
    channel._execute_gog_command = AsyncMock(return_value="unused")

    handled = await channel._maybe_handle_gog_command(
        sender_id="123",
        chat_id=99,
        text="create a doc for me",
        message_id=10,
    )

    assert handled is False
    channel._execute_gog_command.assert_not_called()
    channel._app.bot.send_message.assert_not_called()
