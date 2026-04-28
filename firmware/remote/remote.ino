/*
 * SUMMA V3 — single-button padel scoring remote
 * ================================================
 *
 * One source file flashed to BOTH physical remotes. The only per-board
 * differences are TEAM, TEAM_R, TEAM_G, TEAM_B — flip them and reflash
 * for the second remote.
 *
 * GESTURES (while ON):
 *   • single tap            → +1 point for this team
 *   • double tap            → −1 point for this team
 *   • hold 3 s              → reset the match
 *   • hold 10 s             → soft power OFF the remote
 *
 * GESTURES (while OFF):
 *   • hold 10 s             → soft power ON (LED blinks team color 3×)
 *   • any shorter press     → ignored, briefly wakes and goes back to sleep
 *
 * Hardware (ESP32-C3 Super Mini, 1× CJMCU-123 WS2812, 1× P-MOSFET):
 *
 *     GPIO 2 → THE BUTTON to GND  (internal pull-up, active LOW)
 *     GPIO 7 → WS2812 DIN          (470 Ω in series)
 *     GPIO 8 → P-MOSFET gate       (HIGH = LED power off)
 *
 *     CJMCU-123 VCC → P-MOSFET drain (source = battery +)
 *     MOSFET gate   → 10 kΩ pull-up to battery + (default OFF on sleep)
 *
 * Board:    "ESP32C3 Dev Module"
 * Setting:  Tools → USB CDC On Boot → Enabled
 * Library:  Adafruit NeoPixel
 *
 * Power:
 *   Deep sleep (MOSFET off):     ~12 µA
 *   Active per press:            ~80 ms × ~80 mA  ≈ 0.0018 mAh
 *   Realistic 1000 mAh battery:  ~4–8 months between charges
 *
 * Wire format on ESP-NOW (unchanged from 3-button version):
 *   "<team> <action>|<event_id>"     e.g.  "black addpoint|rb-00041-007"
 *   "reset|<event_id>"               (no team prefix on reset)
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <Adafruit_NeoPixel.h>

// Both remotes AND the receiver MUST agree on this channel (1..13). Pick a
// channel away from any nearby 2.4 GHz APs. Channel 6 is usually quiet.
#define ESPNOW_CHANNEL 6

// ── PER-REMOTE CONFIG ────────────────────────────────────────────────────
#define TEAM     "yellow"            // ← "yellow" for the second remote
#define TEAM_R   255                // identity color shown on power-on
#define TEAM_G   255                // black team → cool white
#define TEAM_B   255                // yellow team → set 255,200,0

// SERIAL_DEBUG = 1  → chip stays awake, USB-CDC stays open, prints a
//                     startup banner, and accepts 1/2/3/4 over Serial as
//                     virtual button gestures. Use for bench testing.
// SERIAL_DEBUG = 0  → production (deep-sleep, ~12 µA, months of battery).
#define SERIAL_DEBUG 1
// ─────────────────────────────────────────────────────────────────────────

// Receiver MAC (the Super Mini plugged into the Pi)
static const uint8_t RECEIVER_MAC[6] = { 0x58, 0x8C, 0x81, 0xAC, 0x2B, 0xA0 };

// Pins
constexpr gpio_num_t PIN_BUTTON   = GPIO_NUM_2;
constexpr int        PIN_LED_DATA = 7;
constexpr int        PIN_LED_PWR  = 8;     // P-MOSFET gate (LOW = LED on)

// Wake on BUTTON going LOW
static const uint64_t WAKE_MASK = (1ULL << PIN_BUTTON);

// Gesture timing thresholds (ms)
constexpr uint32_t T_DEBOUNCE      = 20;
constexpr uint32_t T_DOUBLE_WINDOW = 400;   // 2nd tap must arrive within
constexpr uint32_t T_RESET_HOLD    = 3000;  // 3 s = reset match
constexpr uint32_t T_OFF_HOLD      = 10000; // 10 s = power off / on

// State persisted across deep sleep
RTC_DATA_ATTR bool     is_off      = false; // soft on/off flag
RTC_DATA_ATTR uint32_t boot_count  = 0;
RTC_DATA_ATTR uint32_t press_seq   = 0;

// Send-callback signaling
volatile bool send_done    = false;
volatile bool send_success = false;

// Single pixel
constexpr int LED_COUNT = 1;
Adafruit_NeoPixel strip(LED_COUNT, PIN_LED_DATA, NEO_GRB + NEO_KHZ800);

// ── LED helpers ──────────────────────────────────────────────────────────
void ledPower(bool on) {
  pinMode(PIN_LED_PWR, OUTPUT);
  digitalWrite(PIN_LED_PWR, on ? LOW : HIGH);
}

// Set color & brightness; assumes power is on and strip.begin() done
void ledSet(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness = 70) {
  strip.setBrightness(brightness);
  strip.setPixelColor(0, r, g, b);
  strip.show();
}

void ledOnce(uint8_t r, uint8_t g, uint8_t b, int ms, uint8_t brightness = 70) {
  ledPower(true);
  delayMicroseconds(300);
  strip.begin();
  ledSet(r, g, b, brightness);
  delay(ms);
  strip.clear(); strip.show();
  delay(2);
  ledPower(false);
}

void ledOff() {
  strip.clear(); strip.show();
  delay(2);
  ledPower(false);
}

void ledBlink(uint8_t r, uint8_t g, uint8_t b, int times, int on_ms = 80, int off_ms = 100) {
  for (int i = 0; i < times; i++) {
    ledOnce(r, g, b, on_ms);
    if (i < times - 1) delay(off_ms);
  }
}

// ── ESP-NOW ──────────────────────────────────────────────────────────────
void onSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  send_success = (status == ESP_NOW_SEND_SUCCESS);
  send_done    = true;
}

bool sendOnce(const char *line) {
  send_done = false; send_success = false;
  esp_err_t err = esp_now_send(RECEIVER_MAC, (const uint8_t*)line, strlen(line) + 1);
  if (err != ESP_OK) return false;
  uint32_t t0 = millis();
  while (!send_done && (millis() - t0) < 200) delay(2);
  return send_success;
}

bool sendWithRetry(const char *line) {
  for (int i = 0; i < 3; i++) {
    if (sendOnce(line)) return true;
    delay(40);
  }
  return false;
}

bool initEspNow() {
  // Idempotent — debug loop calls sendAction() many times, and esp_now_init
  // returns ESP_ERR_INVALID_STATE on a second call. Cache the result.
  static bool ready = false;
  if (ready) return true;
  WiFi.mode(WIFI_STA);
  // Pin the radio to a known channel BEFORE esp_now_init so the peer entry
  // matches what the receiver listens on.
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) return false;
  esp_now_register_send_cb(onSent);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, RECEIVER_MAC, 6);
  peer.channel = ESPNOW_CHANNEL; peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) return false;
  ready = true;
  return true;
}

// ── Action sender ────────────────────────────────────────────────────────
void buildPayload(char *out, size_t n, const char *action) {
  press_seq++;
  if (strcmp(action, "reset") == 0)
    snprintf(out, n, "reset|r%c-%05u-%03u",
             TEAM[0], boot_count, press_seq);
  else
    snprintf(out, n, "%s %s|r%c-%05u-%03u",
             TEAM, action, TEAM[0], boot_count, press_seq);
}

bool sendAction(const char *action) {
  if (!initEspNow()) return false;
  char payload[64];
  buildPayload(payload, sizeof(payload), action);
  return sendWithRetry(payload);
}

// ── Sleep ────────────────────────────────────────────────────────────────
void goToSleep() {
  ledOff();
  pinMode(PIN_LED_PWR, INPUT);   // float; pull-up keeps MOSFET OFF
  esp_deep_sleep_enable_gpio_wakeup(WAKE_MASK, ESP_GPIO_WAKEUP_GPIO_LOW);
  esp_deep_sleep_start();
}

// ── Continuous LED feedback while button is held ─────────────────────────
//
//   0 – 3000 ms   : blue, brightening    (reset is coming)
//   3000 ms       : flash green once     (reset triggered)
//   3000 –10000   : red, brightening     (power-off is coming)
//   10000 ms      : red fades out        (powering off)
//
void renderHoldFeedback(uint32_t held_ms, bool& reset_done) {
  ledPower(true);
  strip.begin();

  if (held_ms < T_RESET_HOLD) {
    // Blue ramp 0 → 200 brightness over 3 s
    uint8_t b = (uint8_t)(200UL * held_ms / T_RESET_HOLD);
    ledSet(0, 0, 255, b);
  } else if (!reset_done) {
    // Just crossed 3 s → green confirmation
    ledSet(0, 255, 0, 120);
    delay(120);
    reset_done = true;
  } else if (held_ms < T_OFF_HOLD) {
    // Red ramp 0 → 220 over the remaining 7 s
    uint32_t into_red = held_ms - T_RESET_HOLD;
    uint8_t b = (uint8_t)(220UL * into_red / (T_OFF_HOLD - T_RESET_HOLD));
    ledSet(255, 0, 0, b);
  } else {
    ledSet(255, 0, 0, 220);
  }
}

// ── Wait for the button to be released, returns hold duration ────────────
//    Continuously updates LED feedback while held. Sets reset_triggered to
//    true if the player crosses the 3 s threshold.
//
uint32_t waitForRelease(bool& reset_triggered) {
  uint32_t t0 = millis();
  reset_triggered = false;
  while (digitalRead(PIN_BUTTON) == LOW) {
    uint32_t held = millis() - t0;
    renderHoldFeedback(held, reset_triggered);
    if (held >= T_OFF_HOLD) break;     // power-off threshold reached
    delay(15);
  }
  ledOff();
  return millis() - t0;
}

// ── Wait briefly for a SECOND tap → indicates double-tap ─────────────────
bool waitForSecondTap() {
  uint32_t t0 = millis();
  while (millis() - t0 < T_DOUBLE_WINDOW) {
    if (digitalRead(PIN_BUTTON) == LOW) {
      delay(T_DEBOUNCE);
      // Wait for that second tap to release before returning
      while (digitalRead(PIN_BUTTON) == LOW) delay(5);
      return true;
    }
    delay(5);
  }
  return false;
}

// ── ON-state press handler ───────────────────────────────────────────────
//    Decides between: tap (+1), double-tap (-1), hold-3s (reset),
//    hold-10s (power off).
//
void handlePressOn() {
  delay(T_DEBOUNCE);
  if (digitalRead(PIN_BUTTON) != LOW) return;       // false wake, ignore

  bool reset_triggered = false;
  uint32_t held_ms = waitForRelease(reset_triggered);

  // ── 10 s hold → POWER OFF ─────────────────────────────────────────────
  if (held_ms >= T_OFF_HOLD) {
    is_off = true;
    // Goodbye fade: red brightness 220 → 0 over 600 ms
    ledPower(true); strip.begin();
    for (int b = 220; b >= 0; b -= 8) {
      ledSet(255, 0, 0, b);
      delay(20);
    }
    ledOff();
    return;     // setup() will sleep us
  }

  // ── 3–10 s hold → RESET (already green-flashed during the hold) ───────
  if (reset_triggered) {
    if (sendAction("reset")) {
      delay(80);
      ledOnce(0, 255, 0, 80);   // second green = ack confirmed
    } else {
      ledOnce(255, 0, 0, 300);  // red = no ack
    }
    return;
  }

  // ── Short press → tap or double-tap ───────────────────────────────────
  if (held_ms < 30) return;     // even shorter than debounce, treat as noise

  bool double_tap = waitForSecondTap();

  const char *action = double_tap ? "subtractpoint" : "addpoint";
  bool ok = sendAction(action);

  // Short flash so the user can press again immediately. A long flash
  // would block button polling (ledOnce uses delay()) and the next tap
  // would be lost.
  if (ok) {
    if (double_tap) ledOnce(255, 0, 0, 150);    // red   = -1
    else            ledOnce(0, 255, 0, 150);    // green = +1
  } else {
    ledOnce(255, 0, 0, 200);                    // red = no ack
  }
}

// ── OFF-state press handler ──────────────────────────────────────────────
//    Only a 10-second hold turns the remote back on. Anything shorter:
//    no LED, immediate sleep — so accidental taps are silent.
//
void handlePressOff() {
  delay(T_DEBOUNCE);
  if (digitalRead(PIN_BUTTON) != LOW) return;

  uint32_t t0 = millis();
  // Stay dark for the first 1 s — confirms "remote is off" to the user
  // who briefly tapped it.
  while (digitalRead(PIN_BUTTON) == LOW &&
         (millis() - t0) < 1000) delay(15);
  if (digitalRead(PIN_BUTTON) == HIGH) return;  // released early, stay off

  // From 1 s onward, ramp the team color up to indicate "keep holding"
  ledPower(true); strip.begin();
  while (digitalRead(PIN_BUTTON) == LOW) {
    uint32_t held = millis() - t0;
    if (held >= T_OFF_HOLD) {
      // Crossed 10 s → POWER ON
      is_off = false;
      ledOff();
      delay(80);
      ledBlink(TEAM_R, TEAM_G, TEAM_B, 3, 120, 120);   // welcome blink
      // Wait for the user to let go before exiting
      while (digitalRead(PIN_BUTTON) == LOW) delay(10);
      return;
    }
    // Brightness 20 → 220 between 1 s and 10 s
    uint32_t ramp = (held > 1000) ? (held - 1000) : 0;
    uint8_t b = 20 + (uint8_t)(200UL * ramp / (T_OFF_HOLD - 1000));
    ledSet(TEAM_R, TEAM_G, TEAM_B, b);
    delay(20);
  }
  ledOff();   // released before 10 s — stay off
}

// ── SERIAL DEBUG MODE ────────────────────────────────────────────────────
#if SERIAL_DEBUG

void printBanner() {
  WiFi.mode(WIFI_STA);              // needed for WiFi.macAddress()
  Serial.println();
  Serial.println("=== SUMMA REMOTE (SERIAL_DEBUG) ===");
  Serial.print  ("Team       : "); Serial.println(TEAM);
  Serial.print  ("This MAC   : "); Serial.println(WiFi.macAddress());
  Serial.printf ("Receiver   : %02X:%02X:%02X:%02X:%02X:%02X\n",
                 RECEIVER_MAC[0], RECEIVER_MAC[1], RECEIVER_MAC[2],
                 RECEIVER_MAC[3], RECEIVER_MAC[4], RECEIVER_MAC[5]);
  Serial.print  ("Boot count : "); Serial.println(boot_count);
  Serial.print  ("Press seq  : "); Serial.println(press_seq);
  Serial.print  ("Soft state : "); Serial.println(is_off ? "OFF" : "ON");
  Serial.println();
  Serial.println("Physical button (GPIO 2) gestures:");
  Serial.println("  tap         -> +1");
  Serial.println("  double tap  -> -1");
  Serial.println("  hold 3 s    -> reset");
  Serial.println("  hold 10 s   -> power off");
  Serial.println();
  Serial.println("Serial commands:");
  Serial.println("  1 = +1 point   (addpoint)");
  Serial.println("  2 = -1 point   (subtractpoint)");
  Serial.println("  3 = reset match");
  Serial.println("  4 = power off  (deep sleep)");
  Serial.println();
}

void debugSend(const char *action, uint8_t r, uint8_t g, uint8_t b) {
  Serial.printf("CMD %s -> sending... ", action);
  bool ok = sendAction(action);
  if (ok) {
    Serial.println("ack OK");
    ledOnce(r, g, b, 1000);   // 1 s feedback — green for +1, red for -1, blue for reset
  } else {
    Serial.println("NO ACK (check receiver MAC / power)");
    ledOnce(255, 0, 0, 300);
  }
}

void serialLoop() {
  Serial.println("ready — typing 1/2/3/4 + Enter, or press the GPIO 2 button");
  Serial.println();
  bool prev_high = (digitalRead(PIN_BUTTON) == HIGH);

  while (true) {
    // Physical button still works — re-uses the gesture handler so tap /
    // double-tap / hold-3s all behave exactly like the production build.
    bool now_high = (digitalRead(PIN_BUTTON) == HIGH);
    if (prev_high && !now_high) {
      Serial.println("[button] press detected");
      handlePressOn();
      Serial.println("[button] handled");
      while (digitalRead(PIN_BUTTON) == LOW) delay(5);
      prev_high = true;
    } else {
      prev_high = now_high;
    }

    if (Serial.available()) {
      int c = Serial.read();
      switch (c) {
        case '1': debugSend("addpoint",      0,   255, 0  ); break;
        case '2': debugSend("subtractpoint", 255, 0,   0  ); break;
        case '3': debugSend("reset",         0,   0,   255); break;
        case '4':
          Serial.println("CMD power off -> deep sleep (press button to wake)");
          is_off = true;
          delay(50);
          goToSleep();   // never returns
          break;
        case '\r': case '\n': case ' ': case '\t': break;
        default:
          Serial.printf("? unknown '%c' — use 1/2/3/4\n", (char)c);
      }
    }
    delay(5);
  }
}

#endif // SERIAL_DEBUG

// ── Main ─────────────────────────────────────────────────────────────────
void setup() {
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  ledPower(false);                  // LED off by default on every boot
  boot_count++;

#if SERIAL_DEBUG
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 2000) delay(10);
  printBanner();
#endif

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  bool cold_boot = (cause != ESP_SLEEP_WAKEUP_GPIO);

  if (cold_boot) {
    // Fresh flash / USB plug-in / battery insert: clear soft-off and
    // welcome with the team-color blink so the user sees identity.
    is_off = false;
    ledBlink(TEAM_R, TEAM_G, TEAM_B, 3, 120, 120);
  }

#if SERIAL_DEBUG
  // Stay awake forever so the COM port keeps enumerating and the user can
  // watch logs / drive the remote from the keyboard. Physical button is
  // polled inside serialLoop().
  if (!initEspNow()) Serial.println("ERR: esp_now_init failed");
  serialLoop();                     // never returns
#else
  if (cold_boot) goToSleep();
  if (is_off) handlePressOff();
  else        handlePressOn();
  goToSleep();
#endif
}

void loop() {}    // never reached — every wake calls setup() then sleeps
