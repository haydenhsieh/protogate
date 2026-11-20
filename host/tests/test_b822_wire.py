"""Off-hardware validation of the b822 firmware wire logic.

Synthesises the ideal (A, B) waveform the firmware would drive for a frame
(using the same encoding rules as the C code), writes a sigrok .sr, and decodes
it with the real la_convert `mc363_b822` decoder. The decoded printer bytes must
equal the input frame -- proving the TX framing/bit-order/SOF logic is correct.

Run:  python -I host/tests/test_b822_wire.py
Needs: sigrok-cli, and the mc363_b822 decoder on SIGROKDECODE_DIR.
"""

import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protogate.b822 import build_frame, synth_waveform, CMD12, DEFAULT_UID

DECODER_DIR = Path.home() / "workspace/la_convert/decoders"
SAMPLE_HZ = 25_000_000


def write_sr(path: Path, a, b, sample_hz: int) -> None:
    """Write a minimal sigrok .sr with A on D1 and B on D2 (D0 unused)."""
    buf = bytes(((a[i] & 1) << 1) | ((b[i] & 1) << 2) for i in range(len(a)))
    meta = (
        "[global]\nsigrok version=0.7.2\n\n[device 1]\n"
        "capturefile=logic-1-1\ntotal probes=3\nsamplerate=%d Hz\n"
        "total analog=0\nunitsize=1\n"
        "probe1=D0\nprobe2=D1\nprobe3=D2\n" % sample_hz
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version", "2")
        z.writestr("metadata", meta)
        z.writestr("logic-1-1", buf)


def decode(path: Path):
    env = dict(os.environ, SIGROKDECODE_DIR=str(DECODER_DIR))
    out = subprocess.run(
        ["sigrok-cli", "-i", str(path), "-P", "mc363_b822:a=D1:b=D2",
         "-A", "mc363_b822"],
        capture_output=True, text=True, env=env, check=True,
    ).stdout
    return [int(m, 16) for m in re.findall(r"Printer 0x([0-9A-Fa-f]{2})", out)]


def main() -> int:
    # A real decoded CMD12 body (21 bytes) from mc363.sr frame 7.
    body = [0x4B, 0x72, 0x23, 0x29, 0x81, 0x24, 0x51, 0x9A, 0x48, 0x24, 0xE1,
            0x47, 0xDA, 0xCE, 0x26, 0x31, 0x11, 0xBC, 0x81, 0xDA, 0x0F]
    frame = build_frame(CMD12, body, DEFAULT_UID)
    a, b = synth_waveform(frame, sample_hz=SAMPLE_HZ)

    with tempfile.TemporaryDirectory() as td:
        sr = Path(td) / "synth.sr"
        write_sr(sr, a, b, SAMPLE_HZ)
        got = decode(sr)

    exp = frame
    ok = got == exp
    print("frame  :", " ".join("%02X" % x for x in exp))
    print("decoded:", " ".join("%02X" % x for x in got))
    print("samples:", len(a), " result:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
