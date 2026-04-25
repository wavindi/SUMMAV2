#!/usr/bin/env python3
"""
SUMMA V3 backend — Raspberry Pi runner.

Same Flask + Socket.IO app as `padel_backend.py` (which is what `backend_pc.py`
also runs), with defaults tuned for a Pi driving a TV in kiosk mode:

  * Binds to 0.0.0.0 so other devices on the LAN (including the kiosk
    Chromium on the same Pi) can reach it.
  * Reads SUMMA_NODE_TOKEN from a persistent file (~/.summa_token) so the
    bridge / mock tools see the same value across reboots. Generates one
    on first run.
  * Tees logs to /var/log/summa/backend.log (falls back to ~/.summa/backend.log
    if /var/log isn't writable, which is normal for the `pi` user).
  * Threading async mode (Pi 3B has only 1 GB and 4 weak A53 cores — the
    threading worker is far lighter than eventlet for our load).

Run directly:
    python3 backend_pi.py

Or via systemd (see systemd/summa-backend.service and docs/PI_SETUP.md).
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _resolve_token() -> str:
    """Persistent shared token.

    Order of precedence:
      1. SUMMA_NODE_TOKEN env var (override).
      2. ~/.summa_token file (auto-created once, 0600).
    """
    env = os.environ.get("SUMMA_NODE_TOKEN")
    if env:
        return env

    token_file = Path.home() / ".summa_token"
    if token_file.exists():
        return token_file.read_text().strip()

    token = "pi-" + secrets.token_urlsafe(16)
    token_file.write_text(token)
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token


def _resolve_log_path() -> Path:
    for p in (Path("/var/log/summa/backend.log"), Path.home() / ".summa" / "backend.log"):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            # touch to confirm writable
            p.touch(exist_ok=True)
            return p
        except OSError:
            continue
    return Path.home() / "summa-backend.log"


def main() -> int:
    os.environ.setdefault("SUMMA_HOST", "0.0.0.0")
    os.environ.setdefault("SUMMA_PORT", "5000")
    os.environ.setdefault("SUMMA_LOG_LEVEL", "INFO")

    token = _resolve_token()
    os.environ["SUMMA_NODE_TOKEN"] = token

    log_path = _resolve_log_path()
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(os.environ["SUMMA_LOG_LEVEL"].upper())

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import padel_backend as b

    host = os.environ["SUMMA_HOST"]
    port = int(os.environ["SUMMA_PORT"])
    lan = _lan_ip()

    banner = (
        f"\n[backend_pi] bind        {host}:{port}\n"
        f"[backend_pi] LAN URL     http://{lan}:{port}/\n"
        f"[backend_pi] kiosk URL   http://127.0.0.1:{port}/\n"
        f"[backend_pi] token       {token}\n"
        f"[backend_pi] token file  {Path.home() / '.summa_token'}\n"
        f"[backend_pi] log file    {log_path}\n"
    )
    print(banner, flush=True)
    b.log.info("backend_pi starting on %s:%s — LAN %s", host, port, lan)

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
