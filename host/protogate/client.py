"""Synchronous JSON-lines client for the ProtoGate firmware."""

from __future__ import annotations

import json
import socket
from typing import Any, Dict, Iterable, Optional


class ProtoGateError(RuntimeError):
    """Raised when transport or firmware reports a failed request."""


class ProtoGate:
    def __init__(self, stream: Any, owns_stream: bool = True) -> None:
        self._stream = stream
        self._owns_stream = owns_stream
        self._sequence = 0

    @classmethod
    def serial(cls, port: str, baudrate: int = 115200, timeout: float = 5.0) -> "ProtoGate":
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProtoGateError("Install serial support with: pip install protogate[serial]") from exc
        return cls(serial.Serial(port, baudrate=baudrate, timeout=timeout))

    @classmethod
    def tcp(cls, host: str, port: int = 4242, timeout: float = 5.0) -> "ProtoGate":
        sock = socket.create_connection((host, port), timeout=timeout)
        return cls(sock.makefile("rwb", buffering=0))

    def close(self) -> None:
        if self._owns_stream:
            self._stream.close()

    def __enter__(self) -> "ProtoGate":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def request(self, command: Dict[str, Any]) -> Dict[str, Any]:
        self._sequence += 1
        request = dict(command)
        request.setdefault("id", self._sequence)
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            self._stream.write(encoded)
            if hasattr(self._stream, "flush"):
                self._stream.flush()
            line = self._stream.readline()
        except (OSError, TimeoutError) as exc:
            raise ProtoGateError(f"transport error: {exc}") from exc
        if not line:
            raise ProtoGateError("timed out waiting for board response")
        # Firmware may announce readiness before the first command response.
        while line:
            try:
                response = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtoGateError(f"invalid response from board: {line!r}") from exc
            if "event" not in response:
                break
            line = self._stream.readline()
        else:
            raise ProtoGateError("timed out waiting for board response")
        if not response.get("ok", False):
            raise ProtoGateError(response.get("error", "board rejected request"))
        return response

    def system_info(self) -> Dict[str, Any]:
        return self.request({"op": "system.info"})

    def i2c_scan(self, address_start: int = 0x08, address_end: int = 0x77) -> Iterable[int]:
        return self.request({"op": "i2c.scan", "address_start": address_start,
                             "address_end": address_end})["devices"]

    def i2c_write(self, address: int, data: Iterable[int]) -> int:
        return self.request({"op": "i2c.write", "address": address,
                             "data": list(data)})["written"]

    def i2c_read(self, address: int, length: int) -> Iterable[int]:
        return self.request({"op": "i2c.read", "address": address,
                             "length": length})["data"]

    def i2c_write_read(self, address: int, write: Iterable[int],
                       read_count: int) -> Iterable[int]:
        return self.request({"op": "i2c.write_read", "address": address,
                             "write": list(write), "length": read_count})["data"]

    def spi_transfer(self, data: Iterable[int], clock_hz: int = 1_000_000,
                     mode: int = 0, bit_order: str = "msb",
                     cs_pin: Optional[int] = None) -> Iterable[int]:
        command: Dict[str, Any] = {"op": "spi.transfer", "data": list(data),
                                   "clock_hz": clock_hz, "mode": mode,
                                   "bit_order": bit_order}
        if cs_pin is not None:
            command["cs_pin"] = cs_pin
        return self.request(command)["data"]

    # -- OKI MC363/B822 printer-emulation bus -------------------------------

    def b822_frame(self, frame: Iterable[int], rx_phases: Optional[int] = None,
                   raw: bool = False) -> Dict[str, Any]:
        """Send one printer frame (full byte stream after SOF) and decode the
        cartridge's reply on the board. Returns the firmware response: ``reply``
        (decoded bytes) and ``nphases``; with ``raw`` also ``ebits``, the per-phase notch
        bits as a ``0``/``1`` string (decode with ``b822.decode_ebits``).

        ``rx_phases`` is the number of RX clock phases to listen for; reply length is
        command-specific (``0`` for no reply, about 220 for an 18-byte reply)."""
        command: Dict[str, Any] = {"op": "b822.frame", "frame": list(frame)}
        if rx_phases is not None:
            command["rx_phases"] = rx_phases
        if raw:
            command["raw"] = True
        return self.request(command)

    def b822_session(self, frame12: Iterable[int], frame11: Iterable[int],
                     rx_phases: Optional[int] = None,
                     raw: bool = False) -> Dict[str, Any]:
        """Run a full CMD12 -> Ca, 18 ms, CMD11 -> Cb handshake. ``frame12`` and
        ``frame11`` are the complete byte streams (e.g. ``03 12 <uid> <body>``).
        Returns ``ca``/``cb`` decoded bytes and ``nphases``; with ``raw`` also
        ``ebits_a``/``ebits_b`` (see ``b822_frame``)."""
        command: Dict[str, Any] = {"op": "b822.session",
                                   "frame12": list(frame12),
                                   "frame11": list(frame11)}
        if rx_phases is not None:
            command["rx_phases"] = rx_phases
        if raw:
            command["raw"] = True
        return self.request(command)

    def b822_sequence(self, frames: Iterable[Iterable[int]],
                      rx_phases: Optional[Iterable[int]] = None,
                      raw: bool = False,
                      powerup_ms: Optional[int] = None,
                      gap_ms: Optional[int] = None,
                      reset_ms: Optional[int] = None) -> Dict[str, Any]:
        """Send several frames back-to-back with the cartridge kept powered between
        them (it needs the ``0D``/``5D`` init frames before a ``5C`` read-ID), and
        decode each command's reply on the board. Returns
        ``{"frames": [{"reply": [...], "nphases": n}, ...]}``.

        ``rx_phases`` gives one RX clock-phase count per frame, because the printer keeps
        clocking for a command-specific time (measured from a real printer): ``0`` for
        ``0D``, ``0D 16 53`` and ``5A``; about 44 for ``5D``; about 220 for an 18-byte
        reply (read-ID, CMD12, CMD11). ``5D`` must NOT get ``0``: the cartridge only
        answers the next command if the printer clocks through its short exchange. ``raw=True`` also returns each frame's per-phase notch
        bits as an ``ebits`` string of ``0``/``1`` (decode with ``b822.decode_ebits``).

        ``reset_ms`` powers the chip DOWN for that long before the first frame (both pads
        low), then ``powerup_ms`` of power before it. A real printer does this once, between
        reading the cartridge ID and starting the authentication session (``0D 16 53``).
        After a ``reset_ms`` power-off, the generic wake frames (``0D``, ``5D`` — the same
        ones used at session start) must be resent before ``0D 16 53``, or nothing after
        them gets a reply, not even read-ID. ``0D 16 53`` alone is not enough.
        """
        command: Dict[str, Any] = {"op": "b822.sequence",
                                   "frames": [list(f) for f in frames]}
        if rx_phases is not None:
            command["rx_phases_list"] = list(rx_phases)
        if raw:
            command["raw"] = True
        if powerup_ms is not None:
            command["powerup_ms"] = powerup_ms
        if gap_ms is not None:
            command["gap_ms"] = gap_ms
        if reset_ms is not None:
            command["reset_ms"] = reset_ms
        return self.request(command)
