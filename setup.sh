#!/usr/bin/env bash
# =============================================================================
# SUMMA V3 — Full Raspberry Pi Setup
# =============================================================================
#
# One script. Run it once. Everything is ready.
# After this finishes the only thing left to do is plug the ESP32 receiver
# into a USB port and power the Pi on.
#
# What this script does:
#   1.  Pre-flight checks  (OS, user, repo path)
#   2.  apt packages       (Python, Chromium, serial, X11 tools)
#   3.  pip packages       (Flask, SocketIO, pyserial, CORS)
#   4.  dialout group      (so the bridge can open /dev/ttyACM0 without sudo)
#   5.  systemd services   (backend + bridge + kiosk — patched & enabled)
#   6.  Screen blanking    (disabled in lightdm and xset)
#   7.  Post-install verification (imports, binaries, services, HTTP, USB)
#   8.  Summary            (PASS / WARN / FAIL for every check)
#
# Run from the SUMMAV3 directory:
#   bash setup.sh
#
# Safe to re-run — all steps are idempotent.
#
# Tested on: Raspberry Pi OS Bookworm (12) with Desktop, Pi 3B 1 GB.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# ── Colours ───────────────────────────────────────────────────────────────────
C_BOLD='\033[1m'
C_RED='\033[91m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_BLUE='\033[94m'
C_CYAN='\033[96m'
C_GRAY='\033[90m'
C_END='\033[0m'

PASS="${C_GREEN}[PASS]${C_END}"
WARN="${C_YELLOW}[WARN]${C_END}"
FAIL="${C_RED}[FAIL]${C_END}"
INFO="${C_CYAN}[INFO]${C_END}"
STEP="${C_BLUE}[STEP]${C_END}"

# Accumulate verification results
declare -a _CHECK_RESULTS=()

check_pass() { _CHECK_RESULTS+=("${PASS} $1"); }
check_warn() { _CHECK_RESULTS+=("${WARN} $1"); }
check_fail() { _CHECK_RESULTS+=("${FAIL} $1"); _FAIL_COUNT=$(( _FAIL_COUNT + 1 )); }
_FAIL_COUNT=0

header() {
    echo
    echo -e "${C_BOLD}${C_BLUE}$(printf '=%.0s' {1..70})${C_END}"
    echo -e "${C_BOLD}${C_CYAN}$(printf '%*s' $(( (70 + ${#1}) / 2 )) "$1")${C_END}"
    echo -e "${C_BOLD}${C_BLUE}$(printf '=%.0s' {1..70})${C_END}"
    echo
}

# =============================================================================
# 0. Locate repo & identify user
# =============================================================================
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"

header "SUMMA V3 — SETUP"
echo -e "${INFO} Repo:   ${C_BOLD}${REPO_DIR}${C_END}"
echo -e "${INFO} User:   ${C_BOLD}${RUN_USER}${C_END}"
echo -e "${INFO} Home:   ${C_BOLD}${RUN_HOME}${C_END}"
echo -e "${INFO} Date:   $(date)"
echo

# =============================================================================
# 1. Pre-flight checks
# =============================================================================
header "Step 1 — Pre-flight checks"

# Must be Linux
if [[ "$(uname -s)" != "Linux" ]]; then
    echo -e "${FAIL} This script only runs on Linux (Raspberry Pi OS)."
    exit 1
fi
echo -e "${PASS} Running on Linux"

# Warn if not a Raspberry Pi
if grep -qi "raspberry" /proc/cpuinfo 2>/dev/null \
   || grep -qi "raspberry" /sys/firmware/devicetree/base/model 2>/dev/null; then
    echo -e "${PASS} Raspberry Pi hardware detected"
else
    echo -e "${WARN} Raspberry Pi NOT detected — continuing anyway"
fi

# Must have sudo access
if ! sudo -n true 2>/dev/null; then
    echo -e "${INFO} This script needs sudo. You may be prompted for your password."
fi
echo -e "${PASS} sudo available"

# Repo sanity
for f in backend_pi.py tools/serial_bridge.py tools/start_kiosk.sh \
          systemd/summa-backend.service systemd/summa-bridge.service \
          systemd/summa-kiosk.service; do
    if [[ ! -f "${REPO_DIR}/${f}" ]]; then
        echo -e "${FAIL} Missing expected file: ${f}"
        echo "       Run this script from the SUMMAV3 directory."
        exit 1
    fi
done
echo -e "${PASS} Repo structure looks correct"

# =============================================================================
# 2. apt packages
# =============================================================================
header "Step 2 — System packages (apt)"

echo -e "${STEP} Updating package lists..."
sudo apt-get update -y -qq

echo -e "${STEP} Installing packages..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-serial \
    unclutter \
    curl \
    wget \
    x11-xserver-utils \
    xdotool \
    2>&1 | grep -E "^(Get|Inst|Unpacking|Setting up|Processing)" || true

# Chromium: Bookworm calls it 'chromium', older images 'chromium-browser'
CHROMIUM_BIN=""
if sudo apt-get install -y chromium -qq 2>/dev/null; then
    CHROMIUM_BIN="chromium"
elif sudo apt-get install -y chromium-browser -qq 2>/dev/null; then
    CHROMIUM_BIN="chromium-browser"
else
    echo -e "${WARN} Could not install chromium — kiosk mode will not work."
    echo "       Install manually: sudo apt install chromium"
fi

if [[ -n "$CHROMIUM_BIN" ]]; then
    echo -e "${PASS} Chromium installed: $(command -v ${CHROMIUM_BIN} 2>/dev/null || echo ${CHROMIUM_BIN})"
fi

# =============================================================================
# 3. Python packages (pip)
# =============================================================================
header "Step 3 — Python packages (pip)"

# On Bookworm+ (PEP 668) pip needs --break-system-packages
PIP_FLAGS=""
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    PIP_FLAGS="--break-system-packages"
fi

echo -e "${STEP} Installing Flask, SocketIO, pyserial, Flask-Cors..."
sudo -u "$RUN_USER" python3 -m pip install $PIP_FLAGS --quiet \
    "Flask>=3.0" \
    "Flask-Cors>=4.0" \
    "Flask-SocketIO>=5.3" \
    "python-socketio>=5.9" \
    "pyserial>=3.5"

echo -e "${PASS} Python packages installed"

# Verify critical imports right away
for pkg in flask flask_socketio flask_cors serial; do
    if python3 -c "import ${pkg}" 2>/dev/null; then
        echo -e "  ${PASS} import ${pkg}"
    else
        echo -e "  ${FAIL} import ${pkg} — package not importable"
    fi
done

# =============================================================================
# 4. dialout group (serial port access for the bridge)
# =============================================================================
header "Step 4 — Serial port access"

if groups "$RUN_USER" | grep -q dialout; then
    echo -e "${PASS} ${RUN_USER} is already in the 'dialout' group"
else
    echo -e "${STEP} Adding ${RUN_USER} to dialout group..."
    sudo usermod -a -G dialout "$RUN_USER"
    echo -e "${PASS} Added — takes effect on next login (or after reboot)"
    echo -e "${WARN} You will need to reboot for serial access to work without sudo"
fi

# =============================================================================
# 5. systemd services
# =============================================================================
header "Step 5 — systemd services"

PYTHON3_BIN="$(command -v python3)"

install_unit() {
    local name="$1"          # e.g.  summa-backend
    local template="${REPO_DIR}/systemd/${name}.service"
    local dest="/etc/systemd/system/${name}.service"

    echo -e "${STEP} Installing ${name}.service ..."

    local tmpfile
    tmpfile=$(mktemp)

    # Determine ExecStart based on service name
    local execstart
    case "$name" in
        summa-backend)
            execstart="${PYTHON3_BIN} ${REPO_DIR}/backend_pi.py"
            ;;
        summa-bridge)
            execstart="${PYTHON3_BIN} ${REPO_DIR}/tools/serial_bridge.py"
            ;;
        summa-kiosk)
            execstart="/bin/bash ${REPO_DIR}/tools/start_kiosk.sh"
            ;;
    esac

    sed \
        -e "s|^User=.*|User=${RUN_USER}|" \
        -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_DIR}|" \
        -e "s|^ExecStart=.*|ExecStart=${execstart}|" \
        -e "s|^Environment=XAUTHORITY=.*|Environment=XAUTHORITY=${RUN_HOME}/.Xauthority|" \
        "$template" > "$tmpfile"

    sudo install -m 644 "$tmpfile" "$dest"
    rm -f "$tmpfile"

    echo -e "  ${PASS} Installed to ${dest}"
}

# Make shell scripts executable
chmod +x "${REPO_DIR}/tools/start_kiosk.sh"
chmod +x "${REPO_DIR}/tools/serial_bridge.py"

install_unit summa-backend
install_unit summa-bridge

# Kiosk only makes sense on a Desktop image
if systemctl get-default 2>/dev/null | grep -q graphical; then
    install_unit summa-kiosk
    KIOSK_ENABLED=true
else
    echo -e "${WARN} Lite image detected (no graphical target) — kiosk service NOT installed."
    echo -e "       Connect a browser on another device to the LAN URL printed by the backend."
    KIOSK_ENABLED=false
fi

echo -e "${STEP} Reloading systemd daemon..."
sudo systemctl daemon-reload

echo -e "${STEP} Enabling and starting summa-backend..."
sudo systemctl enable --now summa-backend.service
echo -e "  ${PASS} summa-backend enabled"

echo -e "${STEP} Enabling summa-bridge (starts when ESP is plugged in)..."
sudo systemctl enable summa-bridge.service
# Don't start it yet — no ESP may be connected
sudo systemctl start summa-bridge.service 2>/dev/null || true
echo -e "  ${PASS} summa-bridge enabled"

if [[ "$KIOSK_ENABLED" == "true" ]]; then
    echo -e "${STEP} Enabling summa-kiosk..."
    sudo systemctl enable summa-kiosk.service
    echo -e "  ${PASS} summa-kiosk enabled (starts with graphical session)"
fi

# =============================================================================
# 6. Screen blanking — disable globally
# =============================================================================
header "Step 6 — Disable screen blanking"

# lightdm.conf (covers the X server itself)
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
if [[ -f "$LIGHTDM_CONF" ]]; then
    if ! grep -q "xserver-command=X -s 0 -dpms" "$LIGHTDM_CONF"; then
        sudo sed -i 's|^#xserver-command=X|xserver-command=X -s 0 -dpms|' \
            "$LIGHTDM_CONF" 2>/dev/null || true
        echo -e "${PASS} lightdm.conf: DPMS disabled"
    else
        echo -e "${PASS} lightdm.conf: DPMS already disabled"
    fi
else
    echo -e "${WARN} /etc/lightdm/lightdm.conf not found — skipping"
fi

# raspi-config non-interactive (if available)
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_blanking 1 2>/dev/null && \
        echo -e "${PASS} raspi-config: blanking disabled" || \
        echo -e "${WARN} raspi-config blanking command not available"
fi

echo -e "${INFO} Per-session xset commands are in tools/start_kiosk.sh"

# =============================================================================
# 7. Post-install verification
# =============================================================================
header "Step 7 — Verification"

echo -e "${INFO} Waiting 4 s for backend to start up..."
sleep 4

# ── 7a. Python imports ────────────────────────────────────────────────────────
echo -e "\n${C_BOLD}Python imports:${C_END}"
for pkg in flask flask_socketio flask_cors serial; do
    if python3 -c "import ${pkg}" 2>/dev/null; then
        check_pass "python import ${pkg}"
        echo -e "  ${PASS} import ${pkg}"
    else
        check_fail "python import ${pkg}"
        echo -e "  ${FAIL} import ${pkg}"
    fi
done

# ── 7b. Chromium binary ───────────────────────────────────────────────────────
echo -e "\n${C_BOLD}Binaries:${C_END}"
CHROM_FOUND=false
for cand in chromium chromium-browser /usr/bin/chromium /usr/bin/chromium-browser; do
    if command -v "$cand" &>/dev/null || [[ -x "$cand" ]]; then
        check_pass "chromium binary: $(command -v $cand 2>/dev/null || echo $cand)"
        echo -e "  ${PASS} chromium: $cand"
        CHROM_FOUND=true
        break
    fi
done
if [[ "$CHROM_FOUND" == "false" ]]; then
    check_fail "chromium binary not found"
    echo -e "  ${FAIL} chromium not found — install: sudo apt install chromium"
fi

for bin in xdotool unclutter curl python3; do
    if command -v "$bin" &>/dev/null; then
        check_pass "$bin found"
        echo -e "  ${PASS} $bin: $(command -v $bin)"
    else
        check_warn "$bin not found"
        echo -e "  ${WARN} $bin not found"
    fi
done

# ── 7c. Services ──────────────────────────────────────────────────────────────
echo -e "\n${C_BOLD}systemd services:${C_END}"
CHECK_SVCS=("summa-backend" "summa-bridge")
[[ "$KIOSK_ENABLED" == "true" ]] && CHECK_SVCS+=("summa-kiosk")

for svc in "${CHECK_SVCS[@]}"; do
    state=$(systemctl is-active "${svc}.service" 2>/dev/null || echo "inactive")
    enabled=$(systemctl is-enabled "${svc}.service" 2>/dev/null || echo "disabled")
    if [[ "$state" == "active" ]]; then
        check_pass "${svc}: active + ${enabled}"
        echo -e "  ${PASS} ${svc}: active / ${enabled}"
    elif [[ "$state" == "activating" ]]; then
        check_warn "${svc}: still activating"
        echo -e "  ${WARN} ${svc}: activating (may need a moment)"
    elif [[ "$svc" == "summa-bridge" && "$state" == "failed" ]]; then
        # Bridge failing at start is expected when no ESP is plugged in
        check_warn "summa-bridge: not running (no ESP plugged in yet — plug it in after reboot)"
        echo -e "  ${WARN} summa-bridge: ${state} — plug the ESP32 in after reboot"
    elif [[ "$svc" == "summa-kiosk" ]]; then
        # Kiosk won't be active until graphical session is up
        check_warn "${svc}: ${state} (will start with graphical session)"
        echo -e "  ${WARN} ${svc}: ${state} — will auto-start with the desktop"
    else
        check_fail "${svc}: ${state}"
        echo -e "  ${FAIL} ${svc}: ${state}"
        echo -e "       Diagnose: journalctl -u ${svc}.service -n 30 --no-pager"
    fi
done

# ── 7d. Backend HTTP health check ─────────────────────────────────────────────
echo -e "\n${C_BOLD}Backend HTTP:${C_END}"
BACKEND_URL="http://127.0.0.1:5000"
HTTP_CODE=""
for attempt in 1 2 3; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
                     --max-time 3 "${BACKEND_URL}/" 2>/dev/null || echo "000")
    [[ "$HTTP_CODE" =~ ^[23] ]] && break
    sleep 2
done

if [[ "$HTTP_CODE" =~ ^[23] ]]; then
    check_pass "backend responds HTTP ${HTTP_CODE} at ${BACKEND_URL}"
    echo -e "  ${PASS} backend responds: HTTP ${HTTP_CODE}"
else
    check_fail "backend not responding at ${BACKEND_URL} (code: ${HTTP_CODE})"
    echo -e "  ${FAIL} backend not responding (HTTP ${HTTP_CODE})"
    echo -e "       Check: journalctl -u summa-backend.service -n 30 --no-pager"
fi

# /logs endpoint
LOGS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
                 --max-time 3 "${BACKEND_URL}/logs?limit=1" 2>/dev/null || echo "000")
if [[ "$LOGS_CODE" == "200" ]]; then
    check_pass "/logs endpoint returns 200"
    echo -e "  ${PASS} /logs endpoint: OK"
else
    check_warn "/logs endpoint returned ${LOGS_CODE}"
    echo -e "  ${WARN} /logs endpoint returned ${LOGS_CODE}"
fi

# ── 7e. USB / serial — ESP32 detection ───────────────────────────────────────
echo -e "\n${C_BOLD}ESP32 / USB serial:${C_END}"
ESP_FOUND=false
if ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | grep -q .; then
    for dev in /dev/ttyACM* /dev/ttyUSB*; do
        [[ -e "$dev" ]] || continue
        check_pass "USB serial device found: ${dev}"
        echo -e "  ${PASS} Found: ${dev}"
        ESP_FOUND=true
    done
fi
if [[ "$ESP_FOUND" == "false" ]]; then
    check_warn "No ESP32 detected — plug the USB receiver into the Pi after setup"
    echo -e "  ${WARN} No ESP32 on USB yet"
    echo -e "       Plug it in after reboot and summa-bridge will connect automatically."
fi

# dialout group membership
if groups "$RUN_USER" | grep -q dialout; then
    check_pass "${RUN_USER} is in dialout group"
    echo -e "  ${PASS} ${RUN_USER} is in dialout group"
else
    check_warn "${RUN_USER} not yet in dialout group (takes effect after reboot)"
    echo -e "  ${WARN} dialout group takes effect after reboot"
fi

# ── 7f. Token file ────────────────────────────────────────────────────────────
echo -e "\n${C_BOLD}Token:${C_END}"
TOKEN_FILE="${RUN_HOME}/.summa_token"
if [[ -f "$TOKEN_FILE" ]]; then
    check_pass "token file exists: ${TOKEN_FILE}"
    echo -e "  ${PASS} token: $(cat "$TOKEN_FILE" | cut -c1-12)... (${TOKEN_FILE})"
else
    check_warn "token file not created yet (backend generates it on first run)"
    echo -e "  ${WARN} token file not found yet — backend creates it on first run"
fi

# =============================================================================
# 8. Summary
# =============================================================================
header "Setup Summary"

PASS_COUNT=0
WARN_COUNT=0
for r in "${_CHECK_RESULTS[@]}"; do
    echo -e "  $r"
    [[ "$r" == *"[PASS]"* ]] && PASS_COUNT=$(( PASS_COUNT + 1 ))
    [[ "$r" == *"[WARN]"* ]] && WARN_COUNT=$(( WARN_COUNT + 1 ))
done

echo
if [[ $_FAIL_COUNT -eq 0 ]]; then
    echo -e "${C_GREEN}${C_BOLD}All checks passed! (${PASS_COUNT} passed, ${WARN_COUNT} warnings)${C_END}"
else
    echo -e "${C_RED}${C_BOLD}${_FAIL_COUNT} check(s) failed. Review the FAIL items above.${C_END}"
fi

echo
echo -e "${C_BOLD}${C_CYAN}Next steps:${C_END}"
echo -e "  1. ${C_BOLD}Reboot the Pi:${C_END}  sudo reboot"
echo -e "  2. ${C_BOLD}Plug in the ESP32 receiver${C_END} via USB — the bridge connects automatically."
echo -e "  3. The scoreboard opens in Chromium on the TV. Done."
echo
echo -e "${C_BOLD}Useful commands after reboot:${C_END}"
echo -e "  python view_logs.py              # interactive log viewer + service control"
echo -e "  python view_logs.py --status     # quick service status"
echo -e "  python view_logs.py -f           # live tail all logs"
echo -e "  journalctl -u summa-backend -f   # raw backend logs"
echo -e "  journalctl -u summa-bridge  -f   # raw bridge logs"
echo
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "Pi-IP")
echo -e "${C_BOLD}Scoreboard URLs:${C_END}"
echo -e "  Local (on Pi):  http://127.0.0.1:5000/"
echo -e "  LAN:            http://${LAN_IP}:5000/"
echo
echo -e "${C_GRAY}Setup complete — $(date)${C_END}"
echo
