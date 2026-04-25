# Raspberry Pi 3B setup (1 GB RAM, TV display)

The same SUMMAV3 backend runs on PC and Pi — only the launcher changes.

| Where  | Run                       | Binds                  | Browser            |
|--------|---------------------------|------------------------|--------------------|
| PC     | `python backend_pc.py`    | `127.0.0.1:5000`       | Auto-opens         |
| Pi 3B  | `python3 backend_pi.py`   | `0.0.0.0:5000`         | systemd → kiosk    |

## What the Pi does

1. **`backend_pi.py`** boots the Flask + Socket.IO app, binding `0.0.0.0:5000`
   so the LAN (and the local kiosk Chromium) can reach it. Token is read from
   `~/.summa_token` (auto-generated on first run, persists across reboots).
   Logs rotate at 2 MB into `/var/log/summa/backend.log` (or
   `~/.summa/backend.log` if the user can't write `/var/log`).
2. **`tools/start_kiosk.sh`** launches Chromium full-screen at
   `http://127.0.0.1:5000/`, disables screen blanking, hides the cursor,
   and applies a 1-pixel layout shift each launch as a tiny burn-in
   mitigation.
3. **systemd** keeps both alive across reboots (`summa-backend.service`,
   `summa-kiosk.service`).

## Hardware assumptions

- Raspberry Pi 3B, 1 GB RAM (Pi 4 / Pi 5 also work — same script).
- **Raspberry Pi OS Bookworm with Desktop** (LXDE/Wayfire). Required for
  kiosk mode. Lite (headless) works for the backend; just point a separate
  device at the LAN URL.
- TV connected via HDMI, set to the Pi's native output resolution (1080p).
- The Pi can join Wi-Fi (or be wired) — same network as the future
  ESP-NOW USB-serial bridge that will plug in.

## One-time install

```bash
ssh pi@<pi-ip>
git clone <your repo>      # or scp the SUMMAV3 folder over
cd ~/summa/SUMMAV3
bash tools/setup_pi.sh
sudo reboot
```

After the reboot, the TV should boot straight into the scoreboard.

## What `setup_pi.sh` does

1. `apt install` Python 3, Chromium, `unclutter` (cursor hider),
   `x11-xserver-utils` (xset for DPMS).
2. `pip install -r requirements.txt` (Flask, Flask-Cors, Flask-SocketIO,
   python-socketio, eventlet, pytest). On Bookworm that requires
   `--break-system-packages`, which the script handles.
3. Patches the systemd unit files with your actual `User=` and absolute
   paths, installs them to `/etc/systemd/system/`, enables both.
4. Disables DPMS in `lightdm.conf` (defence in depth — `start_kiosk.sh`
   also disables it per session via `xset`).

It does **not** install pigpio, smbus2, or pygame. Those V1 dependencies
are intentionally gone.

## Pi 3B 1 GB tuning baked in

| Concern                  | Mitigation                                                  |
|--------------------------|-------------------------------------------------------------|
| Limited RAM (1 GB)       | systemd `MemoryHigh=350M MemoryMax=450M` on the backend     |
| Slow ARMv7 cores         | `async_mode="threading"` in the app (no eventlet runtime overhead) |
| Chromium memory          | `--enable-low-end-device-mode`                              |
| Background CPU drain     | `--disable-component-update --disable-background-networking --disable-sync` |
| Screen blanking          | `xset s off / -dpms / s noblank` + `lightdm` xserver flags   |
| Pop-ups / autocomplete   | `--noerrdialogs --disable-infobars --disable-translate`     |
| Crash recovery           | Ephemeral profile dir per launch + `Restart=on-failure`     |
| OLED burn-in             | 0..4 px window-position shift per launch (cycles every 10 m)|

## Day-to-day commands

```bash
# Live logs
journalctl -u summa-backend.service -f
journalctl -u summa-kiosk.service -f

# Restart just the backend (kiosk auto-recovers)
sudo systemctl restart summa-backend.service

# Restart the kiosk (TV will reload the page)
sudo systemctl restart summa-kiosk.service

# Stop autostart entirely
sudo systemctl disable --now summa-backend.service summa-kiosk.service

# Read the bearer token (paste into your remote / bridge)
cat ~/.summa_token
```

## Confirming PC ↔ Pi parity

The two launchers are deliberately small wrappers around the **same**
`padel_backend.py`. Endpoints, Socket.IO events, scoring rules, idempotency
cache, SQLite persistence, `/remote_event`, `/matches` — all identical on
both. The only differences:

| Area                  | PC (`backend_pc.py`) | Pi (`backend_pi.py`)               |
|-----------------------|----------------------|------------------------------------|
| Default bind          | `127.0.0.1`          | `0.0.0.0`                          |
| Token                 | Ephemeral per run    | Persistent in `~/.summa_token`     |
| Auto-open browser     | Yes                  | No (kiosk handles display)         |
| Logging               | stdout only          | stdout + rotating file             |
| Run mode              | Manual / dev         | systemd                            |

## Troubleshooting

**Black screen / no Chromium after boot.**
```bash
systemctl status summa-kiosk.service
journalctl -u summa-kiosk.service -n 80
```
Most common causes: not booted into Desktop image (`systemctl get-default`
should print `graphical.target`), or Wayfire instead of LXDE on Bookworm —
edit the `Environment=DISPLAY=:0` line if your session uses a different
display number.

**Backend up but page won't load.**
- `curl http://127.0.0.1:5000/health` from the Pi itself.
- Check the firewall: `sudo iptables -L` (Pi OS ships with no firewall by
  default — should be empty).

**Token mismatch with mock / bridge tools.**
- The token in `~/.summa_token` is the source of truth on the Pi.
- `export SUMMA_NODE_TOKEN=$(cat ~/.summa_token)` before running
  `tools/mock_esp32_node.py`.

**TV goes to sleep mid-match.**
- `xset q` should report `DPMS is Disabled`. If not, the `xset` calls in
  `start_kiosk.sh` weren't picked up — check the kiosk service ran with the
  correct `DISPLAY` and `XAUTHORITY`.

**Match data lost after reboot.**
- `ls -lh ~/summa/SUMMAV3/padel_matches.db` should show a non-empty file.
- `sqlite3 ~/summa/SUMMAV3/padel_matches.db "SELECT id, winner, ended_at FROM matches ORDER BY id DESC LIMIT 5;"`
