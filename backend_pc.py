#!/usr/bin/env python3
"""
SUMMA V2 backend — PC runner.

Same Flask + Socket.IO app as `padel_backend.py` (which is the Raspberry Pi
target), but with defaults tuned for running on a Windows/macOS/Linux laptop:

  - Binds to 127.0.0.1 by default (loopback) so Windows Firewall does not
    prompt on first launch. Override with SUMMA_HOST=0.0.0.0 to expose on LAN
    (needed if real ESP32 nodes should reach this PC).
  - Default port 5000; override with SUMMA_PORT.
  - If SUMMA_NODE_TOKEN is unset, generates an ephemeral one for this process
    and prints it to the console so the mock ESP32 tool can pick it up.
  - Opens the scoreboard in the default browser on startup (disable with
    SUMMA_NO_BROWSER=1).

Run:
    python backend_pc.py

Then in another shell:
    set SUMMA_NODE_TOKEN=<printed-token>
    python tools/mock_esp32_node.py --both --demo-match
"""
import os
import secrets
import socket
import sys
import threading
import webbrowser


def _lan_ip() -> str:
    """Best-effort local LAN IP (for the console banner)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    os.environ.setdefault("SUMMA_HOST", "127.0.0.1")
    os.environ.setdefault("SUMMA_PORT", "5000")
    os.environ.setdefault("SUMMA_LOG_LEVEL", "INFO")

    if not os.environ.get("SUMMA_NODE_TOKEN"):
        token = "pc-" + secrets.token_urlsafe(16)
        os.environ["SUMMA_NODE_TOKEN"] = token
        print(f"[backend_pc] ephemeral SUMMA_NODE_TOKEN={token}")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import padel_backend as b

    host = os.environ["SUMMA_HOST"]
    port = int(os.environ["SUMMA_PORT"])
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}/"

    print(f"[backend_pc] bind        {host}:{port}")
    print(f"[backend_pc] scoreboard  {url}")
    if host == "0.0.0.0":
        print(f"[backend_pc] LAN access  http://{_lan_ip()}:{port}/")
    print(f"[backend_pc] token       {os.environ['SUMMA_NODE_TOKEN']}")

    if os.environ.get("SUMMA_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    b.socketio.run(
        b.app,
        host=host,
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
