from pathlib import Path

import pytest

from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError


# ---------- helpers ---------------------------------------------------------

_FAKE_CLIENT_JSON = (
    '{"installed":{"client_id":"cid","project_id":"pid",'
    '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
    '"token_uri":"https://oauth2.googleapis.com/token",'
    '"auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",'
    '"client_secret":"sec","redirect_uris":["http://localhost"]}}'
)


class _DummyCreds:
    valid = False
    expired = False
    refresh_token = None

    def to_json(self) -> str:
        return '{"access_token":"x"}'


class _DummyCredentials:
    @staticmethod
    def from_authorized_user_file(_path: str, _scopes):
        return None


class _FlowLocalServerFails:
    """Local-server OAuth flow test double."""

    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self.redirect_uri: str = ""
        self.credentials = None
        self.oauth2session = type("OAuth2SessionStub", (), {"scope": None})()

    @classmethod
    def from_client_config(cls, _cfg, _scopes):
        return cls()

    def run_local_server(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.credentials = _DummyCreds()
        return self.credentials


class _FlowLocalServerErrors(_FlowLocalServerFails):
    def run_local_server(self, **kwargs):
        type(self).last_kwargs = kwargs
        raise RuntimeError("port bind failed")


# ---------- tests -----------------------------------------------------------


def test_local_server_flow_succeeds_and_writes_token(tmp_path: Path, monkeypatch) -> None:
    client = GoogleWorkspaceClient(
        credentials_json=_FAKE_CLIENT_JSON,
        token_path=str(tmp_path / "token.json"),
    )

    def _fake_import():
        return object, _DummyCredentials, _FlowLocalServerFails, object, object

    monkeypatch.setattr(client, "_import_google_modules", _fake_import)
    _FlowLocalServerFails.last_kwargs = None

    creds = client._get_credentials()
    assert isinstance(creds, _DummyCreds)
    assert Path(client.token_path).exists()
    assert _FlowLocalServerFails.last_kwargs is not None
    assert _FlowLocalServerFails.last_kwargs["host"] == "localhost"
    assert _FlowLocalServerFails.last_kwargs["port"] == 0
    assert _FlowLocalServerFails.last_kwargs["open_browser"] is True
    assert _FlowLocalServerFails.last_kwargs["access_type"] == "offline"
    assert "AgentAssistly" in _FlowLocalServerFails.last_kwargs["success_message"]


def test_local_server_flow_works_without_interactive_terminal(tmp_path: Path, monkeypatch) -> None:
    client = GoogleWorkspaceClient(
        credentials_json=_FAKE_CLIENT_JSON,
        token_path=str(tmp_path / "token.json"),
    )

    def _fake_import():
        return object, _DummyCredentials, _FlowLocalServerFails, object, object

    monkeypatch.setattr(client, "_import_google_modules", _fake_import)
    monkeypatch.setattr(client, "_is_interactive_terminal", lambda: False)

    creds = client._get_credentials()
    assert isinstance(creds, _DummyCreds)


def test_local_server_flow_raises_clear_error_on_failure(tmp_path: Path, monkeypatch) -> None:
    client = GoogleWorkspaceClient(
        credentials_json=_FAKE_CLIENT_JSON,
        token_path=str(tmp_path / "token.json"),
    )

    def _fake_import():
        return object, _DummyCredentials, _FlowLocalServerErrors, object, object

    monkeypatch.setattr(client, "_import_google_modules", _fake_import)

    with pytest.raises(GoogleWorkspaceError) as exc:
        client._get_credentials()
    assert "local-server login failed" in str(exc.value).lower()
    assert "localhost redirects" in str(exc.value).lower()


def test_existing_token_is_reused_without_new_oauth(tmp_path: Path, monkeypatch) -> None:
    class _ValidCreds:
        valid = True
        expired = False
        refresh_token = None

        def has_scopes(self, _scopes):
            return True

    token_path = tmp_path / "token.json"
    token_path.write_text('{"access_token":"cached"}', encoding="utf-8")
    client = GoogleWorkspaceClient(
        credentials_json=_FAKE_CLIENT_JSON,
        token_path=str(token_path),
    )

    def _fake_import():
        return object, _DummyCredentials, _FlowLocalServerFails, object, object

    monkeypatch.setattr(_DummyCredentials, "from_authorized_user_file", staticmethod(lambda _path, _scopes: _ValidCreds()))
    monkeypatch.setattr(client, "_import_google_modules", _fake_import)
    _FlowLocalServerFails.last_kwargs = None

    creds = client._get_credentials()
    assert isinstance(creds, _ValidCreds)
    assert _FlowLocalServerFails.last_kwargs is None
