from unittest.mock import MagicMock, patch

import pytest

from prj3bot.agent.tools.google_workspace import GoogleWorkspaceTool
from prj3bot.config.schema import GoogleWorkspaceConfig
from prj3bot.local_app.intents import IntentRouter


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


def test_intent_router_detects_email_send() -> None:
    router = IntentRouter()
    intent = router.detect("Send an email to jane@example.com subject Weekly update body I finished the draft.")

    assert intent.name == "send_email"
    assert intent.action == "send_email"
    assert "jane@example.com" in intent.parameters["to"]


def test_intent_router_detects_read_mail_without_misrouting_to_send() -> None:
    router = IntentRouter()
    intent = router.detect("Read my mail")

    assert intent.name == "read_email"
    assert intent.action == "read_email"
    assert intent.parameters["count"] == 3


def test_intent_router_detects_read_mail_count() -> None:
    router = IntentRouter()
    intent = router.detect("Read 5 emails from my inbox")

    assert intent.name == "read_email"
    assert intent.parameters["count"] == 5


def test_intent_router_detects_read_mail_from_specific_sender() -> None:
    router = IntentRouter()
    intent = router.detect("Read mail from kartavyadev3@gmail.com")

    assert intent.name == "read_email"
    assert intent.action == "read_email"
    assert intent.parameters["from"] == ["kartavyadev3@gmail.com"]


def test_intent_router_detects_docs_create() -> None:
    router = IntentRouter()
    intent = router.detect("Create a Google Doc titled Project Notes with agenda items and next steps.")

    assert intent.name == "google_tools"
    assert intent.action == "google_tools"


@pytest.mark.asyncio
async def test_google_workspace_tool_docs_create() -> None:
    tool = GoogleWorkspaceTool(_google_config())
    fake_client = MagicMock()
    fake_client.docs_create.return_value = {
        "title": "Project Notes",
        "url": "https://docs.google.com/document/d/doc123/edit",
    }

    with patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config",
        return_value=fake_client,
    ):
        result = await tool.execute(
            action="docs_create",
            title="Project Notes",
            content="Agenda and action items",
        )

    assert "Google Docs created: Project Notes" in result
    fake_client.docs_create.assert_called_once_with("Project Notes", "Agenda and action items")


@pytest.mark.asyncio
async def test_google_workspace_tool_sheets_append() -> None:
    tool = GoogleWorkspaceTool(_google_config())
    fake_client = MagicMock()

    with patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config",
        return_value=fake_client,
    ):
        result = await tool.execute(
            action="sheets_append",
            sheet_ref="https://docs.google.com/spreadsheets/d/sheet123/edit",
            range="A1",
            values=["Item", "Status"],
        )

    assert "Google Sheets row appended." in result
    fake_client.sheets_append.assert_called_once_with(
        "https://docs.google.com/spreadsheets/d/sheet123/edit",
        ["Item", "Status"],
        "A1",
    )
