"""Random access response (RAR) MAC PDU: build and parse (3GPP TS 38.321 sec. 6.1.5, 6.2.2, 6.2.3, 7.2).

RAR PDU = subPDUs back to back, then optional padding (all bytes after the subPDU with E=0):

    backoff indicator subPDU (at most one, first) | E T=0 R R BI(4) |                      1 octet
    RAPID subPDU                                  | E T=1 RAPID(6) | MAC RAR (7 octets)   8 octets

    MAC RAR = R(1) | Timing Advance Command(12) | UL grant(27) | Temporary C-RNTI(16)
    UL grant (TS 38.213 Table 8.2-1) = hopping(1) | freq. allocation(14) | time allocation(4) |
                                       MCS(4) | TPC(3) | CSI request(1)

Not covered: the RAPID-only subPDU (SI request acknowledgment: the header alone cannot tell it apart from a
RAPID subPDU, so a PDU that carries one is misread), MsgB (2-step RA), the Rel-16 NR-U ChannelAccess-CPext bits.
Errors raise MacPduError, the same exception as the rest of the MAC library.
"""
from __future__ import annotations
from dataclasses import dataclass

from .macce import MacPduError

# Backoff Parameter values in ms, BI index 0..13 (Table 7.2-1; 13 = 1920 ms was added in Rel-16). 14, 15 reserved.
BI_MS = (5, 10, 20, 30, 40, 60, 80, 120, 160, 240, 320, 480, 960, 1920)
MAC_RAR_BYTES = 7


def _check(name: str, v: int, lo: int, hi: int) -> None:
    if not isinstance(v, int) or not lo <= v <= hi:
        raise MacPduError(f"{name}={v!r} out of range {lo}..{hi}")


def backoff_ms(bi_idx: int) -> int:
    """Backoff Parameter value of a BI index. Reserved indices (14, 15) raise MacPduError."""
    _check("BI index", bi_idx, 0, len(BI_MS) - 1)
    return BI_MS[bi_idx]


def ra_rnti(s_id: int, t_id: int, f_id: int, ul_carrier: int = 0) -> int:
    """RA-RNTI of a PRACH occasion (TS 38.321 sec. 5.1.3).

    s_id: first OFDM symbol (0..13), t_id: first slot in the system frame (0..79, numerology of the PRACH SCS),
    f_id: frequency index (0..7), ul_carrier: 0 = NUL, 1 = SUL.
    """
    _check("s_id", s_id, 0, 13)
    _check("t_id", t_id, 0, 79)
    _check("f_id", f_id, 0, 7)
    _check("ul_carrier", ul_carrier, 0, 1)
    return 1 + s_id + 14 * t_id + 14 * 80 * f_id + 14 * 80 * 8 * ul_carrier


# ---------------------------------------------------------------- 27-bit UL grant for Msg3
def pack_ul_grant(hop: int, freq: int, time: int, mcs: int, tpc: int, csi: int) -> int:
    for name, v, bits in (("hop", hop, 1), ("freq", freq, 14), ("time", time, 4),
                          ("mcs", mcs, 4), ("tpc", tpc, 3), ("csi", csi, 1)):
        _check(name, v, 0, (1 << bits) - 1)
    return (hop << 26) | (freq << 12) | (time << 8) | (mcs << 4) | (tpc << 1) | csi


def unpack_ul_grant(g: int) -> dict:
    _check("UL grant", g, 0, (1 << 27) - 1)
    return {"hop": (g >> 26) & 1, "freq": (g >> 12) & 0x3FFF, "time": (g >> 8) & 0xF,
            "mcs": (g >> 4) & 0xF, "tpc": (g >> 1) & 0x7, "csi": g & 1}


# ---------------------------------------------------------------- MAC RAR (7 octets)
def encode_mac_rar(ta: int, grant: int, tc_rnti: int) -> bytes:
    _check("TA", ta, 0, (1 << 12) - 1)
    _check("UL grant", grant, 0, (1 << 27) - 1)
    _check("TC-RNTI", tc_rnti, 0, 0xFFFF)
    return bytes([
        ta >> 5,                                            # R=0 | TA high 7 bits
        ((ta & 0x1F) << 3) | (grant >> 24),                 # TA low 5 | grant high 3
        (grant >> 16) & 0xFF, (grant >> 8) & 0xFF, grant & 0xFF,
        tc_rnti >> 8, tc_rnti & 0xFF,
    ])


def decode_mac_rar(b: bytes) -> tuple[int, int, int]:
    """(TA, UL grant, TC-RNTI). The R bit is ignored, as a receiver must."""
    if len(b) != MAC_RAR_BYTES:
        raise MacPduError(f"MAC RAR must be {MAC_RAR_BYTES} bytes, got {len(b)}")
    ta = ((b[0] & 0x7F) << 5) | (b[1] >> 3)
    grant = ((b[1] & 0x07) << 24) | (b[2] << 16) | (b[3] << 8) | b[4]
    return ta, grant, (b[5] << 8) | b[6]


@dataclass(frozen=True)
class RarEntry:
    rapid: int          # random access preamble ID, 0..63
    ta: int             # timing advance command, 0..4095
    grant: int          # 27-bit UL grant for Msg3 (pack_ul_grant)
    tc_rnti: int        # temporary C-RNTI

    def __post_init__(self):
        _check("RAPID", self.rapid, 0, 63)
        encode_mac_rar(self.ta, self.grant, self.tc_rnti)     # range checks


# ---------------------------------------------------------------- build / parse
def build_rar_pdu(entries, bi_idx: int | None = None, tb_size: int | None = None) -> bytes:
    """BI subPDU first (if given), then one RAPID subPDU per entry, then zero padding up to `tb_size` bytes."""
    subs = []                                                # (header without E, payload)
    if bi_idx is not None:
        backoff_ms(bi_idx)                                   # rejects reserved indices
        subs.append((bi_idx, b""))                           # T=0
    for e in entries:
        subs.append((0x40 | e.rapid, encode_mac_rar(e.ta, e.grant, e.tc_rnti)))   # T=1
    if not subs:
        raise MacPduError("empty RAR PDU")
    out = bytearray()
    for i, (hdr, payload) in enumerate(subs):
        out += bytes([hdr | (0x80 if i < len(subs) - 1 else 0)]) + payload       # E=1: another subPDU follows
    if tb_size is not None:
        if len(out) > tb_size:
            raise MacPduError(f"RAR PDU of {len(out)} bytes does not fit in a {tb_size}-byte transport block")
        out += bytes(tb_size - len(out))
    return bytes(out)


def parse_rar_pdu(data: bytes) -> tuple[int | None, list[RarEntry]]:
    """(BI index or None, RAR entries). Bytes after the subPDU with E=0 are padding and are ignored.
    A reserved BI index is returned as is; backoff_ms() rejects it."""
    i, bi, entries = 0, None, []
    while True:
        if i >= len(data):
            raise MacPduError(f"truncated RAR PDU: subheader expected at byte {i}")
        h = data[i]
        i += 1
        if h & 0x40:                                         # T=1: RAPID + MAC RAR
            if i + MAC_RAR_BYTES > len(data):
                raise MacPduError(f"truncated MAC RAR at byte {i}")
            ta, grant, tc = decode_mac_rar(data[i:i + MAC_RAR_BYTES])
            entries.append(RarEntry(h & 0x3F, ta, grant, tc))
            i += MAC_RAR_BYTES
        else:                                                # T=0: backoff indicator, only as the first subPDU
            if i != 1:
                raise MacPduError(f"backoff indicator subPDU at byte {i - 1}: it must be first and appear once")
            bi = h & 0x0F
        if not h & 0x80:
            return bi, entries
