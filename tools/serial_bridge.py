#!/usr/bin/env python3
"""
SUMMAV3 — serial bridge (Raspberry Pi).

Reads lines from the ESP32 receiver (plugged in over USB-CDC) and POSTs
scoring events to the local backend via /remote_event.

Pipeline:
  Sender ESP (PC serial monitor)
       --> ESP-NOW (wireless)
            --> Receiver ESP (USB cable)
                 --> this script (serial read)
                      --> POST /remote_event
                           --> Flask backend
                                --> scoreboard on TV

Line protocol (from receiver_test.ino):
  got: black addpoint
  got: yellow addpoint
  got: black subtractpoint
  got: yellow subtractpoint
  got: reset

Install once on the Pi:
    sudo apt install -y python3-serial
    # or: pip install pyserial --break-system-packages
    sudo usermod -a -G dialout $USER   # then log out + back in

Run:
    python3 tools/serial_bridge.py
    python3 tools/serial_bridge.py --port /dev/ttyACM0 --url http://127.0.0.1:5000

The token is read from ~/.summa_token (written by backend_pi.py on first run).
Override with: export SUMMA_NODE_TOKEN=<token>

Quit: Ctrl-C.
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("bridge")


class _BackendIngestHandler(logging.Handler):
    """
    logging.Handler that buffers records and ships them to backend's
    /logs/ingest endpoint in small batches.

    Network failures are swallowed — losing a few log lines is far better
    than crashing the bridge or blocking it on a slow backend.
    """
    def __init__(self, url: str, token: str, batch_size: int = 20,
                 flush_interval: float = 1.0, max_queue: int = 2000):
        super().__init__()
        self.url            = url.rstrip("/") + "/logs/ingest"
        self.token          = token
        self.batch_size     = batch_size
        self.flush_interval = flush_interval
        self.q: "queue.Queue[dict]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        t = threading.Thread(target=self._worker, name="log-ingest", daemon=True)
        t.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait({
                "level":  record.levelname,
                "logger": record.name,
                "msg":    self.format(record),
                "ts":     datetime.fromtimestamp(record.created)
                                  .isoformat(timespec="seconds"),
            })
        except queue.Full:
            pass  # backend offline & we're chatty; drop oldest by skipping

    def _worker(self) -> None:
        while not self._stop.is_set():
            batch = []
            try:
                # Block for the first item, then drain quickly
                batch.append(self.q.get(timeout=self.flush_interval))
                while len(batch) < self.batch_size:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            if batch:
                self._send(batch)

    def _send(self, batch: list) -> None:
        body = _json.dumps({"source": "bridge", "entries": batch}).encode()
        req = urllib_req.Request(
            self.url,
            data=body,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with urllib_req.urlopen(req, timeout=2) as _resp:
                pass
        except Exception:
            pass  # backend asleep; we'll keep buffering


def _setup_logging(level: str, ingest_url: str, token: str) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Console always
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # Backend ingest (no files on disk)
    bh = _BackendIngestHandler(ingest_url, token)
    bh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(bh)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit(
        "ERROR: pyserial not installed.\n"
        "  sudo apt install -y python3-serial\n"
        "  or: pip install pyserial --break-system-packages\n"
    )

try:
    import urllib.request as urllib_req
    import urllib.error
    import json as _json
except ImportError:
    sys.exit("ERROR: standard library missing (urllib / json)?")

# ---------------------------------------------------------------------------
BAUD = 115200
BACKEND_URL = "http://127.0.0.1:5000"
# Matches:
#   "got: black addpoint"
#   "got: yellow subtractpoint|rb-00041-007"   ← new firmware adds an event_id
#   "got: reset|rb-00041-008"
# The trailing "|<event_id>" is optional, so the old sender_test.ino
# (which does not embed an ID) still works — bridge falls back to its
# own counter-based ID in that case.
_LINE_RE = re.compile(
    r"got:\s+(?:(black|yellow)\s+(addpoint|subtractpoint)|(reset))"
    r"(?:\|(\S+))?",
    re.IGNORECASE,
)
# ---------------------------------------------------------------------------


def _read_token() -> str:
    env = os.environ.get("SUMMA_NODE_TOKEN")
    if env:
        return env
    token_file = Path.home() / ".summa_token"
    if token_file.exists():
        return token_file.read_text().strip()
    log.warning("no SUMMA_NODE_TOKEN found — POSTs will be rejected. "
                "Run backend_pi.py first (it creates ~/.summa_token).")
    return "no-token"


def _post_event(url: str, token: str, team: str | None, action: str, event_id: str) -> bool:
    payload: dict = {"action": action, "event_id": event_id}
    if team:
        payload["team"] = team
    body = _json.dumps(payload).encode()
    req = urllib_req.Request(
        f"{url}/remote_event",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib_req.urlopen(req, timeout=3) as resp:
            result = _json.loads(resp.read())
            deduped = result.get("deduped", False)
            tag = " [deduped]" if deduped else ""
            log.info("POST %s team=%s id=%s%s -> %s",
                     action, team or "-", event_id, tag,
                     result.get("message", "ok"))
            return True
    except urllib.error.HTTPError as e:
        log.error("HTTP %s on POST /remote_event — %s",
                  e.code, e.read().decode(errors="replace"))
    except Exception as e:
        log.error("POST failed: %s", e)
    return False


def _autodetect_port() -> str | None:
    for p in list_ports.comports():
        dev = (p.device or "").lower()
        hwid = (p.hwid or "").lower()
        desc = (p.description or "").lower()
        if "ttyacm" in dev:
            return p.device
        if "esp32" in desc or "espressif" in desc or "303a" in hwid:
            return p.device
    for p in list_ports.comports():
        if "ttyusb" in (p.device or "").lower():
            return p.device
    return None


def bridge_loop(port: str, url: str, token: str) -> None:
    counter = 0
    ser = None

    while True:
        # --- connect ---
        if ser is None:
            try:
                ser = serial.Serial(port, BAUD, timeout=1)
                log.info("connected to %s @ %d", port, BAUD)
            except (serial.SerialException, FileNotFoundError):
                log.warning("waiting for %s...", port)
                time.sleep(2)
                continue

        # --- read one line ---
        try:
            raw = ser.readline()
        except serial.SerialException:
            log.warning("serial disconnected, retrying...")
            try: ser.close()
            except Exception: pass
            ser = None
            time.sleep(2)
            continue

        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        log.debug("serial: %s", line)

        m = _LINE_RE.search(line)
        if not m:
            log.debug("ignored (no match): %s", line)
            continue  # heartbeat, OLED status line, etc. — ignore

        if m.group(3):  # reset (no team)
            team = None
            action = "reset"
        else:
            team = m.group(1).lower()
            action = m.group(2).lower()

        # Prefer the remote-supplied event_id if present (new firmware).
        # Falls back to a bridge-side counter ID for the old sender_test.ino.
        remote_id = m.group(4)
        counter += 1
        event_id = remote_id or f"bridge-{int(time.time()*1000)}-{counter:05d}"
        _post_event(url, token, team, action, event_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="SUMMAV3 serial bridge")
    ap.add_argument("--port", help="Serial device (auto-detected if omitted)")
    ap.add_argument("--url", default=BACKEND_URL,
                    help=f"Backend base URL (default: {BACKEND_URL})")
    ap.add_argument("--log-level", default=os.environ.get("SUMMA_LOG_LEVEL", "INFO"),
                    help="DEBUG | INFO | WARNING | ERROR (default INFO)")
    args = ap.parse_args()

    token = _read_token()
    url   = args.url.rstrip("/")
    _setup_logging(args.log_level, url, token)

    port = args.port or _autodetect_port()
    if not port:
        log.error("no ESP serial device found. Plug the receiver in, "
                  "or specify --port /dev/ttyACM0")
        return 2

    log.info("=== SUMMAV3 serial bridge starting ===")
    log.info("port:     %s", port)
    log.info("backend:  %s", url)
    log.info("token:    %s... (truncated)", token[:8])
    log.info("logs:     console + backend /logs (run: python view_logs.py)")
    log.info("ready — waiting for scoring events. Ctrl-C to quit.")

    try:
        bridge_loop(port, url, token)
    except KeyboardInterrupt:
        log.info("stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
