/*
 * SUMMA V3 — padel scoring receiver firmware
 * ============================================
 *
 * Plugged into the Raspberry Pi over USB-C. Always powered, no sleep.
 *
 * Job:
 *   1. Listen for ESP-NOW packets from the two single-button remotes.
 *   2. Print every received payload on USB serial, prefixed "got: ".
 *   3. Show the last action + a counter on the 72×40 SSD1306 OLED.
 *
 * Wire format (set by remote.ino):
 *   "<team> <action>|<event_id>"   e.g. "black addpoint|rb-00041-007"
 *   "reset|<event_id>"             reset has no team prefix
 *
 *   <action> is one of: addpoint | subtractpoint
 *   (Coming from the new gesture remote: tap=+1, double-tap=-1, hold-3s=reset.
 *    Power on/off gestures stay LOCAL to the remote — they never hit the air.)
 *
 * The Pi-side tools/serial_bridge.py reads "got: ..." lines, picks the
 * trailing |<event_id>, and POSTs to the Flask /remote_event endpoint.
 * This sketch contains zero scoring logic by design — all rules live in
 * Python where they are easy to change without reflashing.
 *
 * Board:    "ESP32C3 Dev Module"
 * Setting:  Tools → USB CDC On Boot → Enabled
 * Library:  U8g2  (Library Manager → "U8g2 by oliver")
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <U8g2lib.h>
#include <Wire.h>

// MUST match the same constant in firmware/remote/remote.ino. ESP-NOW only
// hears packets on the channel the radio is currently parked on.
#define ESPNOW_CHANNEL 6

// 72×40 SSD1306 on the ESP32-C3 Super Mini's onboard OLED pads.
// SCL = GPIO 6, SDA = GPIO 5 (these are the populated pads on the purple board).
U8G2_SSD1306_72X40_ER_F_HW_I2C oled(U8G2_R0, /*reset*/U8X8_PIN_NONE, /*SCL*/6, /*SDA*/5);

// Last-event bookkeeping. We split the parsed payload into a short tag
// (+1 / -1 / RST) and a team tag (BLK / YEL / —) so the 72-px-wide OLED
// can show both at once in big readable type, instead of cramming the
// raw "black addpoint" string in 5×7 pixels.
char     last_msg[64]  = "READY";    // raw payload (still printed on serial)
char     last_tag[8]   = "";         // "+1" | "-1" | "RST"
char     last_team[8]  = "";         // "BLK" | "YEL" | ""
uint32_t last_msg_at   = 0;
uint32_t recv_count    = 0;

// ── Parse "<team> <action>|..." into the short OLED tags ─────────────────
void parsePayload(const char *msg) {
  last_tag[0]  = '\0';
  last_team[0] = '\0';

  if (strncmp(msg, "reset", 5) == 0) {
    strcpy(last_tag, "RST");
    return;
  }
  if (strncmp(msg, "black", 5) == 0)       strcpy(last_team, "BLK");
  else if (strncmp(msg, "yellow", 6) == 0) strcpy(last_team, "YEL");
  else                                     return;

  if (strstr(msg, "addpoint"))           strcpy(last_tag, "+1");
  else if (strstr(msg, "subtractpoint")) strcpy(last_tag, "-1");
}

// ── ESP-NOW receive callback ─────────────────────────────────────────────
void onRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  size_t n = (len < (int)sizeof(last_msg) - 1) ? len : (int)sizeof(last_msg) - 1;
  memcpy(last_msg, data, n);
  last_msg[n] = '\0';

  // Strip trailing newlines / nulls in case the sender included them
  while (n > 0 && (last_msg[n - 1] == '\n' ||
                   last_msg[n - 1] == '\r' ||
                   last_msg[n - 1] == '\0')) {
    last_msg[--n] = '\0';
  }

  recv_count++;
  parsePayload(last_msg);

  // The line the Pi bridge greps for. Keep "got: " prefix exactly.
  Serial.print("got: ");
  Serial.println(last_msg);

  last_msg_at = millis();
}

// ── OLED rendering ───────────────────────────────────────────────────────
void drawOLED() {
  oled.clearBuffer();

  // Top line: counter
  oled.setFont(u8g2_font_6x10_tr);
  oled.setCursor(0, 9);
  oled.print("RX:");
  oled.print(recv_count);
  oled.drawHLine(0, 11, 72);

  bool fresh = (last_msg_at > 0) && (millis() - last_msg_at < 2000);

  if (fresh && last_tag[0]) {
    // Big tag on the left ("+1" / "-1" / "RST")
    oled.setFont(u8g2_font_logisoso16_tr);
    oled.setCursor(0, 32);
    oled.print(last_tag);

    // Team tag stacked on the right
    if (last_team[0]) {
      oled.setFont(u8g2_font_6x10_tr);
      oled.setCursor(46, 24);
      oled.print(last_team);
    }
  } else {
    // Idle screen
    oled.setFont(u8g2_font_6x10_tr);
    oled.setCursor(0, 26);
    oled.print("waiting...");
  }

  oled.sendBuffer();
}

// ── Setup ────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);                          // give USB-CDC a moment to enumerate
  WiFi.mode(WIFI_STA);
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

  Serial.println();
  Serial.println("=== SUMMA RECEIVER ===");
  Serial.print("MAC: "); Serial.println(WiFi.macAddress());
  Serial.print("CH : "); Serial.println(ESPNOW_CHANNEL);

  oled.begin();
  oled.setBusClock(400000);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init FAILED");
    oled.clearBuffer();
    oled.setFont(u8g2_font_6x10_tr);
    oled.setCursor(0, 12); oled.print("ESP-NOW");
    oled.setCursor(0, 24); oled.print("INIT FAIL");
    oled.sendBuffer();
    while (true) delay(1000);
  }
  esp_now_register_recv_cb(onRecv);

  Serial.println("ready");
}

// ── Loop ─────────────────────────────────────────────────────────────────
void loop() {
  drawOLED();
  delay(50);                           // 20 Hz redraw is plenty for a status pane
}
