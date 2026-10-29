# ProtoGate

ProtoGate turns a LinkIt 7697 into a remotely controlled hardware interface. A host sends newline-delimited JSON commands over the board's USB-to-UART serial port or a Wi-Fi TCP connection. The firmware executes a peripheral operation and returns one JSON response line.

The initial firmware supports I²C and SPI. The host package provides the same API over either transport, plus a small command line interface. Commands are intentionally explicit: pin numbers, bus settings, addresses, and payload bytes are supplied by the caller.

## Hardware and toolchain

The LinkIt 7697 HDK exposes USB through an onboard CP2102N USB-to-UART bridge; this is a serial command channel, not native USB. The board includes Wi-Fi, I²C, and SPI. The firmware targets the MediaTek LinkIt 7697 Arduino add-on and uses its `LWiFi` API. Install that board package in Arduino IDE, then install ArduinoJson 6.

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

SPI defaults to 1 MHz, mode 0, MSB first, and no chip-select pin. When `cs_pin` is supplied, firmware drives it low for the transaction and high afterward. I²C uses the board's default `Wire` pins and 100 kHz clock. Transfers are limited to 96 bytes per request in this first firmware version.

## Automation

The Python API accepts ordinary dictionaries in `request`, so scripts can compose and loop over operations. The CLI is suitable for one-off calls and shell automation. A future iteration can add a task runner once the command set and safety needs are clearer.

## Limitations

This is an initial implementation. It supports I²C and SPI only; additional protocols such as UART, GPIO, and ADC can be added using the same request/response model. The Wi-Fi TCP listener is unauthenticated and should only be used on a trusted network.
