# SUMMA V3 — ESP32-C3 firmware

Two sketches for the wireless scoring link.

```
firmware/
├── remote/        flashed on BOTH battery-powered remotes
│   └── remote.ino
└── receiver/      flashed on the Super Mini wired to the Pi via USB
    └── receiver.ino
```

## Flashing

Both sketches target **"ESP32C3 Dev Module"** in the Arduino IDE.
Set **Tools → USB CDC On Boot → Enabled** (mandatory on the C3 — without
this the board doesn't show up as a serial device after reboot).

### remote.ino — flash twice, with a few-line edit between

The two remotes share one source file. The only differences are the team
name and the team identity color (used for the power-on welcome blink).

1. Open `remote/remote.ino`.
2. Confirm the BLACK config block:
   ```
   #define TEAM     "black"
   #define TEAM_R   255
   #define TEAM_G   255
   #define TEAM_B   255      // cool white — stands in for "black"
   ```
3. Plug in the **black** Super Mini, click Upload.
4. Change the block to YELLOW:
   ```
   #define TEAM     "yellow"
   #define TEAM_R   255
   #define TEAM_G   200
   #define TEAM_B   0
   ```
5. Plug in the **yellow** Super Mini, click Upload.
6. Change it back to BLACK so the source file matches the daily-driver
   board, and you'll forget you ever flipped it.

Required Arduino library: **Adafruit NeoPixel** (Library Manager).

### receiver.ino — flash once, leave it plugged into the Pi

Required Arduino library: **U8g2** (Library Manager → "U8g2 by oliver").

After flashing, plug the receiver into the Pi's USB port. It should show
up as `/dev/ttyACM0`. The Pi's `tools/serial_bridge.py` reads from that
device and POSTs to the Flask backend.

## Hardware (per remote)

| Pin    | Wired to |
|--------|----------|
| GPIO 2 | Single tactile button → GND (pull-up handled in firmware) |
| GPIO 7 | WS2812 / CJMCU-123 DIN, in series with a 470 Ω resistor |
| GPIO 8 | P-channel MOSFET gate (HIGH = LED powered off) |

Plus:

- 10 kΩ pull-up from the MOSFET gate to battery + (default OFF on sleep).
- P-MOSFET source = battery +, drain = WS2812 VCC.
- 1 × 1000 mAh LiPo, charged through a TP4056 USB-C module.
- (Optional) slide switch in the battery line for hard-off in storage —
  not required, since the firmware has a soft power-off (10 s hold).

The MOSFET is **not optional** — without it, the WS2812's quiescent draw
(~1 mA) drains the 1000 mAh battery in ~40 days. With it, expect 4–8
months between charges.

## Gestures (one button does everything)

| State | Gesture | Action | LED feedback |
|---|---|---|---|
| ON  | Tap (<400 ms)             | +1 point         | Green flash 80 ms |
| ON  | Double-tap (within 400 ms)| −1 point         | Orange flash 90 ms |
| ON  | Hold 3 s                  | Reset match      | Blue ramp → green confirm |
| ON  | Hold 10 s                 | Power off (soft) | Red ramp → red fade-out |
| OFF | Hold 10 s                 | Power on         | Team color blinks 3× |
| OFF | Anything <10 s            | Ignored          | LED stays dark |

The soft on/off state survives deep sleep (kept in RTC memory). To fully
reset the remote, pop the battery for ~10 s.

## Wire format on ESP-NOW

```
"<team> <action>|<event_id>"      e.g.  "black addpoint|rb-00041-007"
"reset|<event_id>"                e.g.  "reset|rb-00041-008"
```

Where `<action>` is `addpoint` or `subtractpoint`. Power on/off gestures
stay LOCAL to the remote — they never go on the air, because the
scoreboard doesn't care whether a remote is asleep.

The receiver prints each payload on USB serial as `got: <payload>` and
shows a short tag on its OLED (`+1 BLK`, `-1 YEL`, `RST`). The Pi-side
`tools/serial_bridge.py` parses out team / action / event_id and POSTs
to `/remote_event`. The backend's idempotency cache keys off the
event_id, so radio retries can't double-count.

## LED feedback (CJMCU-123 single pixel)

| Color                     | Meaning |
|---------------------------|---------|
| Team color × 3 (cold boot)| Battery just inserted or fresh flash |
| Green 80 ms               | +1 sent and acknowledged |
| Orange 90 ms              | −1 sent and acknowledged |
| Blue ramp (0 → 3 s hold)  | "Keep holding for reset" |
| Green flash at 3 s        | Reset triggered (confirmation) |
| Red ramp (3 → 10 s hold)  | "Keep holding for power-off" |
| Red fade out at 10 s      | Powering off |
| Team color ramp (off→on)  | "Keep holding for power-on" |
| Team color × 3 (release)  | Power-on confirmed |
| Red 300 ms                | Send not acknowledged after 3 retries |

## Why this design

- **Deep sleep + GPIO wake**: ~12 µA idle. The remote spends ~99.99 % of
  its life asleep. Wake-to-send latency is ~80 ms — imperceptible.
- **Single button**: less to break, less to mis-press, no labels needed,
  a kid can use it. The 400 ms wait for a possible double-tap is the
  one cost — players will see +1 land on the TV ~½ second after tapping.
- **Event ID generated on the remote**: server-side dedup survives a
  bridge restart. If the radio path drops a packet, the retry has the
  same ID and the score doesn't double.
- **MOSFET-gated WS2812**: rich color feedback without the WS2812's
  always-on quiescent draw eating the battery.
- **Soft on/off in RTC memory**: no physical power switch needed.
  Survives deep sleep but cleared by removing the battery.
- **Receiver is dumb**: it just forwards bytes. All scoring rules live
  in Python so they can change without reflashing two boards.
