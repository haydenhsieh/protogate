"""Off-hardware check of the host-side reply decode (``b822.decode_ebits``).

The board reports one ``0``/``1`` character per RX clock phase (1 = the cartridge
notched that phase) and decodes it on-device as 10-bit words (start 0, 8 data
LSB-first, stop 1). ``decode_ebits`` mirrors that decode. This builds synthetic notch
bits for a known reply at every word alignment, with a preamble and trailing fill like a
real reply, and checks the bytes come back, including with a flipped data bit.

Run:  python -I host/tests/test_b822_ebits.py      (needs no hardware or sigrok)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate.b822 import decode_ebits

REPLY = [0x50, 0x41, 0x53, 0x0F, 0x10, 0x03, 0x0D, 0x00, 0xE0, 0x1C, 0x11, 0x12,
         0x40, 0x40, 0x40, 0x00, 0xAB, 0xD8]          # a real read-ID reply


# Per-phase notch bits captured from a real cartridge on the board (read-ID, 220 phases).
REAL_BITS = (
    "111111111111111000001010101000001010110010101011110000100000"
    "100010110000001010110000100000000010000001111000111000101000"
    "100010010010001000000010100000001010000000101000000000101101"
    "0101100001101111111111111000000000000000"
)


def word(byte: int) -> str:
    """start 0, 8 data bits LSB-first, stop 1."""
    return "0" + "".join("1" if byte >> k & 1 else "0" for k in range(8)) + "1"


def encode(data, lead: int, tail: int = 25) -> str:
    return "1" * lead + "".join(word(b) for b in data) + "1" * tail


def contains(seq, sub) -> bool:
    return any(seq[i:i + len(sub)] == sub for i in range(len(seq) - len(sub) + 1))


def main() -> int:
    ok = True
    for lead in range(0, 24):                 # every alignment, with and without a preamble
        got = decode_ebits(encode(REPLY, lead))
        good = contains(got, REPLY)
        ok &= good
        if not good:
            print("FAIL lead=%d: %s" % (lead, " ".join("%02X" % b for b in got)))

    # An explicit phase overrides the automatic choice.
    bits = encode(REPLY, 3)
    ok &= decode_ebits(bits, phase=3)[:len(REPLY)] == REPLY

    # One flipped data bit corrupts exactly one byte and nothing else. The words start
    # at bit 5, which is also the best alignment, so the reply decodes from index 0.
    bits = list(encode(REPLY, 5))
    flip = 5 + 10 * 4 + 1 + 2                 # data bit 2 of byte index 4
    bits[flip] = "0" if bits[flip] == "1" else "1"
    got = decode_ebits("".join(bits))
    diff = [i for i, (a, b) in enumerate(zip(got, REPLY)) if a != b]
    ok &= diff == [4] and got[4] == REPLY[4] ^ 0x04

    # The real captured bits decode to the reply read with the logic analyzer.
    ok &= contains(decode_ebits(REAL_BITS), REPLY)

    # No bits, or too few for one word, decode to nothing.
    ok &= decode_ebits("") == [] and decode_ebits("0101") == []

    print("decode_ebits:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
