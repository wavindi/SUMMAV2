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
import os
import re
import sys
import time
import threading
from pathlib import Path

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
# Matches: "got: black addpoint" / "got: yellow subtractpoint" / "got: reset"
_LINE_RE = re.compile(
    r"got:\s+(black|yellow)\s+(addpoint|subtractpoint)"
    r"|got:\s+(reset)",
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
    print("[bridge] WARNING: no SUMMA_NODE_TOKEN found — POSTs will be rejected.")
    print("         Run backend_pi.py first (it creates ~/.summa_token).")
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
            print(f"[bridge] {action} {team or ''}{tag} -> {result.get('message','ok')}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[bridge] HTTP {e.code} on POST — {e.read().decode(errors='replace')}")
    except Exception as e:
        print(f"[bridge] POST failed: {e}")
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
                print(f"[bridge] connected to {port}")
            except (serial.SerialException, FileNotFoundError):
                print(f"[bridge] waiting for {port}...")
                time.sleep(2)
                continue

        # --- read one line ---
        try:
            raw = ser.readline()
        except serial.SerialException:
            print("[bridge] serial disconnected, retrying...")
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

        print(f"[serial] {line}")

        m = _LINE_RE.search(line)
        if not m:
            continue  # heartbeat, OLED status line, etc. — ignore

        if m.group(3):  # reset (no team)
            team = None
            action = "reset"
        else:
            team = m.group(1).lower()
            action = m.group(2).lower()

        counter += 1
        event_id = f"bridge-{int(time.time()*1000)}-{counter:05d}"
        _post_event(url, token, team, action, event_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="SUMMAV3 serial bridge")
    ap.add_argument("--port", help="Serial device (auto-detected if omitted)")
    ap.add_argument("--url", default=BACKEND_URL,
                    help=f"Backend base URL (default: {BACKEND_URL})")
    args = ap.parse_args()

    port = args.port or _autodetect_port()
    if not port:
        sys.stderr.write(
            "ERROR: no ESP serial device found. Plug the receiver in.\n"
            "       Or specify: --port /dev/ttyACM0\n"
        )
        return 2

    token = _read_token()
    url   = args.url.rstrip("/")

    print(f"[bridge] port    {port}")
    print(f"[bridge] backend {url}")
    print(f"[bridge] token   {token[:8]}... (truncated)")
    print("[bridge] ready — waiting for scoring events. Ctrl-C to quit.\n")

    try:
        bridge_loop(port, url, token)
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
