"""MAC control elements (3GPP TS 38.321 sec. 6.1.3): one class per CE, for DL-SCH and UL-SCH.

Every CE has `lcid`, `direction`, `encode()` -> payload bytes (no subheader) and `decode(payload)`.
The same LCID means different things in DL and UL, so look-ups always need the direction.
Use decode_ce(direction, lcid, payload) to decode, and macpdu.pack / unpack for whole PDUs.

CEs whose fields are not modelled (beam / CSI activation) are carried as RawCE with the right length rules.
Not covered: eLCID (LCID 33/34) and other Rel-16+ CEs; type-2 PH in the multiple-entry PHR.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import ClassVar

from .bsr_tables import long_bytes, long_index, short_bytes, short_index

DL, UL = "DL", "UL"


class MacPduError(ValueError):
    """A MAC PDU or CE cannot be encoded or decoded."""


def _check(name, v, lo, hi):
    if not lo <= v <= hi:
        raise MacPduError(f"{name} {v} out of range {lo}..{hi}")


def _bitmap(indices) -> int:
    return sum(1 << i for i in indices)


def _bits(v: int, lo: int, hi: int) -> frozenset:
    return frozenset(i for i in range(lo, hi + 1) if v >> i & 1)


# ---------------------------------------------------------------- DL-SCH
@dataclass(frozen=True)
class TACommand:
    tag_id: int
    ta: int
    lcid: ClassVar[int] = 61
    direction: ClassVar[str] = DL

    def __post_init__(self):
        _check("tag_id", self.tag_id, 0, 3)
        _check("ta", self.ta, 0, 63)

    def encode(self) -> bytes:
        return bytes([self.tag_id << 6 | self.ta])

    @classmethod
    def decode(cls, p):
        return cls(p[0] >> 6, p[0] & 0x3F)


@dataclass(frozen=True)
class SCellActivation:
    """One octet, C7..C1 R: SCells to activate (all others are deactivated)."""
    active: frozenset
    lcid: ClassVar[int] = 58
    direction: ClassVar[str] = DL
    N_OCTETS: ClassVar[int] = 1

    def __post_init__(self):
        object.__setattr__(self, "active", frozenset(self.active))
        for i in self.active:
            _check("SCell index", i, 1, 8 * self.N_OCTETS - 1)

    def encode(self) -> bytes:
        return _bitmap(self.active).to_bytes(self.N_OCTETS, "little")

    @classmethod
    def decode(cls, p):
        return cls(_bits(int.from_bytes(p, "little"), 1, 8 * cls.N_OCTETS - 1))


@dataclass(frozen=True)
class SCellActivation4(SCellActivation):
    """Four octets: C7..C1 R | C15..C8 | C23..C16 | C31..C24."""
    lcid: ClassVar[int] = 57
    N_OCTETS: ClassVar[int] = 4


@dataclass(frozen=True)
class DuplicationActivation:
    """One octet D7..D0: DRBs with PDCP duplication active."""
    active: frozenset
    lcid: ClassVar[int] = 56
    direction: ClassVar[str] = DL

    def __post_init__(self):
        object.__setattr__(self, "active", frozenset(self.active))
        for i in self.active:
            _check("DRB position", i, 0, 7)

    def encode(self) -> bytes:
        return bytes([_bitmap(self.active)])

    @classmethod
    def decode(cls, p):
        return cls(_bits(p[0], 0, 7))


class _Empty:
    def encode(self) -> bytes:
        return b""

    @classmethod
    def decode(cls, p):
        return cls()


@dataclass(frozen=True)
class DRXCommand(_Empty):
    lcid: ClassVar[int] = 60
    direction: ClassVar[str] = DL


@dataclass(frozen=True)
class LongDRXCommand(_Empty):
    lcid: ClassVar[int] = 59
    direction: ClassVar[str] = DL


@dataclass(frozen=True)
class ContentionResolution:
    """The 48 bits of the UL CCCH SDU echoed back in Msg4."""
    identity: bytes
    lcid: ClassVar[int] = 62
    direction: ClassVar[str] = DL

    def __post_init__(self):
        if len(self.identity) != 6:
            raise MacPduError("contention resolution identity must be 6 bytes")

    def encode(self) -> bytes:
        return bytes(self.identity)

    @classmethod
    def decode(cls, p):
        return cls(bytes(p))


@dataclass(frozen=True)
class RecommendedBitRate:
    """LCID(6) UL/DL(1) BitRate(6) R(3). `uplink` says which link the rate is about."""
    channel: int
    uplink: bool
    rate_index: int
    lcid: ClassVar[int] = 47
    direction: ClassVar[str] = DL

    def __post_init__(self):
        _check("channel", self.channel, 0, 63)
        _check("rate_index", self.rate_index, 0, 63)

    def encode(self) -> bytes:
        return (self.channel << 10 | int(self.uplink) << 9 | self.rate_index << 3).to_bytes(2, "big")

    @classmethod
    def decode(cls, p):
        v = int.from_bytes(p, "big")
        return cls(v >> 10, bool(v >> 9 & 1), v >> 3 & 0x3F)


# ---------------------------------------------------------------- UL-SCH
@dataclass(frozen=True)
class RecommendedBitRateQuery(RecommendedBitRate):
    lcid: ClassVar[int] = 53
    direction: ClassVar[str] = UL


@dataclass(frozen=True)
class CRnti:
    rnti: int
    lcid: ClassVar[int] = 58
    direction: ClassVar[str] = UL

    def __post_init__(self):
        _check("rnti", self.rnti, 0, 0xFFFF)

    def encode(self) -> bytes:
        return self.rnti.to_bytes(2, "big")

    @classmethod
    def decode(cls, p):
        return cls(int.from_bytes(p, "big"))


@dataclass(frozen=True)
class ConfiguredGrantConfirmation(_Empty):
    lcid: ClassVar[int] = 55
    direction: ClassVar[str] = UL


@dataclass(frozen=True)
class ShortBSR:
    """LCG ID(3) | Buffer Size index(5)."""
    lcg: int
    index: int
    lcid: ClassVar[int] = 61
    direction: ClassVar[str] = UL

    def __post_init__(self):
        _check("lcg", self.lcg, 0, 7)
        _check("index", self.index, 0, 31)

    @classmethod
    def from_bytes(cls, lcg: int, n_bytes: int):
        return cls(lcg, short_index(n_bytes))

    def n_bytes(self) -> int:
        return short_bytes(self.index)

    def encode(self) -> bytes:
        return bytes([self.lcg << 5 | self.index])

    @classmethod
    def decode(cls, p):
        return cls(p[0] >> 5, p[0] & 0x1F)


@dataclass(frozen=True)
class ShortTruncatedBSR(ShortBSR):
    lcid: ClassVar[int] = 59


@dataclass(frozen=True)
class LongBSR:
    """LCG7..LCG0 bitmap, then one 8-bit Buffer Size index per set bit, lowest LCG first."""
    sizes: dict                # lcg -> 8-bit index
    lcid: ClassVar[int] = 62
    direction: ClassVar[str] = UL

    def __post_init__(self):
        object.__setattr__(self, "sizes", dict(sorted(self.sizes.items())))
        for g, i in self.sizes.items():
            _check("lcg", g, 0, 7)
            _check("index", i, 0, 254)

    @classmethod
    def from_bytes(cls, per_lcg: dict):
        """per_lcg: lcg -> bytes waiting. LCGs without data are left out."""
        return cls({g: long_index(n) for g, n in per_lcg.items() if n > 0})

    def n_bytes(self) -> dict:
        return {g: long_bytes(i) for g, i in self.sizes.items()}

    def encode(self) -> bytes:
        return bytes([_bitmap(self.sizes), *self.sizes.values()])

    @classmethod
    def decode(cls, p):
        lcgs = sorted(_bits(p[0], 0, 7)) if p else []
        if len(p) != 1 + len(lcgs):
            raise MacPduError(f"long BSR: {len(p)} bytes do not match its LCG bitmap")
        return cls(dict(zip(lcgs, p[1:])))


@dataclass(frozen=True)
class LongTruncatedBSR:
    """Bitmap of every LCG with data, but Buffer Sizes for only some of them.

    The sizes follow LCG *priority*, which the CE itself does not carry, so they are kept as a plain tuple.
    """
    lcgs: frozenset
    indices: tuple
    lcid: ClassVar[int] = 60
    direction: ClassVar[str] = UL

    def __post_init__(self):
        object.__setattr__(self, "lcgs", frozenset(self.lcgs))
        object.__setattr__(self, "indices", tuple(self.indices))
        for g in self.lcgs:
            _check("lcg", g, 0, 7)
        for i in self.indices:
            _check("index", i, 0, 254)
        if len(self.indices) > len(self.lcgs):
            raise MacPduError("long truncated BSR: more buffer sizes than LCGs")

    def encode(self) -> bytes:
        return bytes([_bitmap(self.lcgs), *self.indices])

    @classmethod
    def decode(cls, p):
        if not p:
            raise MacPduError("long truncated BSR: empty payload")
        return cls(_bits(p[0], 0, 7), tuple(p[1:]))


@dataclass(frozen=True)
class SingleEntryPHR:
    """R R PH(6) | R R PCMAX(6). Raw indices; the dB mapping is in TS 38.133."""
    ph: int
    pcmax: int
    lcid: ClassVar[int] = 57
    direction: ClassVar[str] = UL

    def __post_init__(self):
        _check("ph", self.ph, 0, 63)
        _check("pcmax", self.pcmax, 0, 63)

    def encode(self) -> bytes:
        return bytes([self.ph, self.pcmax])

    @classmethod
    def decode(cls, p):
        return cls(p[0] & 0x3F, p[1] & 0x3F)


@dataclass(frozen=True)
class PhrEntry:
    """P V PH(6), followed by R R PCMAX(6) for a real transmission (V=0); pcmax=None is V=1 (reference format)."""
    ph: int
    pcmax: int | None = None
    p: bool = False

    def __post_init__(self):
        _check("ph", self.ph, 0, 63)
        if self.pcmax is not None:
            _check("pcmax", self.pcmax, 0, 63)


@dataclass(frozen=True)
class MultiEntryPHR:
    """One-octet Ci bitmap (C7..C1 R), then the PCell entry, then SCell entries in index order."""
    pcell: PhrEntry
    scells: dict = field(default_factory=dict)      # serving cell index -> PhrEntry
    lcid: ClassVar[int] = 56
    direction: ClassVar[str] = UL
    N_OCTETS: ClassVar[int] = 1

    def __post_init__(self):
        object.__setattr__(self, "scells", dict(sorted(self.scells.items())))
        for i in self.scells:
            _check("SCell index", i, 1, 8 * self.N_OCTETS - 1)

    def encode(self) -> bytes:
        out = bytearray(_bitmap(self.scells).to_bytes(self.N_OCTETS, "little"))
        for e in (self.pcell, *self.scells.values()):
            out.append(int(e.p) << 7 | (e.pcmax is None) << 6 | e.ph)
            if e.pcmax is not None:
                out.append(e.pcmax)
        return bytes(out)

    @classmethod
    def decode(cls, p):
        n = cls.N_OCTETS
        if len(p) < n:
            raise MacPduError("multiple-entry PHR: truncated bitmap")
        cells = sorted(_bits(int.from_bytes(p[:n], "little"), 1, 8 * n - 1))
        rest, entries = iter(p[n:]), []
        try:
            for _ in range(1 + len(cells)):
                b = next(rest)
                entries.append(PhrEntry(b & 0x3F, None if b & 0x40 else next(rest) & 0x3F, bool(b & 0x80)))
        except StopIteration:
            raise MacPduError("multiple-entry PHR: truncated entries") from None
        if next(rest, None) is not None:
            raise MacPduError("multiple-entry PHR: trailing bytes")
        return cls(entries[0], dict(zip(cells, entries[1:])))


@dataclass(frozen=True)
class MultiEntryPHR4(MultiEntryPHR):
    """Four-octet Ci bitmap (up to SCell 31)."""
    lcid: ClassVar[int] = 54
    N_OCTETS: ClassVar[int] = 4


# ---------------------------------------------------------------- undecoded CEs + registry
@dataclass(frozen=True)
class RawCE:
    """A CE whose fields are not modelled; the payload is passed through unchanged."""
    direction: str
    lcid: int
    payload: bytes

    def __post_init__(self):
        if self.lcid not in _RAW.get(self.direction, {}):
            raise MacPduError(f"{self.direction} LCID {self.lcid} is not a raw CE")
        n = _RAW[self.direction][self.lcid]
        if (n is not None and len(self.payload) != n) or not 1 <= len(self.payload) <= 0xFFFF:
            raise MacPduError(f"{self.direction} LCID {self.lcid}: bad payload length {len(self.payload)}")

    def encode(self) -> bytes:
        return bytes(self.payload)


# LCID -> payload length, None = variable (has an L field)
_RAW = {
    DL: {48: 2,        # SP ZP CSI-RS resource set activation/deactivation
         49: 3,        # PUCCH spatial relation activation/deactivation
         50: None,     # SP SRS activation/deactivation
         51: 2,        # SP CSI reporting on PUCCH activation/deactivation
         52: 2,        # TCI state indication for UE-specific PDCCH
         53: None,     # TCI states activation/deactivation for UE-specific PDSCH
         54: None,     # aperiodic CSI trigger state subselection
         55: None},    # SP CSI-RS / CSI-IM resource set activation/deactivation
    UL: {},
}
_TYPED = {                              # class -> payload length, None = variable
    TACommand: 1, SCellActivation: 1, SCellActivation4: 4, DuplicationActivation: 1, DRXCommand: 0,
    LongDRXCommand: 0, ContentionResolution: 6, RecommendedBitRate: 2,
    RecommendedBitRateQuery: 2, CRnti: 2, ConfiguredGrantConfirmation: 0, ShortBSR: 1, ShortTruncatedBSR: 1,
    LongBSR: None, LongTruncatedBSR: None, SingleEntryPHR: 2, MultiEntryPHR: None, MultiEntryPHR4: None,
}
_CLASS = {(c.direction, c.lcid): c for c in _TYPED}
CE_LENGTH = {(c.direction, c.lcid): n for c, n in _TYPED.items()}
CE_LENGTH.update({(d, l): n for d, m in _RAW.items() for l, n in m.items()})
CE_TYPES = (*_TYPED, RawCE)


def decode_ce(direction: str, lcid: int, payload: bytes):
    """Decode one CE payload (subheader already removed)."""
    key = (direction, lcid)
    if key not in CE_LENGTH:
        raise MacPduError(f"unsupported {direction} CE LCID {lcid}")
    n = CE_LENGTH[key]
    if n is not None and len(payload) != n:
        raise MacPduError(f"{direction} LCID {lcid}: expected {n} bytes, got {len(payload)}")
    payload = bytes(payload)
    return _CLASS[key].decode(payload) if key in _CLASS else RawCE(direction, lcid, payload)


# ---------------------------------------------------------------- BSR format selection (sec. 5.4.5)
def regular_bsr(per_lcg: dict):
    """Regular / periodic BSR: long if more than one LCG has data, else short."""
    busy = {g: n for g, n in per_lcg.items() if n > 0}
    if len(busy) > 1:
        return LongBSR.from_bytes(busy)
    g, n = next(iter(busy.items()), (0, 0))
    return ShortBSR.from_bytes(g, n)


def padding_bsr(per_lcg: dict, space: int, priority=None):
    """BSR that fits in `space` padding bytes (subheader included), or None.

    priority: LCGs from highest to lowest priority (default: ascending LCG).
    """
    if space < 2:
        return None
    busy = {g: n for g, n in per_lcg.items() if n > 0}
    if len(busy) <= 1:
        return regular_bsr(busy)
    if space >= 2 + 1 + len(busy):
        return LongBSR.from_bytes(busy)
    order = [g for g in (priority if priority is not None else sorted(busy)) if g in busy]
    if space == 2:
        return ShortTruncatedBSR.from_bytes(order[0], busy[order[0]])
    return LongTruncatedBSR(frozenset(busy), tuple(long_index(busy[g]) for g in order[:space - 3]))
