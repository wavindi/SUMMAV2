#!/usr/bin/env bash
# SUMMA V3 — Chromium kiosk launcher for Raspberry Pi 3B (1 GB RAM)
#
# Tuned for:
#   * a TV display (no scrollbars, no toolbars, no autocomplete bubbles)
#   * burn-in mitigation (very subtle 1-pixel layout shift via window-position)
#   * low RAM and a slow GPU (low-end-device-mode + disabled background tasks)
#   * surviving a few seconds of network blip on boot (waits for backend)
#
# Invoked by systemd/summa-kiosk.service or by the user manually:
#     bash tools/start_kiosk.sh

set -euo pipefail

URL="${SUMMA_KIOSK_URL:-http://127.0.0.1:5000/}"

# 1. Disable screen blanking, screensaver, and DPMS for as long as we run.
#    The TV must never go to sleep mid-match.
xset s off       || true
xset s noblank   || true
xset -dpms       || true

# Hide the mouse cursor after 0.5s of idle
unclutter -idle 0.5 -root &

# 2. Wait until the backend answers. systemd already orders us After= it,
#    but on a cold boot the socket may not be open yet.
for i in $(seq 1 60); do
  if curl -fsS --max-time 1 "${URL}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# 3. Disposable per-session profile so a crash never wedges the next boot.
PROFILE_DIR="$(mktemp -d -t summa-chromium-XXXX)"
trap 'rm -rf "$PROFILE_DIR"' EXIT

# 4. Pick whichever Chromium binary is installed (Bookworm uses /usr/bin/chromium).
BIN=""
for cand in chromium chromium-browser /usr/bin/chromium /usr/bin/chromium-browser; do
  if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then
    BIN="$cand"
    break
  fi
done
if [ -z "$BIN" ]; then
  echo "ERROR: chromium not found. Install with: sudo apt install -y chromium-browser unclutter" >&2
  exit 1
fi

# 5. Burn-in mitigation: nudge the window 1 px on each launch (cycles 0..4 px).
SHIFT=$(( ( $(date +%s) / 600 ) % 5 ))   # changes every 10 minutes if relaunched

exec "$BIN" \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE_DIR" \
  --window-position=${SHIFT},${SHIFT} \
  --use-gl=egl \
  --force-device-scale-factor=1 \
  --noerrdialogs \
  --disable-infobars \
  --disable-translate \
  --disable-features=Translate,TranslateUI,AutofillServerCommunication \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  --disable-component-update \
  --disable-background-networking \
  --disable-sync \
  --disable-default-apps \
  --no-first-run \
  --no-default-browser-check \
  --password-store=basic \
  --autoplay-policy=no-user-gesture-required \
  --enable-low-end-device-mode \
  --enable-features=OverlayScrollbar
