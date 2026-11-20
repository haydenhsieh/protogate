"""Extract replayable printer frames from a capture.

Runs the la_convert mc363_b822 decoder on a .sr file, groups the printer bytes
into frames (by inter-frame sample gaps), and prints each as a hex byte stream
ready to pass to `protogate-b822 --frame12/--frame11` or `ProtoGate.b822_frame`.

Usage:
  python -I host/tools/decode_frames.py CAPTURE.sr [--a D1 --b D2] [--gap 20000]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_DECODER_DIR = str(Path.home() / "workspace/la_convert/decoders")


def main() -> int:
    p = argparse.ArgumentParser(description="Extract printer frames from a capture")
    p.add_argument("capture", help="sigrok .sr file")
    p.add_argument("--a", default="D1", help="wire A channel (default D1)")
    p.add_argument("--b", default="D2", help="wire B channel (default D2)")
    p.add_argument("--gap", type=int, default=20000,
                   help="min sample gap between frames (default 20000)")
    p.add_argument("--decoder-dir", default=DEFAULT_DECODER_DIR)
    args = p.parse_args()

    env = dict(os.environ, SIGROKDECODE_DIR=args.decoder_dir)
    # Ask for sample-numbered printer annotations.
    out = subprocess.run(
        ["sigrok-cli", "-i", args.capture,
         "-P", "mc363_b822:a=%s:b=%s" % (args.a, args.b),
         "-A", "mc363_b822=printer", "--protocol-decoder-samplenum"],
        capture_output=True, text=True, env=env).stdout

    rows = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)-\d+\s+.*0x([0-9A-Fa-f]{2})", line)
        if m:
            rows.append((int(m.group(1)), int(m.group(2), 16)))
    if not rows:
        print("no printer bytes decoded (check channel mapping)", file=sys.stderr)
        return 1

    frames, cur = [], [rows[0]]
    for prev, (s, v) in zip(rows, rows[1:]):
        if s - prev[0] > args.gap:
            frames.append(cur)
            cur = []
        cur.append((s, v))
    frames.append(cur)

    for i, f in enumerate(frames):
        bs = [v for _, v in f]
        kind = ""
        if len(bs) >= 2 and bs[0] == 0x03:
            kind = {0x12: " CMD12", 0x11: " CMD11"}.get(bs[1], "")
        print("# frame %d (%d bytes)%s" % (i, len(bs), kind))
        print(" ".join("%02X" % b for b in bs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
