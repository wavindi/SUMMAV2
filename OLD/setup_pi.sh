#!/usr/bin/env bash
# SUMMA V3 — one-shot Raspberry Pi installer.
#
# What it does:
#   1. Installs apt + pip dependencies (light: no pigpio, no smbus, no pygame).
#   2. Patches the systemd unit files with the current user + repo path.
#   3. Installs and enables both units (backend + kiosk).
#   4. Disables screen blanking system-wide.
#
# Run from the SUMMAV3 folder:
#   bash tools/setup_pi.sh
#
# Tested on Raspberry Pi OS Bookworm, Raspberry Pi 3B, 1 GB.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"

echo "==> Installing as user: $RUN_USER"
echo "==> Repo dir:           $REPO_DIR"
echo "==> Home dir:           $RUN_HOME"

# ---------------------------------------------------------------------------
# 1. Packages
# ---------------------------------------------------------------------------
echo "==> apt install"
sudo apt-get update -y
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    unclutter curl x11-xserver-utils

# Chromium package was renamed in Trixie. Try the new name first, fall back.
if ! sudo apt-get install -y chromium; then
    echo "==> 'chromium' not available, trying 'chromium-browser'"
    sudo apt-get install -y chromium-browser
fi

echo "==> pip install (system, --break-system-packages on Bookworm)"
PIP_FLAGS=""
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; then
    PIP_FLAGS="--break-system-packages"
fi
sudo -u "$RUN_USER" python3 -m pip install $PIP_FLAGS -r "$REPO_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 2. Patch systemd units with this user / path
# ---------------------------------------------------------------------------
echo "==> Installing systemd units"
TMP_BACKEND=$(mktemp)
TMP_KIOSK=$(mktemp)

sed -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_DIR}|" \
    -e "s|^ExecStart=.*|ExecStart=/usr/bin/python3 ${REPO_DIR}/backend_pi.py|" \
    "$REPO_DIR/systemd/summa-backend.service" > "$TMP_BACKEND"

sed -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^Environment=XAUTHORITY=.*|Environment=XAUTHORITY=${RUN_HOME}/.Xauthority|" \
    -e "s|^ExecStart=.*|ExecStart=/bin/bash ${REPO_DIR}/tools/start_kiosk.sh|" \
    "$REPO_DIR/systemd/summa-kiosk.service" > "$TMP_KIOSK"

sudo install -m 644 "$TMP_BACKEND" /etc/systemd/system/summa-backend.service
sudo install -m 644 "$TMP_KIOSK"   /etc/systemd/system/summa-kiosk.service
rm -f "$TMP_BACKEND" "$TMP_KIOSK"

chmod +x "$REPO_DIR/tools/start_kiosk.sh"

sudo systemctl daemon-reload
sudo systemctl enable --now summa-backend.service

# Only enable kiosk if a graphical target exists (i.e. Desktop image).
if systemctl get-default | grep -q graphical; then
    sudo systemctl enable --now summa-kiosk.service
    echo "==> Kiosk enabled (Desktop image detected)."
else
    echo "==> Lite image detected — kiosk NOT enabled."
    echo "    Point a browser on another device at the LAN URL printed by"
    echo "    'sudo systemctl status summa-backend.service'."
fi

# ---------------------------------------------------------------------------
# 3. Disable system-wide screen blanking (belt-and-braces; xset in
#    start_kiosk.sh handles the per-session case).
# ---------------------------------------------------------------------------
if [ -d /etc/lightdm ] && [ -f /etc/lightdm/lightdm.conf ]; then
    if ! grep -q "xserver-command=X -s 0 -dpms" /etc/lightdm/lightdm.conf; then
        echo "==> Disabling DPMS via lightdm"
        sudo sed -i 's|^#xserver-command=X|xserver-command=X -s 0 -dpms|' /etc/lightdm/lightdm.conf || true
    fi
fi

# ---------------------------------------------------------------------------
echo
echo "==> DONE."
echo "    Token file: ${RUN_HOME}/.summa_token"
echo "    Logs:       journalctl -u summa-backend.service -f"
echo "    LAN URL:    http://$(hostname -I | awk '{print $1}'):5000/"
echo
echo "    Reboot recommended:  sudo reboot"
