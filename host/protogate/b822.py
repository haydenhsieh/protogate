"""Host-side helpers for the OKI MC363/B822 printer-emulation bus.

The ProtoGate firmware is byte-level: it takes the complete printer frame byte
stream and returns the cartridge's decoded reply. This module builds those byte
streams, decodes the firmware's per-phase notch bits (``ebits``), and emits capture
rows in the project's 12-column CSV format.

Wire conventions match the validated ``la_convert`` ``mc363_b822`` decoder and
the real chip firmware (OKI_MC363/MEGAWIN):

  * two wires take turns, one high pulse each, bus idles both-low;
  * printer bit = pulse WIDTH on the active wire (narrow = 0, wide = 1);
  * SOF = a long pulse; bytes are 10-bit words (start 0, 8 data LSB-first, stop 1);
  * cartridge reply = fixed-width pulses, a short notch = 1, clean = 0.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import packer

# UID seen in the reference captures (03_12_11分析.txt / mc363.sr frames). Each
# physical cartridge has its OWN id, so read it with READ_ID_CMD instead of
# assuming this one.
DEFAULT_UID: Tuple[int, ...] = (0x41, 0xBE, 0x08, 0x12, 0x04, 0x0B, 0x00, 0xE0)

CMD12 = 0x12
CMD11 = 0x11
HEAD = 0x03

# Read-ID command. The cartridge replies `50 XX XX <8-word ID> ...`; the 8 words
# after the leading 0x50 are the ID, which must be used as the UID in CMD12/CMD11.
# UID-independent and deterministic, so it is the best first command to validate
# the link against a real cartridge.
READ_ID_CMD: Tuple[int, ...] = (0x5C, 0x01, 0x99, 0x64)


def extract_id(reply: Sequence[int]) -> List[int]:
    """Pull the 8-byte cartridge ID out of a read-ID reply (`50` then the ID)."""
    r = list(reply)
    for i, b in enumerate(r):
        if b == 0x50:
            return r[i + 1:i + 9]
    return []


# ---------------------------------------------------------------------------
# Frame assembly
# ---------------------------------------------------------------------------
def build_frame(cmd: int, body: Sequence[int],
                uid: Sequence[int] = DEFAULT_UID) -> List[int]:
    """Assemble a printer frame byte stream: ``03 <cmd> <uid8> <body>``.

    ``body`` is the command-specific payload exactly as it appears on the wire
    (the 21-byte authentication body for CMD12/CMD11). This is the byte stream
    the firmware transmits as 10-bit LSB-first words after the SOF. To send a
    body built from a chosen Pa/ctrl rather than one replayed from a real
    capture, use :func:`build_auth_frame`.
    """
    uid = list(uid)
    if len(uid) != 8:
        raise ValueError("uid must be 8 bytes")
    return [HEAD & 0xFF, cmd & 0xFF, *(b & 0xFF for b in uid),
            *(b & 0xFF for b in body)]


def build_auth_frame(cmd: int, pa: Sequence[int], ctrl: Sequence[int],
                     uid: Sequence[int] = DEFAULT_UID,
                     p_unk: int = 0, prt_common: int = 0) -> List[int]:
    """Build a CMD12/CMD11 frame from a chosen logical Pa (or Pb) and ctrl,
    bit-packing them into the 21-byte body (see ``packer.pack_body``).

    ``prt_common``/``p_unk`` are the body's two free-looking fields: across
    130 real captured CMD12/CMD11 rows (``~/workspace/protogate/tmp/
    a112_captured_samples.txt``) both vary close to uniformly at random,
    uncorrelated with ctrl, Pa, or CMD12-vs-CMD11, with no observed
    constraint -- so any fixed value (the default, 0) is as good as another
    for a frame we're constructing rather than replaying. ``pack_body`` is
    confirmed against that same real sample file: all 130 rows round-trip
    through it exactly (see ``host/tests/test_b822_packer.py``), though no
    frame built this way has yet been confirmed accepted by a real cartridge.
    """
    body = packer.pack_body(bytes(b & 0xFF for b in pa), bytes(b & 0xFF for b in ctrl),
                            p_unk, prt_common)
    return build_frame(cmd, body, uid)


# ---------------------------------------------------------------------------
# Frame checksum
# ---------------------------------------------------------------------------
def crc16_x25(data: Sequence[int]) -> int:
    """CRC-16/X-25 (polynomial 0x1021 reflected, init 0xFFFF, final XOR 0xFFFF).

    The trailing two bytes of the captured ``0D``, ``5D``, ``5C``, ``5A``, ``50``,
    ``C2``, ``03 20`` and ``03 23`` frames are this CRC over every earlier byte of the
    frame (command byte included), low byte first. The 31-byte CMD12/CMD11 frames do
    NOT follow it: their 21-byte body is authentication data, and no standard CRC-16
    over any range of those frames matches."""
    c = 0xFFFF
    for b in data:
        c ^= b & 0xFF
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    return c ^ 0xFFFF


def seal_frame(frame: Sequence[int]) -> List[int]:
    """``frame`` with its CRC-16/X-25 appended, low byte first (see ``crc16_x25``).
    Use it for the frames that carry a CRC, e.g. ``seal_frame([0x5A, *uid])``."""
    c = crc16_x25(frame)
    return [b & 0xFF for b in frame] + [c & 0xFF, c >> 8]


# ---------------------------------------------------------------------------
# RLE sample dumps (firmware ``b822.probe`` output) -> flat samples
# ---------------------------------------------------------------------------
def expand_rle(pairs: Sequence[int]) -> List[int]:
    """Expand the firmware's ``[state, runlen, state, runlen, ...]`` RLE into a
    flat list of 2-bit samples (bit0 = DATA1, bit1 = DATA2)."""
    out: List[int] = []
    it = iter(pairs)
    for state, run in zip(it, it):
        out.extend([state] * run)
    return out


# ---------------------------------------------------------------------------
# Reply decode (cartridge direction)
# ---------------------------------------------------------------------------
def decode_ebits(bits: str, phase: Optional[int] = None) -> List[int]:
    """Decode the firmware's per-phase notch bits into bytes.

    ``bits`` is the ``ebits`` string a ``raw`` request returns: one ``0``/``1``
    character per RX clock phase (``1`` = the cartridge notched that phase). Bytes are
    10-bit words (start 0, 8 data LSB-first, stop 1). ``phase`` is the bit offset of
    the first word; by default it is the offset that gives the most well-formed words,
    which is how the firmware picks it on-device. The result includes any all-ones
    preamble word ahead of the reply (it decodes as ``0xFF``)."""
    n = len(bits)
    if phase is None:
        best, phase = -1, 0
        for ph in range(10):
            good = sum(1 for k in range(ph, n - 9, 10)
                       if bits[k] == "0" and bits[k + 9] == "1")
            if good > best:
                best, phase = good, ph
    out: List[int] = []
    for k in range(phase, n - 9, 10):
        v = 0
        for b in range(8):
            if bits[k + 1 + b] == "1":
                v |= 1 << b
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# TX waveform synthesis (for off-hardware validation through pd.py)
# ---------------------------------------------------------------------------
def synth_waveform(frame: Sequence[int], *, sample_hz: int = 25_000_000,
                   narrow_ns: int = 4700, wide_ns: int = 14200,
                   sof_ns: int = 43000, gap_ns: int = 300
                   ) -> Tuple[List[int], List[int]]:
    """Synthesise the ideal (A, B) sample traces the firmware would drive for a
    frame, using the same encoding rules. Feed the result to the la_convert
    ``mc363_b822`` decoder to confirm it round-trips back to ``frame``."""
    a: List[int] = []
    b: List[int] = []

    def ns(x: int) -> int:
        return max(1, round(x * sample_hz / 1_000_000_000))

    def push(level_a: int, level_b: int, count: int) -> None:
        a.extend([level_a] * count)
        b.extend([level_b] * count)

    # SOF: long A-high then long B-high.
    push(1, 0, ns(sof_ns))
    push(0, 1, ns(sof_ns))
    push(0, 0, ns(gap_ns))

    phase = 0  # 0 -> DATA1(A), 1 -> DATA2(B)

    def symbol(bit: int) -> None:
        nonlocal phase
        width = ns(wide_ns if bit else narrow_ns)
        if phase == 0:
            push(1, 0, width)
        else:
            push(0, 1, width)
        push(0, 0, ns(gap_ns))
        phase ^= 1

    for byte in frame:
        symbol(0)                       # start
        for k in range(8):              # 8 data bits LSB-first
            symbol((byte >> k) & 1)
        symbol(1)                       # stop

    # End marker: a trailing SOF (long A then long B) finishes the last stop bit
    # and closes the frame, then the bus idles.
    push(1, 0, ns(sof_ns))
    push(0, 1, ns(sof_ns))
    push(0, 0, ns(sof_ns))
    return a, b


# ---------------------------------------------------------------------------
# CSV emission (12-column project format)
# ---------------------------------------------------------------------------
CSV_HEADER = "xp,xc,pa,ca,pb,cb,cta,ctb,pua,cua,pub,cub"


def _hexs(bs: Sequence[int]) -> str:
    return " ".join("%02x" % (x & 0xFF) for x in bs)


def _xor(x: Sequence[int], y: Sequence[int]) -> List[int]:
    return [(a ^ b) & 0xFF for a, b in zip(x, y)]


def session_csv_row(pa: Sequence[int], cta: Sequence[int], ca: Sequence[int],
                    pb: Sequence[int], ctb: Sequence[int], cb: Sequence[int],
                    pua: int = 0, cua: int = 0, pub: int = 0, cub: int = 0
                    ) -> str:
    """Build one CSV row (project column order). pa/pb/ca/cb are 10-byte vectors,
    cta/ctb are the 4-byte control params; unknown columns default to 0."""
    xp = _xor(pa, pb)
    xc = _xor(ca, cb)
    fields = [_hexs(xp), _hexs(xc), _hexs(pa), _hexs(ca), _hexs(pb), _hexs(cb),
              _hexs(cta), _hexs(ctb), "%02x" % pua, "%02x" % cua,
              "%02x" % pub, "%02x" % cub]
    return ",".join(fields)
