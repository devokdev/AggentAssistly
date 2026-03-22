from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from prj3bot.bus.queue import MessageBus
from prj3bot.channels import telegram as telegram_module
from prj3bot.channels.telegram import TelegramChannel
from prj3bot.config.schema import TelegramConfig


def _telegram_config() -> TelegramConfig:
    return TelegramConfig(enabled=True, token="token", allow_from=["*"])


class _FakeBot:
    def __init__(self) -> None:
        self.commands = None

    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(username="prj3bot")

    async def set_my_commands(self, commands) -> None:
        self.commands = commands

    async def send_message(self, *args, **kwargs) -> None:
        return None

    async def send_chat_action(self, *args, **kwargs) -> None:
        return None


class _FakeUpdater:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def start_polling(self, *args, **kwargs) -> None:
        self.started.set()
        await self.stopped.wait()

    async def stop(self) -> None:
        self.stopped.set()


class _FakeApp:
    def __init__(self) -> None:
        self.bot = _FakeBot()
        self.updater = _FakeUpdater()
        self.handlers = []
        self.error_handlers = []
        self.initialized = False
        self.started = False
        self.stopped = False
        self.shutdown_called = False

    def add_error_handler(self, handler) -> None:
        self.error_handlers.append(handler)

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None:
        self.initialized = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeBuilder:
    def __init__(self, app: _FakeApp) -> None:
        self.app = app

    def token(self, _token: str) -> _FakeBuilder:
        return self

    def request(self, _request) -> _FakeBuilder:
        return self

    def get_updates_request(self, _request) -> _FakeBuilder:
        return self

    def proxy(self, _proxy: str) -> _FakeBuilder:
        return self

    def get_updates_proxy(self, _proxy: str) -> _FakeBuilder:
        return self

    def build(self) -> _FakeApp:
        return self.app


@pytest.mark.asyncio
async def test_telegram_start_registers_stop_handler(monkeypatch) -> None:
    fake_app = _FakeApp()
    monkeypatch.setattr(
        telegram_module.Application,
        "builder",
        classmethod(lambda cls: _FakeBuilder(fake_app)),
    )

    channel = TelegramChannel(_telegram_config(), MessageBus())
    task = asyncio.create_task(channel.start())
    await asyncio.wait_for(fake_app.updater.started.wait(), timeout=1)

    stop_handlers = [
        handler
        for handler in fake_app.handlers
        if getattr(handler, "commands", None) == frozenset({"stop"})
    ]

    assert stop_handlers, "Telegram /stop command should be registered"

    await channel.stop()
    await asyncio.wait_for(task, timeout=1)
