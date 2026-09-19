#!/usr/bin/env bash
# SUMMA V3 — one-command Raspberry Pi installer and updater
#
# Target: Raspberry Pi 3B (1 GB RAM), Raspberry Pi OS Bookworm with Desktop.
# Run from inside the SUMMAV3 repository:
#
#   bash install.sh
#
# By default this fast-forwards the V3 branch, installs/updates every required
# package, configures the services and kiosk, and runs the full verification.
# Add --reboot to reboot automatically after a successful installation.

set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
readonly REPO_DIR="$(dirname "$SCRIPT_PATH")"
readonly TARGET_BRANCH="${SUMMA_BRANCH:-V3}"

DO_UPDATE=1
DO_REBOOT=0
ORIGINAL_ARGS=("$@")

info() { printf '\033[1;36m[SUMMA]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[PASS]\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
SUMMA V3 Raspberry Pi installer/updater

Usage:
  bash install.sh [options]

Options:
  --no-update   Install the currently checked-out files without git pull
  --reboot      Reboot automatically after every check passes
  -h, --help    Show this help

Examples:
  bash install.sh             Update V3, install, configure and verify
  bash install.sh --no-update Install local/offline files without updating
  bash install.sh --reboot    Do everything and reboot when successful
EOF
}

on_error() {
    local exit_code=$?
    local line_no=${BASH_LINENO[0]:-unknown}
    printf '\n\033[1;31m[FAIL]\033[0m Installation stopped at line %s (exit %s).\n' \
        "$line_no" "$exit_code" >&2
    printf 'Fix the reported error, then safely run: bash install.sh\n' >&2
    exit "$exit_code"
}
trap on_error ERR

while (($#)); do
    case "$1" in
        --no-update) DO_UPDATE=0 ;;
        --reboot)    DO_REBOOT=1 ;;
        -h|--help)   usage; exit 0 ;;
        *)           usage; die "Unknown option: $1" ;;
    esac
    shift
done

[[ "$(uname -s)" == "Linux" ]] || die "This installer only runs on Linux."
command -v python3 >/dev/null 2>&1 || die "python3 is required."
command -v sudo >/dev/null 2>&1 || die "sudo is required."
[[ -f "$REPO_DIR/setup.py" ]] || die "setup.py is missing from $REPO_DIR."

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
    die "Run this as the normal desktop user, not as root: bash install.sh"
fi

cd "$REPO_DIR"

info "SUMMA V3 installer"
info "Repository: $REPO_DIR"

if [[ -r /sys/firmware/devicetree/base/model ]]; then
    PI_MODEL="$(tr -d '\0' </sys/firmware/devicetree/base/model)"
    info "Hardware: $PI_MODEL"
    [[ "$PI_MODEL" == *"Raspberry Pi"* ]] || warn "Raspberry Pi hardware was not detected."
else
    warn "Could not read the Raspberry Pi model; setup.py will check again."
fi

# Ask for sudo once at the beginning instead of interrupting later steps.
info "Checking administrator access..."
sudo -v

# Updating a running shell script in place can produce mixed old/new commands.
# After a successful pull, restart this script once from the updated file.
if ((DO_UPDATE)) && [[ "${SUMMA_INSTALL_UPDATED:-0}" != "1" ]]; then
    if [[ -d .git ]]; then
        command -v git >/dev/null 2>&1 || die "git is required for automatic updates."

        CURRENT_BRANCH="$(git branch --show-current)"
        [[ "$CURRENT_BRANCH" == "$TARGET_BRANCH" ]] || \
            die "Expected branch $TARGET_BRANCH, but found ${CURRENT_BRANCH:-detached HEAD}."

        if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
            die "Tracked files have local changes. Commit or stash them before updating."
        fi

        git remote get-url origin >/dev/null 2>&1 || die "Git remote 'origin' is missing."
        info "Updating origin/$TARGET_BRANCH (fast-forward only)..."
        git fetch --prune origin "$TARGET_BRANCH"
        git pull --ff-only origin "$TARGET_BRANCH"
        ok "Code is up to date."

        exec env SUMMA_INSTALL_UPDATED=1 bash "$SCRIPT_PATH" "${ORIGINAL_ARGS[@]}"
    else
        warn "This is not a git checkout; installing the local files without updating."
    fi
fi

# Keep installation predictable on the Pi 3B's 1 GB of RAM.
export MAKEFLAGS="-j1"
export CMAKE_BUILD_PARALLEL_LEVEL="1"
export PIP_NO_CACHE_DIR="1"
export PYTHONUNBUFFERED="1"

AVAILABLE_MB="$(awk '/MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || true)"
if [[ -n "$AVAILABLE_MB" ]]; then
    info "Available memory: ${AVAILABLE_MB} MB"
    if ((AVAILABLE_MB < 180)); then
        warn "Available RAM is low. Close other applications before continuing if setup fails."
    fi
fi

info "Installing packages, services, remote bridge and TV kiosk..."
python3 "$REPO_DIR/setup.py"

ok "SUMMA V3 installation and verification completed."
printf '\nUseful commands:\n'
printf '  python3 view_logs.py --status\n'
printf '  python3 view_logs.py -f\n'
printf '  sudo systemctl restart summa-backend summa-bridge summa-kiosk\n\n'

if ((DO_REBOOT)); then
    info "Rebooting now..."
    sudo systemctl reboot
else
    info "Reboot is required before first use. Run: sudo reboot"
fi
