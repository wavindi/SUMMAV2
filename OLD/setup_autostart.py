#!/usr/bin/env python3

"""
SUMMA Padel Scoreboard - Complete Setup & Dependency Installer
Modes:
  1. Full Install — Raspberry Pi LITE (headless)
  2. Full Install — Raspberry Pi DESKTOP (GUI/LXDE)
  3. Dependencies Only (packages + pigpio, no autostart)
  4. Configure Autostart Only (skip packages)
  5. Check System Status + Recommendations
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path
import time

# ════════════════════════════════════════════════════════════════
#  COLORS
# ════════════════════════════════════════════════════════════════

class Colors:
    GREEN   = '\033[92m'
    YELLOW  = '\033[93m'
    RED     = '\033[91m'
    BLUE    = '\033[94m'
    CYAN    = '\033[96m'
    MAGENTA = '\033[95m'
    BOLD    = '\033[1m'
    END     = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}\n")

def print_success(text): print(f"{Colors.GREEN}✅ {text}{Colors.END}")
def print_error(text):   print(f"{Colors.RED}❌ {text}{Colors.END}")
def print_warning(text): print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")
def print_info(text):    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")
def print_step(step, total, text): print(f"\n{Colors.CYAN}[{step}/{total}] {text}{Colors.END}")
def print_section(text): print(f"\n{Colors.BOLD}{Colors.YELLOW}── {text} {'─'*(65-len(text))}{Colors.END}")

# ════════════════════════════════════════════════════════════════
#  CORE UTILITIES
# ════════════════════════════════════════════════════════════════

def check_root():
    if os.geteuid() != 0:
        print_error("This script must be run with sudo!")
        print_info("Usage: sudo python3 setup_autostart.py")
        sys.exit(1)

def run_command(cmd, check=True, show_output=False):
    try:
        if show_output:
            result = subprocess.run(cmd, shell=True, check=check)
            return result.returncode == 0, "", ""
        else:
            result = subprocess.run(cmd, shell=True, check=check,
                                    capture_output=True, text=True)
            return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout if hasattr(e, 'stdout') else "", e.stderr if hasattr(e, 'stderr') else ""

def find_executable(names):
    for name in names:
        path = shutil.which(name)
        if path and os.path.exists(path):
            return path
        for prefix in ['/usr/bin/', '/usr/local/bin/', '/bin/']:
            full_path = prefix + name
            if os.path.exists(full_path):
                return full_path
    return None

def get_actual_user():
    return os.environ.get('SUDO_USER', 'pi')

def get_project_paths():
    script_dir = Path(__file__).parent.resolve()
    actual_user = get_actual_user()

    paths = {
        'project_root':  script_dir,
        'sensor_script': script_dir / 'sensor_script.py',
        'backend_script': script_dir / 'padel_backend.py',
        'html_file':     script_dir / 'padel_scoreboard.html',
        'user':          actual_user,
        'user_home':     Path(f'/home/{actual_user}'),
    }

    if not paths['sensor_script'].exists():
        paths['sensor_script'] = script_dir / 'sensor' / 'sensor_script.py'

    print_header("Validating Project Files")
    all_exist = True
    for name, path in paths.items():
        if name in ('project_root', 'user', 'user_home'):
            continue
        if path.exists():
            print_success(f"Found: {path}")
        else:
            print_error(f"Missing: {path}")
            all_exist = False

    if not all_exist:
        print_error("Some required files are missing!")
        print_info("Make sure you're running from the SUMMAV1 project directory.")
        print_info("Required: padel_backend.py, padel_scoreboard.html, sensor_script.py")
        sys.exit(1)

    print_info(f"Project root : {paths['project_root']}")
    print_info(f"Running as   : {paths['user']}")
    return paths

# ════════════════════════════════════════════════════════════════
#  SYSTEM UPDATE
# ════════════════════════════════════════════════════════════════

def update_system():
    print_info("Updating package lists...")
    run_command('apt-get update -qq', check=False)
    print_success("Package lists updated")

# ════════════════════════════════════════════════════════════════
#  PIGPIO INSTALL
# ════════════════════════════════════════════════════════════════

def install_pigpio():
    """
    Try apt first (Bookworm-safe, no distutils needed).
    Fall back to source compile if apt fails.
    """
    print_info("Trying apt install for pigpio (Bookworm-safe)...")
    ok, _, _ = run_command('apt-get install -y pigpio python3-pigpio', check=False)
    if ok:
        print_success("pigpio installed via apt")
        run_command('pip3 install pigpio --break-system-packages -q', check=False)
        return True

    # Fallback: source compile
    print_warning("apt install failed — falling back to source compile...")
    run_command('apt-get install -y python3-setuptools gcc make libc6-dev', check=False)

    actual_user = get_actual_user()
    temp_dir = f'/tmp/pigpio_install_{int(time.time())}'
    os.makedirs(temp_dir, exist_ok=True)
    os.chdir(temp_dir)

    print_info("Downloading pigpio...")
    ok, _, _ = run_command(
        'wget -q https://github.com/joan2937/pigpio/archive/master.zip -O master.zip',
        check=False)
    if not ok:
        ok, _, _ = run_command(
            'curl -L https://github.com/joan2937/pigpio/archive/master.zip -o master.zip',
            check=False)
    if not ok:
        print_error("Failed to download pigpio — check internet connection")
        return False

    run_command('unzip -q master.zip', check=False)
    os.chdir('pigpio-master')

    print_info("Compiling pigpio (2-3 minutes)...")
    ok, _, err = run_command('make -j4 > /dev/null 2>&1', check=False)
    if not ok:
        print_error(f"Compile failed: {err[:200]}")
        return False

    print_info("Installing pigpio...")
    ok, _, err = run_command('make install > /dev/null 2>&1', check=False)
    if not ok:
        print_error(f"Install failed: {err[:200]}")
        return False

    os.chdir('/')
    run_command(f'rm -rf {temp_dir}', check=False)
    run_command('pip3 install pigpio --break-system-packages -q', check=False)
    print_success("pigpio compiled and installed from source")
    return True

# ════════════════════════════════════════════════════════════════
#  PIGPIOD SERVICE
# ════════════════════════════════════════════════════════════════

def setup_pigpiod():
    """Create, enable and start pigpiod — auto-detects binary path."""
    print_section("Setting Up pigpiod Service")

    # Find pigpiod binary (apt → /usr/bin, source → /usr/local/bin)
    pigpiod_path = find_executable(['pigpiod']) or '/usr/bin/pigpiod'

    service = f"""[Unit]
Description=pigpio daemon for GPIO control
After=network.target

[Service]
Type=forking
ExecStart={pigpiod_path} -l
ExecStop=/bin/systemctl kill pigpiod
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
    with open('/etc/systemd/system/pigpiod.service', 'w') as f:
        f.write(service)

    run_command('killall pigpiod 2>/dev/null; true', check=False)
    time.sleep(1)
    run_command('systemctl daemon-reload', check=False)
    run_command('systemctl enable pigpiod', check=False)
    ok, _, _ = run_command('systemctl start pigpiod', check=False)
    if ok:
        print_success(f"pigpiod enabled and started ({pigpiod_path})")
    else:
        print_warning("pigpiod may not have started — check: sudo systemctl status pigpiod")
    return True

# ════════════════════════════════════════════════════════════════
#  SYSTEM PACKAGES
# ════════════════════════════════════════════════════════════════

def install_base_system_packages():
    packages = [
        'python3-pip', 'python3-dev', 'python3-venv', 'build-essential',
        'git', 'wget', 'unzip', 'curl',
        'python3-flask', 'python3-requests',
        'python3-rpi.gpio', 'python3-smbus', 'python3-pygame',
        'i2c-tools', 'libasound2-dev',
        'unclutter', 'xdotool', 'wmctrl',
    ]
    print_info(f"Installing {len(packages)} base system packages...")
    ok, _, _ = run_command(f'apt-get install -y {" ".join(packages)}', check=False)
    if ok: print_success("Base system packages installed")
    else:  print_warning("Some packages had issues — continuing...")

def install_lite_display_packages():
    """X11 + Openbox + Chromium + Audio + Fonts — required on Pi Lite."""
    print_section("Installing Pi Lite Display Layer")
    packages = [
        # X11
        'xserver-xorg', 'xserver-xorg-video-fbdev',
        'xserver-xorg-input-evdev', 'x11-xserver-utils',
        'x11-utils', 'xinit',
        # WM
        'openbox',
        # Browser
        'chromium-browser',
        # Kiosk utilities
        'unclutter', 'xdotool', 'wmctrl',
        # Audio (pygame mixer / change.mp3)
        'alsa-utils', 'pulseaudio', 'libsdl2-mixer-2.0-0',
        # Fonts
        'fonts-liberation', 'fonts-dejavu-core', 'fontconfig',
        # SSL
        'ca-certificates',
    ]
    print_info(f"Installing {len(packages)} display/kiosk packages...")
    ok, _, _ = run_command(f'apt-get install -y {" ".join(packages)}', check=False)
    if ok: print_success("Display layer installed")
    else:  print_warning("Some display packages had issues — continuing...")

def install_gui_extra_packages():
    """Extra packages for Desktop (GUI) mode."""
    print_section("Installing GUI Mode Extras")
    packages = [
        'chromium-browser', 'unclutter', 'xdotool', 'wmctrl',
        'alsa-utils', 'fonts-liberation', 'ca-certificates',
    ]
    ok, _, _ = run_command(f'apt-get install -y {" ".join(packages)}', check=False)
    if ok: print_success("GUI extra packages installed")
    else:  print_warning("Some GUI packages had issues — continuing...")

# ════════════════════════════════════════════════════════════════
#  PYTHON PIP PACKAGES
# ════════════════════════════════════════════════════════════════

def install_python_packages():
    print_section("Installing Python pip Packages")
    actual_user = get_actual_user()

    pip_packages = [
        'flask-cors', 'flask-socketio', 'python-socketio',
        'smbus2', 'pigpio', 'eventlet', 'pygame', 'requests',
    ]

    # Ensure pip is available
    ok, _, _ = run_command('python3 -m pip --version', check=False)
    if not ok:
        run_command('apt-get install -y python3-pip', check=False)

    for pkg in pip_packages:
        # Try as actual user first
        ok, _, _ = run_command(
            f'sudo -u {actual_user} python3 -m pip install {pkg} -q',
            check=False)
        if not ok:
            ok, _, _ = run_command(
                f'pip3 install {pkg} --break-system-packages -q',
                check=False)
        if ok: print_success(f"  {pkg} ✓")
        else:  print_warning(f"  {pkg} — may have issues")

    print_success("Python packages done")

# ════════════════════════════════════════════════════════════════
#  INTERFACES (I2C / SPI / GPIO)
# ════════════════════════════════════════════════════════════════

def enable_interfaces():
    print_section("Enabling Hardware Interfaces (I2C / SPI)")

    config_file = '/boot/firmware/config.txt'
    if not os.path.exists(config_file):
        config_file = '/boot/config.txt'
    if not os.path.exists(config_file):
        print_warning("config.txt not found — enable I2C/SPI manually via raspi-config")
        return True

    try:
        with open(config_file, 'r') as f:
            content = f.read()
        lines = content.split('\n')
        modified = False

        if 'dtparam=i2c_arm=on' not in content:
            lines.append('dtparam=i2c_arm=on')
            modified = True
            print_success("I2C enabled in config.txt")
        else:
            print_info("I2C already enabled")

        if 'dtparam=spi=on' not in content:
            lines.append('dtparam=spi=on')
            modified = True
            print_success("SPI enabled in config.txt")
        else:
            print_info("SPI already enabled")

        if modified:
            with open(config_file, 'w') as f:
                f.write('\n'.join(lines))
            print_warning("Reboot required for interface changes to take effect")

    except Exception as e:
        print_error(f"Failed to modify config.txt: {e}")

    run_command('modprobe i2c-dev', check=False)
    run_command('modprobe i2c-bcm2708', check=False)

    actual_user = get_actual_user()
    run_command(f'usermod -a -G i2c,gpio,spi {actual_user}', check=False)
    print_success(f"User '{actual_user}' added to i2c/gpio/spi groups")
    return True

# ════════════════════════════════════════════════════════════════
#  AUTOSTART — LITE (Openbox + .xinitrc)
# ════════════════════════════════════════════════════════════════

def configure_autostart_lite():
    print_section("Configuring Kiosk Autostart — Lite Mode")
    actual_user = get_actual_user()
    user_home = Path(f'/home/{actual_user}')

    # Auto-login on tty1
    autologin_dir = "/etc/systemd/system/getty@tty1.service.d"
    os.makedirs(autologin_dir, exist_ok=True)
    with open(f"{autologin_dir}/autologin.conf", 'w') as f:
        f.write("[Service]\nExecStart=\n"
                f"ExecStart=-/sbin/agetty --autologin {actual_user} --noclear %I $TERM\n")
    run_command('systemctl daemon-reload', check=False)
    print_success(f"Auto-login configured for '{actual_user}' on tty1")

    # ~/.bash_profile
    bash_profile = user_home / '.bash_profile'
    with open(bash_profile, 'w') as f:
        f.write("# SUMMA: Auto-start X on tty1\n")
        f.write("if [ -z \"$DISPLAY\" ] && [ \"$(tty)\" = \"/dev/tty1\" ]; then\n")
        f.write("    startx -- -nocursor 2>/dev/null\n")
        f.write("fi\n")
    run_command(f'chown {actual_user}:{actual_user} {bash_profile}', check=False)
    print_success("~/.bash_profile configured")

    # ~/.xinitrc
    xinitrc = user_home / '.xinitrc'
    with open(xinitrc, 'w') as f:
        f.write("#!/bin/bash\n# SUMMA Padel Scoreboard — Kiosk Mode (Lite)\n\n")
        f.write("xset s off\nxset s noblank\nxset -dpms\n\n")
        f.write("unclutter -idle 0.1 -root &\n")
        f.write("openbox &\n\n")
        f.write("echo 'Waiting for SUMMA backend...'\n")
        f.write("for i in $(seq 1 30); do\n")
        f.write("    curl -s http://localhost:5000/health > /dev/null 2>&1 && break\n")
        f.write("    echo \"  attempt $i/30...\"\n")
        f.write("    sleep 1\n")
        f.write("done\n\n")
        f.write("chromium-browser --kiosk \\\n")
        f.write("  --no-first-run \\\n")
        f.write("  --disable-infobars \\\n")
        f.write("  --disable-session-crashed-bubble \\\n")
        f.write("  --disable-restore-session-state \\\n")
        f.write("  --autoplay-policy=no-user-gesture-required \\\n")
        f.write("  --check-for-update-interval=31536000 \\\n")
        f.write("  http://localhost:5000\n")
    run_command(f'chmod +x {xinitrc}', check=False)
    run_command(f'chown {actual_user}:{actual_user} {xinitrc}', check=False)
    print_success("~/.xinitrc kiosk config created")

    _create_backend_service()
    _create_sensor_service()
    print_success("Lite autostart fully configured")

# ════════════════════════════════════════════════════════════════
#  AUTOSTART — GUI (LXDE autostart)
# ════════════════════════════════════════════════════════════════

def configure_autostart_gui():
    print_section("Configuring Kiosk Autostart — GUI / LXDE Mode")
    actual_user = get_actual_user()

    lxde_dir = f"/home/{actual_user}/.config/lxsession/LXDE-pi"
    os.makedirs(lxde_dir, exist_ok=True)
    with open(f"{lxde_dir}/autostart", 'w') as f:
        f.write("# SUMMA Padel Scoreboard — Kiosk Mode (GUI)\n")
        f.write("@xset s off\n@xset s noblank\n@xset -dpms\n")
        f.write("@unclutter -idle 0.1 -root\n")
        f.write("@bash -c 'for i in $(seq 1 30); do "
                "curl -s http://localhost:5000/health > /dev/null 2>&1 && break; "
                "sleep 1; done; "
                "chromium-browser --kiosk --no-first-run --disable-infobars "
                "--autoplay-policy=no-user-gesture-required "
                "http://localhost:5000'\n")
    run_command(f'chown -R {actual_user}:{actual_user} /home/{actual_user}/.config', check=False)
    print_success("LXDE autostart configured for kiosk mode")

    _create_backend_service()
    _create_sensor_service()
    print_success("GUI autostart fully configured")

# ════════════════════════════════════════════════════════════════
#  SYSTEMD SERVICES
# ════════════════════════════════════════════════════════════════

def _create_backend_service():
    actual_user = get_actual_user()
    script_dir = Path(__file__).parent.resolve()
    service = f"""[Unit]
Description=SUMMA Padel Backend
After=network.target pigpiod.service
Requires=pigpiod.service

[Service]
Type=simple
User={actual_user}
WorkingDirectory={script_dir}
ExecStart=/usr/bin/python3 {script_dir}/padel_backend.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
"""
    with open('/etc/systemd/system/padel-backend.service', 'w') as f:
        f.write(service)
    run_command('systemctl daemon-reload', check=False)
    run_command('systemctl enable padel-backend', check=False)
    run_command('systemctl start padel-backend', check=False)
    print_success("padel-backend.service enabled and started")

def _create_sensor_service():
    actual_user = get_actual_user()
    script_dir = Path(__file__).parent.resolve()
    service = f"""[Unit]
Description=SUMMA Sensor Script
After=network.target pigpiod.service padel-backend.service
Requires=pigpiod.service

[Service]
Type=simple
User={actual_user}
WorkingDirectory={script_dir}
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 {script_dir}/sensor_script.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
"""
    with open('/etc/systemd/system/padel-sensor.service', 'w') as f:
        f.write(service)
    run_command('systemctl daemon-reload', check=False)
    run_command('systemctl enable padel-sensor', check=False)
    run_command('systemctl start padel-sensor', check=False)
    print_success("padel-sensor.service enabled and started")

# ════════════════════════════════════════════════════════════════
#  MANAGEMENT SCRIPT
# ════════════════════════════════════════════════════════════════

def create_management_script():
    script_dir = Path(__file__).parent.resolve()
    actual_user = get_actual_user()
    script_path = script_dir / 'manage_services.sh'

    content = """#!/bin/bash
# SUMMA Padel Scoreboard — Service Manager

SERVICES="pigpiod padel-backend padel-sensor"

case "$1" in
    status)
        for s in $SERVICES; do sudo systemctl status $s --no-pager; echo; done ;;
    start)
        for s in $SERVICES; do sudo systemctl start $s && echo "✅ Started $s"; done ;;
    stop)
        for s in padel-sensor padel-backend pigpiod; do
            sudo systemctl stop $s && echo "✅ Stopped $s"; done ;;
    restart)
        for s in $SERVICES; do sudo systemctl restart $s && echo "✅ Restarted $s"; done ;;
    logs)
        if [ -z "$2" ]; then
            sudo journalctl -f -u pigpiod -u padel-backend -u padel-sensor
        else
            sudo journalctl -f -u padel-$2
        fi ;;
    enable)
        for s in $SERVICES; do sudo systemctl enable $s && echo "✅ Enabled $s"; done ;;
    disable)
        for s in $SERVICES; do sudo systemctl disable $s && echo "✅ Disabled $s"; done ;;
    check)
        echo "=== SUMMA Health Check ==="
        for s in $SERVICES; do
            systemctl is-active --quiet $s \
                && echo "✅ $s running" \
                || echo "❌ $s NOT running"
        done
        echo ""
        curl -s http://localhost:5000/health > /dev/null 2>&1 \
            && echo "✅ Backend responding on :5000" \
            || echo "❌ Backend not responding"
        echo ""
        echo "=== I2C Devices ==="
        command -v i2cdetect &>/dev/null \
            && sudo i2cdetect -y 1 2>/dev/null \
            || echo "⚠️  i2c-tools not installed" ;;
    *)
        echo "Usage: $0 {status|start|stop|restart|logs|enable|disable|check}"
        echo "  logs [backend|sensor]  — stream specific service logs"
        exit 1 ;;
esac
"""
    with open(script_path, 'w') as f:
        f.write(content)
    os.chmod(script_path, 0o755)
    run_command(f'chown {actual_user}:{actual_user} {script_path}', check=False)
    print_success(f"Management script created: {script_path}")
    print_info("Usage: ./manage_services.sh check")

# ════════════════════════════════════════════════════════════════
#  VERIFY
# ════════════════════════════════════════════════════════════════

def verify_installation():
    print_section("Quick Verification")
    checks = [
        ('Chromium',       'which chromium-browser',              True),
        ('Python3',        'which python3',                       True),
        ('Flask',          'python3 -c "import flask"',           True),
        ('Flask-SocketIO', 'python3 -c "import flask_socketio"',  True),
        ('Flask-CORS',     'python3 -c "import flask_cors"',      True),
        ('RPi.GPIO',       'python3 -c "import RPi.GPIO"',        True),
        ('pygame',         'python3 -c "import pygame" 2>/dev/null', True),
        ('smbus2',         'python3 -c "import smbus2"',          True),
        ('pigpio module',  'python3 -c "import pigpio"',          True),
        ('pigpiod binary', 'which pigpiod',                       True),
        ('pigpiod active', 'systemctl is-active pigpiod',         False),
        ('backend svc',    'systemctl is-active padel-backend',   False),
        ('sensor svc',     'systemctl is-active padel-sensor',    False),
    ]
    failed = False
    for name, cmd, critical in checks:
        ok, stdout, _ = run_command(cmd, check=False)
        passed = ok or 'active' in stdout
        if passed:
            print_success(f"  {name}")
        elif critical:
            print_error(f"  {name}  ← MISSING")
            failed = True
        else:
            print_warning(f"  {name}  ← not active yet (will start on reboot)")
    return not failed

# ════════════════════════════════════════════════════════════════
#  CHECK STATUS + RECOMMENDATIONS  (Mode 5)
# ════════════════════════════════════════════════════════════════

def mode_check_status():
    print_header("System Status & Recommendations")

    checks = [
        ("Display",  "Xorg",            "which Xorg",                                    False),
        ("Display",  "xinit",           "which xinit",                                   False),
        ("Display",  "openbox",         "which openbox",                                 False),
        ("Display",  "Chromium",        "which chromium-browser",                        True),
        ("Display",  "unclutter",       "which unclutter",                               False),
        ("Audio",    "ALSA (aplay)",    "which aplay",                                   True),
        ("Audio",    "PulseAudio",      "which pulseaudio",                              False),
        ("Kiosk",    ".xinitrc",        "test -f /home/pi/.xinitrc",                     False),
        ("Kiosk",    ".bash_profile",   "test -f /home/pi/.bash_profile",               False),
        ("Kiosk",    "LXDE autostart",  "test -f /home/pi/.config/lxsession/LXDE-pi/autostart", False),
        ("App",      "SUMMAV1 folder",  "test -d /home/pi/SUMMAV1",                     True),
        ("App",      "padel_backend",   "test -f /home/pi/SUMMAV1/padel_backend.py",    True),
        ("App",      "sensor_script",   "test -f /home/pi/SUMMAV1/sensor_script.py",    True),
        ("App",      "scoreboard.html", "test -f /home/pi/SUMMAV1/padel_scoreboard.html", True),
        ("App",      "change.mp3",      "test -f /home/pi/SUMMAV1/change.mp3",          False),
        ("Python",   "python3",         "which python3",                                 True),
        ("Python",   "flask",           "python3 -c 'import flask'",                    True),
        ("Python",   "flask_socketio",  "python3 -c 'import flask_socketio'",           True),
        ("Python",   "flask_cors",      "python3 -c 'import flask_cors'",               True),
        ("Python",   "RPi.GPIO",        "python3 -c 'import RPi.GPIO'",                 True),
        ("Python",   "pygame",          "python3 -c 'import pygame' 2>/dev/null",        True),
        ("Python",   "requests",        "python3 -c 'import requests'",                 True),
        ("Python",   "smbus2",          "python3 -c 'import smbus2'",                   True),
        ("Python",   "pigpio",          "python3 -c 'import pigpio'",                   True),
        ("Python",   "eventlet",        "python3 -c 'import eventlet'",                 False),
        ("Services", "pigpiod",         "systemctl is-active pigpiod",                  True),
        ("Services", "padel-backend",   "systemctl is-active padel-backend",            True),
        ("Services", "padel-sensor",    "systemctl is-active padel-sensor",             True),
        ("Hardware", "GPIO accessible", "test -d /sys/class/gpio",                      True),
        ("Hardware", "I2C device",      "ls /dev/i2c-* 2>/dev/null",                    False),
        ("Hardware", "pigpiod binary",  "which pigpiod",                                True),
    ]

    results = {}
    cats = ["Display", "Audio", "Kiosk", "App", "Python", "Services", "Hardware"]
    missing_critical = []
    missing_optional = []

    for category, name, cmd, critical in checks:
        ok, stdout, _ = run_command(cmd, check=False)
        passed = ok or (stdout.strip() != "")
        results.setdefault(category, []).append((name, passed, critical))

    for cat in cats:
        if cat not in results:
            continue
        print_section(cat)
        for name, passed, critical in results[cat]:
            if passed:
                print_success(f"  {name}")
            elif critical:
                print_error(f"  {name}  [CRITICAL]")
                missing_critical.append((cat, name))
            else:
                print_warning(f"  {name}  [optional]")
                missing_optional.append((cat, name))

    # Recommendations
    print_header("Recommendations")

    if not missing_critical and not missing_optional:
        print_success("Everything looks great — system is fully configured!")
        print_info("Run: sudo reboot  →  scoreboard should auto-launch ✅")
        return

    if missing_critical:
        print(f"{Colors.RED}{Colors.BOLD}🔴 Critical Issues:{Colors.END}")
        for cat, name in missing_critical:
            print(f"  {Colors.RED}• [{cat}] {name} is missing{Colors.END}")
        print()

        names = set(n for _, n in missing_critical)
        cats_missing = set(c for c, _ in missing_critical)

        if "Display" in cats_missing or "Kiosk" in cats_missing:
            print_warning("→ Display/kiosk layer missing")
            print_info("  Run this script → Option 1 (Lite) or Option 2 (GUI)")

        if "SUMMAV1 folder" in names or "padel_backend" in names:
            print_warning("→ App files not found in /home/pi/SUMMAV1")
            print_info("  Run: git clone --branch V1 https://github.com/wavindi/SUMMAV1.git ~/SUMMAV1")

        if any(n in names for n in ["flask", "flask_socketio", "RPi.GPIO", "pigpio", "pygame"]):
            print_warning("→ Python packages missing")
            print_info("  Run this script → Option 3 (Dependencies Only)")

        if "pigpiod" in names:
            print_warning("→ pigpiod not running")
            print_info("  Run: sudo systemctl start pigpiod")

        if "padel-backend" in names or "padel-sensor" in names:
            print_warning("→ SUMMA services not running")
            print_info("  Run this script → Option 4 (Configure Autostart)")
            print_info("  Then: sudo reboot")

    if missing_optional:
        print()
        print(f"{Colors.YELLOW}{Colors.BOLD}🟡 Optional Items Missing:{Colors.END}")
        for cat, name in missing_optional:
            print(f"  {Colors.YELLOW}• [{cat}] {name}{Colors.END}")
        if any(n == "change.mp3" for _, n in missing_optional):
            print_info("  → Place change.mp3 in /home/pi/SUMMAV1/ for side-switch audio")

# ════════════════════════════════════════════════════════════════
#  FINAL INSTRUCTIONS
# ════════════════════════════════════════════════════════════════

def print_final_instructions(mode_name):
    print_header(f"✅ {mode_name} Complete!")
    print(f"{Colors.GREEN}{Colors.BOLD}SUMMA Padel Scoreboard is configured!{Colors.END}\n")
    print(f"{Colors.BOLD}Boot sequence:{Colors.END}")
    print("  1. pigpiod daemon starts")
    print("  2. padel-backend starts (Flask on :5000)")
    print("  3. padel-sensor starts (after 5s delay)")
    print("  4. Chromium opens in kiosk mode → localhost:5000\n")
    print(f"{Colors.BOLD}Useful commands:{Colors.END}")
    print("  ./manage_services.sh check     — quick health check")
    print("  ./manage_services.sh logs      — stream all logs")
    print("  ./manage_services.sh restart   — restart everything")
    print("  sudo journalctl -u padel-backend -f  — backend logs\n")
    print(f"{Colors.YELLOW}💡 Run: sudo reboot  →  to test full auto-launch{Colors.END}")

# ════════════════════════════════════════════════════════════════
#  MAIN MENU
# ════════════════════════════════════════════════════════════════

def main():
    print_header("🏓 SUMMA Padel Scoreboard")
    print_header("Complete Setup & Dependency Installer")

    print(f"{Colors.BOLD}Select an option:{Colors.END}\n")
    print(f"  {Colors.GREEN}1{Colors.END}  Full Install — Raspberry Pi {Colors.BOLD}Lite{Colors.END} (headless, Openbox + Chromium kiosk)")
    print(f"  {Colors.GREEN}2{Colors.END}  Full Install — Raspberry Pi {Colors.BOLD}Desktop{Colors.END} (GUI / LXDE + Chromium kiosk)")
    print(f"  {Colors.GREEN}3{Colors.END}  {Colors.BOLD}Dependencies Only{Colors.END} (packages + pigpio, skip autostart)")
    print(f"  {Colors.GREEN}4{Colors.END}  {Colors.BOLD}Configure Autostart Only{Colors.END} (skip package install)")
    print(f"  {Colors.GREEN}5{Colors.END}  {Colors.BOLD}Check System Status{Colors.END} + recommendations")
    print(f"  {Colors.RED}0{Colors.END}  Exit\n")

    choice = input(f"{Colors.CYAN}Enter choice (0-5): {Colors.END}").strip()

    if choice == '0':
        print_info("Exiting.")
        sys.exit(0)

    if choice in ['1', '2', '3', '4']:
        check_root()

    start = time.time()

    # ── Mode 1: Lite Full Install ────────────────────────────
    if choice == '1':
        print_header("MODE 1 — Full Install: Raspberry Pi LITE")
        update_system()
        print_step(1, 6, "Installing Lite Display Layer")
        install_lite_display_packages()
        print_step(2, 6, "Installing Base System Packages")
        install_base_system_packages()
        print_step(3, 6, "Installing pigpio")
        install_pigpio()
        print_step(4, 6, "Setting Up pigpiod Service")
        setup_pigpiod()
        print_step(5, 6, "Installing Python pip Packages")
        install_python_packages()
        print_step(6, 6, "Enabling Interfaces + Configuring Kiosk Autostart")
        enable_interfaces()
        configure_autostart_lite()
        create_management_script()
        verify_installation()
        print_final_instructions("Lite Mode Install")

    # ── Mode 2: GUI Full Install ─────────────────────────────
    elif choice == '2':
        print_header("MODE 2 — Full Install: Raspberry Pi DESKTOP")
        update_system()
        print_step(1, 5, "Installing GUI Extra Packages")
        install_gui_extra_packages()
        print_step(2, 5, "Installing Base System Packages")
        install_base_system_packages()
        print_step(3, 5, "Installing pigpio")
        install_pigpio()
        print_step(4, 5, "Setting Up pigpiod Service")
        setup_pigpiod()
        print_step(5, 5, "Installing Python pip Packages + Autostart")
        install_python_packages()
        enable_interfaces()
        configure_autostart_gui()
        create_management_script()
        verify_installation()
        print_final_instructions("GUI Mode Install")

    # ── Mode 3: Dependencies Only ────────────────────────────
    elif choice == '3':
        print_header("MODE 3 — Dependencies Only")
        update_system()
        print_step(1, 4, "Installing Base System Packages")
        install_base_system_packages()
        print_step(2, 4, "Installing pigpio")
        install_pigpio()
        print_step(3, 4, "Setting Up pigpiod Service")
        setup_pigpiod()
        print_step(4, 4, "Installing Python pip Packages")
        install_python_packages()
        enable_interfaces()
        verify_installation()
        print_header("✅ Dependencies Installed")
        print_info("Run this script again → Option 4 to configure autostart when ready.")

    # ── Mode 4: Autostart Only ───────────────────────────────
    elif choice == '4':
        print_header("MODE 4 — Configure Autostart Only")
        print(f"\n{Colors.CYAN}Which OS are you on?{Colors.END}")
        print("  1. Raspberry Pi Lite (headless)")
        print("  2. Raspberry Pi Desktop (GUI/LXDE)")
        os_choice = input(f"\n{Colors.CYAN}Enter choice (1 or 2): {Colors.END}").strip()
        if os_choice == '1':
            configure_autostart_lite()
        elif os_choice == '2':
            configure_autostart_gui()
        else:
            print_error("Invalid choice")
            sys.exit(1)
        create_management_script()
        print_header("✅ Autostart Configured")
        print_info("Run: sudo reboot  →  to test full auto-launch")

    # ── Mode 5: Check Status ─────────────────────────────────
    elif choice == '5':
        mode_check_status()

    else:
        print_error("Invalid choice")
        sys.exit(1)

    duration = int(time.time() - start)
    if choice != '5':
        print(f"\n{Colors.BLUE}⏱️  Done in {duration // 60}m {duration % 60}s{Colors.END}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️  Interrupted by user{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
