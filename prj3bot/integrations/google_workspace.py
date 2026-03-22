"""Google Workspace integration helpers (Docs/Sheets/Drive/Classroom/Slides)."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any

from loguru import logger

GOOGLE_WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
]


class GoogleWorkspaceError(RuntimeError):
    """Raised when Google Workspace setup or API operations fail."""


class GoogleWorkspaceClient:
    """Thin wrapper around Google Workspace APIs."""

    def __init__(
        self,
        credentials_json: str = "",
        credentials_path: str = "~/.prj3bot/google/credentials.json",
        token_path: str = "~/.prj3bot/google/token.json",
        scopes: list[str] | None = None,
    ) -> None:
        self.credentials_json = credentials_json.strip()
        self.credentials_path = Path(credentials_path).expanduser()
        self.token_path = Path(token_path).expanduser()
        self.scopes = scopes or list(GOOGLE_WORKSPACE_SCOPES)
        self._services: dict[tuple[str, str], Any] = {}

    @classmethod
    def from_config(cls, google_workspace_config: Any) -> "GoogleWorkspaceClient":
        env_json = os.getenv("PRJ3BOT_GOOGLE_CREDENTIALS_JSON", "").strip()
        credentials_json = (google_workspace_config.credentials_json or "").strip() or env_json
        return cls(
            credentials_json=credentials_json,
            credentials_path=google_workspace_config.credentials_path,
            token_path=google_workspace_config.token_path,
        )

    def _import_google_modules(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except Exception as e:
            raise GoogleWorkspaceError(
                "Google dependencies are missing. Install: "
                "google-api-python-client google-auth google-auth-oauthlib"
            ) from e
        return Request, Credentials, InstalledAppFlow, build, HttpError

    def _load_client_config(self) -> dict[str, Any]:
        if self.credentials_json:
            try:
                return json.loads(self.credentials_json)
            except json.JSONDecodeError as e:
                raise GoogleWorkspaceError("Invalid google_workspace.credentialsJson.") from e

        if self.credentials_path.exists():
            try:
                return json.loads(self.credentials_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise GoogleWorkspaceError(
                    f"Invalid Google credentials JSON at {self.credentials_path}"
                ) from e

        raise GoogleWorkspaceError(
            "Google credentials not found. Set googleWorkspace.credentialsJson "
            "or place credentials file at googleWorkspace.credentialsPath."
        )

    def _get_credentials(self):
        Request, Credentials, InstalledAppFlow, _, _ = self._import_google_modules()
        creds = None

        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), self.scopes)
            if creds and hasattr(creds, "has_scopes") and not creds.has_scopes(self.scopes):
                logger.info("Google token is missing new scopes, re-running OAuth consent flow")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                client_config = self._load_client_config()
                creds = self._run_local_server_oauth_flow(InstalledAppFlow, client_config)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return creds

    @staticmethod
    def _is_interactive_terminal() -> bool:
        try:
            return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
        except Exception:
            return False

    @staticmethod
    def _extract_code_from_url(url: str) -> str | None:
        """Pull the 'code' query-parameter out of a redirect URL."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        values = qs.get("code")
        return values[0] if values else None

    @staticmethod
    def _extract_scopes_from_url(url: str) -> list[str]:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        value = (qs.get("scope") or [""])[0]
        return [part for part in value.split() if part]

    @staticmethod
    def _pick_redirect_uri(client_config: dict[str, Any]) -> str:
        section = client_config.get("installed") or client_config.get("web") or {}
        redirect_uris = [u.strip() for u in section.get("redirect_uris", []) if isinstance(u, str)]
        for prefix in ("http://localhost", "http://127.0.0.1"):
            for uri in redirect_uris:
                if uri.startswith(prefix):
                    return uri.rstrip("/")
        if redirect_uris:
            return redirect_uris[0].rstrip("/")
        return "http://localhost"

    def _run_local_server_oauth_flow(self, installed_app_flow: Any, client_config: dict[str, Any]):
        flow = installed_app_flow.from_client_config(client_config, self.scopes)
        try:
            return flow.run_local_server(
                host="localhost",
                port=0,
                authorization_prompt_message=(
                    "Opening your browser for Google sign-in. "
                    "If it does not open automatically, visit this URL: {url}"
                ),
                success_message=(
                    "Google authorization complete. You can close this window and return to AgentAssistly."
                ),
                open_browser=True,
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
        except Exception as e:
            raise GoogleWorkspaceError(
                "Google OAuth local-server login failed. Make sure your Google OAuth desktop client "
                "allows localhost redirects and retry. Details: " + str(e)
            ) from e

    def _service(self, name: str, version: str):
        _, _, _, build, _ = self._import_google_modules()
        key = (name, version)
        if key in self._services:
            return self._services[key]

        creds = self._get_credentials()
        service = build(name, version, credentials=creds, cache_discovery=False)
        self._services[key] = service
        return service

    def _authorized_http_session(self):
        try:
            from google.auth.transport.requests import AuthorizedSession
        except Exception as e:
            raise GoogleWorkspaceError(
                "Google dependencies are missing. Install: "
                "google-api-python-client google-auth google-auth-oauthlib"
            ) from e
        return AuthorizedSession(self._get_credentials())

    @staticmethod
    def _extract_google_id(ref: str) -> str:
        candidate = (ref or "").strip()
        if not candidate:
            return ""
        if re.fullmatch(r"[a-zA-Z0-9_-]{15,}", candidate):
            return candidate
        patterns = [
            r"/d/([a-zA-Z0-9_-]+)",
            r"[?&]id=([a-zA-Z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, candidate)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _require_id(cls, ref: str) -> str:
        resource_id = cls._extract_google_id(ref)
        if not resource_id:
            raise GoogleWorkspaceError(
                "Could not parse Google resource ID. Provide a valid ID or full Google URL."
            )
        return resource_id

    @staticmethod
    def _parse_calendar_datetime(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            raise GoogleWorkspaceError(
                "Missing datetime. Use `YYYY-MM-DD HH:MM` or ISO format like `2026-03-12T18:30`."
            )

        normalized = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized.replace(" ", "T"))
        except ValueError as e:
            raise GoogleWorkspaceError(
                "Invalid datetime format. Use `YYYY-MM-DD HH:MM` or ISO format like "
                "`2026-03-12T18:30`."
            ) from e

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.isoformat()

    @staticmethod
    def _normalize_meet_name(ref: str) -> str:
        raw = (ref or "").strip()
        if not raw:
            raise GoogleWorkspaceError("Missing Meet space reference.")

        parsed = urlparse(raw)
        if parsed.netloc.endswith("meet.google.com") and parsed.path.strip("/"):
            raw = parsed.path.strip("/")

        if raw.startswith("spaces/"):
            return raw
        return f"spaces/{raw}"

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def calendar_list(self, limit: int = 10) -> list[dict[str, str]]:
        calendar = self._service("calendar", "v3")
        response = calendar.calendarList().list(maxResults=max(1, int(limit))).execute()
        items = response.get("items", [])
        return [
            {
                "id": item.get("id", ""),
                "summary": item.get("summary", "(untitled)"),
                "timeZone": item.get("timeZone", ""),
                "primary": "yes" if item.get("primary") else "no",
            }
            for item in items
        ]

    def calendar_events(self, calendar_ref: str = "primary", limit: int = 10) -> list[dict[str, Any]]:
        calendar_id = (calendar_ref or "").strip() or "primary"
        calendar = self._service("calendar", "v3")
        response = calendar.events().list(
            calendarId=calendar_id,
            timeMin=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            maxResults=max(1, int(limit)),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return response.get("items", [])

    def calendar_create_event(
        self,
        calendar_ref: str,
        summary: str,
        start_at: str,
        end_at: str,
        description: str = "",
        create_meet_link: bool = False,
    ) -> dict[str, str]:
        calendar_id = (calendar_ref or "").strip() or "primary"
        start_iso = self._parse_calendar_datetime(start_at)
        end_iso = self._parse_calendar_datetime(end_at)
        if datetime.fromisoformat(end_iso) <= datetime.fromisoformat(start_iso):
            raise GoogleWorkspaceError("Event end time must be after start time.")

        calendar = self._service("calendar", "v3")
        body: dict[str, Any] = {
            "summary": summary.strip() or "prj3bot event",
            "description": description.strip(),
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
        }
        if create_meet_link:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        created = calendar.events().insert(
            calendarId=calendar_id,
            body=body,
            conferenceDataVersion=1 if create_meet_link else 0,
            sendUpdates="none",
        ).execute()

        meet_link = ""
        for entry in (created.get("conferenceData", {}) or {}).get("entryPoints", []) or []:
            if entry.get("entryPointType") == "video":
                meet_link = entry.get("uri", "")
                break

        return {
            "id": created.get("id", ""),
            "summary": created.get("summary", summary),
            "url": created.get("htmlLink", ""),
            "start": start_iso,
            "end": end_iso,
            "meetLink": meet_link,
        }

    # ------------------------------------------------------------------
    # Docs
    # ------------------------------------------------------------------

    def docs_create(self, title: str, content: str) -> dict[str, str]:
        docs = self._service("docs", "v1")
        created = docs.documents().create(body={"title": title}).execute()
        document_id = created["documentId"]
        if content.strip():
            docs.documents().batchUpdate(
                documentId=document_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
            ).execute()
        return {
            "id": document_id,
            "title": title,
            "url": f"https://docs.google.com/document/d/{document_id}/edit",
        }

    def docs_read(self, doc_ref: str) -> dict[str, str]:
        document_id = self._require_id(doc_ref)
        docs = self._service("docs", "v1")
        payload = docs.documents().get(documentId=document_id).execute()
        title = payload.get("title", "(untitled)")

        chunks: list[str] = []
        for item in payload.get("body", {}).get("content", []):
            para = item.get("paragraph")
            if not para:
                continue
            for elem in para.get("elements", []):
                text_run = elem.get("textRun", {})
                text = text_run.get("content", "")
                if text:
                    chunks.append(text)

        text = "".join(chunks).strip()
        return {
            "id": document_id,
            "title": title,
            "url": f"https://docs.google.com/document/d/{document_id}/edit",
            "text": text,
        }

    def docs_list(self, limit: int = 10) -> list[dict[str, str]]:
        drive = self._service("drive", "v3")
        response = drive.files().list(
            q="mimeType='application/vnd.google-apps.document' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=max(1, int(limit)),
            fields="files(id,name,webViewLink,modifiedTime)",
        ).execute()
        files = response.get("files", [])
        return [
            {
                "id": f.get("id", ""),
                "name": f.get("name", "(untitled)"),
                "url": f.get("webViewLink", ""),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ]

    # ------------------------------------------------------------------
    # Sheets
    # ------------------------------------------------------------------

    def sheets_create(self, title: str, values: list[list[str]] | None = None) -> dict[str, str]:
        sheets = self._service("sheets", "v4")
        created = sheets.spreadsheets().create(body={"properties": {"title": title}}).execute()
        spreadsheet_id = created["spreadsheetId"]
        if values:
            sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range="A1",
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
        return {
            "id": spreadsheet_id,
            "title": title,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        }

    def sheets_read(self, sheet_ref: str, cell_range: str = "A1:Z50") -> dict[str, Any]:
        spreadsheet_id = self._require_id(sheet_ref)
        sheets = self._service("sheets", "v4")
        payload = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        title = payload.get("properties", {}).get("title", "(untitled)")
        data = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=cell_range,
        ).execute()
        values = data.get("values", [])
        return {
            "id": spreadsheet_id,
            "title": title,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "range": cell_range,
            "values": values,
        }

    def sheets_append(self, sheet_ref: str, values: list[str], cell_range: str = "A1") -> None:
        spreadsheet_id = self._require_id(sheet_ref)
        sheets = self._service("sheets", "v4")
        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=cell_range,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()

    def sheets_list(self, limit: int = 10) -> list[dict[str, str]]:
        drive = self._service("drive", "v3")
        response = drive.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=max(1, int(limit)),
            fields="files(id,name,webViewLink,modifiedTime)",
        ).execute()
        files = response.get("files", [])
        return [
            {
                "id": f.get("id", ""),
                "name": f.get("name", "(untitled)"),
                "url": f.get("webViewLink", ""),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ]

    # ------------------------------------------------------------------
    # Drive
    # ------------------------------------------------------------------

    def drive_list(self, limit: int = 10) -> list[dict[str, str]]:
        drive = self._service("drive", "v3")
        response = drive.files().list(
            q="trashed=false",
            orderBy="modifiedTime desc",
            pageSize=max(1, int(limit)),
            fields="files(id,name,mimeType,webViewLink,modifiedTime)",
        ).execute()
        return response.get("files", [])

    def drive_search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        drive = self._service("drive", "v3")
        safe_query = query.replace("'", "\\'")
        response = drive.files().list(
            q=f"trashed=false and name contains '{safe_query}'",
            orderBy="modifiedTime desc",
            pageSize=max(1, int(limit)),
            fields="files(id,name,mimeType,webViewLink,modifiedTime)",
        ).execute()
        return response.get("files", [])

    def drive_get(self, file_ref: str) -> dict[str, str]:
        file_id = self._require_id(file_ref)
        drive = self._service("drive", "v3")
        return drive.files().get(
            fileId=file_id,
            fields="id,name,mimeType,webViewLink,modifiedTime",
        ).execute()

    # ------------------------------------------------------------------
    # Slides
    # ------------------------------------------------------------------

    def slides_create(
        self,
        title: str,
        slide_count: int = 3,
        slides_content: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        slides = self._service("slides", "v1")
        created = slides.presentations().create(body={"title": title}).execute()
        presentation_id = created["presentationId"]

        outline = slides_content or []
        if outline:
            try:
                presentation = slides.presentations().get(presentationId=presentation_id).execute()
                requests: list[dict[str, Any]] = []
                first_slide_id = ((presentation.get("slides") or [{}])[0]).get("objectId")
                if first_slide_id:
                    requests.append({"deleteObject": {"objectId": first_slide_id}})

                for slide in outline:
                    slide_id = f"slide_{uuid.uuid4().hex[:12]}"
                    title_id = f"title_{uuid.uuid4().hex[:12]}"
                    body_id = f"body_{uuid.uuid4().hex[:12]}"
                    requests.append(
                        {
                            "createSlide": {
                                "objectId": slide_id,
                                "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                                "placeholderIdMappings": [
                                    {
                                        "layoutPlaceholder": {"type": "TITLE", "index": 0},
                                        "objectId": title_id,
                                    },
                                    {
                                        "layoutPlaceholder": {"type": "BODY", "index": 0},
                                        "objectId": body_id,
                                    },
                                ],
                            }
                        }
                    )
                    requests.append(
                        {
                            "insertText": {
                                "objectId": title_id,
                                "insertionIndex": 0,
                                "text": slide.get("title", "").strip() or "Untitled Slide",
                            }
                        }
                    )
                    body = slide.get("body", "").strip() or "-"
                    requests.append(
                        {
                            "insertText": {
                                "objectId": body_id,
                                "insertionIndex": 0,
                                "text": body,
                            }
                        }
                    )

                if requests:
                    slides.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={"requests": requests},
                    ).execute()
                    slide_count = len(outline)
            except Exception as e:
                logger.warning("Slides created but content generation failed: {}", e)
        else:
            requests = [
                {"createSlide": {"slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"}}}
                for _ in range(max(0, int(slide_count) - 1))
            ]
            if requests:
                try:
                    slides.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={"requests": requests},
                    ).execute()
                except Exception as e:
                    logger.warning("Slides created but extra slide creation failed: {}", e)

        return {
            "id": presentation_id,
            "title": title,
            "url": f"https://docs.google.com/presentation/d/{presentation_id}/edit",
            "slides": slide_count,
        }

    def slides_get(self, slide_ref: str) -> dict[str, Any]:
        presentation_id = self._require_id(slide_ref)
        slides = self._service("slides", "v1")
        payload = slides.presentations().get(presentationId=presentation_id).execute()
        return {
            "id": presentation_id,
            "title": payload.get("title", "(untitled)"),
            "slides": len(payload.get("slides", [])),
            "url": f"https://docs.google.com/presentation/d/{presentation_id}/edit",
        }

    def slides_list(self, limit: int = 10) -> list[dict[str, str]]:
        drive = self._service("drive", "v3")
        response = drive.files().list(
            q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=max(1, int(limit)),
            fields="files(id,name,webViewLink,modifiedTime)",
        ).execute()
        files = response.get("files", [])
        return [
            {
                "id": f.get("id", ""),
                "name": f.get("name", "(untitled)"),
                "url": f.get("webViewLink", ""),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ]

    # ------------------------------------------------------------------
    # Classroom
    # ------------------------------------------------------------------

    def classroom_courses(self, limit: int = 20) -> list[dict[str, str]]:
        classroom = self._service("classroom", "v1")
        response = classroom.courses().list(pageSize=max(1, int(limit))).execute()
        return response.get("courses", [])

    def classroom_coursework(self, course_ref: str, limit: int = 20) -> list[dict[str, str]]:
        course_id = self._require_id(course_ref) if "/" in course_ref else course_ref.strip()
        if not course_id:
            raise GoogleWorkspaceError("Missing course ID for classroom coursework.")
        classroom = self._service("classroom", "v1")
        response = classroom.courses().courseWork().list(
            courseId=course_id,
            pageSize=max(1, int(limit)),
        ).execute()
        return response.get("courseWork", [])

    def classroom_announcements(self, course_ref: str, limit: int = 20) -> list[dict[str, str]]:
        course_id = self._require_id(course_ref) if "/" in course_ref else course_ref.strip()
        if not course_id:
            raise GoogleWorkspaceError("Missing course ID for classroom announcements.")
        classroom = self._service("classroom", "v1")
        response = classroom.courses().announcements().list(
            courseId=course_id,
            pageSize=max(1, int(limit)),
        ).execute()
        return response.get("announcements", [])

    # ------------------------------------------------------------------
    # Meet
    # ------------------------------------------------------------------

    def meet_create(self) -> dict[str, str]:
        session = self._authorized_http_session()
        response = session.post("https://meet.googleapis.com/v2/spaces", json={})
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                f"Meet API error {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        return {
            "name": payload.get("name", ""),
            "meetingCode": payload.get("meetingCode", ""),
            "meetingUri": payload.get("meetingUri", ""),
        }

    def meet_get(self, meet_ref: str) -> dict[str, Any]:
        name = self._normalize_meet_name(meet_ref)
        session = self._authorized_http_session()
        response = session.get(f"https://meet.googleapis.com/v2/{name}")
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                f"Meet API error {response.status_code}: {response.text[:500]}"
            )
        return response.json()
