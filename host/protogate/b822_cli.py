"""Capture CLI for the OKI MC363/B822 printer-emulation bus.

Runs one CMD12 -> Ca, 18 ms, CMD11 -> Cb session against a real cartridge via a
ProtoGate board and prints (or appends) a row in the project's 12-column CSV
format.

Frames can be supplied two ways:

  * Pre-built, as hex byte streams (the full wire bytes after SOF, e.g.
    "03 12 41 BE 08 12 04 0B 00 E0 <21-byte body>") via --frame12/--frame11 --
    the only option for replaying a frame decoded from a real capture.
  * Or built from a chosen logical Pa/ctrla and Pb/ctrlb via --pa/--cta/--pb/
    --ctb (plus --uid), using `b822.build_auth_frame` (confirmed against 130
    real capture-file rows, see packer.py's module docstring) to bit-pack them
    into the wire body.

--frame12/--frame11 take precedence if given; otherwise --pa/--cta/--pb/--ctb
are required to build the frames. Either way, --pa/--pb/--cta/--ctb also label
the CSV row's logical columns (left blank with --frame12/--frame11 given,
these are zero-filled, since they can't be recovered from the frame bytes).

Examples:
  protogate-b822 --serial /dev/linkit7697 \\
      --frame12 "03 12 41 BE ... body12" --frame11 "03 11 41 BE ... body11" \\
      --pa "<10 bytes>" --cta "01 14 00 00" --pb "<10 bytes>" --ctb "01 14 00 00"

  protogate-b822 --serial /dev/linkit7697 --uid "41 BE 08 12 04 0B 00 E0" \\
      --pa "<10 bytes>" --cta "01 00 00 00" --pb "<10 bytes>" --ctb "01 00 00 00"
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .client import ProtoGate, ProtoGateError
from . import b822
from . import packer


def _hex(s: str) -> List[int]:
    s = s.replace(",", " ").strip()
    return [int(t, 16) for t in s.split()] if s else []


def main() -> None:
    p = argparse.ArgumentParser(description="Run a b822 CMD12/CMD11 session and emit a CSV row")
    t = p.add_mutually_exclusive_group(required=True)
    t.add_argument("--serial", metavar="PORT")
    t.add_argument("--tcp", metavar="HOST")
    p.add_argument("--port", type=int, default=4242)
    p.add_argument("--frame12", default="", help="hex bytes of the CMD12 frame (pre-built)")
    p.add_argument("--frame11", default="", help="hex bytes of the CMD11 frame (pre-built)")
    p.add_argument("--uid", default="", help="8-byte cartridge UID, for building frames")
    p.add_argument("--rx-phases", type=int, default=None,
                   help="RX clock phases to listen for per reply (default: firmware's)")
    # Logical Pa/ctrla, Pb/ctrlb: used to build --frame12/--frame11 when those
    # aren't given directly, and always label the CSV row (zero-filled if
    # blank and a pre-built --frame12/--frame11 was used instead).
    p.add_argument("--pa", default="")
    p.add_argument("--pb", default="")
    p.add_argument("--cta", default="")
    p.add_argument("--ctb", default="")
    # The body's two free-looking fields (see b822.build_auth_frame) -- only
    # used when building frames from --pa/--cta/--pb/--ctb.
    p.add_argument("--p-unk", type=lambda s: int(s, 16), default=0, metavar="HH")
    p.add_argument("--p-unk-b", type=lambda s: int(s, 16), default=0, metavar="HH")
    p.add_argument("--prt-common", type=lambda s: int(s, 2), default=0, metavar="BBB")
    p.add_argument("--prt-common-b", type=lambda s: int(s, 2), default=0, metavar="BBB")
    p.add_argument("--csv", metavar="FILE", help="append the row to FILE (with header if new)")
    p.add_argument("--header", action="store_true", help="print the CSV header first")
    args = p.parse_args()

    pa, pb = _hex(args.pa) or [0] * 10, _hex(args.pb) or [0] * 10
    cta, ctb = _hex(args.cta) or [0] * 4, _hex(args.ctb) or [0] * 4

    f12 = _hex(args.frame12)
    f11 = _hex(args.frame11)
    if not f12 or not f11:
        if not (args.pa and args.cta and args.pb and args.ctb and args.uid):
            p.error("need --frame12/--frame11, or --uid with --pa/--cta/--pb/--ctb to build them")
        uid = _hex(args.uid)
        f12 = b822.build_auth_frame(b822.CMD12, pa, cta, uid, args.p_unk, args.prt_common)
        f11 = b822.build_auth_frame(b822.CMD11, pb, ctb, uid, args.p_unk_b, args.prt_common_b)

    try:
        gate = ProtoGate.serial(args.serial) if args.serial else ProtoGate.tcp(args.tcp, args.port)
        with gate:
            resp = gate.b822_session(f12, f11, rx_phases=args.rx_phases)
    except (ValueError, ProtoGateError, OSError) as exc:
        p.exit(2, f"protogate-b822: {exc}\n")

    # resp["ca"]/["cb"] are raw wire-decoded bytes (optional leading 0xFF
    # preamble + the 16-byte bit-interleaved chp window + optional trailing
    # fill), not yet the logical 10-byte Ca/Cb -- unpack them (confirmed
    # against a real cartridge reply, see packer.py's module docstring).
    raw_ca = list(resp.get("ca", []))
    raw_cb = list(resp.get("cb", []))
    ca = list(packer.unpack_reply(raw_ca)["chp"]) if raw_ca else [0] * 10
    cb = list(packer.unpack_reply(raw_cb)["chp"]) if raw_cb else [0] * 10
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
