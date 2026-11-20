"""Check the frame CRC against frames captured from a real printer and a real cartridge.

Run:  python -I host/tests/test_b822_crc.py      (needs no hardware or sigrok)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate.b822 import crc16_x25, seal_frame

# Frames from the reference printer capture (mc363.sr), each ending in its CRC. Only
# independently captured frames belong here: a frame this code generated proves nothing.
CAPTURED = [
    "0D 00 00 B3 39",
    "5D 00 00 50 BA",
    "5C 01 99 64",
    "5C 00 10 75",
    "0D 16 53 EC 98",
    "5A 41 BE 08 12 04 0B 00 E0 CC 41",
    "03 20 41 BE 08 12 04 0B 00 E0 A0 B7 D5",
    "03 23 41 BE 08 12 04 0B 00 E0 36 03 3C B0",
]


def main() -> int:
    ok = True
    for text in CAPTURED:
        frame = [int(t, 16) for t in text.split()]
        good = seal_frame(frame[:-2]) == frame
        ok &= good
        if not good:
            print("FAIL", text, "->", " ".join("%02X" % b for b in seal_frame(frame[:-2])))
    ok &= crc16_x25(b"123456789") == 0x906E        # the standard CRC-16/X-25 check value
    print("crc16_x25:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
