from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from prj3bot.cli.commands import app
from prj3bot.config.schema import Config
from prj3bot.providers.base import LLMResponse

runner = CliRunner()


def _google_ready_config() -> Config:
    cfg = Config()
    gw = cfg.google_workspace
    gw.enabled = True
    gw.credentials_json = (
        '{"installed":{"client_id":"cid","project_id":"pid","auth_uri":"https://accounts.google.com/o/oauth2/auth",'
        '"token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",'
        '"client_secret":"secret","redirect_uris":["http://localhost"]}}'
    )
    gw.credentials_path = "~/.prj3bot/google/credentials.json"
    gw.token_path = "~/.prj3bot/google/token.json"
    return cfg


def test_gog_doc_create_from_instruction() -> None:
    cfg = _google_ready_config()

    provider_mock = AsyncMock()
    provider_mock.chat = AsyncMock(
        return_value=LLMResponse(
            content='{"title":"Transformer Overview","body":"Transformers are neural architectures for sequence modeling."}'
        )
    )
    fake_google = AsyncMock()
    fake_google.docs_create = lambda _title, _body: {
        "id": "doc123",
        "title": "Transformer Overview",
        "url": "https://docs.google.com/document/d/doc123/edit",
    }

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.cli.commands._make_provider", return_value=provider_mock
    ), patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config", return_value=fake_google
    ):
        result = runner.invoke(
            app,
            [
                "gog",
                "-m",
                "doc do create a 200 words article on transformer and give me that link",
            ],
        )

    assert result.exit_code == 0
    assert "Google Doc created: Transformer Overview" in result.stdout
    assert "https://docs.google.com/document/d/doc123/edit" in result.stdout


def test_gog_drive_list() -> None:
    cfg = _google_ready_config()
    fake_google = AsyncMock()
    fake_google.drive_list = lambda _limit: [
        {
            "id": "f1",
            "name": "Research Notes",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://drive.google.com/file/d/f1/view",
        }
    ]

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config", return_value=fake_google
    ):
        result = runner.invoke(app, ["gog", "-m", "drive list 1"])

    assert result.exit_code == 0
    assert "Found 1 file(s)" in result.stdout
    assert "Research Notes" in result.stdout


def test_gog_calendar_list() -> None:
    cfg = _google_ready_config()
    fake_google = AsyncMock()
    fake_google.calendar_list = lambda _limit: [
        {
            "id": "primary",
            "summary": "Primary Calendar",
            "primary": "yes",
        }
    ]

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config", return_value=fake_google
    ):
        result = runner.invoke(app, ["gog", "-m", "calendar list 1"])

    assert result.exit_code == 0
    assert "Found 1 calendar(s)" in result.stdout
    assert "Primary Calendar" in result.stdout


def test_gog_meet_create() -> None:
    cfg = _google_ready_config()
    fake_google = AsyncMock()
    fake_google.meet_create = lambda: {
        "name": "spaces/abc123",
        "meetingCode": "abc-defg-hij",
        "meetingUri": "https://meet.google.com/abc-defg-hij",
    }

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config", return_value=fake_google
    ):
        result = runner.invoke(app, ["gog", "-m", "meet create"])

    assert result.exit_code == 0
    assert "Google Meet created" in result.stdout
    assert "https://meet.google.com/abc-defg-hij" in result.stdout


def test_gog_slides_create_generates_contentful_deck() -> None:
    cfg = _google_ready_config()

    provider_mock = AsyncMock()
    provider_mock.chat = AsyncMock(
        return_value=LLMResponse(
            content=(
                '{"title":"Transformers Deck","slides":['
                '{"title":"What Are Transformers?","body":"- Neural architecture\\n- Uses attention\\n- Strong for language tasks"},'
                '{"title":"Core Components","body":"- Self-attention\\n- Positional encoding\\n- Feed-forward layers"}'
                "]}"
            )
        )
    )
    fake_google = MagicMock()
    fake_google.slides_create.return_value = {
        "id": "deck123",
        "title": "Transformers Deck",
        "url": "https://docs.google.com/presentation/d/deck123/edit",
        "slides": 2,
    }

    with patch("prj3bot.config.loader.load_config", return_value=cfg), patch(
        "prj3bot.cli.commands._make_provider", return_value=provider_mock
    ), patch(
        "prj3bot.integrations.google_workspace.GoogleWorkspaceClient.from_config", return_value=fake_google
    ):
        result = runner.invoke(app, ["gog", "-m", "slides do create a deck on transformers"])

    assert result.exit_code == 0
    assert "Google Slides created: Transformers Deck" in result.stdout
    assert "Slides created: 2" in result.stdout
    fake_google.slides_create.assert_called_once_with(
        "Transformers Deck",
        2,
        [
            {
                "title": "What Are Transformers?",
                "body": "- Neural architecture\n- Uses attention\n- Strong for language tasks",
            },
            {
                "title": "Core Components",
                "body": "- Self-attention\n- Positional encoding\n- Feed-forward layers",
            },
        ],
    )


def test_gog_fails_when_google_config_missing() -> None:
    cfg = Config()
    with patch("prj3bot.config.loader.load_config", return_value=cfg):
        result = runner.invoke(app, ["gog", "-m", "help"])

    assert result.exit_code == 1
    assert "Google Workspace config is incomplete" in result.stdout
