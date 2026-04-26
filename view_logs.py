#!/usr/bin/env python3
"""
SUMMA V3 — Log Viewer & Service Control

Logs live in memory (no files). The backend exposes them at GET /logs.
The serial bridge ships its own lines to the same buffer via POST /logs/ingest,
so this one viewer shows everything:
  * backend  — Flask / Socket.IO / scoring engine
  * bridge   — ESP32 serial -> /remote_event  ("the remote")

Usage:
  python view_logs.py                        # interactive menu
  python view_logs.py -f                     # tail all logs (follow)
  python view_logs.py -s bridge -f           # tail remote/bridge only
  python view_logs.py -s backend -n 100      # last 100 backend lines
  python view_logs.py --grep addpoint        # substring search
  python view_logs.py --status               # service status
  python view_logs.py --restart backend      # restart one service
  python view_logs.py --restart all          # restart all services
  python view_logs.py --url http://summa-pi.local:5000

Service control uses sudo systemctl — only works on Linux (the Pi).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


# ── Colours ─────────────────────────────────────────────────────────────────
class Colors:
    HEADER    = '\033[95m'
    BLUE      = '\033[94m'
    CYAN      = '\033[96m'
    GREEN     = '\033[92m'
    YELLOW    = '\033[93m'
    RED       = '\033[91m'
    BOLD      = '\033[1m'
    UNDERLINE = '\033[4m'
    END       = '\033[0m'
    GRAY      = '\033[90m'
    MAGENTA   = '\033[95m'
    DIM       = '\033[2m'


SERVICES = [
    ("summa-backend", "Backend Service"),
    ("summa-bridge",  "Bridge Service (remote)"),
    ("summa-kiosk",   "Kiosk Service"),
]
IS_LINUX = platform.system() == "Linux"
DEFAULT_URL = os.environ.get("SUMMA_URL", "http://127.0.0.1:5000")


# ── Helpers ──────────────────────────────────────────────────────────────────
def print_header(text: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.END}\n")


def _pause() -> None:
    try:
        input(f"\n{Colors.GRAY}Press Enter to continue...{Colors.END}")
    except (EOFError, KeyboardInterrupt):
        pass
    print("\n")


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _color_ok() -> bool:
    return not os.environ.get("NO_COLOR") and sys.stdout.isatty()


# ── Log fetching ─────────────────────────────────────────────────────────────
def _fetch(url: str, since: int, level: str | None,
           source: str | None, limit: int) -> dict:
    qs = [f"since={since}", f"limit={limit}"]
    if level:  qs.append(f"level={level}")
    if source: qs.append(f"source={source}")
    full = f"{url.rstrip('/')}/logs?{'&'.join(qs)}"
    with urllib.request.urlopen(full, timeout=4) as resp:
        return json.loads(resp.read())


def _colorize(e: dict) -> str:
    """Format a single log entry dict with V1-style coloured prefix."""
    level  = e.get("level", "INFO")
    source = e.get("source", "?")
    ts     = e.get("ts", "")[:19]
    msg    = e.get("msg", "")

    src_prefix = (
        f"{Colors.GREEN}[REMOTE ]{Colors.END} "
        if source == "bridge"
        else f"{Colors.BLUE}[BACKEND]{Colors.END} "
    )

    msg_lower = msg.lower()
    if any(w in msg_lower for w in ["error", "failed", "exception", "fatal"]):
        color = Colors.RED
    elif any(w in msg_lower for w in ["warning", "warn"]):
        color = Colors.YELLOW
    elif any(w in msg_lower for w in ["win", "match", "reset", "started", "ready"]):
        color = Colors.GREEN
    elif any(w in msg_lower for w in ["addpoint", "subtractpoint", "point"]):
        color = Colors.CYAN
    elif level == "DEBUG":
        color = Colors.GRAY
    else:
        color = ""

    end = Colors.END if color else ""
    return f"{Colors.GRAY}{ts}{Colors.END} {src_prefix}{color}{msg}{end}"


def _print_entries(entries: list, grep: str | None = None) -> None:
    pat = grep.lower() if grep else None
    for e in entries:
        if pat and pat not in e.get("msg", "").lower():
            continue
        print(_colorize(e))


# ── Streaming ─────────────────────────────────────────────────────────────────
def tail_logs(url: str, source: str | None = None,
              level: str | None = None, lines: int = 50,
              grep: str | None = None) -> None:
    """Fetch and print recent log lines, then exit."""
    try:
        data = _fetch(url, since=0, level=level, source=source, limit=lines)
    except urllib.error.URLError as exc:
        print(f"{Colors.RED}❌ Cannot reach {url} — {exc.reason}{Colors.END}")
        print(f"{Colors.GRAY}   Is the backend running? "
              f"(systemctl status summa-backend){Colors.END}")
        return
    _print_entries(data["entries"], grep)
    print(f"{Colors.GRAY}({len(data['entries'])} lines){Colors.END}")


def stream_logs(url: str, source: str | None = None,
                level: str | None = None, lines: int = 50,
                grep: str | None = None, interval: float = 1.0) -> None:
    """Follow logs in real time (Ctrl-C to stop)."""
    src_label = source or "ALL"
    print(f"{Colors.BOLD}📡 Streaming {src_label} logs (Ctrl+C to stop)...{Colors.END}")

    try:
        data = _fetch(url, since=0, level=level, source=source, limit=lines)
    except urllib.error.URLError as exc:
        print(f"{Colors.RED}❌ Cannot reach {url} — {exc.reason}{Colors.END}")
        return

    _print_entries(data["entries"], grep)
    last_seq = data["last_seq"]

    try:
        while True:
            time.sleep(interval)
            try:
                data = _fetch(url, since=last_seq, level=level,
                              source=source, limit=2000)
            except urllib.error.URLError:
                continue
            if data["entries"]:
                _print_entries(data["entries"], grep)
                last_seq = data["last_seq"]
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⏹️  Stopped streaming{Colors.END}")


# ── Service control ───────────────────────────────────────────────────────────
def _systemctl_ok() -> bool:
    return IS_LINUX and shutil.which("systemctl") is not None


def show_service_status() -> None:
    print_header("SERVICE STATUS")
    if not _systemctl_ok():
        print(f"{Colors.YELLOW}⚠️  systemctl not available "
              f"(service control only works on the Pi){Colors.END}\n")
        return
    for svc, label in SERVICES:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=5,
            )
            state = r.stdout.strip() or "unknown"
        except Exception as exc:
            state = f"error: {exc}"

        if state == "active":
            icon  = f"{Colors.GREEN}✅{Colors.END}"
        elif state in ("inactive", "failed"):
            icon  = f"{Colors.RED}❌{Colors.END}"
        else:
            icon  = f"{Colors.YELLOW}⏸️ {Colors.END}"

        print(f"  {icon}  {label:<28} {state}")
    print()


def _refresh_kiosk() -> None:
    """Send F5 to the Chromium kiosk window (Linux only, best-effort)."""
    print(f"{Colors.CYAN}🔄 Refreshing kiosk display...{Colors.END}")
    try:
        subprocess.run(
            ["sudo", "-u", "pi", "DISPLAY=:0", "xdotool", "key", "F5"],
            capture_output=True, timeout=4,
        )
        print(f"{Colors.GREEN}✅ Kiosk refreshed (F5){Colors.END}")
    except Exception as exc:
        print(f"{Colors.YELLOW}⚠️  Could not refresh kiosk: {exc}{Colors.END}")


def restart_service(name: str) -> bool:
    """
    Restart summa-backend / summa-bridge / summa-kiosk.
    For backend: force-stop + clean port 5000 first, then refresh kiosk.
    """
    if not _systemctl_ok():
        print(f"{Colors.YELLOW}⚠️  systemctl not available — "
              f"service control only works on the Pi{Colors.END}")
        return False

    svc = f"summa-{name}.service"
    print(f"\n{Colors.YELLOW}🔄 Restarting {svc}...{Colors.END}\n")

    try:
        if name == "backend":
            print(f"{Colors.CYAN}📍 Step 1/4: Stopping backend service...{Colors.END}")
            subprocess.run(["sudo", "systemctl", "stop", svc],
                           capture_output=True, timeout=10)
            time.sleep(0.5)

            print(f"{Colors.CYAN}📍 Step 2/4: Killing any process on port 5000...{Colors.END}")
            subprocess.run(["sudo", "fuser", "-k", "5000/tcp"],
                           capture_output=True, timeout=5)
            time.sleep(1)
            print(f"{Colors.GREEN}✅ Port cleaned{Colors.END}")

            print(f"{Colors.CYAN}📍 Step 3/4: Starting backend...{Colors.END}")

        r = subprocess.run(
            ["sudo", "systemctl", "restart", svc],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            print(f"{Colors.RED}❌ restart failed: {r.stderr.strip()}{Colors.END}")
            return False

        time.sleep(2)
        status = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True, text=True,
        ).stdout.strip()

        if status == "active":
            print(f"{Colors.GREEN}✅ {svc} restarted successfully!{Colors.END}")
            if name == "backend":
                step = "Step 4/4"
                print(f"{Colors.CYAN}📍 {step}: Refreshing kiosk display...{Colors.END}")
                time.sleep(1)
                _refresh_kiosk()
            return True
        else:
            print(f"{Colors.RED}❌ {svc} did not come up (state: {status}){Colors.END}")
            return False

    except subprocess.TimeoutExpired:
        print(f"{Colors.RED}❌ Command timed out — trying force restart...{Colors.END}")
        subprocess.run(["sudo", "systemctl", "kill", svc], capture_output=True)
        time.sleep(1)
        subprocess.run(["sudo", "systemctl", "restart", svc], capture_output=True)
        time.sleep(2)
        state = subprocess.run(["systemctl", "is-active", svc],
                                capture_output=True, text=True).stdout.strip()
        if state == "active":
            print(f"{Colors.GREEN}✅ {svc} force restarted{Colors.END}")
            if name == "backend":
                _refresh_kiosk()
            return True
        print(f"{Colors.RED}❌ Force restart failed{Colors.END}")
        return False
    except Exception as exc:
        print(f"{Colors.RED}❌ Error: {exc}{Colors.END}")
        return False


def restart_all_services() -> None:
    print_header("RESTARTING ALL SERVICES")
    ok = 0
    for svc, label in SERVICES:
        name = svc.replace("summa-", "")
        print(f"\n{Colors.BOLD}{label}{Colors.END}")
        if restart_service(name):
            ok += 1
        time.sleep(0.5)
    print()
    total = len(SERVICES)
    if ok == total:
        print(f"{Colors.GREEN}✅ All {total} services restarted successfully!{Colors.END}")
    else:
        print(f"{Colors.YELLOW}⚠️  {ok}/{total} services restarted{Colors.END}")


# ── Interactive menu ──────────────────────────────────────────────────────────
def interactive_menu(url: str) -> None:
    while True:
        print_header("SUMMA V3 — LOG VIEWER & SERVICE CONTROL")

        print(f"{Colors.BOLD}Real-time Logs (follow):{Colors.END}")
        print("   1. Stream ALL logs (backend + remote)")
        print("   2. Stream BACKEND logs only")
        print("   3. Stream REMOTE / bridge logs only")
        print()

        print(f"{Colors.BOLD}Historical Logs:{Colors.END}")
        print("   4. Show last 50 lines  — all sources")
        print("   5. Show last 50 lines  — backend only")
        print("   6. Show last 50 lines  — remote only")
        print("   7. Show last 200 lines — all sources")
        print("   8. Show last 200 lines — backend only")
        print("   9. Show last 200 lines — remote only")
        print()

        print(f"{Colors.BOLD}Filter:{Colors.END}")
        print("  10. Show WARNING and above (all sources)")
        print("  11. Search (substring, all sources)")
        print()

        print(f"{Colors.BOLD}Service Management:{Colors.END}")
        print(f"  {Colors.GREEN}12. Restart Backend{Colors.END}          (force stop + clean port + refresh kiosk)")
        print(f"  {Colors.GREEN}13. Restart Bridge{Colors.END} (remote)  (reconnects to ESP32)")
        print(f"  {Colors.GREEN}14. Restart Kiosk{Colors.END}            (relaunch browser)")
        print(f"  {Colors.YELLOW}15. Restart ALL services{Colors.END}")
        print(f"  {Colors.CYAN}16. Service Status{Colors.END}")
        print()
        print("   0. Exit")
        print()

        choice = _ask(f"{Colors.CYAN}Enter choice: {Colors.END}")
        print()

        if choice == "0":
            print(f"{Colors.GREEN}👋 Goodbye!{Colors.END}")
            break

        elif choice == "1":
            stream_logs(url, source=None, lines=20)
        elif choice == "2":
            stream_logs(url, source="backend", lines=20)
        elif choice == "3":
            stream_logs(url, source="bridge", lines=20)

        elif choice == "4":
            tail_logs(url, source=None, lines=50)
        elif choice == "5":
            tail_logs(url, source="backend", lines=50)
        elif choice == "6":
            tail_logs(url, source="bridge", lines=50)
        elif choice == "7":
            tail_logs(url, source=None, lines=200)
        elif choice == "8":
            tail_logs(url, source="backend", lines=200)
        elif choice == "9":
            tail_logs(url, source="bridge", lines=200)

        elif choice == "10":
            tail_logs(url, level="WARNING", lines=500)
        elif choice == "11":
            pattern = _ask("Search pattern: ")
            if pattern:
                tail_logs(url, lines=1000, grep=pattern)
            else:
                print(f"{Colors.RED}❌ No pattern entered{Colors.END}")

        elif choice == "12":
            restart_service("backend")
        elif choice == "13":
            restart_service("bridge")
        elif choice == "14":
            restart_service("kiosk")
        elif choice == "15":
            confirm = _ask(
                f"{Colors.YELLOW}⚠️  Restart ALL services? (yes/no): {Colors.END}"
            ).lower()
            if confirm in ("yes", "y"):
                restart_all_services()
            else:
                print(f"{Colors.GRAY}Cancelled{Colors.END}")
        elif choice == "16":
            show_service_status()

        else:
            print(f"{Colors.RED}❌ Invalid choice{Colors.END}")

        if choice != "0":
            _pause()


# ── CLI entry ─────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        description="SUMMA V3 log viewer & service control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                        # interactive menu
  %(prog)s -f                     # stream all logs
  %(prog)s -s bridge -f           # stream remote/bridge logs
  %(prog)s -s backend -n 100      # last 100 backend lines
  %(prog)s --grep addpoint        # search all logs
  %(prog)s --status               # show service status
  %(prog)s --restart backend      # restart backend
  %(prog)s --restart all          # restart all services
  %(prog)s --url http://summa-pi.local:5000
        """,
    )
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"Backend base URL (default: {DEFAULT_URL})")
    ap.add_argument("-n", "--lines", type=int, default=50,
                    help="Lines to show (default 50)")
    ap.add_argument("-f", "--follow", action="store_true",
                    help="Stream logs in real time (Ctrl-C to stop)")
    ap.add_argument("-s", "--source", choices=["backend", "bridge"],
                    help="Filter by source  ('bridge' = the remote)")
    ap.add_argument("-l", "--level",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                    help="Minimum log level to show")
    ap.add_argument("--grep", default=None,
                    help="Client-side substring filter (case-insensitive)")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="Poll interval in seconds when --follow (default 1.0)")
    ap.add_argument("--status", action="store_true",
                    help="Show systemd service status and exit")
    ap.add_argument("--restart",
                    choices=["backend", "bridge", "kiosk", "all"],
                    help="Restart a service and exit")
    args = ap.parse_args()

    # One-shot service ops
    if args.status:
        show_service_status()
        return 0
    if args.restart:
        if args.restart == "all":
            restart_all_services()
        else:
            restart_service(args.restart)
        return 0

    # No flags → interactive menu when stdin is a TTY
    no_flags = not (args.follow or args.source or args.level
                    or args.grep or args.lines != 50)
    if no_flags and sys.stdin.isatty():
        try:
            interactive_menu(args.url)
        except KeyboardInterrupt:
            print(f"\n{Colors.GREEN}👋 Goodbye!{Colors.END}")
        return 0

    # One-shot / pipe mode
    if args.follow:
        stream_logs(args.url, source=args.source, level=args.level,
                    lines=args.lines, grep=args.grep, interval=args.interval)
    else:
        tail_logs(args.url, source=args.source, level=args.level,
                  lines=args.lines, grep=args.grep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
