---
gitea: none
include_toc: true
---

# ProtoGate

ProtoGate turns a LinkIt 7697 into a remotely controlled hardware interface. A host sends newline-delimited JSON commands over the board's USB-to-UART serial port or a Wi-Fi TCP connection. The firmware executes a peripheral operation and returns one JSON response line.

The initial firmware supports I²C and SPI. The host package provides the same API over either transport, plus a small command line interface. Commands are intentionally explicit: pin numbers, bus settings, addresses, and payload bytes are supplied by the caller.

## Hardware and toolchain

The LinkIt 7697 HDK exposes USB through an onboard CP2102N USB-to-UART bridge; this is a serial command channel, not native USB. The board includes Wi-Fi, I²C, and SPI. The firmware targets the MediaTek LinkIt 7697 Arduino add-on and uses its `LWiFi` API. Install that board package in Arduino IDE, then install ArduinoJson 6.

## I/O pins and connections

Pin numbers in requests use the board's **P labels**, not raw chip GPIO numbers.
The current firmware uses the following connections:

| Protocol | Signal | Board pin | Chip GPIO |
| --- | --- | --- | --- |
| I²C | SCL — clock | P8 | GPIO27 |
| I²C | SDA — data | P9 | GPIO28 |
| SPI | MOSI — board to peripheral | P11 | GPIO29 |
| SPI | MISO — peripheral to board | P12 | GPIO30 |
| SPI | SCK — clock | P13 | GPIO31 |
| SPI | CS — chip select | P10 suggested | GPIO32 |
| USB serial | USB cable | Micro-USB connector | Onboard UART bridge |
| Wi-Fi TCP | Wireless | No external pins | Built-in antenna |

These assignments follow the board package and the
[LinkIt 7697 pinout](https://static.chipdip.ru/lib/834/DOC003834652.pdf).
Connect peripheral GND to board GND and use **3.3 V logic**.

- **I²C:** configured at 100 kHz. SDA and SCL need pull-ups to 3.3 V; check whether the peripheral already provides them.
- **SPI:** use `"cs_pin":10` to select P10. Chip select is active-low; without `cs_pin`, firmware does not drive a chip-select pin. The installed board package (0.10.21) implements SPI in software and does not honor the requested `clock_hz` as an exact bus frequency.
- **USB serial:** 115200 baud. The included udev rule creates `/dev/linkit7697` on Linux.
- **Wi-Fi TCP:** port 4242. Wi-Fi stays disabled while the configured SSID is empty.

UART peripherals, GPIO commands, and ADC commands are not yet exposed by ProtoGate.

## Firmware setup

1. Open `firmware/linkit7697/linkit7697.ino` in Arduino IDE with the LinkIt 7697 board selected.
2. Install ArduinoJson 6 from Library Manager.
3. Copy `wifi_config.example.h` to `wifi_config.h`. Leave the defaults for USB serial only, or set the network name and password for Wi-Fi, then flash. The local configuration is ignored by Git.
4. Open the serial monitor at 115200 baud or connect to the board's IP address on TCP port 4242.

The Wi-Fi server starts only when a non-empty SSID is configured. Serial remains available regardless.

### Arduino CLI on Linux

The original MediaTek package server is unavailable. The local index below
points to the official MediaTek GitHub release archives, with SHA-256 checksums.
Run these commands from the repository root (the upload tool requires Python 2.7,
and includes its own Python modules):

```sh
~/bin/arduino-cli config add board_manager.additional_urls "file://$PWD/firmware/toolchain/package_linkit_archive_index.json"
~/bin/arduino-cli core update-index
~/bin/arduino-cli core install LinkIt:linkit_rtos@0.10.21
~/bin/arduino-cli lib install ArduinoJson@6.21.5
cp -n firmware/linkit7697/wifi_config.example.h firmware/linkit7697/wifi_config.h
~/bin/arduino-cli compile --fqbn LinkIt:linkit_rtos:linkit_7697 --build-path /tmp/protogate-linkit-build firmware/linkit7697
~/bin/arduino-cli upload --fqbn LinkIt:linkit_rtos:linkit_7697 --port /dev/ttyUSB0 --input-dir /tmp/protogate-linkit-build firmware/linkit7697
```

Install the udev rule to grant the active local user access and create the stable
`/dev/linkit7697` device name:

```sh
sudo install -m 0644 firmware/toolchain/70-linkit7697.rules /etc/udev/rules.d/70-linkit7697.rules
sudo udevadm control --reload-rules
```

Unplug and reconnect the board after installing the rule. It matches USB ID
`10c4:ea60` and serial number `0001`, as reported by the connected board.
For access outside an active local desktop session, add your user to `dialout`
and log in again. For temporary access to the current connection, use
`sudo setfacl -m u:"$USER":rw /dev/ttyUSB0`.

## Host client

Install the local package with `python -m pip install -e '.[serial]'`. Serial support uses pySerial; Wi-Fi uses Python's standard library.

```python
from protogate import ProtoGate

with ProtoGate.serial("/dev/ttyUSB0") as gate:
    result = gate.i2c_write_read(address=0x48, write=[0x00], read_count=2)
    print(result)
```

For Wi-Fi, use `ProtoGate.tcp("192.168.1.50")`. Both forms expose `request`, `i2c_write`, `i2c_read`, `i2c_write_read`, and `spi_transfer`. The CLI can send a command from a JSON file or stdin:

```sh
protogate --serial /dev/ttyUSB0 '{"op":"i2c.scan"}'
protogate --tcp 192.168.1.50 '{"op":"i2c.write","address":72,"data":[0]}'
```

## Wire protocol

Each request and response is a UTF-8 JSON object terminated by `\n`. One connection can carry multiple requests; each request receives one response. Successful responses contain `ok: true`; failures contain `ok: false` and an `error` string. Requests may include an `id`, which is copied to the response.

Supported operations:

| Operation | Fields | Result |
| --- | --- | --- |
| `system.info` | — | Firmware and board capabilities |
| `i2c.scan` | optional `address_start`, `address_end` | `devices` array |
| `i2c.write` | `address`, `data` byte array | `written` count |
| `i2c.read` | `address`, `length` | `data` byte array |
| `i2c.write_read` | `address`, `write`, `length` | `data` byte array |
| `spi.transfer` | `data` byte array, optional `clock_hz`, `mode`, `bit_order`, `cs_pin` | received `data` byte array |
| `b822.frame` | `frame` byte array, optional `rx_phases`, `raw` | `reply` bytes, `nphases`, optional `ebits` |
| `b822.session` | `frame12`, `frame11` byte arrays, optional `rx_phases`, `raw` | `ca`, `cb` bytes, `nphases`, optional `ebits_a`/`ebits_b` |
| `b822.sequence` | `frames` (list of byte arrays), optional `rx_phases_list`, `raw`, `powerup_ms`, `gap_ms` | `frames[]`, each with `reply` bytes (and `ebits` with `raw`) |
| `b822.peek` / `b822.poke` | `addr` (and `value`) as unsigned 32-bit | `addr`, `value` — bring-up aids for raw register access |
| `b822.eint` | optional `deb`, `level`, `mux`, `widths_ns`, `lat`, `wic` | edge-latch characterisation results (bring-up aid) |

SPI requests default to `clock_hz: 1000000`, mode 0, MSB first, and no chip-select pin. The board package's software SPI implementation does not honor `clock_hz` as an exact bus frequency. When `cs_pin` is supplied, firmware drives it low for the transaction and high afterward. I²C uses the board's default `Wire` pins and 100 kHz clock. Transfers are limited to 96 bytes per request in this first firmware version.

## OKI MC363 / B822 CRUM bus (`b822.*`)

This firmware can act as the **printer** (tier A) on the two-wire OKI MC363/B822
printer↔cartridge CRUM authentication bus, driving a real cartridge and reading
its responses. It emulates the printer so Ca/Cb replies can be collected for
arbitrary Pa/Pb — a step toward emulating the cartridge.

A session is: printer sends a **CMD12** frame (Pa + ctrla); the cartridge replies
**Ca** within 43 µs; after an 18 ms gap the printer sends a **CMD11** frame
(Pb + ctrlb) and the cartridge replies **Cb**.

**Wire layer** (matches the `la_convert` `mc363_b822` decoder, the real chip
firmware, and bench observation). The cartridge has **only two pads and no
ground** — a **complementary pair** powered across itself: `D2 = POWER` idles HIGH
(powers the chip), `D1 = DATA` idles LOW. The two wires swing in opposite phase;
each symbol is one phase whose **width** codes a bit (narrow ≈ 4.7 µs = 0, wide
≈ 14.2 µs = 1). The POWER wire is held HIGH ~21 ms before the first command. A
frame opens with **SOF = DATA high / POWER low (~43 µs) then the reverse** (the
power wire dips low then high). Bytes are 10-bit words (start 0, 8 data LSB-first,
stop 1). To read a reply the printer keeps clocking the same complementary pair
(~4.7 µs phases) and, in each phase, **releases the high wire**; the cartridge answers
by briefly **pulling that released wire down** — a notch of roughly 0.1–0.2 µs
(notch = 1, clean = 0). Everywhere else the printer drives **push-pull**, and it
holds POWER high between commands so the chip stays powered for a whole session.
The notches are far shorter than a polled GPIO read (~0.35 µs per sample on the
AON domain), so polling would miss most of them — see **Replies** for how the board
catches them in hardware.

**Pins.** `D1 = DATA = P4` (GPIO34), `D2 = POWER = P5` (GPIO33) — both on the AON
GPIO domain bank, so one register read (`DIN2`) samples both wires. Use **3.3 V
logic**. There is no ground pad: board GND is not wired to the cartridge; the chip
is powered across the two pads. `b822.config` swaps the DATA/POWER pin assignment
at runtime if your wiring is reversed. **Do not** jumper the loopback self-test to
P6/P7 — one is a boot-strap pin and a jumper there wedges the board into its
bootloader.

**Required: a 1 kΩ pull-up from each pad to 3.3 V** (both D1/DATA and D2/POWER).
The firmware drives the pair push-pull, but while it releases a wire to listen,
something must hold that wire high for the cartridge to pull down. The chip's internal
pull-up (~47 kΩ) is too weak for that: with the external resistors removed, the
released wire fell low within about a microsecond and the cartridge returned nothing,
even with the internal pull-up enabled and its pull-down cleared. With 1 kΩ on both
pads a real cartridge answers. Only 1 kΩ has been tried.

| Signal | Board pin | Chip GPIO | Idle | External pull-up |
| --- | --- | --- | --- | --- |
| D1 DATA | P4 | GPIO34 | low | 1 kΩ to 3.3 V |
| D2 POWER | P5 | GPIO33 | high | 1 kΩ to 3.3 V |

**Frames are byte-level.** The host supplies the complete frame byte stream (the
bytes after the SOF, e.g. `03 12 <uid8> <21-byte body>`); the firmware owns the
precise timing, 10-bit framing, and notch decode. All Pa/ctrl↔body encoding lives
on the host, in `protogate.packer`: the 21-byte body bit-packs a 3-bit "common"
header into every one of 16 raw bytes alongside 5 payload bits each (16×5 = 80
bits = the 10-byte Pa), plus 4 literal ctrl bytes and 1 free byte (`p_unk`).
`b822.build_auth_frame(cmd, pa, ctrl, uid, p_unk, prt_common)` builds a frame this
way from a chosen Pa/ctrl (`prt_common`/`p_unk` can be any fixed value — both vary
close to uniformly at random across real captures, uncorrelated with ctrl or Pa).
This is confirmed against all 130 real rows of `tmp/a112_captured_samples.txt` —
the raw capture file that feeds the separate `a112_chip` project's `packer.rb` to
build its `captured/*.txt` dataset — which unpack and round-trip through it
exactly (see `host/tests/test_b822_packer.py`). The reference `.sr` captures in
this repo are a *different* exception: they replay one reused CMD12 body across
different UIDs and don't fit this model, which per the project owner reflects a
MC363-vs-B822 common-bits convention difference, not a flaw in it. Frames can
still be replayed byte-for-byte from any capture instead of built:

```sh
python -I host/tools/decode_frames.py capture.sr      # prints replayable frames
```

**Replies.** After each command the printer keeps clocking the pair for a
command-specific time, so the receiving ops take an RX clock-phase count: `rx_phases`
for `b822.frame`/`b822.session`, and `rx_phases_list` (one count per frame) for
`b822.sequence`. Measured from a real printer's capture:

| Command | RX phases | Reply |
| --- | --- | --- |
| `0D`, `0D 16 53`, `5A` | 0 | none |
| `5D` | about 44 | a short (~40-bit) exchange |
| `5C` read-ID, CMD12, CMD11 | about 200–220 | 18 bytes |

`5D` must not be given 0. The cartridge only answers the following command if the
printer clocks through the `5D` exchange: with 0 phases the next read-ID was answered
0 of 8 times, with 20 or more, 8 of 8.

The board decodes replies with the MT7697's hardware edge-detect unit, which records a
notch regardless of how short it is. For each phase the sensed pad is muxed to its WIC
function for the (released) sense window, the latch is cleared, and the pending bit is
read at the phase end. The per-phase bits are decoded as 10-bit words, so a real
cartridge's 18-byte read-ID reply comes back correctly with no logic analyzer.

- It exists only for GPIO33/34, i.e. the default P4/P5 wiring.
- The trigger word defaults to `0x080` (either edge, debounce off — the debounce clock
  is ≤ 8 kHz, which would swallow the notch) and can be changed per request with
  `eint_deb`.
- `"raw": true` adds the per-phase notch bits as an `ebits` string (`ebits_a`/`ebits_b`
  for a session); `protogate.b822.decode_ebits` decodes them on the host.
- The reply starts with a `FF` preamble byte before the first real byte (`50` for
  read-ID), and can end with a `FF 00` of trailing fill.
- For CMD12/CMD11, `ca`/`cb` are still the *raw* wire-level bytes, bit-packed the
  same way as the TX body (above) — not yet the logical 10-byte Ca/Cb. Unpack them
  with `protogate.packer.unpack_reply`, which strips the preamble/trailing fill and
  un-splits the common/payload bits; confirmed against a real cartridge reply (both
  Ca and Cb decode with a uniform 3-bit common header across all 16 raw bytes).

**Frame CRC.** The trailing two bytes of every captured frame except CMD12/CMD11 are
a CRC-16/X-25 over the preceding bytes (command byte included), low byte first;
`protogate.b822.seal_frame` appends it, e.g. `seal_frame([0x5A, *uid])` for the `5A`
select frame. The 31-byte CMD12/CMD11 frames do not follow it: their 21-byte body is
authentication data and no standard CRC-16 over any range fits.

**Session order.** A real printer's session, as seen in the reference capture, is:
`0D 00 00`, `5D`, `5C` read-ID (and `5C 00 10 75` to find any further cartridges); then
the chip is **powered off** (both pads low for about 29 ms) and powered up again for
about 22 ms; then `0D 16 53`, `5A` + ID + CRC (each followed only by the long reply-SOF
pulse, no clocking), CMD12, CMD11, and `03 20` + ID + one byte + CRC, whose reply is
read as status. After the power-off the gaps between frames are about 40 ms (`0D 16 53`
→ `5A` 39.6 ms, → CMD12 41.1 ms, → CMD11 36.8 ms, → `03 20` 36.0 ms). In the client,
`b822_sequence(..., reset_ms=30, powerup_ms=22, gap_ms=40)` reproduces the power-off
and the gaps.

**The power-off needs a full re-wake, not just `0D 16 53`.** The first attempts at the
session above sent `0D 16 53` as the only frame after the power-off, matching what the
reference capture's printer sends there — and got no reply to anything after it:
not `5A`, not CMD12/CMD11, not the `03 20` status read, even though read-ID had just
been answered moments earlier in the same run. Isolating it (see git history on this
file for the diagnostic steps) showed it wasn't about the auth body, the UID, or
`reset_ms`/`powerup_ms` timing: even **read-ID** failed when sent as the first command
after any power-off, and the exact 3-frame call that was otherwise 100% reliable
(`0D`, `5D`, `5C` read-ID) still worked fine. The fix is to resend the **same generic
wake** used at session start — `0D 00 00 B3 39`, `5D 00 00 50 BA` — right after the
power-off, before `0D 16 53`. With that, CMD12, CMD11 and `03 20` are all answered.
So the full sequence after the power-off is: `0D`, `5D` (generic wake, again), then
`0D 16 53`, `5A`, CMD12, CMD11, `03 20`, with the ~40 ms gaps above. In the client:

```python
frames = [[0x0D,0,0,0xB3,0x39], [0x5D,0,0,0x50,0xBA],       # generic wake, again
          [0x0D,0x16,0x53,0xEC,0x98], seal_frame([0x5A, *uid]),
          cmd12_frame, cmd11_frame, seal_frame([0x03,0x20,*uid,0xA0])]
r = g.b822_sequence(frames, rx_phases=[0,44,0,0,220,220,170],
                    reset_ms=30, powerup_ms=22, gap_ms=40)
```

**CMD12/CMD11 are answered.** With the fix above, replaying the reference capture's
bodies with this cartridge's ID spliced in gets real cartridge replies to CMD12 and
CMD11 — reproducible across repeated runs, 80–125 of 220 notches each. CMD12's reply
was independently confirmed on a logic analyzer: `la_convert`'s `mc363_b822` decoder
read `7E 9F 1F 9D DC DC 5D FE 1E 9D 9C DE 7D FC BC 3E BF AE` off the wire, matching the
board's own on-device decode byte for byte (after its leading preamble byte). CMD11's
reply was not re-captured on the analyzer — the run that caught CMD12 ran out of its
2M-sample buffer just before CMD11's SOF — but it comes from the identical, now-proven
receive path. The `03 20` status reply is the same `FF 02 A0 FD 99 FF 00...` on every
run, while CMD12's and CMD11's replies differ each time the sequence is sent: a
deterministic status next to a varying authentication reply is what a real
challenge-response exchange looks like. This does not mean authentication actually
*succeeds* — the bodies are still a different cartridge's captured challenge/response,
so the cartridge almost certainly rejects them as invalid credentials — only that the
mechanical exchange (frame out, real cartridge-driven reply back, decoded correctly)
is now demonstrated working, which was the open question. A valid CMD12/CMD11 body for
*this* cartridge — from its own real printer capture or its challenge/response
algorithm — would be needed to test actual authentication.

**Known issue.** The first read after opening a fresh serial session (the board resets)
is sometimes lost: 5 of 19 such first reads failed in testing, and raising
`powerup_ms` from 21 to 60 did not clearly help. Reads after that were reliable (dozens
of consecutive correct reads per setting). The cause is not yet established; cold
instruction-cache timing on the first transmit is a suspect. Check that a read-ID reply
contains `50` and retry once if not.

**Host API and CLI.**

```python
from protogate import ProtoGate
from protogate import b822, packer

# Build frames from a chosen Pa/ctrl (bit-packed on the host, see above):
f12 = b822.build_auth_frame(b822.CMD12, pa, cta, uid)
f11 = b822.build_auth_frame(b822.CMD11, pb, ctb, uid)
# ...or replay literal frame bytes decoded from a capture instead:
# f12 = b822.build_frame(b822.CMD12, body12)   # 03 12 <uid> <body12>

with ProtoGate.serial("/dev/linkit7697") as g:
    r = g.b822_session(f12, f11, raw=True)
    ca = packer.unpack_reply(r["ca"])["chp"]     # raw reply -> logical 10-byte Ca
    cb = packer.unpack_reply(r["cb"])["chp"]
```

Reading a cartridge's ID takes the two init frames and then the read-ID frame, kept
powered together:

```python
INIT = [[0x0D, 0, 0, 0xB3, 0x39], [0x5D, 0, 0, 0x50, 0xBA], [0x5C, 0x01, 0x99, 0x64]]
with ProtoGate.serial("/dev/linkit7697") as g:
    r = g.b822_sequence(INIT, rx_phases=[0, 60, 220])
    print(r["frames"][2]["reply"])    # FF 50 <8-byte UID> ...
```

`host/tools/eint_sweep.py` runs that sequence for a list of edge-unit settings and
scores each against a known-good reply, for tuning against a new cartridge.

```sh
# Build frames from a chosen Pa/ctrl (bit-packed on the host):
protogate-b822 --serial /dev/linkit7697 --uid "41 BE 08 12 04 0B 00 E0" \
    --pa "<10 bytes>" --cta "01 00 00 00" --pb "<10 bytes>" --ctb "01 00 00 00" \
    --csv run.csv

# ...or replay pre-built frame bytes decoded from a capture instead:
protogate-b822 --serial /dev/linkit7697 \
    --frame12 "03 12 41 BE 08 12 04 0B 00 E0 <body>" \
    --frame11 "03 11 41 BE 08 12 04 0B 00 E0 <body>" \
    --cta "01 14 00 00" --ctb "01 14 00 00" --csv run.csv
```

`protogate-b822` prints a row in the project's 12-column capture format
(`xp,xc,pa,ca,pb,cb,cta,ctb,pua,cua,pub,cub`), unpacking the cartridge's raw Ca/Cb
reply bytes first; `--rx-phases` sets how many RX clock phases to listen for each
reply. `host/tools/capture_to_csv.py` batch-converts a JSONL log of sessions into
appended rows the same way.

**Validation (no hardware).** The wire logic is checked against the real decoder:
`python -I host/tests/test_b822_wire.py` synthesises the TX waveform and confirms
`pd.py` decodes it back to the input frame (it needs `sigrok-cli` and the `mc363_b822`
decoder). `python -I host/tests/test_b822_ebits.py` checks the host-side reply decode on
synthetic notch bits at every alignment and on bits captured from a real cartridge; it
needs neither. `python -I host/tests/test_b822_packer.py` checks the body/reply bit
(un)packing against real raw CMD12/CMD11 samples; it needs neither either.

## Automation

The Python API accepts ordinary dictionaries in `request`, so scripts can compose and loop over operations. The CLI is suitable for one-off calls and shell automation. A future iteration can add a task runner once the command set and safety needs are clearer.

## Limitations

This is an initial implementation. It supports I²C and SPI only; additional protocols such as UART, GPIO, and ADC can be added using the same request/response model. The Wi-Fi TCP listener is unauthenticated and should only be used on a trusted network.
