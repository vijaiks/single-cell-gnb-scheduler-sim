"""Pack / unpack DL-SCH and UL-SCH MAC PDUs (3GPP TS 38.321 sec. 6.1.2, 6.2.1).

    fixed-size CE, padding, UL CCCH  | R R LCID(6) |                      (no L field)
    MAC SDU, variable-size CE        | R F LCID(6) | L (8 bits if F=0, 16 bits if F=1) |

Order: DL = CEs, SDUs, padding. UL = SDUs, CEs, padding.
The CEs are in macce.py. Unsupported LCIDs raise MacPduError: their length is unknown here, so nothing
after them could be located.
"""
from __future__ import annotations
from dataclasses import dataclass

from .macce import (CE_LENGTH, CE_TYPES, DL, UL, MacPduError, decode_ce,
                    TACommand, SCellActivation)      # noqa: F401  (re-exported)

LCID_MAX_SDU = 32          # 0 = CCCH, 1-32 = logical channel identity (SRBs, DRBs)
LCID_UL_CCCH48 = 52
LCID_PADDING = 63
UL_CCCH_BYTES = {0: 8, LCID_UL_CCCH48: 6}      # UL CCCH SDUs are fixed-size and have no L field


@dataclass(frozen=True)
class MacSdu:
    lcid: int
    data: bytes

    def __post_init__(self):
        if not (0 <= self.lcid <= LCID_MAX_SDU or self.lcid == LCID_UL_CCCH48):
            raise MacPduError(f"SDU LCID {self.lcid} out of range 0..{LCID_MAX_SDU}")
        if not 1 <= len(self.data) <= 0xFFFF:
            raise MacPduError(f"SDU length {len(self.data)} out of range 1..65535")


@dataclass(frozen=True)
class Padding:
    length: int            # filler bytes after the one-byte subheader

    def __post_init__(self):
        if self.length < 0:
            raise MacPduError("padding length must be >= 0")


def _check_direction(direction):
    if direction not in (DL, UL):
        raise MacPduError(f"direction must be 'DL' or 'UL', not {direction!r}")


def _with_length(lcid: int, data: bytes) -> bytes:
    n = len(data)
    head = bytes([lcid, n]) if n <= 0xFF else bytes([0x40 | lcid]) + n.to_bytes(2, "big")
    return head + data


def pack_subpdu(s, direction: str = DL) -> bytes:
    _check_direction(direction)
    if isinstance(s, CE_TYPES):
        if s.direction != direction:
            raise MacPduError(f"{type(s).__name__} is a {s.direction} CE, not {direction}")
        data = s.encode()
        fixed = CE_LENGTH[direction, s.lcid] is not None
        return bytes([s.lcid]) + data if fixed else _with_length(s.lcid, data)
    if isinstance(s, MacSdu):
        if direction == UL and s.lcid in UL_CCCH_BYTES:
            if len(s.data) != UL_CCCH_BYTES[s.lcid]:
                raise MacPduError(f"UL CCCH (LCID {s.lcid}) must be {UL_CCCH_BYTES[s.lcid]} bytes")
            return bytes([s.lcid]) + s.data
        if s.lcid > LCID_MAX_SDU:
            raise MacPduError(f"LCID {s.lcid} is not a {direction} SDU")
        return _with_length(s.lcid, s.data)
    if isinstance(s, Padding):
        return bytes([LCID_PADDING]) + bytes(s.length)
    raise MacPduError(f"cannot pack {type(s).__name__}")


def _rank(s, direction) -> int:
    if isinstance(s, Padding):
        return 2
    return int(isinstance(s, MacSdu) == (direction == DL))


def pack(subpdus, tb_bytes: int | None = None, direction: str = DL) -> bytes:
    """Encode subPDUs. With `tb_bytes` (= TBS // 8), padding is added to fill the transport block exactly."""
    subpdus = list(subpdus)
    pdu = b"".join(pack_subpdu(s, direction) for s in subpdus)
    ranks = [_rank(s, direction) for s in subpdus]
    if ranks != sorted(ranks) or ranks.count(2) > 1:
        want = "MAC CEs, then SDUs" if direction == DL else "SDUs, then MAC CEs"
        raise MacPduError(f"{direction} order must be {want}, then at most one padding")
    if tb_bytes is None:
        return pdu
    if len(pdu) > tb_bytes:
        raise MacPduError(f"PDU of {len(pdu)} bytes does not fit in a {tb_bytes}-byte transport block")
    if len(pdu) < tb_bytes:
        if 2 in ranks:
            raise MacPduError("give either explicit Padding or tb_bytes to pad to, not both")
        pdu += pack_subpdu(Padding(tb_bytes - len(pdu) - 1), direction)
    return pdu


def max_payload_for(space: int) -> int:
    """Largest payload that fits in `space` bytes together with its 2- or 3-byte subheader."""
    if space <= 2:
        return 0
    return space - 2 if space - 2 <= 255 else min(space - 3, 0xFFFF)


def build_tb(tb_bytes: int, ces, sdu_alloc, rlc_pull, direction: str = DL) -> bytes:
    """Build one transport block.

    ces       : CE objects
    sdu_alloc : [(lcid, bytes)] from LCP, subheader included
    rlc_pull  : rlc_pull(lcid, n) -> up to n bytes. Not called unless the CEs fit, so no data is lost.
    """
    ces = list(ces)
    left = tb_bytes - sum(len(pack_subpdu(c, direction)) for c in ces)
    if left < 0:
        raise MacPduError(f"CEs do not fit in a {tb_bytes}-byte transport block")
    sdus = []
    for lcid, alloc in sdu_alloc:
        net = max_payload_for(min(alloc, left))
        data = rlc_pull(lcid, net) if net else b""
        if data:
            sdus.append(MacSdu(lcid, bytes(data)))
            left -= len(pack_subpdu(sdus[-1], direction))
    return pack(ces + sdus if direction == DL else sdus + ces, tb_bytes, direction)


def unpack(pdu: bytes, direction: str = DL) -> list:
    """Decode a MAC PDU. Reserved (R) bits and padding contents are ignored, as a receiver must."""
    _check_direction(direction)
    out, i = [], 0

    def take(n: int, what: str) -> bytes:
        nonlocal i
        if i + n > len(pdu):
            raise MacPduError(f"truncated {what} at byte {i}: need {n}, have {len(pdu) - i}")
        i += n
        return bytes(pdu[i - n:i])

    while i < len(pdu):
        at, head = i, take(1, "subheader")[0]
        lcid = head & 0x3F
        if lcid == LCID_PADDING:
            out.append(Padding(len(pdu) - i))
            break
        is_ce = (direction, lcid) in CE_LENGTH
        if is_ce:
            n = CE_LENGTH[direction, lcid]
        elif direction == UL and lcid in UL_CCCH_BYTES:
            n = UL_CCCH_BYTES[lcid]
        elif lcid <= LCID_MAX_SDU:
            n = None
        else:
            raise MacPduError(f"unsupported {direction} LCID {lcid} at byte {at}")
        if n is None:
            n = int.from_bytes(take(2 if head & 0x40 else 1, "length field"), "big")
        data = take(n, f"{'CE' if is_ce else 'SDU'} (LCID {lcid})")
        out.append(decode_ce(direction, lcid, data) if is_ce else MacSdu(lcid, data))
    return out


def dispatch(subpdus, rlc_rx, ce_rx) -> None:
    """SDUs go to rlc_rx(lcid, data); CEs go to ce_rx(ce). Padding is dropped."""
    for s in subpdus:
        if isinstance(s, MacSdu):
            rlc_rx(s.lcid, s.data)
        elif not isinstance(s, Padding):
            ce_rx(s)
