"""Browser launcher for the local AgentAssistly app."""

from __future__ import annotations

import os
import threading
import time
import webbrowser

from werkzeug.serving import make_server

from prj3bot.local_app.runtime import LocalAppRuntime
from prj3bot.local_app.server import create_app


def _pick_port() -> int:
    raw = os.environ.get("PRJ3BOT_LOCAL_APP_PORT", "8765").strip()
    try:
        port = int(raw)
    except ValueError:
        port = 8765
    return max(1024, min(65535, port))


def main() -> None:
    """Start the local server and open the chat UI."""
    runtime = LocalAppRuntime.create()
    app = create_app(runtime)
    host = os.environ.get("PRJ3BOT_LOCAL_APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _pick_port()

    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://{host}:{port}"
    if os.environ.get("PRJ3BOT_NO_BROWSER", "").strip().lower() not in {"1", "true", "yes"}:
        webbrowser.open_new(url)

    print(f"AgentAssistly local app running at {url}")
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
