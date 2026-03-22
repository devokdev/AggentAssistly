"""Flask API for local desktop prj3bot."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import mimetypes
import re
from uuid import uuid4
from zipfile import ZipFile

from flask import Flask, jsonify, request, send_file, send_from_directory

from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError
from prj3bot.local_app.runtime import LocalAppRuntime
from prj3bot.providers.transcription import GroqTranscriptionProvider


def _upload_dir(runtime: LocalAppRuntime) -> Path:
    path = runtime.workspace / ".local_app_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sanitize_upload_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "upload").strip())
    return safe[:120] or "upload"


def _save_upload(runtime: LocalAppRuntime, storage) -> Path:
    filename = _sanitize_upload_name(getattr(storage, "filename", "") or "upload")
    path = _upload_dir(runtime) / f"{uuid4().hex}_{filename}"
    storage.save(path)
    return path


def _extract_docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    xml = xml.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", xml).strip()


def _extract_attachment_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".py", ".json", ".csv", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".xml", ".yaml", ".yml", ".log"}:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    if suffix == ".docx":
        return _extract_docx_text(path)
    return ""


def _is_audio_file(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(str(path))
    return bool(mime and mime.startswith("audio/"))


def _is_image_file(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(str(path))
    return bool(mime and mime.startswith("image/"))


def create_app(runtime: LocalAppRuntime) -> Flask:
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).parent / "static"),
        static_url_path="/static",
    )
    app.config["RUNTIME"] = runtime

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/health")
    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "app": "agentassistly-desktop-local",
                "has_agent": runtime.agent_loop is not None,
            }
        )

    @app.get("/config/status")
    def config_status():
        return jsonify(runtime.config_status())

    @app.post("/config")
    def save_config():
        payload = request.get_json(silent=True) or {}
        try:
            status = runtime.save_user_config(payload)
            return jsonify({"saved": True, "status": status})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/chat")
    @app.post("/api/chat")
    def chat():
        attachments: list[dict[str, str]] = []
        media_paths: list[str] = []
        transcription = ""

        if request.content_type and request.content_type.startswith("multipart/form-data"):
            message = str(request.form.get("message", "")).strip()
            session_id = str(request.form.get("session_id", "default")).strip() or "default"
            email_uid = str(request.form.get("email_uid", "")).strip()
            for storage in request.files.getlist("files"):
                if not storage or not getattr(storage, "filename", ""):
                    continue
                saved_path = _save_upload(runtime, storage)
                if _is_image_file(saved_path) or _is_audio_file(saved_path):
                    media_paths.append(str(saved_path))
                extracted = _extract_attachment_text(saved_path)
                if extracted:
                    attachments.append({"name": saved_path.name, "content": extracted[:20000]})
                if _is_audio_file(saved_path):
                    transcriber = GroqTranscriptionProvider(api_key=runtime.config.providers.groq.api_key or None)
                    text = asyncio.run(transcriber.transcribe(saved_path))
                    if text.strip():
                        transcription = f"{transcription}\n{text}".strip()
        else:
            payload = request.get_json(silent=True) or {}
            message = str(payload.get("message", "")).strip()
            session_id = str(payload.get("session_id", "default")).strip() or "default"
            email_uid = str(payload.get("email_uid", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        try:
            result = asyncio.run(
                runtime.handle_message(
                    message,
                    session_id,
                    email_uid=email_uid,
                    media_paths=media_paths,
                    attachments=attachments,
                    transcription=transcription,
                )
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/send-email")
    def send_email():
        payload = request.get_json(silent=True) or {}
        try:
            result = asyncio.run(runtime.send_email(payload))
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/emails")
    def emails():
        try:
            limit = int(request.args.get("limit", "3"))
        except ValueError:
            limit = 3
        unread = request.args.get("unread", "0").lower() in {"1", "true", "yes"}
        try:
            items = runtime.list_emails(limit=limit, unread_only=unread)
            return jsonify({"type": "email_list", "emails": items, "items": items})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/documents/<document_id>/download")
    def download_document(document_id: str):
        export_format = str(request.args.get("format", "pdf")).strip().lower()
        mime_map = {
            "pdf": ("application/pdf", "pdf"),
            "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        }
        export_meta = mime_map.get(export_format)
        if not export_meta:
            return jsonify({"error": "Unsupported document format"}), 400

        try:
            client = GoogleWorkspaceClient.from_config(runtime.config.google_workspace)
            payload = client.docs_export(document_id, export_meta[0])
            details = client.docs_read(document_id)
        except GoogleWorkspaceError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        safe_title = (details.get("title", "document") or "document").strip() or "document"
        filename = f"{safe_title}.{export_meta[1]}".replace("/", "-").replace("\\", "-")
        return send_file(
            BytesIO(payload),
            mimetype=export_meta[0],
            as_attachment=True,
            download_name=filename,
        )

    return app
