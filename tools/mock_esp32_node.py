#!/usr/bin/env python3
"""
mock_esp32_node.py — simulate one or both XIAO ESP32-C3 sensor nodes.

Talks to the V2 backend exactly like real firmware would: bearer token,
/sensor_heartbeat every 2 s, and /addpoint|/subtractpoint|/resetmatch POSTs
with event_id idempotency keys.

Usage:
  python mock_esp32_node.py --team black                      # heartbeat only
  python mock_esp32_node.py --team black --event addpoint     # one event
  python mock_esp32_node.py --team black --burst 20           # 20 rapid points
  python mock_esp32_node.py --both --demo-match               # play a full match
"""

from __future__ import annotations

import argparse
import itertools
import os
import random
import sys
import threading
import time
from typing import Optional

import requests

DEFAULT_URL = os.environ.get("SUMMA_URL", "http://127.0.0.1:5000")
DEFAULT_TOKEN = os.environ.get("SUMMA_NODE_TOKEN", "change-me-in-env")

_event_counter = itertools.count(1)


def event_id(node_id: str) -> str:
    return f"{node_id}-{int(time.time() * 1000)}-{next(_event_counter):05d}"


class Node:
    def __init__(self, team: str, url: str, token: str):
        self.team = team
        self.node_id = f"xiao-{team}"
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Content-Type"] = "application/json"
        self.boot_ts = time.time()
        self.fw = "v2.0.0-mock"
        self._stop = threading.Event()

    def heartbeat(self, state: str = "idle") -> None:
        payload = {
            "node_id": self.node_id,
            "team": self.team,
            "source": "esp32",
            "rssi": random.randint(-75, -45),
            "vbat_mv": random.randint(3700, 4150),
            "uptime_s": int(time.time() - self.boot_ts),
            "fw": self.fw,
            "state": state,
        }
        try:
            r = self.session.post(f"{self.url}/sensor_heartbeat", json=payload, timeout=1.5)
            if r.status_code != 200:
                print(f"[{self.node_id}] heartbeat {r.status_code}: {r.text}")
        except requests.RequestException as e:
            print(f"[{self.node_id}] heartbeat err: {e}")

    def event(self, kind: str, hold_ms: int = 900, eid: Optional[str] = None) -> dict:
        assert kind in ("addpoint", "subtractpoint", "resetmatch")
        payload = {
            "team": self.team,
            "node_id": self.node_id,
            "event_id": eid or event_id(self.node_id),
            "source": "esp32",
            "hold_ms": hold_ms,
            "fw": self.fw,
        }
        try:
            r = self.session.post(f"{self.url}/{kind}", json=payload, timeout=2.0)
            return r.json()
        except requests.RequestException as e:
            return {"success": False, "error": str(e)}

    def heartbeat_loop(self, interval_s: float = 2.0) -> None:
        while not self._stop.is_set():
            self.heartbeat()
            self._stop.wait(interval_s)

    def start_heartbeat(self) -> threading.Thread:
        t = threading.Thread(target=self.heartbeat_loop, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()


def cmd_single(args) -> None:
    node = Node(args.team, args.url, args.token)
    if args.heartbeat_only:
        print(f"[{node.node_id}] heartbeat-only mode, Ctrl+C to stop")
        node.heartbeat_loop(args.interval)
        return
    node.start_heartbeat(args.interval)
    if args.burst:
        print(f"[{node.node_id}] burst of {args.burst} addpoints")
        for i in range(args.burst):
            resp = node.event("addpoint", hold_ms=random.randint(300, 2500))
            print(f"  {i + 1:02d}: deduped={resp.get('deduped', False)} matchwon={resp.get('matchwon', False)}")
            time.sleep(args.burst_delay)
    elif args.event:
        resp = node.event(args.event, hold_ms=args.hold_ms)
        print(f"[{node.node_id}] {args.event}: {resp}")
    if args.keep_alive:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    node.stop()


def cmd_demo_match(args) -> None:
    black = Node("black", args.url, args.token)
    yellow = Node("yellow", args.url, args.token)
    black.start_heartbeat(args.interval)
    yellow.start_heartbeat(args.interval)

    set_mode = requests.post(
        f"{args.url}/setgamemode",
        json={"mode": "competition"},
        timeout=2,
    )
    print(f"set mode: {set_mode.json()}")

    time.sleep(0.5)
    # Black wins 6-2, 6-3
    script = (["black"] * 4 + ["yellow"] * 1) * 2 + ["black"] * 4 * 4 + \
             ["yellow"] * 4 * 1 + ["black"] * 4 * 4 + ["yellow"] * 4 * 3
    for team in script:
        (black if team == "black" else yellow).event("addpoint", hold_ms=random.randint(400, 2000))
        time.sleep(args.delay)
    print("demo finished")
    black.stop(); yellow.stop()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--token", default=DEFAULT_TOKEN)
    p.add_argument("--team", choices=["black", "yellow"], default="black")
    p.add_argument("--interval", type=float, default=2.0, help="heartbeat interval (s)")
    p.add_argument("--event", choices=["addpoint", "subtractpoint", "resetmatch"])
    p.add_argument("--hold-ms", type=int, default=900)
    p.add_argument("--burst", type=int, default=0)
    p.add_argument("--burst-delay", type=float, default=0.05)
    p.add_argument("--heartbeat-only", action="store_true")
    p.add_argument("--keep-alive", action="store_true")
    p.add_argument("--demo-match", action="store_true")
    args = p.parse_args()

    if args.demo_match:
        cmd_demo_match(args)
    else:
        cmd_single(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
