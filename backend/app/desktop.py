"""Native Desktop Application runner for Grailed Liquidity Analyzer.

Runs the FastAPI backend as a dedicated subprocess with CREATE_NO_WINDOW
and opens a native WebView2 desktop window with splash screen.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import webview

# Ensure compliance acknowledgement in standalone desktop environment
if "APP_LIVE_COMPLIANCE_ACKNOWLEDGED" not in os.environ:
    os.environ["APP_LIVE_COMPLIANCE_ACKNOWLEDGED"] = "true"


def find_free_port(preferred_port: int = 8000) -> int:
    """Check if preferred port is free; if not, find an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        res = sock.connect_ex(("127.0.0.1", preferred_port))
        if res != 0:
            return preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_server_process(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI ASGI server in a dedicated process."""
    import uvicorn

    from app.main import app as fastapi_app

    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )


def start_server_subprocess(port: int) -> subprocess.Popen[Any]:
    """Start the backend server as a separate child process."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--server", "--port", str(port)]
    else:
        cmd = [sys.executable, "-m", "app.desktop", "--server", "--port", str(port)]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    env = os.environ.copy()
    env["APP_LIVE_COMPLIANCE_ACKNOWLEDGED"] = "true"

    from app.core.config import PROJECT_ROOT

    cwd = str(PROJECT_ROOT / "backend") if not getattr(sys, "frozen", False) else None

    return subprocess.Popen(
        cmd,
        cwd=cwd,
        creationflags=creationflags,
        env=env,
    )


def wait_for_server(
    url: str, server_proc: subprocess.Popen[Any], timeout_s: float = 30.0
) -> bool:
    """Poll the backend health endpoint until it responds or times out."""
    import httpx

    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_s:
        if server_proc.poll() is not None:
            return False
        try:
            response = httpx.get(f"{url}/api/health", timeout=1.0)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def get_splash_html() -> str:
    """Return inline HTML splash screen displayed while backend initializes."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Grailed Liquidity Analyzer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0d1117;
            color: #e6edf3;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            user-select: none;
            -webkit-user-select: none;
        }
        .container {
            text-align: center;
            max-width: 480px;
            padding: 32px;
        }
        .logo {
            font-size: 44px;
            margin-bottom: 16px;
            animation: pulse 2s infinite ease-in-out;
        }
        h1 {
            font-size: 22px;
            font-weight: 600;
            margin-bottom: 8px;
            letter-spacing: -0.5px;
        }
        p {
            font-size: 13px;
            color: #8b949e;
            margin-bottom: 24px;
        }
        .spinner {
            width: 32px;
            height: 32px;
            border: 3px solid rgba(88, 166, 255, 0.2);
            border-top-color: #58a6ff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 0 auto;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.08); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">🛍️</div>
        <h1>Grailed Liquidity Analyzer</h1>
        <p>Запуск аналитического движка и базы данных...</p>
        <div class="spinner"></div>
    </div>
</body>
</html>"""


def _load_app_when_ready(
    window: webview.Window,
    server_url: str,
    server_proc: subprocess.Popen[Any],
    port: int,
) -> None:
    """Wait for FastAPI server to initialize and load it into the native window."""
    time.sleep(0.5)
    ready = wait_for_server(server_url, server_proc, timeout_s=30.0)
    if ready:
        window.load_url(server_url)
    else:
        error_html = (
            "<!DOCTYPE html><html><body style='background:#0d1117;color:#f85149;"
            "font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h2>Ошибка запуска сервера</h2>"
            f"<p style='color:#8b949e;margin-top:10px;'>Не удалось подключиться "
            f"к локальному серверу FastAPI на порту {port}</p>"
            "</body></html>"
        )
        window.load_html(error_html)


def launch_desktop() -> None:
    """Launch the native desktop window and supervise process lifecycle."""
    port = find_free_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    server_proc = start_server_subprocess(port)

    window = webview.create_window(
        title="Grailed Liquidity Analyzer",
        html=get_splash_html(),
        width=1360,
        height=860,
        min_size=(1100, 700),
        resizable=True,
        text_select=True,
        zoomable=True,
        background_color="#0d1117",
    )
    if window is None:
        if server_proc.poll() is None:
            server_proc.kill()
        msg = "Failed to create desktop window"
        raise RuntimeError(msg)

    threading.Thread(
        target=_load_app_when_ready,
        args=(window, server_url, server_proc, port),
        daemon=True,
        name="ServerReadyWatcher",
    ).start()

    def on_window_closing() -> bool:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=2.0)
            except Exception:
                server_proc.kill()
        return True

    window.events.closing += on_window_closing

    try:
        webview.start(
            debug=False,
            http_server=False,
            gui="edgechromium",
        )
    finally:
        if server_proc.poll() is None:
            server_proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Grailed Liquidity Analyzer Desktop")
    parser.add_argument("--server", action="store_true", help="Run background API server")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    args = parser.parse_args()

    if args.server:
        run_server_process(host=args.host, port=args.port)
    else:
        launch_desktop()


if __name__ == "__main__":
    main()
