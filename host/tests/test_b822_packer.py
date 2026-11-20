"""Check packer.py's bit (un)packing against real raw CMD12/CMD11 samples.

Run:  python -I host/tests/test_b822_packer.py      (needs no hardware or Ruby)

Fixtures below are the first 4 rows of
``~/workspace/protogate/tmp/a112_captured_samples.txt`` -- the raw capture
file that actually feeds ``~/workspace/oki_b822/a112_chip/packer.rb`` to
produce this project's `captured/*.txt` dataset. All 130 rows of that file
were cross-checked against ``packer.rb -p`` and matched exactly (unpack AND
the ``pack_body`` round-trip); these 4 are pinned here so the test needs
neither Ruby nor that external file at run time. (An earlier version of this
test used synthetic hand-built fixtures and, separately, ProtoGate frames
replayed from a reference .sr capture -- the .sr frames do NOT decode
consistently with this model, which is still unexplained; see packer.py's
module docstring. The real capture-file samples here are what's confirmed.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate import packer

# Raw 49-byte records: head(1) type(1) uid(8) body(21) chp_raw(16) ignored(2).
ROWS = [
    "03 12 41 EC 13 10 03 0D 00 E0 58 49 58 5B 4A DF DF 5C 01 CE 4A 4A 59 5F CB 5F 5E 00 BC 00 00 "
    "C8 8B 0B 6B 68 69 0B 49 E8 88 08 4A EB 88 AA 4A 88 92",
    "03 11 41 EC 13 10 03 0D 00 E0 4F 4F 58 CF CD 4E DD 4E 01 4B 4F 58 DC 5D 48 4E DC 00 9D 00 00 "
    "B7 F6 36 D4 D7 B7 F7 75 16 B5 34 54 56 34 35 56 D6 0F",
    "03 12 41 EC 13 10 03 0D 00 E0 E6 60 F1 65 64 65 E4 E5 01 F2 E5 65 60 71 F7 F6 75 00 D8 00 00 "
    "90 12 B3 33 F1 12 F0 B1 73 70 72 D0 D3 13 71 D3 51 48",
    "03 11 41 EC 13 10 03 0D 00 E0 B2 24 B4 A4 A3 22 35 35 01 B4 B3 A5 B5 A2 A7 24 B1 00 FF 00 00 "
    "78 D8 5B 58 BB DB FA 98 78 3A 5B BA 7A 9B 78 7A 9B 47",
]

# Expected decode, from `packer.rb -p` on the same 4 rows (field order:
# T, PARAM(ctrl), PH(prt_common), P(pa), PU(p_unk), CH(chp_common), C(chp)).
EXPECTED = [
    {"type": 0x12, "ctrl": "01000000", "prt_common": 0b101,
     "pa": "40 50 b1 7f ec b0 84 97 cd ee", "p_unk": 0xbc,
     "chp_common": 0b010, "chp": "c4 c6 f6 34 69 e4 00 af c2 ca"},
    {"type": 0x11, "ctrl": "01000000", "prt_common": 0b101,
     "pa": "39 d1 7a 9b a6 19 d1 c6 80 dc", "p_unk": 0x9d,
     "chp_common": 0b101, "chp": "bf 8d 8d df ed 15 48 85 10 aa"},
    {"type": 0x12, "ctrl": "01000000", "prt_common": 0b110,
     "pa": "b0 32 52 16 95 d5 4a 04 ff cd", "p_unk": 0xd8,
     "chp_common": 0b100, "chp": "80 ae 7e 8b 95 7b 1d 8d 8d bb"},
    {"type": 0x11, "ctrl": "01000000", "prt_common": 0b010,
     "pa": "d1 39 49 89 ad e6 eb d9 5c 99", "p_unk": 0xff,
     "chp_common": 0b110, "chp": "66 16 8b ef d0 61 97 67 4d 8e"},
]


def _hexbytes(s: str):
    return bytes(int(t, 16) for t in s.split())


def main() -> int:
    ok = True
    for i, (row, exp) in enumerate(zip(ROWS, EXPECTED)):
        raw = [int(t, 16) for t in row.split()]
        if len(raw) != 49:
            print("FAIL row %d: expected 49 bytes, got %d" % (i, len(raw)))
            ok = False
            continue
        body, chp_raw = raw[10:31], raw[31:47]

        u = packer.unpack_body(body)
        chp_common, chp = packer.split_resp_bits(chp_raw)

        good = (raw[1] == exp["type"]
                and "%02x%02x%02x%02x" % tuple(u["ctrl"]) == exp["ctrl"]
                and u["prt_common"] == exp["prt_common"]
                and bytes(u["pa"]) == _hexbytes(exp["pa"])
                and u["p_unk"] == exp["p_unk"]
                and chp_common == exp["chp_common"]
                and bytes(chp) == _hexbytes(exp["chp"]))
        if not good:
            print("FAIL row %d: unpack mismatch -- got pa=%s ctrl=%s ph=%s chp=%s ch=%s"
                  % (i, bytes(u["pa"]).hex(), bytes(u["ctrl"]).hex(),
                     bin(u["prt_common"]), bytes(chp).hex(), bin(chp_common)))
        ok &= good

        packed = packer.pack_body(bytes(u["pa"]), bytes(u["ctrl"]), u["p_unk"], u["prt_common"])
        if list(packed) != body:
            print("FAIL row %d: pack_body round-trip mismatch" % i)
            ok = False

    print("b822 packer:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
