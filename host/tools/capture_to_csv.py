#!/usr/bin/env python3
"""Batch-convert logged ProtoGate b822 sessions into the project's
`captured/` CSV format (12 columns, no header, pua/cua/pub/cub left zero).

Input is JSON Lines, one session per line, each a JSON object:

    {"pa": "<10-byte hex>", "cta": "<4-byte hex>",
     "pb": "<10-byte hex>", "ctb": "<4-byte hex>",
     "ca": "FF <16-byte chp-raw window> [trailing fill]",
     "cb": "FF <16-byte chp-raw window> [trailing fill]"}

(byte lists are also accepted instead of hex strings). `pa`/`cta`/`pb`/`ctb`
are the logical values the session was run with -- supply them directly
(e.g. as the arguments you passed to `b822_session`/`b822_cli`); they are
NOT derived from the frame bytes here. `protogate.packer.unpack_body`
(packer.rb's TX-side bit model, ported for the printer->cartridge
direction) does NOT hold on real ProtoGate frame bodies -- checked against
a real session's frame12/frame11 bytes and found inconsistent at every
possible byte offset. The TX-side body layout is still unconfirmed (see
protogate/README.md's own "requires the body bit-layout, which is
confirmed on hardware at bring-up" caveat).

`ca`/`cb` ARE unpacked here with `protogate.packer.unpack_reply`, which IS
confirmed against a real cartridge reply (both Ca and Cb decode with a
perfectly uniform 3-bit common header across all 16 raw bytes).

Usage:
  python -I host/tools/capture_to_csv.py sessions.jsonl \\
      --out ../b822_a112_claude/captured/protogate_real_b.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate import b822, packer  # noqa: E402


def _as_bytes(v: Any) -> List[int]:
    if isinstance(v, str):
        v = v.replace(",", " ").strip()
        return [int(t, 16) for t in v.split()] if v else []
    return [int(x) & 0xFF for x in v]


def convert_record(rec: dict) -> str:
    """Build one CSV row (project format) from one logged session record."""
    pa, cta = _as_bytes(rec["pa"]), _as_bytes(rec["cta"])
    pb, ctb = _as_bytes(rec["pb"]), _as_bytes(rec["ctb"])
    raw_ca, raw_cb = _as_bytes(rec["ca"]), _as_bytes(rec["cb"])

    ca = packer.unpack_reply(raw_ca)["chp"] if raw_ca else bytes(10)
    cb = packer.unpack_reply(raw_cb)["chp"] if raw_cb else bytes(10)

    return b822.session_csv_row(pa, cta, ca, pb, ctb, cb)


def _load_records(path: Path) -> Iterable[dict]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                yield json.loads(line)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sessions", type=Path, help="JSONL file of logged sessions")
    p.add_argument("--out", type=Path, required=True,
                   help="captured/*.txt file to append rows to")
    args = p.parse_args()

    rows, errors = [], 0
    for i, rec in enumerate(_load_records(args.sessions)):
        try:
            rows.append(convert_record(rec))
        except (KeyError, ValueError) as exc:
            print(f"skip record {i}: {exc}", file=sys.stderr)
            errors += 1

    with args.out.open("a") as fh:
        for row in rows:
            fh.write(row + "\n")

    print(f"appended {len(rows)} rows to {args.out} ({errors} skipped)")
    return 1 if errors and not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
