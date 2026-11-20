"""Sweep the on-device edge-latch (EINT) configuration and score each setting.

Runs the init + read-ID sequence for a list of edge-unit
config words (the ``eint_deb`` request parameter), pulls back the raw per-phase
notch bits, and scores how well they decode to a known-good reply (e.g. one read
with the logic analyzer). Use it to tune debounce/polarity bits against a real
cartridge without re-flashing.

Usage:
  python -I host/tools/eint_sweep.py --serial /dev/ttyUSB0 \
      [--expect "50 41 53 ..."] [--trials 4] [--phases 220]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate import ProtoGate  # noqa: E402
from protogate.b822 import decode_ebits  # noqa: E402

INIT = [[0x0D, 0x00, 0x00, 0xB3, 0x39], [0x5D, 0x00, 0x00, 0x50, 0xBA],
        [0x5C, 0x01, 0x99, 0x64]]

# Edge-unit word: DEBOUNCE_EN(9) POL(8) DUAL(7) PRESC(6:4) CNT(3:0). Keep bit 9 clear:
# the debounce clock is <= 8 kHz and swallows sub-microsecond notches. Measured polarity:
# POL=0 latches a rising edge, POL=1 a falling edge. On the bench 0x080 and 0x000 decoded
# a full read-ID reply every time; 0x100 did too apart from a cold-start miss.
DEFAULT_WORDS = [0x080, 0x000, 0x100, 0x180]


def score(bits: str, expect: list[int]) -> tuple[int, int, list[int]]:
    """Best (matches, alignment, bytes) over all 10 word alignments / byte shifts."""
    best = (-1, 0, [])
    for ph in range(10):
        got = decode_ebits(bits, phase=ph)
        for shift in range(-4, 5):
            m = sum(1 for i, e in enumerate(expect)
                    if 0 <= i + shift < len(got) and got[i + shift] == e)
            if m > best[0]:
                best = (m, ph, got)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serial", default="/dev/ttyUSB0")
    ap.add_argument("--expect",
                    default="50 41 53 0F 10 03 0D 00 E0 1C 11 12 40 40 40 00 AB D8",
                    help="known-good reply bytes (hex) to score against")
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--phases", type=int, default=220)
    ap.add_argument("--words", default=",".join("0x%X" % w for w in DEFAULT_WORDS),
                    help="comma list of eint_deb words to try")
    ap.add_argument("--settle", default="400",
                    help="comma list of eint_settle values (ns) to try")
    args = ap.parse_args()

    expect = [int(t, 16) for t in args.expect.split()]
    words = [int(w, 0) for w in args.words.split(",")]
    settles = [int(s) for s in args.settle.split(",")]

    with ProtoGate.serial(args.serial) as g:
        time.sleep(1.0)
        g.system_info()
        print("expect:", " ".join("%02X" % b for b in expect))
        print("%-7s %-7s %-9s %s" % ("word", "settle", "best/%d" % len(expect),
                                      "per-trial"))
        for w in words:
          for st in settles:
            scores = []
            sample = []
            for _ in range(args.trials):
                r = g.request({"op": "b822.sequence", "frames": INIT,
                               "eint_deb": w, "eint_settle": st, "raw": True,
                               "rx_phases_list": [0, 60, args.phases],
                               "rx_phase_ns": 4700, "powerup_ms": 21, "gap_ms": 21})
                bits = r["frames"][2].get("ebits", "")
                m, ph, got = score(bits, expect)
                scores.append(m)
                if not sample or m >= max(scores):
                    sample = got
            print("0x%03X   %-7d %-9s %s" % (w, st, "%d" % max(scores), scores))
            print("                best decode:",
                  " ".join("%02X" % b for b in sample[:18]))
        g.request({"op": "b822.power", "state": "off"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
