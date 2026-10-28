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
