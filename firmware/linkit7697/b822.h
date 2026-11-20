// b822.h — OKI MC363/B822 printer-side (tier A) emulation for ProtoGate.
//
// The cartridge has two pads driven as a COMPLEMENTARY pair (confirmed in
// la_convert/mc363.sr and on the bench):
//   * D2 = POWER wire: idles HIGH and powers the chip; raised HIGH ~21 ms before
//     the first command.
//   * D1 = DATA  wire: idles LOW.
// During a command the two wires swing in opposite phase: one HIGH, the other
// LOW each symbol. A symbol is one such phase; its WIDTH codes the bit (narrow
// ~4.7 us = 0, wide ~14.2 us = 1). SOF = data HIGH / power LOW (~43 us) then the
// reverse (~43 us) -- i.e. the power wire dips LOW then HIGH. Bytes are 10-bit
// words (start 0, 8 data LSB-first, stop 1). To read a reply the printer keeps clocking
// the pair and, each phase, drives one wire low and RELEASES the other; the cartridge
// answers by briefly pulling the released wire down (a ~0.1-0.2 us notch: 1, clean: 0).
// The printer drives PUSH-PULL otherwise, and each pad needs an external ~1k pull-up to
// 3.3 V so the released wire has a high level for the cartridge to pull against.
//
// Pin mapping (board P-label -> chip GPIO -> AON bank-2 bit), confirmed live:
//   DATA (D1) = P4 = GPIO34 = bit2 ;  POWER (D2) = P5 = GPIO33 = bit1

#ifndef B822_H
#define B822_H

#include <Arduino.h>
#include <ArduinoJson.h>

// Cortex-M4 DWT cycle counter by raw address (no CMSIS include dependency).
#define B822_DWT_CTRL    (*(volatile uint32_t *)0xE0001000UL)
#define B822_DWT_CYCCNT  (*(volatile uint32_t *)0xE0001004UL)
#define B822_DEMCR       (*(volatile uint32_t *)0xE000EDFCUL)
#define B822_DEMCR_TRCENA   (1UL << 24)
#define B822_CYCCNTENA      (1UL << 0)

// ---------------------------------------------------------------------------
// Pins / GPIO registers (AON domain)
// ---------------------------------------------------------------------------
static uint8_t  B822_PIN_D1 = 4;    // DATA  wire (default P4 = GPIO34)
static uint8_t  B822_PIN_D2 = 5;    // POWER wire (default P5 = GPIO33)

#define B822_GPIO_BASE   0x8300B000UL
#define B822_DOUT2_SET   (*(volatile uint32_t *)(B822_GPIO_BASE + 0x74))
#define B822_DOUT2_RESET (*(volatile uint32_t *)(B822_GPIO_BASE + 0x78))
#define B822_OE2_SET     (*(volatile uint32_t *)(B822_GPIO_BASE + 0xA4))  // drive
#define B822_OE2_RESET   (*(volatile uint32_t *)(B822_GPIO_BASE + 0xA8))  // release
#define B822_PU2_SET     (*(volatile uint32_t *)(B822_GPIO_BASE + 0x14))  // pull-up on
#define B822_PU2_RESET   (*(volatile uint32_t *)(B822_GPIO_BASE + 0x18))  // pull-up off
#define B822_PD2_SET     (*(volatile uint32_t *)(B822_GPIO_BASE + 0x44))  // pull-DOWN on
#define B822_PD2_RESET   (*(volatile uint32_t *)(B822_GPIO_BASE + 0x48))  // pull-DOWN off
#define B822_DIN2        (*(volatile uint32_t *)(B822_GPIO_BASE + 0xC4))
// Pull config helpers. The AON GPIO has SEPARATE pull-up (0x10) and pull-down (0x40)
// enable banks; enabling PU alone leaves any default PD on, and the pull-down then
// fights (and wins over) the weak ~47k pull-up, dragging a released wire LOW. So a
// real internal pull-UP must also clear the pull-DOWN.
static inline void b822_pullup(uint32_t mask)    { B822_PD2_RESET = mask; B822_PU2_SET = mask; }
static inline void b822_pullfloat(uint32_t mask) { B822_PU2_RESET = mask; B822_PD2_RESET = mask; }

// ---------------------------------------------------------------------------
// Edge-latch notch detection. A cartridge notch is only ~40-160 ns wide, far shorter than
// a polled GPIO read (~0.35 us per AON-domain read), so it is caught in hardware instead.
// The sensed GPIO's edge sets its NVIC PENDING bit; we leave the NVIC ENABLE off (no ISR)
// and just clear/read the pending bit as a per-phase latch.
//   DATA = GPIO34 = IRQ38 -> ISPR1/ICPR1 bit6 ;  POWER = GPIO33 = IRQ37 -> bit5
#define B822_NVIC_ISPR1  (*(volatile uint32_t *)0xE000E204UL)   // pending (read)
#define B822_NVIC_ICPR1  (*(volatile uint32_t *)0xE000E284UL)   // clear pending (write)
#define B822_NVIC_ICER1  (*(volatile uint32_t *)0xE000E184UL)   // clear enable (keep ISR off)
#define B822_IRQ_D1      (1u << 6)                               // GPIO34 (DATA)
#define B822_IRQ_D2      (1u << 5)                               // GPIO33 (POWER)
// Bench characterisation (b822.eint) showed the edge unit only sees a pad muxed to its
// WIC function (3) -- in plain GPIO mode (8) nothing latches -- and that WIC mode
// DISCONNECTS the GPIO driver from the pad. So RX switches the SENSED pad to WIC only for
// its (released) sense window and back to GPIO afterwards.
// Pinmux for GPIO32..39 is one register, 4 bits per pin:
//   GPIO33 = bits 7:4 (WIC5/IRQ37),  GPIO34 = bits 11:8 (WIC6/IRQ38).
// NOTE: edge latching exists only for GPIO33/34, i.e. the default P4/P5 wiring.
#define B822_PINMUX_R    (*(volatile uint32_t *)0x81023030UL)
static uint32_t b822_mux_gpio = 0, b822_mux_wic_d1 = 0, b822_mux_wic_d2 = 0;
// Trigger word written to both edge units (request param "eint_deb"):
// DEBOUNCE_EN(9) | POL(8) | DUAL(7: both edges) | PRESC(6:4) | CNT(3:0). Debounce MUST
// stay off: its clock is <= 8 kHz (>= 125 us), which swallows any sub-us notch.
// POLARITY, MEASURED (WIC mode, edge sense): POL=0 latches a RISING edge, POL=1 a FALLING
// edge -- the opposite of the HAL enum's naming. Default 0x080 = DUAL (either edge), no
// debounce: a notch is a fall AND a rise, so catching either records it and one missed
// edge cannot drop a bit. On the bench 0x000 (rise) and 0x080 (dual) each decoded a full
// 18-byte read-ID reply 25/25 times; 0x100 (fall) did too apart from one cold-start miss.
static uint32_t b822_deb_val = 0x080;
// NVIC_SENSE bit: 0 = EDGE, 1 = LEVEL (ENUM_NVIC_SENSE_T { eEdge_Trigger, eLevel_Trigger }).
static uint32_t b822_sense_level = 0;
// Optional delay between releasing the sensed wire and clearing its latch (request
// "eint_settle"); 0 is right for normal use.
static uint32_t b822_settle_ns = 0;
// Raw per-phase notch bits from the last b822_rx_eint (for host-side analysis).
static uint8_t b822_ebits[1024];
static int     b822_ebits_n = 0;
// GPIO interrupt source config (MCU_CFG domain 0x8300C000).
#define B822_GINT_SENSE  (*(volatile uint32_t *)(0x8300C000UL + 0x204))  // edge/level select
#define B822_DEB_D2      (*(volatile uint32_t *)(0x8300C000UL + 0x01C))  // GPIO33 debounce/pol
#define B822_DEB_D1      (*(volatile uint32_t *)(0x8300C000UL + 0x020))  // GPIO34 debounce/pol
#define B822_GINT_BITS   ((1u << 5) | (1u << 6))                 // GPIO33 + GPIO34 in sense/pend
// Arm both sensed pins for edge detection with the config word b822_deb_val (default
// 0x080: either edge, no debounce).
static inline void b822_eint_arm() {
  B822_NVIC_ICER1 = B822_IRQ_D1 | B822_IRQ_D2;   // make sure no ISR fires
  if (b822_sense_level) B822_GINT_SENSE |=  B822_GINT_BITS;   // level
  else                  B822_GINT_SENSE &= ~B822_GINT_BITS;   // edge (bit = 0)
  // Replace the low 10 bits (EN|POL|DUAL|PRESC|CNT) with the requested word.
  B822_DEB_D1 = (B822_DEB_D1 & ~0x3FFu) | (b822_deb_val & 0x3FFu);
  B822_DEB_D2 = (B822_DEB_D2 & ~0x3FFu) | (b822_deb_val & 0x3FFu);
  B822_NVIC_ICPR1 = B822_IRQ_D1 | B822_IRQ_D2;   // clear any stale pending
  // Pinmux words: both pins GPIO (function 8), or the sensed one switched to WIC
  // (function 3) for its listening window.
  uint32_t cur = B822_PINMUX_R;
  b822_mux_gpio   = (cur & ~0xFF0u) | (8u << 4) | (8u << 8);
  b822_mux_wic_d1 = (b822_mux_gpio & ~(0xFu << 8)) | (3u << 8);   // GPIO34 -> WIC6
  b822_mux_wic_d2 = (b822_mux_gpio & ~(0xFu << 4)) | (3u << 4);   // GPIO33 -> WIC5
  B822_PINMUX_R = b822_mux_gpio;
}

static uint32_t B822_M_D1 = (1UL << (34 - 32));   // DATA  = bit2
static uint32_t B822_M_D2 = (1UL << (33 - 32));   // POWER = bit1

// Per-wire push-pull level. The external ~1k pull-up on each pad is needed only while a
// wire is released to listen (see b822_rx_eint): the chip's internal ~47k pull-up is too
// weak to hold it high for the cartridge to pull against, and with no external
// pull-ups the cartridge returned nothing.
static inline void b822_lo(uint32_t mask) { B822_DOUT2_RESET = mask; B822_OE2_SET = mask; }
static inline void b822_hi(uint32_t mask) { B822_DOUT2_SET   = mask; B822_OE2_SET = mask; }
// Complementary phases. phaseA = DATA high / POWER low; phaseB = DATA low / POWER
// high (= idle). Drive the new-low wire first, then the other high, to stay
// complementary (a ns-scale both-low transient is absorbed by chip decoupling).
static inline void b822_phaseA() { b822_lo(B822_M_D2); b822_hi(B822_M_D1); }
static inline void b822_phaseB() { b822_lo(B822_M_D1); b822_hi(B822_M_D2); }
static inline void b822_idle()   { b822_phaseB(); }   // power HIGH, data LOW
// Read both wires: bit0 = D1 (DATA), bit1 = D2 (POWER).
static inline uint32_t b822_read_pair() {
  uint32_t d = B822_DIN2;
  return ((d & B822_M_D1) ? 1u : 0u) | ((d & B822_M_D2) ? 2u : 0u);
}
static inline void b822_pins_out() {
  pinMode(B822_PIN_D1, OUTPUT);
  pinMode(B822_PIN_D2, OUTPUT);
  b822_pullup(B822_M_D1 | B822_M_D2);          // internal pull-UP (and clear pull-down)
  b822_idle();                                 // POWER high / DATA low, both driven
}
static inline void b822_pins_in() {           // release both (truly high-Z)
  pinMode(B822_PIN_D1, INPUT);
  pinMode(B822_PIN_D2, INPUT);
  b822_pullfloat(B822_M_D1 | B822_M_D2);      // both pulls OFF: let the bus FLOAT
}
// Power control: with no ground pad the chip is powered across the pair, so both
// pads LOW = 0 V across = powered DOWN; idle (POWER high / DATA low) = powered up.
static inline void b822_both_low()  { B822_DOUT2_RESET = B822_M_D1 | B822_M_D2; B822_OE2_SET = B822_M_D1 | B822_M_D2; }
static inline void b822_power_off() {          // both pads low = 0 V = powered down
  pinMode(B822_PIN_D1, OUTPUT);
  pinMode(B822_PIN_D2, OUTPUT);
  b822_both_low();
}
static inline void b822_power_on()  { b822_pins_out(); }

// ---------------------------------------------------------------------------
// Timing (DWT cycle-accurate)
// ---------------------------------------------------------------------------
#ifndef B822_CPU_HZ
#define B822_CPU_HZ 192000000UL
#endif
static inline uint32_t b822_ns2cyc(uint32_t ns) {
  return (uint32_t)(((uint64_t)ns * B822_CPU_HZ) / 1000000000ULL);
}
static const uint32_t B822_NS_NARROW = 4700;
static const uint32_t B822_NS_WIDE   = 14200;
static const uint32_t B822_NS_SOF    = 43000;

#ifndef B822_RX_SAMPLE_NS
#define B822_RX_SAMPLE_NS 120         // b822.probe sample period (target; AON reads are slower)
#endif
#ifndef B822_RX_MAX_SAMPLES
#define B822_RX_MAX_SAMPLES 24000     // b822.probe buffer size
#endif
#ifndef B822_NS_RXPHASE
#define B822_NS_RXPHASE 4700          // reply clock half-period (per phase)
#endif
#ifndef B822_RX_PHASES
#define B822_RX_PHASES 300            // clock phases to drive (>= reply bit count)
#endif
#ifndef B822_POWERUP_MS
#define B822_POWERUP_MS 21
#endif
#ifndef B822_GAP_18MS_US
#define B822_GAP_18MS_US 18000
#endif

static inline void b822_dwt_init() {
  B822_DEMCR |= B822_DEMCR_TRCENA;
  B822_DWT_CYCCNT = 0;
  B822_DWT_CTRL |= B822_CYCCNTENA;
}
static inline uint32_t b822_cyc() { return B822_DWT_CYCCNT; }
static inline void b822_wait_until(uint32_t deadline) {
  while ((int32_t)(deadline - B822_DWT_CYCCNT) > 0) { /* spin */ }
}

// ---------------------------------------------------------------------------
// Transmit: SOF then 10-bit words as alternating complementary phases, each
// width-coded. Starts/ends at idle (phaseB). Interrupts must be disabled.
// ---------------------------------------------------------------------------
static inline void b822_tx_frame(const uint8_t *bytes, uint32_t n) {
  uint32_t d = b822_cyc();
  b822_phaseA();                                   // SOF: data high / power low
  d += b822_ns2cyc(B822_NS_SOF); b822_wait_until(d);
  b822_phaseB();                                   // SOF: data low / power high
  d += b822_ns2cyc(B822_NS_SOF); b822_wait_until(d);

  uint8_t phase = 0;                               // first data symbol = phaseA
  for (uint32_t i = 0; i < n; ++i) {
    uint8_t b = bytes[i];
    for (int k = -1; k < 9; ++k) {                 // start 0, 8 data LSB, stop 1
      uint8_t bit = (k < 0) ? 0 : (k < 8 ? ((b >> k) & 1) : 1);
      if (phase == 0) b822_phaseA(); else b822_phaseB();
      d += b822_ns2cyc(bit ? B822_NS_WIDE : B822_NS_NARROW);
      b822_wait_until(d);
      phase ^= 1;
    }
  }
  // Reply framing, driven IMMEDIATELY after the last command bit (no gap):
  //   1) a long phase-A SOF pulse (DATA high / POWER low, ~42 us), then
  //   2) one narrow phase-B lead-in (~4.7 us),
  // after which the reply DATA bits begin on phase A, which is where b822_rx_eint
  // starts clocking (start_phase = 0).
  b822_phaseA();
  d += b822_ns2cyc(B822_NS_SOF);
  b822_wait_until(d);
  b822_phaseB();                                   // narrow lead-in phase
  d += b822_ns2cyc(B822_NS_NARROW);
  b822_wait_until(d);
}

// ---------------------------------------------------------------------------
// Receive: the printer keeps clocking the complementary pair after the command (that holds
// power across the pads -- there is no ground) and reads one reply bit per phase. Each
// phase it drives the LOW wire low, precharges the other wire high for the first 1/4 of
// the phase and then RELEASES it (the external pull-up holds it high), so the cartridge
// can signal by briefly PULLING it DOWN (notch = 1, clean = 0). Phase 0 senses DATA (D1),
// phase 1 POWER (D2), alternating.
//
// The notch is caught by the edge latch (see above): the sensed pad is muxed to WIC for
// its sense window, the latch is cleared once the wire is released, and the pending bit is
// read at the end of the phase. The per-phase bits are decoded as 10-bit words (start 0,
// 8 data LSB-first, stop 1), aligned on the offset that yields the most well-formed
// words, into `out`. Returns the byte count. Interrupts must be disabled.
// ---------------------------------------------------------------------------
static inline int b822_rx_eint(uint32_t nphases, uint32_t phase_ns, uint8_t start_phase,
                               uint8_t *out, int outmax) {
  b822_ebits_n = 0;
  if (nphases == 0) { b822_idle(); return 0; }    // no-reply command (e.g. 0D): stay at idle,
                                                  // no RX and no extra low on POWER
  uint8_t *bits = b822_ebits;                     // kept global so the host can fetch raw bits
  const int bitscap = (int)sizeof(b822_ebits);
  const uint32_t phase_cyc = b822_ns2cyc(phase_ns);
  const uint32_t pre_cyc   = phase_cyc / 4;
  const uint32_t settle_cyc = b822_ns2cyc(b822_settle_ns);
  uint8_t phase = start_phase & 1;
  int nb = 0;
  b822_pullup(B822_M_D1 | B822_M_D2);
  B822_DOUT2_RESET = B822_M_D1 | B822_M_D2;       // both latched low: each phase raises only the sensed wire
  b822_eint_arm();
  for (uint32_t p = 0; p < nphases && nb < bitscap; ++p) {
    uint32_t himask = (phase == 0) ? B822_M_D1 : B822_M_D2;   // released / sensed wire
    uint32_t lomask = (phase == 0) ? B822_M_D2 : B822_M_D1;   // driven-low / clock wire
    uint32_t irqbit = (phase == 0) ? B822_IRQ_D1 : B822_IRQ_D2;
    // Re-anchor to the current cycle each phase so one lasts EXACTLY phase_ns (accumulating
    // a deadline let it fall behind and collapse once the loop time drifted).
    uint32_t pstart = b822_cyc();
    uint32_t pend = pstart + phase_cyc;
    B822_OE2_SET = lomask;                                  // drive the clock wire low
    B822_DOUT2_SET = himask; B822_OE2_SET = himask;         // precharge sensed wire high
    while ((int32_t)((pstart + pre_cyc) - b822_cyc()) > 0) { }
    B822_OE2_RESET = himask;                                // release (pull-up holds high)
    // The edge unit only sees a pad muxed to WIC (function 3); that disconnects the GPIO
    // driver, which is fine here because the sensed wire is released anyway.
    B822_PINMUX_R = (phase == 0) ? b822_mux_wic_d1 : b822_mux_wic_d2;
    if (settle_cyc) { uint32_t st = b822_cyc() + settle_cyc;
      while ((int32_t)(st - b822_cyc()) > 0) { } }          // optional settle (default 0)
    B822_NVIC_ICPR1 = irqbit;                               // clear: only edges from here on count
    while ((int32_t)(pend - b822_cyc()) > 0) { }            // sense window: a notch latches
    bits[nb++] = (B822_NVIC_ISPR1 & irqbit) ? 1 : 0;        // notch seen this phase?
    B822_PINMUX_R = b822_mux_gpio;                          // back to GPIO so we can drive again
    B822_DOUT2_RESET = himask;                              // re-latch low for next phase
    phase ^= 1;
  }
  b822_idle();                                              // rest at POWER-high idle: the chip
                                                            // stays powered between commands
  b822_ebits_n = nb;
  int best_ph = 0, best_n = -1;                             // align to 10-bit words
  for (int ph = 0; ph < 10; ++ph) {
    int good = 0;
    for (int k = ph; k + 10 <= nb; k += 10)
      if (bits[k] == 0 && bits[k + 9] == 1) ++good;
    if (good > best_n) { best_n = good; best_ph = ph; }
  }
  int count = 0;
  for (int k = best_ph; k + 10 <= nb && count < outmax; k += 10) {
    uint8_t v = 0;
    for (int b = 0; b < 8; ++b) if (bits[k + 1 + b]) v |= (1u << b);
    out[count++] = v;
  }
  return count;
}

// Send one frame and decode its reply: powers the pins up to idle, then runs TX and RX
// back to back with interrupts off. Returns the number of reply bytes.
static inline int b822_txrx(const uint8_t *frame, uint32_t flen, uint32_t nphases,
                            uint32_t phase_ns, uint8_t *reply, int outmax) {
  b822_pins_out();                                 // idle = POWER high / DATA low
  __disable_irq();
  b822_tx_frame(frame, flen);
  int rn = b822_rx_eint(nphases, phase_ns, 0, reply, outmax);
  __enable_irq();
  return rn;
}

// ---------------------------------------------------------------------------
// RLE of a capture: [state, runlen, ...] (state bit0=D1, bit1=D2). Used by b822.probe.
// ---------------------------------------------------------------------------
#ifndef B822_RLE_MAX_PAIRS
#define B822_RLE_MAX_PAIRS 700
#endif
static inline void b822_emit_rle(JsonArray arr, const uint8_t *buf, uint32_t n) {
  if (n == 0) return;
  uint8_t cur = buf[0]; uint32_t run = 1, pairs = 0;
  for (uint32_t i = 1; i < n; ++i) {
    if (buf[i] == cur) { ++run; continue; }
    if (pairs++ >= B822_RLE_MAX_PAIRS) return;
    arr.add(cur); arr.add(run);
    cur = buf[i]; run = 1;
  }
  if (pairs < B822_RLE_MAX_PAIRS) { arr.add(cur); arr.add(run); }
}
static uint8_t b822_probe_buf[B822_RX_MAX_SAMPLES];

// ---------------------------------------------------------------------------
// JSON helpers
// ---------------------------------------------------------------------------
static inline int b822_copy_bytes(JsonVariantConst v, uint8_t *dst, int cap) {
  if (!v.is<JsonArrayConst>()) return -1;
  JsonArrayConst a = v.as<JsonArrayConst>();
  if ((int)a.size() > cap) return -1;
  int i = 0;
  for (JsonVariantConst e : a) {
    if (!e.is<int>() || e.as<int>() < 0 || e.as<int>() > 255) return -1;
    dst[i++] = (uint8_t)e.as<int>();
  }
  return i;
}
static inline void b822_add_bytes(JsonDocument &resp, const char *key,
                                  const uint8_t *p, int n) {
  JsonArray a = resp.createNestedArray(key);
  for (int i = 0; i < n; ++i) a.add(p[i]);
}
// Add the last receive's raw per-phase notch bits as a '0'/'1' string under `key`.
template <typename T>
static inline void b822_put_ebits(T &obj, const char *key) {
  if (b822_ebits_n <= 0) return;
  static char ebuf[sizeof(b822_ebits) + 1];
  int m = b822_ebits_n < (int)sizeof(b822_ebits) ? b822_ebits_n : (int)sizeof(b822_ebits);
  for (int q = 0; q < m; ++q) ebuf[q] = b822_ebits[q] ? '1' : '0';
  ebuf[m] = 0;
  obj[key] = String(ebuf);                         // String => copied into the document
}
// Edge-unit tuning, shared by every op that receives: "eint_deb" (trigger word) and
// "eint_settle" (ns between release and latch clear).
static inline void b822_read_eint_params(JsonDocument &req) {
  b822_deb_val   = req["eint_deb"]    | 0x080;
  b822_settle_ns = req["eint_settle"] | 0;
}

// ---------------------------------------------------------------------------
// Ops
// ---------------------------------------------------------------------------
// op "b822.config": swap which pin is DATA vs POWER. in: data_pin, power_pin.
static inline void b822_op_config(JsonDocument &req, JsonDocument &resp) {
  int dp = req["data_pin"]  | (int)B822_PIN_D1;
  int pp = req["power_pin"] | (int)B822_PIN_D2;
  auto maskfor = [](int pin) -> uint32_t {
    int gpio = (pin == 4) ? 34 : (pin == 5) ? 33 : (pin == 6) ? 37 : (pin == 7) ? 36 : 34;
    return (1UL << (gpio - 32));
  };
  B822_PIN_D1 = dp; B822_M_D1 = maskfor(dp);
  B822_PIN_D2 = pp; B822_M_D2 = maskfor(pp);
  b822_pins_out();
  resp["data_pin"] = B822_PIN_D1; resp["power_pin"] = B822_PIN_D2;
}

// op "b822.power": control the cartridge supply. in: state "on"|"off", optional
// off_ms (power-cycle: off for off_ms then back on). out: state.
static inline void b822_op_power(JsonDocument &req, JsonDocument &resp) {
  const char *st = req["state"] | "on";
  int off_ms = req["off_ms"] | 0;
  if (!strcmp(st, "off")) { b822_power_off(); resp["state"] = "off"; return; }
  if (off_ms > 0) { b822_power_off(); delay(off_ms); }
  b822_power_on();
  resp["state"] = "on";
}

// op "b822.frame": power up, send one frame, decode its reply.
//   in: frame [bytes], optional rx_phases (RX clock phases; 0 = no reply expected),
//       rx_phase_ns, raw, powerup_ms, reset_ms, eint_deb, eint_settle
//   out: reply [bytes], nphases, ebits (with raw)
static inline void b822_op_frame(JsonDocument &req, JsonDocument &resp) {
  static uint8_t frame[64], reply[48];
  int flen = b822_copy_bytes(req["frame"], frame, sizeof(frame));
  if (flen <= 0) { resp["ok"] = false; resp["error"] = "invalid frame"; return; }
  bool raw = req["raw"] | false;
  b822_read_eint_params(req);
  int powerup = req["powerup_ms"] | B822_POWERUP_MS;  // chip is off when idle
  int reset_ms = req["reset_ms"] | 0;              // power-cycle before the frame
  uint32_t nphases = req["rx_phases"] | B822_RX_PHASES;
  uint32_t phase_ns = req["rx_phase_ns"] | B822_NS_RXPHASE;
  if (reset_ms > 0) { b822_power_off(); delay(reset_ms); }
  b822_pins_out();
  if (powerup > 0) delay(powerup);
  int rn = b822_txrx(frame, flen, nphases, phase_ns, reply, sizeof(reply));
  b822_power_off();                                // power down when idle
  resp["nphases"] = nphases;
  b822_add_bytes(resp, "reply", reply, rn);
  if (raw) b822_put_ebits(resp, "ebits");
}

// op "b822.session": power up, CMD12 -> Ca, 18 ms, CMD11 -> Cb.
//   in: frame12, frame11 [bytes], optional rx_phases, rx_phase_ns, raw, powerup_ms,
//       reset_ms, eint_deb, eint_settle
//   out: ca, cb [bytes], nphases, ebits_a / ebits_b (with raw)
static inline void b822_op_session(JsonDocument &req, JsonDocument &resp) {
  static uint8_t f12[64], f11[64], ca[48], cb[48];
  int l12 = b822_copy_bytes(req["frame12"], f12, sizeof(f12));
  int l11 = b822_copy_bytes(req["frame11"], f11, sizeof(f11));
  if (l12 <= 0 || l11 <= 0) {
    resp["ok"] = false; resp["error"] = "invalid frame12/frame11"; return;
  }
  bool raw = req["raw"] | false;
  b822_read_eint_params(req);
  int powerup = req["powerup_ms"] | B822_POWERUP_MS;
  int reset_ms = req["reset_ms"] | 0;              // power-cycle before the session
  uint32_t nphases = req["rx_phases"] | B822_RX_PHASES;
  uint32_t phase_ns = req["rx_phase_ns"] | B822_NS_RXPHASE;
  if (reset_ms > 0) { b822_power_off(); delay(reset_ms); }
  b822_pins_out();
  if (powerup > 0) delay(powerup);
  int na = b822_txrx(f12, l12, nphases, phase_ns, ca, sizeof(ca));
  if (raw) b822_put_ebits(resp, "ebits_a");        // before the next receive overwrites them
  delay((B822_GAP_18MS_US + 999) / 1000);          // POWER stays high through the gap
  int nb = b822_txrx(f11, l11, nphases, phase_ns, cb, sizeof(cb));
  if (raw) b822_put_ebits(resp, "ebits_b");
  b822_power_off();                                // power down when idle
  resp["nphases"] = nphases;
  b822_add_bytes(resp, "ca", ca, na);
  b822_add_bytes(resp, "cb", cb, nb);
}

// op "b822.sequence": power up ONCE, then send several frames in order keeping
// the chip powered between them (it needs the 0D.., 5D.. init before 5C.. read-ID),
// decoding each reply; power down at the end.
//   in: frames [[bytes],...], optional rx_phases_list (one RX phase count per frame; the
//       reply length is command-specific, 0 = no reply), rx_phases (default for frames
//       past the list), rx_phase_ns, raw, powerup_ms, reset_ms, gap_ms, eint_deb,
//       eint_settle
//   out: frames: [{nphases, reply[], ebits?}, ...]
static inline void b822_op_sequence(JsonDocument &req, JsonDocument &resp) {
  JsonArrayConst frames = req["frames"].as<JsonArrayConst>();
  if (frames.isNull() || frames.size() == 0) {
    resp["ok"] = false; resp["error"] = "no frames"; return;
  }
  bool raw = req["raw"] | false;
  b822_read_eint_params(req);
  JsonArrayConst rpl = req["rx_phases_list"].as<JsonArrayConst>();
  int powerup = req["powerup_ms"] | B822_POWERUP_MS;
  int reset_ms = req["reset_ms"] | 0;
  uint32_t nphases = req["rx_phases"] | B822_RX_PHASES;
  uint32_t phase_ns = req["rx_phase_ns"] | B822_NS_RXPHASE;
  uint32_t gap_ms = req["gap_ms"] | 21;            // POWER-high dwell between commands (~21 ms)

  if (reset_ms > 0) { b822_power_off(); delay(reset_ms); }
  b822_pins_out();
  if (powerup > 0) delay(powerup);

  JsonArray out = resp.createNestedArray("frames");
  static uint8_t frame[64], reply[48];
  int idx = 0;
  for (JsonVariantConst fv : frames) {
    int flen = b822_copy_bytes(fv, frame, sizeof(frame));
    JsonObject fo = out.createNestedObject();
    if (flen <= 0) { fo["error"] = "bad frame"; ++idx; continue; }
    if (idx > 0 && gap_ms > 0) delay(gap_ms);      // POWER-high gap between commands
    uint32_t this_nphases = nphases;               // per-command RX clock count...
    if (!rpl.isNull() && idx < (int)rpl.size())
      this_nphases = rpl[idx] | nphases;           // ...from the host's per-frame list
    fo["nphases"] = this_nphases;
    int rn = b822_txrx(frame, flen, this_nphases, phase_ns, reply, sizeof(reply));
    JsonArray ra = fo.createNestedArray("reply");
    for (int i = 0; i < rn; ++i) ra.add(reply[i]);
    if (raw) b822_put_ebits(fo, "ebits");
    ++idx;
  }
  b822_power_off();
}

// op "b822.probe": observe the wires at rest (released).
static inline void b822_op_probe(JsonDocument &req, JsonDocument &resp) {
  int want = req["samples"] | 4000;
  if (want > B822_RX_MAX_SAMPLES) want = B822_RX_MAX_SAMPLES;
  b822_pins_in();
  __disable_irq();
  const uint32_t period = b822_ns2cyc(B822_RX_SAMPLE_NS);
  uint32_t d = b822_cyc();
  for (int i = 0; i < want; ++i) { b822_probe_buf[i] = (uint8_t)b822_read_pair(); d += period; b822_wait_until(d); }
  __enable_irq();
  resp["samples"] = want;
  b822_emit_rle(resp.createNestedArray("rle"), b822_probe_buf, want);
}

// op "b822.io": set/read the wires. in: d1, d2 (levels) or mode "release".
static inline void b822_op_io(JsonDocument &req, JsonDocument &resp) {
  const char *mode = req["mode"] | "drive";
  if (!strcmp(mode, "release")) { b822_pins_in(); }
  else {
    pinMode(B822_PIN_D1, OUTPUT); pinMode(B822_PIN_D2, OUTPUT);
    b822_pullup(B822_M_D1 | B822_M_D2);
    int d1 = req["d1"] | 0, d2 = req["d2"] | 1;  // 1 = drive high, 0 = drive low
    if (d1) b822_hi(B822_M_D1); else b822_lo(B822_M_D1);
    if (d2) b822_hi(B822_M_D2); else b822_lo(B822_M_D2);
  }
  for (volatile int i = 0; i < 5000; ++i) { }
  resp["d1"] = digitalRead(B822_PIN_D1);
  resp["d2"] = digitalRead(B822_PIN_D2);
  resp["pair"] = b822_read_pair();
}

// op "b822.peek": read a 32-bit MMIO register.
static inline void b822_op_peek(JsonDocument &req, JsonDocument &resp) {
  // as<uint32_t>(), NOT `| 0`: the default-operator path checks for a signed int, so any
  // address >= 0x80000000 (all the peripheral space) silently became 0.
  uint32_t addr = req["addr"].as<uint32_t>();
  resp["addr"] = addr;
  resp["value"] = *(volatile uint32_t *)addr;
}

// op "b822.poke": write a 32-bit MMIO register (bring-up aid, pairs with peek).
// in: addr, value. out: addr, value (read back).
static inline void b822_op_poke(JsonDocument &req, JsonDocument &resp) {
  uint32_t addr = req["addr"].as<uint32_t>();     // unsigned: see b822_op_peek
  uint32_t val  = req["value"].as<uint32_t>();
  *(volatile uint32_t *)addr = val;
  resp["addr"] = addr;
  resp["value"] = *(volatile uint32_t *)addr;
}

// op "b822.eint": edge-latch self-test (no cartridge traffic needed). Arms the latch,
// then for each wire pulses it LOW for each requested width and reports whether the
// pending bit set. in: widths_ns [..] (default [3000,500,200,100,60]), deb, level, mux,
// lat, wic. out: regs (sense, deb_d1, deb_d2 as read back) and per-wire latched[] per width.
static inline void b822_op_eint(JsonDocument &req, JsonDocument &resp) {
  b822_pins_out();
  b822_pullup(B822_M_D1 | B822_M_D2);
  b822_deb_val = req["deb"] | 0x080;              // edge-unit config word to test
  b822_sense_level = req["level"] | 0;            // 0 = edge (default), 1 = level
  b822_eint_arm();
  int mux = req["mux"] | 8;                       // pinmux function for the test: 8=GPIO, 3=WIC
  if (mux == 3)
    B822_PINMUX_R = (b822_mux_gpio & ~0xFF0u) | (3u << 4) | (3u << 8);
  resp["mux_reg"] = B822_PINMUX_R;
  resp["sense"]  = B822_GINT_SENSE;
  resp["deb_d1"] = B822_DEB_D1;
  resp["deb_d2"] = B822_DEB_D2;
  JsonArray w = resp.createNestedArray("widths_ns");
  JsonArray l1 = resp.createNestedArray("latched_d1");
  JsonArray l2 = resp.createNestedArray("latched_d2");
  JsonArray p1 = resp.createNestedArray("padlow_d1");   // pad level read during the low pulse
  JsonArray p2 = resp.createNestedArray("padlow_d2");   // (1 = driver did NOT reach the pad)
  uint32_t defaults[5] = {3000, 500, 200, 100, 60};
  JsonArrayConst rq = req["widths_ns"].as<JsonArrayConst>();
  uint32_t n = rq.isNull() ? 5 : (rq.size() > 8 ? 8 : rq.size());
  for (uint32_t i = 0; i < n; ++i) {
    uint32_t ns = rq.isNull() ? defaults[i] : (uint32_t)rq[i].as<int>();
    w.add(ns);
    for (int wire = 0; wire < 2; ++wire) {
      uint32_t m = wire ? B822_M_D2 : B822_M_D1;
      uint32_t irq = wire ? B822_IRQ_D2 : B822_IRQ_D1;
      B822_DOUT2_SET = m; B822_OE2_SET = m;            // wire high (push-pull)
      for (volatile int k = 0; k < 2000; ++k) { }      // settle
      B822_NVIC_ICPR1 = irq;                           // clear pending
      __disable_irq();
      uint32_t t = b822_cyc() + b822_ns2cyc(ns);
      B822_DOUT2_RESET = m;                            // fall
      b822_wait_until(t);
      uint32_t lowlvl = (B822_DIN2 & m) ? 1 : 0;       // pad level during the low pulse
      B822_DOUT2_SET = m;                              // rise (notch complete)
      __enable_irq();
      bool hit = (B822_NVIC_ISPR1 & irq) != 0;
      (wire ? l2 : l1).add(hit ? 1 : 0);
      (wire ? p2 : p1).add(lowlvl);
    }
  }
  resp["ispr1"] = B822_NVIC_ISPR1;
  // Edge-type / latency probe (request "lat": true): for each wire, generate ONLY a falling
  // edge, then ONLY a rising edge, and time how long the pending bit takes to set (-1 =
  // never within ~20 us). Separates polarity (which edge latches) from latency.
  if (req["lat"] | false) {
    JsonArray lf1 = resp.createNestedArray("lat_fall_ns_d1");
    JsonArray lr1 = resp.createNestedArray("lat_rise_ns_d1");
    JsonArray lf2 = resp.createNestedArray("lat_fall_ns_d2");
    JsonArray lr2 = resp.createNestedArray("lat_rise_ns_d2");
    for (int wire = 0; wire < 2; ++wire) {
      uint32_t m = wire ? B822_M_D2 : B822_M_D1;
      uint32_t irq = wire ? B822_IRQ_D2 : B822_IRQ_D1;
      for (int rep = 0; rep < 3; ++rep) {
        for (int edge = 0; edge < 2; ++edge) {        // 0 = fall test, 1 = rise test
          if (edge == 0) { B822_DOUT2_SET = m; B822_OE2_SET = m; }     // start high
          else           { B822_DOUT2_RESET = m; B822_OE2_SET = m; }   // start low
          for (volatile int k = 0; k < 3000; ++k) { }                  // let it settle
          B822_NVIC_ICPR1 = irq;
          for (volatile int k = 0; k < 3000; ++k) { }                  // drain any lagging edge
          B822_NVIC_ICPR1 = irq;
          __disable_irq();
          uint32_t t0 = b822_cyc();
          if (edge == 0) B822_DOUT2_RESET = m; else B822_DOUT2_SET = m;  // the one edge
          int32_t lat = -1;
          while ((uint32_t)(b822_cyc() - t0) < 4000u) {
            if (B822_NVIC_ISPR1 & irq) { lat = (int32_t)((uint64_t)(b822_cyc() - t0) * 1000 / 192); break; }
          }
          __enable_irq();
          JsonArray &dst = wire ? (edge ? lr2 : lf2) : (edge ? lr1 : lf1);
          dst.add(lat);
        }
      }
    }
  }
  // WIC-mode rising-edge probe (request "wic": true). In WIC mode the GPIO driver is
  // disconnected, so an edge can't be driven there directly -- but the pad can be driven
  // low in GPIO mode and then the mux switched to WIC: the driver lets go and the pull-up
  // produces a clean rising edge while the edge unit is watching. Reports the mux-switch ->
  // pending latency (ns, includes the mux write itself); -1 = never latched.
  if (req["wic"] | false) {
    JsonArray w1 = resp.createNestedArray("wic_rise_ns_d1");
    JsonArray w2 = resp.createNestedArray("wic_rise_ns_d2");
    for (int wire = 0; wire < 2; ++wire) {
      uint32_t m = wire ? B822_M_D2 : B822_M_D1;
      uint32_t irq = wire ? B822_IRQ_D2 : B822_IRQ_D1;
      uint32_t wicmux = wire ? b822_mux_wic_d2 : b822_mux_wic_d1;
      for (int rep = 0; rep < 3; ++rep) {
        B822_PINMUX_R = b822_mux_gpio;
        B822_DOUT2_RESET = m; B822_OE2_SET = m;                   // pad driven low
        for (volatile int k = 0; k < 3000; ++k) { }
        B822_NVIC_ICPR1 = irq;
        for (volatile int k = 0; k < 3000; ++k) { }
        B822_NVIC_ICPR1 = irq;
        __disable_irq();
        uint32_t t0 = b822_cyc();
        B822_PINMUX_R = wicmux;                                   // driver lets go -> pad rises
        int32_t lat = -1;
        while ((uint32_t)(b822_cyc() - t0) < 4000u) {
          if (B822_NVIC_ISPR1 & irq) { lat = (int32_t)((uint64_t)(b822_cyc() - t0) * 1000 / 192); break; }
        }
        __enable_irq();
        (wire ? w2 : w1).add(lat);
        B822_PINMUX_R = b822_mux_gpio;
        B822_DOUT2_SET = m;                                       // leave the pad high
      }
    }
  }
  B822_PINMUX_R = b822_mux_gpio;                  // never leave the pads muxed to WIC (no GPIO drive)
  b822_power_off();
}

static inline void b822_begin() {
  b822_dwt_init();
  b822_pullup(B822_M_D1 | B822_M_D2);            // internal pull-UP (and clear pull-down)
  b822_power_off();                              // idle = powered down (both pads low)
}

static inline bool b822_dispatch(const char *op, JsonDocument &req, JsonDocument &resp) {
  if (!strcmp(op, "b822.frame"))    { b822_op_frame(req, resp);    return true; }
  if (!strcmp(op, "b822.session"))  { b822_op_session(req, resp);  return true; }
  if (!strcmp(op, "b822.sequence")) { b822_op_sequence(req, resp); return true; }
  if (!strcmp(op, "b822.power"))    { b822_op_power(req, resp);    return true; }
  if (!strcmp(op, "b822.probe"))    { b822_op_probe(req, resp);    return true; }
  if (!strcmp(op, "b822.config"))   { b822_op_config(req, resp);   return true; }
  if (!strcmp(op, "b822.io"))       { b822_op_io(req, resp);       return true; }
  if (!strcmp(op, "b822.peek"))     { b822_op_peek(req, resp);     return true; }
  if (!strcmp(op, "b822.poke"))     { b822_op_poke(req, resp);     return true; }
  if (!strcmp(op, "b822.eint"))     { b822_op_eint(req, resp);     return true; }
  return false;
}

#endif  // B822_H
