#!/usr/bin/env python3

"""
SUMMA Padel Scoreboard - Complete Dependency Installer
Prepares system for setup_autostart.py by installing all required packages
"""

import subprocess
import sys
import os
import time

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️ {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ️ {text}{Colors.END}")

def print_step(step, total, text):
    print(f"\n{Colors.CYAN}[{step}/{total}] {text}{Colors.END}")

def check_root():
    """Check if script is run with sudo"""
    if os.geteuid() != 0:
        print_error("This script must be run with sudo!")
        print_info("Usage: sudo python3 install_dependencies.py")
        sys.exit(1)

def run_command(cmd, check=True, show_output=False):
    """Run shell command and return success status"""
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

# ============================================================
# STEP 1 - unchanged
# ============================================================

def update_system():
    """Update package lists"""
    print_step(1, 7, "Updating Package Lists")
    print_info("Running apt-get update...")
    success, _, stderr = run_command('apt-get update -qq', check=False)
    if success:
        print_success("Package lists updated")
        return True
    else:
        print_warning("Update had some warnings, continuing anyway...")
        return True

# ============================================================
# STEP 2 - NEW: Raspberry Pi Lite Display & Kiosk Layer
# ============================================================

def install_lite_display_packages():
    """
    RASPBERRY PI LITE ONLY
    Pi Lite ships with zero GUI. This step installs the minimum
    required to run Firefox in kiosk mode and play audio (change.mp3).
    Skipped silently on full desktop OS since packages already exist.
    """
    print_step(2, 7, "Installing Raspberry Pi Lite - Display & Kiosk Packages")
    print_info("Pi Lite has no GUI layer — installing X11 + browser + audio...")
    print_info("(Safe to run on full OS — will just skip already-installed packages)")

    packages = [
        # --- X11 Display Server (core) ---
        'xserver-xorg',              # X11 display server engine
        'xserver-xorg-video-fbdev',  # Framebuffer video driver for Pi screen
        'xserver-xorg-input-evdev',  # Evdev input driver (mouse/keyboard/touch)
        'x11-xserver-utils',         # xrandr, xset, xdpyinfo tools
        'xinit',                     # startx / xinit launcher (no display manager needed)

        # --- Minimal Window Manager ---
        'openbox',                   # Lightweight WM — no taskbar, pure kiosk
        'x11-utils',                 # xwininfo, xdpyinfo (debugging tools)

        # --- Browser ---
        'firefox-esr',               # Kiosk browser that displays scoreboard on port 5000

        # --- Kiosk Utilities ---
        'unclutter',                 # Hides mouse cursor after inactivity
        'xdotool',                   # Window/keyboard scripting (used by autostart)
        'wmctrl',                    # Force fullscreen/window control from shell

        # --- Audio (needed for change.mp3 via pygame.mixer) ---
        'alsa-utils',                # ALSA sound system (aplay, amixer)
        'pulseaudio',                # PulseAudio daemon — pygame mixer backend on Lite
        'libsdl2-mixer-2.0-0',       # SDL2 audio mixer used by pygame

        # --- Fonts (Firefox needs these to render scoreboard HTML/CSS) ---
        'fonts-liberation',          # Arial/Times/Courier replacements
        'fonts-dejavu-core',         # Default fallback fonts
        'fontconfig',                # Font cache builder

        # --- SSL Certificates ---
        'ca-certificates',           # HTTPS support for requests library
    ]

    print_info(f"Installing {len(packages)} display/kiosk packages...")
    print_info("This may take 3-6 minutes on first install...")
    package_list = ' '.join(packages)
    success, _, stderr = run_command(f'apt-get install -y {package_list}', check=False)
    if success:
        print_success("Display & kiosk packages installed successfully")
    else:
        print_warning("Some display packages had issues — continuing...")

    # --- Auto-login on tty1 (so X can start without keyboard input at boot) ---
    print_info("Configuring auto-login for 'pi' user on tty1...")
    autologin_dir = "/etc/systemd/system/getty@tty1.service.d"
    autologin_conf = f"{autologin_dir}/autologin.conf"
    os.makedirs(autologin_dir, exist_ok=True)
    try:
        with open(autologin_conf, 'w') as f:
            f.write("[Service]\n")
            f.write("ExecStart=\n")
            f.write("ExecStart=-/sbin/agetty --autologin pi --noclear %I $TERM\n")
        run_command('systemctl daemon-reload', check=False)
        print_success("Auto-login configured on tty1")
    except Exception as e:
        print_warning(f"Could not configure auto-login: {e}")

    # --- ~/.bash_profile: auto-start X when pi logs in on tty1 ---
    bash_profile = "/home/pi/.bash_profile"
    try:
        with open(bash_profile, 'w') as f:
            f.write("# SUMMA: Auto-start X display on tty1\n")
            f.write("if [ -z \"$DISPLAY\" ] && [ \"$(tty)\" = \"/dev/tty1\" ]; then\n")
            f.write("    startx -- -nocursor 2>/dev/null\n")
            f.write("fi\n")
        run_command(f'chown pi:pi {bash_profile}', check=False)
        print_success("~/.bash_profile → auto-starts X on tty1 login")
    except Exception as e:
        print_warning(f"Could not create .bash_profile: {e}")

    # --- ~/.xinitrc: launches openbox + Firefox kiosk pointing to Flask ---
    xinitrc = "/home/pi/.xinitrc"
    try:
        with open(xinitrc, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("# SUMMA Padel Scoreboard — Kiosk Mode\n\n")
            f.write("# Disable screen blanking & power saving\n")
            f.write("xset s off\n")
            f.write("xset s noblank\n")
            f.write("xset -dpms\n\n")
            f.write("# Hide mouse cursor\n")
            f.write("unclutter -idle 0.1 -root &\n\n")
            f.write("# Start window manager\n")
            f.write("openbox &\n\n")
            f.write("# Wait for Flask backend (max 30s)\n")
            f.write("for i in $(seq 1 30); do\n")
            f.write("    curl -s http://localhost:5000/health > /dev/null 2>&1 && break\n")
            f.write("    sleep 1\n")
            f.write("done\n\n")
            f.write("# Open scoreboard in kiosk mode\n")
            f.write("firefox-esr --kiosk --no-first-run --no-default-browser-check \\\n")
            f.write("    --disable-infobars http://localhost:5000\n")
        run_command(f'chmod +x {xinitrc}', check=False)
        run_command(f'chown pi:pi {xinitrc}', check=False)
        print_success("~/.xinitrc → kiosk mode config created")
    except Exception as e:
        print_warning(f"Could not create .xinitrc: {e}")

    print_success("Raspberry Pi Lite display layer ready!")
    return True

# ============================================================
# STEP 3 - unchanged (was step 2)
# ============================================================

def install_system_packages():
    """Install system-level packages"""
    print_step(3, 7, "Installing System Packages")

    packages = [
        'python3-pip',
        'python3-dev',
        'python3-venv',
        'build-essential',
        'git',
        'unclutter',
        'xdotool',
        'wmctrl',
        'python3-flask',
        'python3-requests',
        'python3-rpi.gpio',
        'python3-smbus',
        'python3-pygame',
        'wget',
        'unzip',
        'i2c-tools',       # i2cdetect — useful for sensor debugging
        'libasound2-dev',  # ALSA headers needed by pygame audio
    ]

    print_info(f"Installing {len(packages)} packages...")
    print_info("This may take 2-5 minutes depending on your connection...")
    package_list = ' '.join(packages)
    success, _, stderr = run_command(f'apt-get install -y {package_list}', check=False)
    if success:
        print_success("System packages installed successfully")
        return True
    else:
        print_warning("Some packages may have had issues, but continuing...")
        return True

# ============================================================
# STEP 4 - unchanged (was step 3)
# ============================================================

def install_pigpio():
    """Install pigpio from source"""
    print_step(4, 7, "Installing pigpio GPIO Library")

    print_info("Cleaning up old pigpio files...")
    run_command('rm -rf /tmp/pigpio-master /tmp/pigpio.zip /tmp/master.zip', check=False)

    print_info("Downloading pigpio from GitHub...")
    success, _, stderr = run_command(
        'cd /tmp && wget -q https://github.com/joan2937/pigpio/archive/master.zip -O pigpio.zip',
        check=False
    )
    if not success:
        print_error("Failed to download pigpio")
        return False

    print_info("Extracting archive...")
    success, _, _ = run_command('cd /tmp && unzip -q pigpio.zip', check=False)
    if not success:
        print_error("Failed to extract pigpio")
        return False

    print_info("Compiling pigpio (this takes 2-3 minutes)...")
    print_info("Please be patient...")
    success, _, stderr = run_command(
        'cd /tmp/pigpio-master && make -j4 > /dev/null 2>&1',
        check=False
    )
    if not success:
        print_error("Compilation failed")
        print_error(f"Error: {stderr[:200]}")
        return False

    print_info("Installing pigpio system-wide...")
    success, _, stderr = run_command(
        'cd /tmp/pigpio-master && make install > /dev/null 2>&1',
        check=False
    )
    if not success:
        print_error("Installation failed")
        return False

    print_success("pigpio compiled and installed successfully")
    return True

# ============================================================
# STEP 5 - unchanged (was step 4)
# ============================================================

def create_pigpiod_service():
    """Create pigpiod systemd service"""
    print_step(5, 7, "Setting Up pigpiod Service")

    service_content = """[Unit]
Description=pigpio daemon for GPIO control
After=network.target

[Service]
Type=forking
ExecStart=/usr/local/bin/pigpiod -l
ExecStop=/bin/systemctl kill pigpiod
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
    service_path = '/etc/systemd/system/pigpiod.service'
    try:
        with open(service_path, 'w') as f:
            f.write(service_content)
        print_success(f"Created {service_path}")
    except Exception as e:
        print_error(f"Failed to create service file: {e}")
        return False

    print_info("Reloading systemd daemon...")
    run_command('systemctl daemon-reload', check=False)

    print_info("Enabling pigpiod to start at boot...")
    success, _, _ = run_command('systemctl enable pigpiod', check=False)
    if success:
        print_success("pigpiod service enabled")
    else:
        print_warning("Could not enable service, but continuing...")

    print_info("Starting pigpiod now...")
    success, _, _ = run_command('systemctl start pigpiod', check=False)
    if success:
        print_success("pigpiod service started")
    else:
        print_warning("Could not start service immediately")

    return True

# ============================================================
# STEP 6 - unchanged (was step 5)
# ============================================================

def install_python_packages():
    """Install Python packages using pip"""
    print_step(6, 7, "Installing Python Packages")

    pip_packages = [
        'flask-cors',
        'flask-socketio',
        'python-socketio',
        'smbus2',
        'pigpio',
        'eventlet',   # Stable async mode for flask-socketio on Pi Lite
    ]

    print_info(f"Installing {len(pip_packages)} Python packages via pip...")
    for package in pip_packages:
        print_info(f"Installing {package}...")
        success, _, _ = run_command(
            f'pip3 install {package} --break-system-packages -q',
            check=False
        )
        if success:
            print_success(f"  {package} ✓")
        else:
            print_warning(f"  {package} - may have issues")

    print_success("Python packages installation complete")
    return True

# ============================================================
# STEP 7 - unchanged (was step 6), verify checks extended
# ============================================================

def verify_installation():
    """Verify all components are installed"""
    print_step(7, 7, "Verifying Installation")

    checks = [
        # --- Pi Lite display layer ---
        ('Xorg',             'which Xorg',                          True),
        ('xinit',            'which xinit',                         True),
        ('openbox',          'which openbox',                       True),
        ('unclutter',        'which unclutter',                     True),
        ('xdotool',          'which xdotool',                       True),
        ('ALSA (aplay)',     'which aplay',                         True),
        ('.xinitrc',         'test -f /home/pi/.xinitrc',           True),
        ('.bash_profile',    'test -f /home/pi/.bash_profile',      True),
        # --- Browser ---
        ('Firefox',          'which firefox-esr',                   True),
        # --- Python & backend ---
        ('Python3',          'which python3',                       True),
        ('pip3',             'which pip3',                          True),
        ('Flask',            'python3 -c "import flask"',           True),
        ('Flask-SocketIO',   'python3 -c "import flask_socketio"',  True),
        ('Flask-CORS',       'python3 -c "import flask_cors"',      True),
        ('RPi.GPIO',         'python3 -c "import RPi.GPIO"',        True),
        ('pygame',           'python3 -c "import pygame" 2>/dev/null', True),
        ('requests',         'python3 -c "import requests"',        True),
        ('smbus2',           'python3 -c "import smbus2"',          True),
        ('eventlet',         'python3 -c "import eventlet"',        False),
        # --- GPIO daemon ---
        ('pigpiod binary',   'which pigpiod',                       True),
        ('pigpio module',    'python3 -c "import pigpio"',          True),
        ('pigpiod service',  'systemctl is-active pigpiod',         False),
    ]

    all_ok = True
    critical_failed = False

    for name, cmd, critical in checks:
        success, stdout, _ = run_command(cmd, check=False)
        if success or (not critical and 'active' in stdout):
            print_success(f"{name:20} - OK")
        else:
            if critical:
                print_error(f"{name:20} - MISSING (critical)")
                critical_failed = True
                all_ok = False
            else:
                print_warning(f"{name:20} - Not running (will start later)")

    return all_ok, critical_failed

def print_final_summary(success, critical_failed):
    """Print final summary and next steps"""
    print_header("Installation Summary")

    if success and not critical_failed:
        print(f"{Colors.GREEN}{Colors.BOLD}✨ ALL DEPENDENCIES INSTALLED SUCCESSFULLY! ✨{Colors.END}\n")
        print(f"{Colors.BOLD}Your system is now ready for the padel scoreboard.{Colors.END}\n")
        print(f"{Colors.CYAN}{'━'*70}{Colors.END}")
        print(f"{Colors.BOLD}NEXT STEPS:{Colors.END}\n")
        print(f"  {Colors.GREEN}1.{Colors.END} Run the autostart setup:")
        print(f"     {Colors.CYAN}cd ~/SUMMAV1{Colors.END}")
        print(f"     {Colors.CYAN}sudo python3 setup_autostart.py{Colors.END}\n")
        print(f"  {Colors.GREEN}2.{Colors.END} When prompted, answer 'y' to start services immediately\n")
        print(f"  {Colors.GREEN}3.{Colors.END} Check everything is working:")
        print(f"     {Colors.CYAN}./manage_services.sh check{Colors.END}\n")
        print(f"  {Colors.GREEN}4.{Colors.END} Test auto-start by rebooting:")
        print(f"     {Colors.CYAN}sudo reboot{Colors.END}\n")
        print(f"{Colors.CYAN}{'━'*70}{Colors.END}\n")
        print(f"{Colors.YELLOW}💡 TIP:{Colors.END} Use 'python3 view_logs.py' to monitor your sensors and backend")
        print(f"{Colors.YELLOW}💡 TIP:{Colors.END} Firefox will open automatically in kiosk mode after setup\n")

    elif not critical_failed:
        print(f"{Colors.YELLOW}{Colors.BOLD}⚠️ INSTALLATION COMPLETED WITH WARNINGS{Colors.END}\n")
        print("Some non-critical components may not be installed correctly.")
        print("You can still proceed with setup_autostart.py\n")
        print(f"{Colors.BOLD}Next step:{Colors.END}")
        print(f"  {Colors.CYAN}sudo python3 setup_autostart.py{Colors.END}\n")
    else:
        print(f"{Colors.RED}{Colors.BOLD}❌ INSTALLATION FAILED{Colors.END}\n")
        print("Critical components are missing. Please review the errors above.")
        print("You may need to:")
        print("  • Check your internet connection")
        print("  • Run this script again")
        print("  • Manually install missing packages\n")

def main():
    """Main installation process"""
    print_header("🏓 SUMMA Padel Scoreboard")
    print_header("Complete Dependency Installer")

    print(f"{Colors.BOLD}This script will install:{Colors.END}")
    print("  • [NEW] Pi Lite display layer (X11, openbox, Firefox, audio, fonts)")
    print("  • System packages (build tools, Python libraries)")
    print("  • pigpio GPIO library (compiled from source)")
    print("  • pigpiod service (for GPIO control)")
    print("  • Python packages (Flask, SocketIO, etc.)")
    print()
    print(f"{Colors.YELLOW}⏱️ Estimated time: 8-15 minutes on Pi Lite{Colors.END}")
    print()

    check_root()

    response = input(f"{Colors.CYAN}Continue with installation? (y/n): {Colors.END}").lower()
    if response != 'y':
        print_warning("Installation cancelled by user")
        sys.exit(0)

    start_time = time.time()

    steps = [
        update_system,
        install_lite_display_packages,   # ← NEW Step 2 for Pi Lite
        install_system_packages,
        install_pigpio,
        create_pigpiod_service,
        install_python_packages,
    ]

    for step in steps:
        if not step():
            print_error(f"Step failed: {step.__name__}")
            print_warning("Continuing with remaining steps...")

    success, critical_failed = verify_installation()

    duration = int(time.time() - start_time)
    minutes = duration // 60
    seconds = duration % 60
    print()
    print(f"{Colors.BLUE}⏱️ Total installation time: {minutes}m {seconds}s{Colors.END}")

    print_final_summary(success, critical_failed)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️ Installation interrupted by user{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
