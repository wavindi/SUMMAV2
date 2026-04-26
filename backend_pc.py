#!/usr/bin/env python3
"""
SUMMA V3 backend — PC runner.

Thin wrapper around `backend_pi.py` (the engine) with defaults tuned for
running on a Windows / macOS / Linux laptop:

  * Binds to 127.0.0.1 by default (loopback) so Windows Firewall does not
    prompt on first launch. Override with SUMMA_HOST=0.0.0.0 to expose on
    LAN (needed if real hardware should reach this PC).
  * Generates an EPHEMERAL token for this process so we don't trample the
    persistent ~/.summa_token a Pi might be using on the same machine.
  * Quieter logging (no rotating file handler — console only).
  * Opens the scoreboard in the default browser on startup
    (disable with SUMMA_NO_BROWSER=1).

Run:
    python backend_pc.py

Then in another shell, drive scoring without hardware:
    set SUMMA_NODE_TOKEN=<printed-token>     # Windows
    export SUMMA_NODE_TOKEN=<printed-token>  # macOS / Linux
    python tools/mock_esp32_node.py --both --demo-match
"""
from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import webbrowser


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    # ── 1. Set PC-friendly defaults BEFORE importing the engine ──────────
    os.environ.setdefault("SUMMA_HOST", "127.0.0.1")
    os.environ.setdefault("SUMMA_PORT", "5000")
    os.environ.setdefault("SUMMA_LOG_LEVEL", "INFO")

    # Ephemeral token: don't write/read ~/.summa_token on the dev machine.
    # Setting SUMMA_NODE_TOKEN short-circuits the engine's token file logic.
    if not os.environ.get("SUMMA_NODE_TOKEN"):
        os.environ["SUMMA_NODE_TOKEN"] = "pc-" + secrets.token_urlsafe(16)

    # No log files anywhere — the engine keeps the last N records in memory
    # and exposes them via GET /logs.  See: python view_logs.py

    # ── 2. Import the engine (this triggers all module-level setup) ──────
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import backend_pi as b

    host = os.environ["SUMMA_HOST"]
    port = int(os.environ["SUMMA_PORT"])
    url  = f"http://{'127.0.0.1' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}/"

    print(f"[backend_pc] bind        {host}:{port}")
    print(f"[backend_pc] scoreboard  {url}")
    if host == "0.0.0.0":
        print(f"[backend_pc] LAN access  http://{_lan_ip()}:{port}/")
    print(f"[backend_pc] token       {os.environ['SUMMA_NODE_TOKEN']}")
    print(f"[backend_pc] logs        in-memory only — run: python view_logs.py")

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
