"""Agent tool for Google Workspace operations."""

from __future__ import annotations

import asyncio
from typing import Any

from prj3bot.agent.tools.base import Tool


class GoogleWorkspaceTool(Tool):
    """Expose Google Workspace operations to the agent."""

    def __init__(self, google_workspace_config: Any) -> None:
        self._config = google_workspace_config

    @property
    def name(self) -> str:
        return "google_workspace"

    @property
    def description(self) -> str:
        return (
            "Use Google Workspace directly for Docs, Sheets, Calendar, Meet, Drive, Slides, and Classroom. "
            "Use this instead of saying you cannot access Google Calendar or Meet. "
            "Supports creating and reading Docs/Sheets, listing calendars, listing upcoming events, creating calendar events "
            "with optional Meet links, creating Meet spaces, listing/searching Drive files, "
            "listing and creating Slides, and reading Classroom courses/coursework/announcements."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "docs_create",
                        "docs_read",
                        "docs_list",
                        "sheets_create",
                        "sheets_read",
                        "sheets_append",
                        "sheets_list",
                        "calendar_list",
                        "calendar_events",
                        "calendar_create_event",
                        "meet_create",
                        "meet_get",
                        "drive_list",
                        "drive_search",
                        "drive_get",
                        "slides_list",
                        "slides_get",
                        "slides_create",
                        "classroom_courses",
                        "classroom_coursework",
                        "classroom_announcements",
                    ],
                    "description": "Google Workspace action to perform.",
                },
                "calendar_ref": {
                    "type": "string",
                    "description": "Calendar ID or 'primary'.",
                },
                "document_ref": {
                    "type": "string",
                    "description": "Google Docs document ID or URL.",
                },
                "resource_ref": {
                    "type": "string",
                    "description": "Resource ID/URL/reference for Drive, Slides, or Meet.",
                },
                "sheet_ref": {
                    "type": "string",
                    "description": "Google Sheets spreadsheet ID or URL.",
                },
                "course_ref": {
                    "type": "string",
                    "description": "Classroom course ID.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for Drive.",
                },
                "title": {
                    "type": "string",
                    "description": "Title for Docs, Sheets, Slides, or Calendar event.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to insert into a document.",
                },
                "start_at": {
                    "type": "string",
                    "description": "Event start time, e.g. 2026-03-15 18:00.",
                },
                "end_at": {
                    "type": "string",
                    "description": "Event end time, e.g. 2026-03-15 19:00.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description.",
                },
                "create_meet_link": {
                    "type": "boolean",
                    "description": "Whether to attach a Google Meet link to a created calendar event.",
                },
                "slide_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many slides to create.",
                },
                "slides_content": {
                    "type": "array",
                    "description": "Optional slide outline for richer deck creation.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["title", "body"],
                    },
                },
                "values": {
                    "type": "array",
                    "description": "Tabular values for Sheets create/append.",
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "range": {
                    "type": "string",
                    "description": "Cell range for Sheets read/append.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError

        if not getattr(self._config, "enabled", False):
            return (
                "Google Workspace is not enabled. Set `googleWorkspace.enabled=true` in "
                "`~/.prj3bot/config.json`."
            )

        client = GoogleWorkspaceClient.from_config(self._config)
        action = kwargs.get("action", "")
        limit = int(kwargs.get("limit") or 10)

        try:
            if action == "docs_create":
                title = (kwargs.get("title") or "prj3bot document").strip()
                content = (kwargs.get("content") or "").strip()
                item = await asyncio.to_thread(client.docs_create, title, content)
                return (
                    f"Google Docs created: {item.get('title', title)}\n"
                    f"{item.get('url', '')}"
                )

            if action == "docs_read":
                item = await asyncio.to_thread(client.docs_read, kwargs.get("document_ref") or "")
                text = (item.get("text", "") or "").strip()
                if len(text) > 500:
                    text = text[:497] + "..."
                return (
                    f"Title: {item.get('title', '')}\n"
                    f"URL: {item.get('url', '')}\n"
                    f"{text}"
                ).strip()

            if action == "docs_list":
                items = await asyncio.to_thread(client.docs_list, limit)
                if not items:
                    return "No Google Docs found."
                lines = [f"Found {len(items)} document(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action == "sheets_create":
                title = (kwargs.get("title") or "prj3bot sheet").strip()
                values = kwargs.get("values") or None
                item = await asyncio.to_thread(client.sheets_create, title, values)
                return (
                    f"Google Sheets created: {item.get('title', title)}\n"
                    f"{item.get('url', '')}"
                )

            if action == "sheets_read":
                item = await asyncio.to_thread(
                    client.sheets_read,
                    kwargs.get("sheet_ref") or "",
                    kwargs.get("range") or "A1:Z50",
                )
                rows = item.get("values", []) or []
                preview = "\n".join(
                    "\t".join(str(cell) for cell in row)
                    for row in rows[:10]
                )
                if len(preview) > 500:
                    preview = preview[:497] + "..."
                return (
                    f"Title: {item.get('title', '')}\n"
                    f"Range: {item.get('range', '')}\n"
                    f"URL: {item.get('url', '')}\n"
                    f"{preview}"
                ).strip()

            if action == "sheets_append":
                values = kwargs.get("values") or []
                if not values:
                    return "Sheets append requires `values`."
                row = values[0] if values and isinstance(values[0], list) else values
                await asyncio.to_thread(
                    client.sheets_append,
                    kwargs.get("sheet_ref") or "",
                    row,
                    kwargs.get("range") or "A1",
                )
                return "Google Sheets row appended."

            if action == "sheets_list":
                items = await asyncio.to_thread(client.sheets_list, limit)
                if not items:
                    return "No Google Sheets found."
                lines = [f"Found {len(items)} spreadsheet(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action == "calendar_list":
                items = await asyncio.to_thread(client.calendar_list, limit)
                if not items:
                    return "No Google Calendars found."
                lines = [f"Found {len(items)} calendar(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('summary', '(untitled)')} | id: {item.get('id', '')} | "
                        f"primary: {item.get('primary', 'no')}"
                    )
                return "\n".join(lines)

            if action == "calendar_events":
                items = await asyncio.to_thread(
                    client.calendar_events,
                    (kwargs.get("calendar_ref") or "primary"),
                    limit,
                )
                if not items:
                    return "No upcoming calendar events found."
                lines = [f"Found {len(items)} event(s):"]
                for idx, item in enumerate(items, 1):
                    start = (item.get("start", {}) or {}).get("dateTime") or (item.get("start", {}) or {}).get("date", "")
                    lines.append(
                        f"{idx}. {item.get('summary', '(untitled)')} | {start}\n"
                        f"   {item.get('htmlLink', '')}"
                    )
                return "\n".join(lines)

            if action == "calendar_create_event":
                item = await asyncio.to_thread(
                    client.calendar_create_event,
                    kwargs.get("calendar_ref") or "primary",
                    kwargs.get("title") or "prj3bot event",
                    kwargs.get("start_at") or "",
                    kwargs.get("end_at") or "",
                    kwargs.get("description") or "",
                    bool(kwargs.get("create_meet_link")),
                )
                lines = [
                    f"Calendar event created: {item.get('summary', '')}",
                    item.get("url", ""),
                    f"Start: {item.get('start', '')}",
                    f"End: {item.get('end', '')}",
                ]
                if item.get("meetLink"):
                    lines.append(f"Meet: {item.get('meetLink')}")
                return "\n".join(line for line in lines if line)

            if action == "meet_create":
                item = await asyncio.to_thread(client.meet_create)
                return (
                    f"Google Meet created:\n"
                    f"Space: {item.get('name', '')}\n"
                    f"Code: {item.get('meetingCode', '')}\n"
                    f"URL: {item.get('meetingUri', '')}"
                )

            if action == "meet_get":
                item = await asyncio.to_thread(client.meet_get, kwargs.get("resource_ref") or "")
                return (
                    f"Space: {item.get('name', '')}\n"
                    f"Code: {item.get('meetingCode', '')}\n"
                    f"URL: {item.get('meetingUri', '')}"
                )

            if action == "drive_list":
                items = await asyncio.to_thread(client.drive_list, limit)
                if not items:
                    return "No Drive files found."
                lines = [f"Found {len(items)} file(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('mimeType', '')}\n"
                        f"   {item.get('webViewLink', '')}"
                    )
                return "\n".join(lines)

            if action == "drive_search":
                query = (kwargs.get("query") or "").strip()
                if not query:
                    return "Drive search requires `query`."
                items = await asyncio.to_thread(client.drive_search, query, limit)
                if not items:
                    return "No matching Drive files found."
                lines = [f"Found {len(items)} matching file(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('mimeType', '')}\n"
                        f"   {item.get('webViewLink', '')}"
                    )
                return "\n".join(lines)

            if action == "drive_get":
                item = await asyncio.to_thread(client.drive_get, kwargs.get("resource_ref") or "")
                return (
                    f"Name: {item.get('name', '')}\n"
                    f"Type: {item.get('mimeType', '')}\n"
                    f"Modified: {item.get('modifiedTime', '')}\n"
                    f"URL: {item.get('webViewLink', '')}"
                )

            if action == "slides_list":
                items = await asyncio.to_thread(client.slides_list, limit)
                if not items:
                    return "No Slides decks found."
                lines = [f"Found {len(items)} deck(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action == "slides_get":
                item = await asyncio.to_thread(client.slides_get, kwargs.get("resource_ref") or "")
                return (
                    f"Title: {item.get('title', '')}\n"
                    f"Slides: {item.get('slides', '')}\n"
                    f"URL: {item.get('url', '')}"
                )

            if action == "slides_create":
                slides_content = kwargs.get("slides_content") or None
                item = await asyncio.to_thread(
                    client.slides_create,
                    kwargs.get("title") or "prj3bot slides",
                    int(kwargs.get("slide_count") or (len(slides_content) if slides_content else 5)),
                    slides_content,
                )
                return (
                    f"Google Slides created: {item.get('title', '')}\n"
                    f"{item.get('url', '')}\n"
                    f"Slides created: {item.get('slides', '')}"
                )

            if action == "classroom_courses":
                items = await asyncio.to_thread(client.classroom_courses, limit)
                if not items:
                    return "No Classroom courses found."
                lines = [f"Found {len(items)} course(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(f"{idx}. {item.get('name', '(unnamed)')} | id: {item.get('id', '')}")
                return "\n".join(lines)

            if action == "classroom_coursework":
                items = await asyncio.to_thread(
                    client.classroom_coursework,
                    kwargs.get("course_ref") or "",
                    limit,
                )
                if not items:
                    return "No coursework found for this course."
                lines = [f"Found {len(items)} coursework item(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('title', '(untitled)')} | due: {item.get('dueDate', '')}"
                    )
                return "\n".join(lines)

            if action == "classroom_announcements":
                items = await asyncio.to_thread(
                    client.classroom_announcements,
                    kwargs.get("course_ref") or "",
                    limit,
                )
                if not items:
                    return "No announcements found for this course."
                lines = [f"Found {len(items)} announcement(s):"]
                for idx, item in enumerate(items, 1):
                    txt = (item.get("text", "") or "").replace("\n", " ").strip()
                    if len(txt) > 100:
                        txt = txt[:97] + "..."
                    lines.append(f"{idx}. {txt or '(no text)'}")
                return "\n".join(lines)

            return f"Unsupported google_workspace action: {action}"
        except GoogleWorkspaceError as e:
            return f"Google Workspace error: {e}"
        except Exception as e:
            return f"Google Workspace tool failed: {e}"
