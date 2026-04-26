#!/usr/bin/env python3
"""
SUMMA V3 — Full Raspberry Pi Setup
===================================

One script. Run it once. Everything is ready.
After this finishes the only thing left to do is plug the ESP32 receiver
into a USB port and reboot the Pi.

What this script does:
  1.  Pre-flight checks  (OS, user, repo path, file structure)
  2.  apt packages       (Python, Chromium, serial, X11 tools)
  3.  pip packages       (Flask, SocketIO, pyserial, CORS)
  4.  dialout group      (serial port access without sudo)
  5.  systemd services   (backend + bridge + kiosk — patched & enabled)
  6.  Screen blanking    (disabled via lightdm + raspi-config)
  7.  Post-install verification (imports, binaries, services, HTTP, USB)
  8.  Summary            (PASS / WARN / FAIL for every check)

Run from the SUMMAV3 directory:
    python3 setup.py

Safe to re-run — all steps are idempotent.
Tested on: Raspberry Pi OS Bookworm (12) with Desktop, Pi 3B 1 GB.
"""
from __future__ import annotations

import os
import platform

if platform.system() != "Linux":
    print("ERROR: setup.py only runs on Linux (Raspberry Pi OS).")
    raise SystemExit(1)

import grp
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


# ── Colours ──────────────────────────────────────────────────────────────────
class C:
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    END    = "\033[0m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{C.END}"


TAG_PASS = _c(C.GREEN,  "[PASS]")
TAG_WARN = _c(C.YELLOW, "[WARN]")
TAG_FAIL = _c(C.RED,    "[FAIL]")
TAG_INFO = _c(C.CYAN,   "[INFO]")
TAG_STEP = _c(C.BLUE,   "[STEP]")


def header(title: str) -> None:
    line = "=" * 70
    print(f"\n{_c(C.BOLD + C.BLUE, line)}")
    print(_c(C.BOLD + C.CYAN, title.center(70)))
    print(f"{_c(C.BOLD + C.BLUE, line)}\n")


# ── Check accumulator ─────────────────────────────────────────────────────────
_checks: list[tuple[str, str]] = []   # (tag, message)
_fail_count = 0


def record(tag: str, msg: str) -> None:
    global _fail_count
    _checks.append((tag, msg))
    if tag == TAG_FAIL:
        _fail_count += 1


def ok(msg: str)   -> None: print(f"  {TAG_PASS} {msg}"); record(TAG_PASS, msg)
def warn(msg: str) -> None: print(f"  {TAG_WARN} {msg}"); record(TAG_WARN, msg)
def fail(msg: str) -> None: print(f"  {TAG_FAIL} {msg}"); record(TAG_FAIL, msg)
def info(msg: str) -> None: print(f"  {TAG_INFO} {msg}")
def step(msg: str) -> None: print(f"  {TAG_STEP} {msg}")


# ── Shell helpers ─────────────────────────────────────────────────────────────
def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        timeout: int = 60, user: str | None = None) -> subprocess.CompletedProcess:
    if user and os.geteuid() == 0:
        cmd = ["sudo", "-u", user] + cmd
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        check=check,
    )


def sudo(cmd: list[str], *, check: bool = True, capture: bool = True,
         timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo"] + cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        check=check,
    )


def service_state(name: str) -> str:
    r = subprocess.run(
        ["systemctl", "is-active", f"{name}.service"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or "unknown"


def service_enabled(name: str) -> str:
    r = subprocess.run(
        ["systemctl", "is-enabled", f"{name}.service"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or "unknown"


# ── Resolve repo & running user ───────────────────────────────────────────────
REPO = Path(__file__).resolve().parent

# When called with sudo, SUDO_USER tells us who actually ran it
_run_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
_run_home = Path(pwd.getpwnam(_run_user).pw_dir)

PYTHON = sys.executable   # same interpreter that runs this script


# =============================================================================
# 1. Pre-flight
# =============================================================================
def preflight() -> None:
    header("Step 1 — Pre-flight checks")

    # Must be Linux
    if platform.system() != "Linux":
        print(f"{TAG_FAIL} This script only runs on Linux (Raspberry Pi OS).")
        sys.exit(1)
    info(f"Platform: {platform.system()} {platform.release()}")

    # Raspberry Pi check
    pi_model = ""
    for path in ("/proc/cpuinfo", "/sys/firmware/devicetree/base/model"):
        try:
            text = Path(path).read_text(errors="replace")
            if "raspberry" in text.lower():
                m = re.search(r"Raspberry Pi[^\n]+", text, re.IGNORECASE)
                pi_model = m.group(0).strip("\x00").strip() if m else "Raspberry Pi"
                break
        except OSError:
            pass
    if pi_model:
        ok(f"Raspberry Pi detected: {pi_model}")
    else:
        warn("Raspberry Pi NOT detected — continuing anyway")

    # sudo access
    r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    if r.returncode == 0:
        ok("sudo available (passwordless)")
    else:
        info("sudo may prompt for a password during installation")

    # Repo file structure
    required = [
        "backend_pi.py",
        "tools/serial_bridge.py",
        "tools/start_kiosk.sh",
        "systemd/summa-backend.service",
        "systemd/summa-bridge.service",
        "systemd/summa-kiosk.service",
    ]
    all_ok = True
    for rel in required:
        p = REPO / rel
        if p.exists():
            ok(f"Found: {rel}")
        else:
            print(f"  {TAG_FAIL} Missing: {rel}")
            record(TAG_FAIL, f"Missing file: {rel}")
            all_ok = False
    if not all_ok:
        print(f"\n{TAG_FAIL} Run this script from the SUMMAV3 directory.")
        sys.exit(1)


# =============================================================================
# 2. apt packages
# =============================================================================
APT_PACKAGES = [
    "python3",
    "python3-pip",
    "python3-venv",
    "python3-serial",
    "unclutter",
    "curl",
    "wget",
    "x11-xserver-utils",
    "xdotool",
]

def install_apt() -> None:
    header("Step 2 — System packages (apt)")

    step("Updating package lists...")
    sudo(["apt-get", "update", "-y", "-qq"], capture=False, timeout=120)

    step(f"Installing: {', '.join(APT_PACKAGES)}")
    sudo(["apt-get", "install", "-y"] + APT_PACKAGES, capture=False, timeout=300)
    ok("Core packages installed")

    # Chromium (package name changed between releases)
    for pkg in ("chromium", "chromium-browser"):
        r = sudo(["apt-get", "install", "-y", pkg], capture=True, check=False, timeout=120)
        if r.returncode == 0:
            ok(f"Chromium installed ({pkg})")
            break
    else:
        warn("Could not install chromium — kiosk mode will not work")
        info("Manual fix: sudo apt install chromium")


# =============================================================================
# 3. pip packages
# =============================================================================
PIP_PACKAGES = [
    "Flask>=3.0",
    "Flask-Cors>=4.0",
    "Flask-SocketIO>=5.3",
    "python-socketio>=5.9",
    "pyserial>=3.5",
]

def install_pip() -> None:
    header("Step 3 — Python packages (pip)")

    # PEP 668 (Bookworm+): need --break-system-packages
    ver = sys.version_info
    bsp = ["--break-system-packages"] if (ver.major, ver.minor) >= (3, 11) else []

    cmd = [PYTHON, "-m", "pip", "install", "--quiet"] + bsp + PIP_PACKAGES
    step(f"pip install {' '.join(PIP_PACKAGES)}")
    subprocess.run(cmd, check=True, timeout=180)
    ok("pip packages installed")

    # Quick import smoke-test
    for pkg in ("flask", "flask_socketio", "flask_cors", "serial"):
        r = subprocess.run([PYTHON, "-c", f"import {pkg}"], capture_output=True)
        if r.returncode == 0:
            ok(f"import {pkg}")
        else:
            print(f"  {TAG_FAIL} import {pkg}")
            record(TAG_FAIL, f"import {pkg}")


# =============================================================================
# 4. dialout group
# =============================================================================
def setup_dialout() -> None:
    header("Step 4 — Serial port access (dialout group)")

    try:
        members = grp.getgrnam("dialout").gr_mem
    except KeyError:
        warn("'dialout' group does not exist on this system")
        return

    if _run_user in members:
        ok(f"{_run_user} is already in dialout")
    else:
        step(f"Adding {_run_user} to dialout...")
        sudo(["usermod", "-a", "-G", "dialout", _run_user])
        ok(f"Added — takes effect after reboot")
        warn("Reboot required for serial access without sudo")


# =============================================================================
# 5. systemd services
# =============================================================================
SERVICES = [
    {
        "name":      "summa-backend",
        "label":     "Backend",
        "execstart": f"{PYTHON} {REPO}/backend_pi.py",
        "autostart": True,
    },
    {
        "name":      "summa-bridge",
        "label":     "Bridge (remote / ESP32)",
        "execstart": f"{PYTHON} {REPO}/tools/serial_bridge.py",
        "autostart": True,
    },
    {
        "name":      "summa-kiosk",
        "label":     "Kiosk (Chromium)",
        "execstart": f"/bin/bash {REPO}/tools/start_kiosk.sh",
        "autostart": False,   # only if graphical target exists
    },
]


def _patch_unit(template: Path, execstart: str) -> str:
    text = template.read_text()
    subs = {
        r"^User=.*":             f"User={_run_user}",
        r"^WorkingDirectory=.*": f"WorkingDirectory={REPO}",
        r"^ExecStart=.*":        f"ExecStart={execstart}",
        r"^Environment=XAUTHORITY=.*":
            f"Environment=XAUTHORITY={_run_home}/.Xauthority",
    }
    for pattern, replacement in subs.items():
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    return text


def setup_services() -> None:
    header("Step 5 — systemd services")

    # Make scripts executable
    for rel in ("tools/start_kiosk.sh", "tools/serial_bridge.py"):
        p = REPO / rel
        p.chmod(p.stat().st_mode | 0o755)

    # Detect graphical target
    r = subprocess.run(["systemctl", "get-default"], capture_output=True, text=True)
    graphical = "graphical" in r.stdout

    for svc in SERVICES:
        name  = svc["name"]
        label = svc["label"]

        if name == "summa-kiosk" and not graphical:
            warn(f"Lite image (no graphical target) — {name} skipped")
            info("Point a browser at the LAN URL shown by the backend.")
            continue

        template = REPO / "systemd" / f"{name}.service"
        unit_text = _patch_unit(template, svc["execstart"])

        dest = Path(f"/etc/systemd/system/{name}.service")
        with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as tf:
            tf.write(unit_text)
            tmp = tf.name

        sudo(["install", "-m", "644", tmp, str(dest)])
        Path(tmp).unlink(missing_ok=True)
        step(f"Installed {dest}")

    step("Reloading systemd daemon...")
    sudo(["systemctl", "daemon-reload"])

    for svc in SERVICES:
        name = svc["name"]
        dest = Path(f"/etc/systemd/system/{name}.service")
        if not dest.exists():
            continue   # was skipped (kiosk on Lite)

        sudo(["systemctl", "enable", f"{name}.service"])

        if name == "summa-kiosk":
            ok(f"{name}: enabled (starts with graphical session)")
            continue

        # Start backend immediately; bridge may fail if no ESP — that's fine
        r = sudo(["systemctl", "restart", f"{name}.service"], check=False)
        if r.returncode == 0:
            ok(f"{name}: enabled + started")
        else:
            warn(f"{name}: enabled but did not start yet (may need ESP or a reboot)")


# =============================================================================
# 6. Screen blanking
# =============================================================================
def disable_blanking() -> None:
    header("Step 6 — Disable screen blanking")

    # lightdm.conf
    lightdm = Path("/etc/lightdm/lightdm.conf")
    if lightdm.exists():
        text = lightdm.read_text()
        if "xserver-command=X -s 0 -dpms" not in text:
            new = re.sub(
                r"^#xserver-command=X",
                "xserver-command=X -s 0 -dpms",
                text,
                flags=re.MULTILINE,
            )
            with tempfile.NamedTemporaryFile("w", delete=False) as tf:
                tf.write(new); tmp = tf.name
            sudo(["install", "-m", "644", tmp, str(lightdm)])
            Path(tmp).unlink(missing_ok=True)
            ok("lightdm.conf: DPMS disabled")
        else:
            ok("lightdm.conf: DPMS already disabled")
    else:
        warn("/etc/lightdm/lightdm.conf not found — skipping")

    # raspi-config
    if shutil.which("raspi-config"):
        r = sudo(["raspi-config", "nonint", "do_blanking", "1"], check=False)
        if r.returncode == 0:
            ok("raspi-config: screen blanking disabled")
        else:
            warn("raspi-config do_blanking not available on this version")

    info("Per-session xset commands are handled inside tools/start_kiosk.sh")


# =============================================================================
# 7. Verification
# =============================================================================
def verify() -> None:
    header("Step 7 — Verification")

    info("Waiting 5 s for services to settle...")
    time.sleep(5)

    # ── Python imports ────────────────────────────────────────────────────────
    print(f"\n{C.BOLD}Python imports:{C.END}")
    for pkg in ("flask", "flask_socketio", "flask_cors", "serial"):
        r = subprocess.run([PYTHON, "-c", f"import {pkg}"], capture_output=True)
        if r.returncode == 0:
            ok(f"import {pkg}")
        else:
            fail(f"import {pkg}")

    # ── Binaries ──────────────────────────────────────────────────────────────
    print(f"\n{C.BOLD}Binaries:{C.END}")
    chromium_found = False
    for cand in ("chromium", "chromium-browser",
                  "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        path = shutil.which(cand) or (cand if Path(cand).is_file() else None)
        if path:
            ok(f"chromium: {path}")
            chromium_found = True
            break
    if not chromium_found:
        fail("chromium not found — sudo apt install chromium")

    for bin_name in ("xdotool", "unclutter", "curl", "python3"):
        path = shutil.which(bin_name)
        if path:
            ok(f"{bin_name}: {path}")
        else:
            warn(f"{bin_name} not found")

    # ── systemd services ──────────────────────────────────────────────────────
    print(f"\n{C.BOLD}systemd services:{C.END}")
    for svc in SERVICES:
        name  = svc["name"]
        dest  = Path(f"/etc/systemd/system/{name}.service")
        if not dest.exists():
            warn(f"{name}: not installed (Lite image — kiosk skipped)")
            continue

        state   = service_state(name)
        enabled = service_enabled(name)

        if state == "active":
            ok(f"{name}: active / {enabled}")
        elif name == "summa-bridge" and state in ("failed", "activating"):
            warn(f"{name}: {state} — plug the ESP32 in after reboot")
        elif name == "summa-kiosk":
            warn(f"{name}: {state} — will start with the graphical session")
        else:
            fail(f"{name}: {state} / {enabled}")
            info(f"Diagnose: journalctl -u {name}.service -n 30 --no-pager")

    # ── Backend HTTP ──────────────────────────────────────────────────────────
    print(f"\n{C.BOLD}Backend HTTP:{C.END}")
    url = "http://127.0.0.1:5000"
    http_ok = False
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url + "/", timeout=3) as resp:
                code = resp.status
            if 200 <= code < 400:
                ok(f"backend responds HTTP {code}")
                http_ok = True
                break
        except Exception:
            pass
        if attempt < 3:
            time.sleep(2)
    if not http_ok:
        fail(f"backend not responding at {url}")
        info("Check: journalctl -u summa-backend.service -n 30 --no-pager")

    # /logs endpoint
    try:
        with urllib.request.urlopen(url + "/logs?limit=1", timeout=3) as resp:
            if resp.status == 200:
                ok("/logs endpoint: OK")
            else:
                warn(f"/logs endpoint: HTTP {resp.status}")
    except Exception as exc:
        warn(f"/logs endpoint: {exc}")

    # ── ESP32 / USB serial ────────────────────────────────────────────────────
    print(f"\n{C.BOLD}ESP32 / USB serial:{C.END}")
    esp_devs = [
        p for p in Path("/dev").glob("ttyACM*")
    ] + [
        p for p in Path("/dev").glob("ttyUSB*")
    ]
    if esp_devs:
        for dev in esp_devs:
            ok(f"USB serial device found: {dev}")
    else:
        warn("No ESP32 detected on USB — plug it in after reboot")
        info("summa-bridge will connect automatically when it appears")

    # dialout membership
    try:
        members = grp.getgrnam("dialout").gr_mem
        if _run_user in members:
            ok(f"{_run_user} is in dialout group")
        else:
            warn(f"{_run_user} not yet in dialout (takes effect after reboot)")
    except KeyError:
        warn("dialout group not found")

    # ── Token file ────────────────────────────────────────────────────────────
    print(f"\n{C.BOLD}Token:{C.END}")
    token_file = _run_home / ".summa_token"
    if token_file.exists():
        token_preview = token_file.read_text().strip()[:12]
        ok(f"token file: {token_file}  ({token_preview}...)")
    else:
        warn("token file not created yet — backend generates it on first run")


# =============================================================================
# 8. Summary
# =============================================================================
def summary() -> None:
    header("Setup Summary")

    pass_n = sum(1 for t, _ in _checks if t == TAG_PASS)
    warn_n = sum(1 for t, _ in _checks if t == TAG_WARN)

    for tag, msg in _checks:
        print(f"  {tag} {msg}")

    print()
    if _fail_count == 0:
        print(_c(C.BOLD + C.GREEN,
                 f"All checks passed!  ({pass_n} passed, {warn_n} warnings)"))
    else:
        print(_c(C.BOLD + C.RED,
                 f"{_fail_count} check(s) FAILED — review the items above."))

    try:
        lan_ip = subprocess.check_output(
            ["hostname", "-I"], text=True
        ).split()[0]
    except Exception:
        lan_ip = "Pi-IP"

    print(f"""
{_c(C.BOLD + C.CYAN, "Next steps:")}
  1. {_c(C.BOLD, "Reboot:")}        sudo reboot
  2. {_c(C.BOLD, "Plug in ESP32")} receiver via USB  ->  bridge auto-connects
  3. Scoreboard opens in Chromium on the TV. Done.

{_c(C.BOLD, "Useful commands after reboot:")}
  python3 view_logs.py            # interactive log viewer + service control
  python3 view_logs.py --status   # quick service status
  python3 view_logs.py -f         # live tail all logs

{_c(C.BOLD, "Scoreboard URLs:")}
  Local (on Pi):  http://127.0.0.1:5000/
  LAN:            http://{lan_ip}:5000/
""")


# =============================================================================
# Entry point
# =============================================================================
def main() -> int:
    header("SUMMA V3 — Raspberry Pi Setup")
    print(f"  {TAG_INFO} Repo:  {_c(C.BOLD, str(REPO))}")
    print(f"  {TAG_INFO} User:  {_c(C.BOLD, _run_user)}")
    print(f"  {TAG_INFO} Home:  {_c(C.BOLD, str(_run_home))}")
    print(f"  {TAG_INFO} Python: {_c(C.BOLD, sys.version.split()[0])} ({PYTHON})")

    try:
        preflight()
        install_apt()
        install_pip()
        setup_dialout()
        setup_services()
        disable_blanking()
        verify()
        summary()
    except KeyboardInterrupt:
        print(f"\n{TAG_WARN} Interrupted by user.")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"\n{TAG_FAIL} Command failed: {exc.cmd}")
        print(f"  Return code: {exc.returncode}")
        if exc.stderr:
            print(f"  stderr: {exc.stderr.strip()}")
        return 1

    return 1 if _fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
