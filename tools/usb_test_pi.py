#!/usr/bin/env python3
"""
USB-CDC link test — Raspberry Pi side.

Goes with ESP/usb_test/usb_test.ino.

Behaviour:
  * Auto-detects the ESP on /dev/ttyACM* (falls back to /dev/ttyUSB*).
    Override with --port /dev/ttyACM0.
  * Opens at 115200 baud, prints every line received with a [recv] prefix.
  * Anything you type into the terminal is sent to the ESP, which will
    echo it back as "echo: <line>".
  * Auto-reconnects if the ESP is unplugged/replugged.

Install once:
    sudo apt install -y python3-serial         # or: pip install pyserial
    sudo usermod -a -G dialout $USER           # then log out + back in

Run:
    python3 tools/usb_test_pi.py
    python3 tools/usb_test_pi.py --port /dev/ttyACM0

Quit: Ctrl-C.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write(
        "ERROR: pyserial not installed.\n"
        "  Debian/Pi:  sudo apt install -y python3-serial\n"
        "  pip:        pip install pyserial\n"
    )
    sys.exit(1)


BAUD = 115200


def autodetect_port() -> Optional[str]:
    """Return the first plausible ESP-class USB serial device."""
    for p in list_ports.comports():
        dev = (p.device or "").lower()
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if "ttyacm" in dev:                # native USB-CDC (XIAO, ESP32-C3 Super Mini)
            return p.device
        if "esp32" in desc or "espressif" in desc:
            return p.device
        if "303a" in hwid:                 # Espressif vendor id
            return p.device
    # Last-ditch: any ttyUSB* (CP2102 / CH340 / FTDI bridges)
    for p in list_ports.comports():
        if "ttyusb" in (p.device or "").lower():
            return p.device
    return None


def reader_loop(ser: serial.Serial, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            raw = ser.readline()
        except serial.SerialException:
            print("[link] serial read error — will reconnect")
            stop.set()
            return
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line:
            print(f"[recv] {line}")


def writer_loop(ser: serial.Serial, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            line = input()
        except EOFError:
            stop.set()
            return
        try:
            ser.write((line + "\n").encode("utf-8"))
        except serial.SerialException:
            print("[link] serial write error — will reconnect")
            stop.set()
            return


def open_with_retry(port: str) -> serial.Serial:
    while True:
        try:
            ser = serial.Serial(port, BAUD, timeout=1)
            time.sleep(0.2)  # let the ESP settle after enumeration
            return ser
        except (serial.SerialException, FileNotFoundError):
            print(f"[link] {port} not ready, retrying in 2 s...")
            time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="Serial device, e.g. /dev/ttyACM0. "
                                   "If omitted, auto-detects.")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        sys.stderr.write(
            "ERROR: no ESP-class serial device found.\n"
            "  Plug the ESP in via USB.\n"
            "  List candidates with: ls /dev/ttyACM* /dev/ttyUSB*\n"
        )
        return 2

    print(f"[link] using {port} @ {BAUD}. Type to send. Ctrl-C to quit.\n")

    while True:
        ser = open_with_retry(port)
        stop = threading.Event()
        t_r = threading.Thread(target=reader_loop, args=(ser, stop), daemon=True)
        t_w = threading.Thread(target=writer_loop, args=(ser, stop), daemon=True)
        t_r.start()
        t_w.start()
        try:
            while not stop.is_set():
                stop.wait(0.5)
        except KeyboardInterrupt:
            print("\n[link] bye")
            try: ser.close()
            except Exception: pass
            return 0
        try: ser.close()
        except Exception: pass
        print("[link] reconnecting...")


if __name__ == "__main__":
    raise SystemExit(main())
