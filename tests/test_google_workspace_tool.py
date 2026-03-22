from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prj3bot.agent.loop import AgentLoop
from prj3bot.agent.tools.google_workspace import GoogleWorkspaceTool
from prj3bot.bus.queue import MessageBus
from prj3bot.config.schema import GoogleWorkspaceConfig


def _google_config(enabled: bool = True) -> GoogleWorkspaceConfig:
    cfg = GoogleWorkspaceConfig()
    cfg.enabled = enabled
    cfg.credentials_json = (
        '{"installed":{"client_id":"cid","project_id":"pid","auth_uri":"https://accounts.google.com/o/oauth2/auth",'
        '"token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",'
        '"client_secret":"secret","redirect_uris":["http://localhost"]}}'
    )
    cfg.token_path = "~/.prj3bot/google/token.json"
    return cfg


@pytest.mark.asyncio
async def test_google_workspace_tool_calendar_create_event() -> None:
    tool = GoogleWorkspaceTool(_google_config())
    fake_client = MagicMock()
    fake_client.calendar_create_event.return_value = {
        "summary": "Study Session",
        "url": "https://calendar.google.com/event?eid=abc",
        "start": "2026-03-15T18:00:00+05:30",
        "end": "2026-03-15T19:00:00+05:30",
        "meetLink": "https://meet.google.com/abc-defg-hij",
    }

    with patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config",
        return_value=fake_client,
    ):
        result = await tool.execute(
            action="calendar_create_event",
            calendar_ref="primary",
            title="Study Session",
            start_at="2026-03-15 18:00",
            end_at="2026-03-15 19:00",
            description="Revision",
            create_meet_link=True,
        )

    assert "Calendar event created: Study Session" in result
    assert "https://meet.google.com/abc-defg-hij" in result


@pytest.mark.asyncio
async def test_google_workspace_tool_slides_create_accepts_content() -> None:
    tool = GoogleWorkspaceTool(_google_config())
    fake_client = MagicMock()
    fake_client.slides_create.return_value = {
        "title": "Transformers Deck",
        "url": "https://docs.google.com/presentation/d/deck123/edit",
        "slides": 2,
    }

    with patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config",
        return_value=fake_client,
    ):
        result = await tool.execute(
            action="slides_create",
            title="Transformers Deck",
            slides_content=[
                {"title": "Intro", "body": "- Attention\n- Encoder\n- Decoder"},
                {"title": "Benefits", "body": "- Parallel training\n- Strong NLP performance"},
            ],
        )

    assert "Google Slides created: Transformers Deck" in result
    assert "Slides created: 2" in result
    fake_client.slides_create.assert_called_once_with(
        "Transformers Deck",
        2,
        [
            {"title": "Intro", "body": "- Attention\n- Encoder\n- Decoder"},
            {"title": "Benefits", "body": "- Parallel training\n- Strong NLP performance"},
        ],
    )


@pytest.mark.asyncio
async def test_google_workspace_tool_disabled_returns_setup_message() -> None:
    tool = GoogleWorkspaceTool(_google_config(enabled=False))
    result = await tool.execute(action="calendar_list")
    assert "not enabled" in result.lower()


def test_agent_loop_registers_google_workspace_tool(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        google_workspace_config=_google_config(),
    )

    assert loop.tools.has("google_workspace")


@pytest.mark.asyncio
async def test_agent_loop_intercepts_prefixed_gog_command(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock()
    cfg = _google_config()

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        google_workspace_config=cfg,
    )

    with patch(
        "prj3bot.cli.commands._validate_gog_config",
        return_value=(MagicMock(google_workspace=cfg), []),
    ), patch(
        "prj3bot.cli.commands._gog_execute_command",
        AsyncMock(return_value="Calendar event created: Group Discussion"),
    ) as gog_execute:
        result = await loop.process_direct(
            "prj3bot calendar meet primary | Group Discussion | 2026-03-16 20:00 | 2026-03-16 21:00 | Meet link included"
        )

    assert result == "Calendar event created: Group Discussion"
    gog_execute.assert_awaited_once()
    provider.chat.assert_not_awaited()
