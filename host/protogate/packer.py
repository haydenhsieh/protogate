"""Bit-level (un)packing for the OKI MC363/B822 CMD12/CMD11 frame bodies and
cartridge replies.

The real wire bytes are NOT the project's logical Pa/Ca/Pb/Cb vectors directly:
each of 16 consecutive bytes carries a 3-bit "common" header value (identical
across all 16 bytes of one direction) interleaved with 5 "stripped" payload
bits; the 16x5 = 80 stripped bits concatenated are the logical 10-byte vector.

This is a straight port of the bit algebra in
``~/workspace/oki_b822/a112_chip/packer.rb`` (``Command#split_cmd_bits`` /
``#split_resp_bits``, used by the older logic-analyzer capture pipeline that
produced every file under this project's ``captured/``), plus its inverse
(needed to *build* new frames to send to a real cartridge, not just decode
captured ones).

Byte layout of one raw logged CMD12/CMD11 + reply record (49 bytes,
``Command::MIN_LOG_LENGTH`` in ``packer.rb``):

    offset   len   field
    0        1     head (0x03)
    1        1     type (0x11 / 0x12)
    2-9      8     uid
    10-30    21    body (TX: printer -> cartridge)
    31-46    16    chp raw (RX: cartridge reply)
    47-48    2     ignored (not used in authentication)

Within the 21-byte body (body-relative offsets 0-20, i.e. absolute 10-30):
    - ctrl bytes sit at absolute offsets 18, 27, 29, 30
      (body-relative 8, 17, 19, 20) -- copied through raw, 4 bytes.
    - p_unk is absolute offset 28 (body-relative 18).
    - the other 16 body bytes (body-relative 0-7, 9-16) are "prt_raw", bit-split
      into Pa/Pb (``split_cmd_bits``).

The 16-byte "chp raw" reply window is bit-split into Ca/Cb (``split_resp_bits``).
ProtoGate's firmware reply additionally carries a leading 0xFF preamble byte and
may have trailing fill bytes; strip those to get to the 16-byte window.

**Both directions are now confirmed on real data.** ``split_resp_bits``/
``unpack_reply`` (RX, cartridge -> printer) was confirmed first: run against a
real cartridge session, both Ca and Cb's raw 16-byte reply windows decode with
a perfectly uniform 3-bit common header across all 16 bytes at exactly this
module's assumed bit positions. ``split_cmd_bits``/``unpack_body``/
``pack_body`` (TX, printer -> cartridge) is confirmed the same way against
``~/workspace/protogate/tmp/a112_captured_samples.txt`` -- the actual raw
capture file that feeds ``packer.rb`` to produce this project's
``captured/*.txt`` dataset: all 130 real rows unpack with a uniform 3-bit
common header and round-trip exactly through ``pack_body`` back to the
original bytes (see ``host/tests/test_b822_packer.py``).

An earlier version of this note said the TX side was unconfirmed, based on
replaying CMD12/CMD11 bodies from the reference ``.sr`` captures
(``protogate/tmp/mc363.sr`` / ``host/b822_cmd12_cmd11_waveform.sr`` /
``host/b822_session_full_primed.sr`` -- all three replay the same reused
21-byte CMD12 body with different UIDs spliced in, not independent sessions)
through a live session -- those specific frame bytes do **not** decode
consistently with this model (no 3-bit common position is uniform,
exhaustively checked). Per the project owner, the MC363 (that reference
capture's source) may differ from the B822 in its common-bits convention, so
this is an expected device difference, not a flaw in the model, and isn't
worth chasing further.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

# Bit positions (MSB-first within each byte, matching Ruby's `unpack("B*")`)
# that make up the "common" header value vs. the "stripped" payload value.
CMD_COMMON_BITS = (1, 2, 4)
CMD_STRIPPED_BITS = (0, 3, 5, 6, 7)
RESP_COMMON_BITS = (3, 4, 5)
RESP_STRIPPED_BITS = (0, 1, 2, 6, 7)

# Absolute offsets of the 4 ctrl bytes and the 1 p_unk byte within the 21-byte
# body (body-relative, i.e. already offset by -10 from packer.rb's raw_bytes).
BODY_CTRL_OFFSETS = (8, 17, 19, 20)
BODY_PUNK_OFFSET = 18
BODY_LEN = 21
PRT_RAW_LEN = 16   # 21 - 4 ctrl bytes - 1 p_unk byte
CHP_RAW_LEN = 16


def _byte_bits(b: int) -> List[int]:
    """8 bits of ``b``, MSB-first (index 0 = MSB), as a list of 0/1 ints."""
    return [(b >> (7 - i)) & 1 for i in range(8)]


def _bits_to_int(bits: Sequence[int]) -> int:
    v = 0
    for bit in bits:
        v = (v << 1) | (bit & 1)
    return v


def _bits_to_bytes(bits: Sequence[int]) -> bytes:
    if len(bits) % 8 != 0:
        raise ValueError("bit count must be a multiple of 8, got %d" % len(bits))
    return bytes(_bits_to_int(bits[i:i + 8]) for i in range(0, len(bits), 8))


def _int_to_bits(v: int, n: int) -> List[int]:
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def split_cmd_bits(cmd_bytes: Sequence[int]) -> Tuple[int, bytes]:
    """Split 16 raw printer-direction bytes into (common, stripped-payload).

    ``common`` is the 3-bit header value, which must be identical across all
    16 bytes (raises ValueError otherwise, mirroring packer.rb's
    ``raise "... prt common unmatch"``). ``stripped`` is the 16x5 = 80
    stripped bits packed into 10 bytes (Pa or Pb).
    """
    if len(cmd_bytes) != PRT_RAW_LEN:
        raise ValueError("expected %d bytes, got %d" % (PRT_RAW_LEN, len(cmd_bytes)))
    commons: List[int] = []
    stripped_bits: List[int] = []
    for byte in cmd_bytes:
        bits = _byte_bits(byte)
        commons.append(_bits_to_int([bits[i] for i in CMD_COMMON_BITS]))
        stripped_bits.extend(bits[i] for i in CMD_STRIPPED_BITS)
    if any(c != commons[0] for c in commons):
        raise ValueError("prt common unmatch: %r" % commons)
    return commons[0], _bits_to_bytes(stripped_bits)


def join_cmd_bits(common: int, payload: Sequence[int]) -> bytes:
    """Inverse of :func:`split_cmd_bits`: rebuild 16 raw bytes from a 3-bit
    ``common`` header value and a 10-byte Pa/Pb payload."""
    if len(payload) != 10:
        raise ValueError("payload must be 10 bytes, got %d" % len(payload))
    common_bits = _int_to_bits(common, 3)
    payload_bits = []
    for byte in payload:
        payload_bits.extend(_byte_bits(byte))
    out = []
    for i in range(PRT_RAW_LEN):
        bits = [0] * 8
        for j, pos in enumerate(CMD_COMMON_BITS):
            bits[pos] = common_bits[j]
        for j, pos in enumerate(CMD_STRIPPED_BITS):
            bits[pos] = payload_bits[i * 5 + j]
        out.append(_bits_to_int(bits))
    return bytes(out)


def split_resp_bits(resp_bytes: Sequence[int]) -> Tuple[int, bytes]:
    """Split 16 raw cartridge-reply bytes into (common, stripped-payload).

    Mirrors packer.rb's ``split_resp_bits``. Unlike the cmd side, packer.rb
    only *warns* (does not raise) if the common bits disagree across bytes;
    we do the same here and return the first byte's common value."""
    if len(resp_bytes) != CHP_RAW_LEN:
        raise ValueError("expected %d bytes, got %d" % (CHP_RAW_LEN, len(resp_bytes)))
    commons: List[int] = []
    stripped_bits: List[int] = []
    for byte in resp_bytes:
        bits = _byte_bits(byte)
        commons.append(_bits_to_int([bits[i] for i in RESP_COMMON_BITS]))
        stripped_bits.extend(bits[i] for i in RESP_STRIPPED_BITS)
    return commons[0], _bits_to_bytes(stripped_bits)


def join_resp_bits(common: int, payload: Sequence[int]) -> bytes:
    """Inverse of :func:`split_resp_bits`."""
    if len(payload) != 10:
        raise ValueError("payload must be 10 bytes, got %d" % len(payload))
    common_bits = _int_to_bits(common, 3)
    payload_bits = []
    for byte in payload:
        payload_bits.extend(_byte_bits(byte))
    out = []
    for i in range(CHP_RAW_LEN):
        bits = [0] * 8
        for j, pos in enumerate(RESP_COMMON_BITS):
            bits[pos] = common_bits[j]
        for j, pos in enumerate(RESP_STRIPPED_BITS):
            bits[pos] = payload_bits[i * 5 + j]
        out.append(_bits_to_int(bits))
    return bytes(out)


# ---------------------------------------------------------------------------
# Body-level (21-byte) helpers
# ---------------------------------------------------------------------------
def unpack_body(body: Sequence[int]) -> dict:
    """Unpack a 21-byte CMD12/CMD11 body into its logical fields.

    Returns a dict with ``pa`` (10 bytes), ``ctrl`` (4 bytes), ``p_unk`` (1
    byte, int) and ``prt_common`` (3-bit int, the per-frame header constant).
    """
    body = list(body)
    if len(body) != BODY_LEN:
        raise ValueError("body must be %d bytes, got %d" % (BODY_LEN, len(body)))
    ctrl = bytes(body[i] for i in BODY_CTRL_OFFSETS)
    p_unk = body[BODY_PUNK_OFFSET]
    prt_raw = [b for i, b in enumerate(body)
               if i not in BODY_CTRL_OFFSETS and i != BODY_PUNK_OFFSET]
    prt_common, pa = split_cmd_bits(prt_raw)
    return {"pa": pa, "ctrl": ctrl, "p_unk": p_unk, "prt_common": prt_common}


def pack_body(pa: Sequence[int], ctrl: Sequence[int], p_unk: int, prt_common: int) -> bytes:
    """Inverse of :func:`unpack_body`: build a 21-byte CMD12/CMD11 body."""
    if len(ctrl) != 4:
        raise ValueError("ctrl must be 4 bytes, got %d" % len(ctrl))
    prt_raw = join_cmd_bits(prt_common, pa)
    out = [0] * BODY_LEN
    for i, off in enumerate(BODY_CTRL_OFFSETS):
        out[off] = ctrl[i] & 0xFF
    out[BODY_PUNK_OFFSET] = p_unk & 0xFF
    it = iter(prt_raw)
    for i in range(BODY_LEN):
        if i in BODY_CTRL_OFFSETS or i == BODY_PUNK_OFFSET:
            continue
        out[i] = next(it)
    return bytes(out)


# ---------------------------------------------------------------------------
# Reply-level helpers
# ---------------------------------------------------------------------------
def unpack_reply(raw: Sequence[int]) -> dict:
    """Unpack a firmware-decoded raw reply (leading 0xFF preamble, the
    16-byte chp-raw window, optional trailing fill) into ``chp`` (10 bytes,
    the logical Ca/Cb) and ``chp_common`` (3-bit int).

    Strips a single leading 0xFF preamble byte if present, then takes exactly
    the next 16 bytes as the chp-raw window (any further bytes are trailing
    fill and are ignored, matching packer.rb's handling of the 2 trailing
    ignored bytes in the full 49-byte record).
    """
    raw = list(raw)
    if raw and raw[0] == 0xFF:
        raw = raw[1:]
    if len(raw) < CHP_RAW_LEN:
        raise ValueError("reply too short after preamble strip: %d bytes" % len(raw))
    chp_common, chp = split_resp_bits(raw[:CHP_RAW_LEN])
    return {"chp": chp, "chp_common": chp_common}
