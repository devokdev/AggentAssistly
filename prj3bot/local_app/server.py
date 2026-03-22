"""Flask API for local desktop prj3bot."""

from __future__ import annotations

import asyncio
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from prj3bot.local_app.runtime import LocalAppRuntime


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
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        session_id = str(payload.get("session_id", "default")).strip() or "default"
        email_uid = str(payload.get("email_uid", "")).strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        try:
            result = asyncio.run(runtime.handle_message(message, session_id, email_uid=email_uid))
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
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            limit = 10
        unread = request.args.get("unread", "0").lower() in {"1", "true", "yes"}
        try:
            items = runtime.list_emails(limit=limit, unread_only=unread)
            return jsonify({"type": "email_list", "emails": items, "items": items})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app
