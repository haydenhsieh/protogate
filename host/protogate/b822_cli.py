"""Capture CLI for the OKI MC363/B822 printer-emulation bus.

Runs one CMD12 -> Ca, 18 ms, CMD11 -> Cb session against a real cartridge via a
ProtoGate board and prints (or appends) a row in the project's 12-column CSV
format. Frames are supplied as hex byte streams (the full wire bytes after SOF,
e.g. "03 12 41 BE 08 12 04 0B 00 E0 <21-byte body>"). The replies are decoded on the
board with the hardware edge latch.

Example:
  protogate-b822 --serial /dev/linkit7697 \\
      --frame12 "03 12 41 BE ... body12" --frame11 "03 11 41 BE ... body11" \\
      --pa "<10 bytes>" --cta "01 14 00 00" --pb "<10 bytes>" --ctb "01 14 00 00"
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .client import ProtoGate, ProtoGateError
from . import b822


def _hex(s: str) -> List[int]:
    s = s.replace(",", " ").strip()
    return [int(t, 16) for t in s.split()] if s else []


def main() -> None:
    p = argparse.ArgumentParser(description="Run a b822 CMD12/CMD11 session and emit a CSV row")
    t = p.add_mutually_exclusive_group(required=True)
    t.add_argument("--serial", metavar="PORT")
    t.add_argument("--tcp", metavar="HOST")
    p.add_argument("--port", type=int, default=4242)
    p.add_argument("--frame12", required=True, help="hex bytes of the CMD12 frame")
    p.add_argument("--frame11", required=True, help="hex bytes of the CMD11 frame")
    p.add_argument("--rx-phases", type=int, default=None,
                   help="RX clock phases to listen for per reply (default: firmware's)")
    # Optional labels for the CSV (pa/pb/ctrl are the logical inputs; ca/cb come
    # from the cartridge). Left blank -> zero-filled.
    p.add_argument("--pa", default="")
    p.add_argument("--pb", default="")
    p.add_argument("--cta", default="")
    p.add_argument("--ctb", default="")
    p.add_argument("--csv", metavar="FILE", help="append the row to FILE (with header if new)")
    p.add_argument("--header", action="store_true", help="print the CSV header first")
    args = p.parse_args()

    f12, f11 = _hex(args.frame12), _hex(args.frame11)
    pa, pb = _hex(args.pa) or [0] * 10, _hex(args.pb) or [0] * 10
    cta, ctb = _hex(args.cta) or [0] * 4, _hex(args.ctb) or [0] * 4

    try:
        gate = ProtoGate.serial(args.serial) if args.serial else ProtoGate.tcp(args.tcp, args.port)
        with gate:
            resp = gate.b822_session(f12, f11, rx_phases=args.rx_phases)
    except (ValueError, ProtoGateError, OSError) as exc:
        p.exit(2, f"protogate-b822: {exc}\n")

    ca = (list(resp.get("ca", [])) + [0] * 10)[:10]
    cb = (list(resp.get("cb", [])) + [0] * 10)[:10]
    row = b822.session_csv_row(pa, cta, ca, pb, ctb, cb)

    lines = []
    if args.header:
        lines.append(b822.CSV_HEADER)
    lines.append(row)
    print("\n".join(lines))
    if args.csv:
        path = Path(args.csv)
        new = not path.exists()
        with path.open("a") as fh:
            if new:
                fh.write(b822.CSV_HEADER + "\n")
            fh.write(row + "\n")


if __name__ == "__main__":
    main()
