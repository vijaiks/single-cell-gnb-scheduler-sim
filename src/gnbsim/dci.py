"""PDCCH order: DCI format 1_0 with CRC scrambled by C-RNTI that starts random access (TS 38.212 sec. 7.3.1.2.1).

Fields, most significant bit first:

    identifier for DCI formats (1) = 1
    frequency domain resource assignment (ceil(log2(N(N+1)/2)) bits) = all ones  -> "this is a PDCCH order"
    random access preamble index (6)    0 = contention-based RA, 1..63 = this dedicated preamble
    UL/SUL indicator (1)                \
    SS/PBCH index (6)                    > reserved when the preamble index is 0
    PRACH mask index (4)                /
    reserved (10)

N is the number of PRBs that sizes DCI 1_0 (CORESET 0 or the active DL BWP). The DCI is bit-aligned, not
byte-aligned: to_bytes() left-aligns it and pads the last byte with zeros. Not covered: the size alignment of
DCI 1_0 with other formats, the Rel-16 shared-spectrum reserved field (12 bits).
"""
from __future__ import annotations
from dataclasses import dataclass

from .macce import MacPduError

RESERVED_BITS = 10


def freq_field_bits(n_rb: int) -> int:
    """Width of the frequency domain resource assignment field: ceil(log2(N(N+1)/2))."""
    if not isinstance(n_rb, int) or not 1 <= n_rb <= 275:
        raise MacPduError(f"n_rb={n_rb!r} out of range 1..275")
    return (n_rb * (n_rb + 1) // 2 - 1).bit_length()


def size_bits(n_rb: int) -> int:
    return 1 + freq_field_bits(n_rb) + 6 + 1 + 6 + 4 + RESERVED_BITS


@dataclass(frozen=True)
class PdcchOrder:
    preamble: int           # ra-PreambleIndex: 0 = contention-based, 1..63 = dedicated
    ul_sul: int = 0
    ssb: int = 0            # SS/PBCH index the preamble goes with
    prach_mask: int = 0     # PRACH mask index: which PRACH occasions are allowed (0 = all)

    def __post_init__(self):
        for name, v, hi in (("preamble", self.preamble, 63), ("ul_sul", self.ul_sul, 1),
                            ("ssb", self.ssb, 63), ("prach_mask", self.prach_mask, 15)):
            if not isinstance(v, int) or not 0 <= v <= hi:
                raise MacPduError(f"{name}={v!r} out of range 0..{hi}")
        if self.preamble == 0 and (self.ul_sul or self.ssb or self.prach_mask):
            raise MacPduError("with preamble index 0 the UL/SUL, SS/PBCH and PRACH mask fields are reserved (0)")

    @property
    def contention_free(self) -> bool:
        return self.preamble != 0

    def encode(self, n_rb: int) -> int:
        f = freq_field_bits(n_rb)
        v = 1                                            # identifier: DL DCI format
        v = (v << f) | ((1 << f) - 1)                    # all ones: PDCCH order
        v = (v << 6) | self.preamble
        v = (v << 1) | self.ul_sul
        v = (v << 6) | self.ssb
        v = (v << 4) | self.prach_mask
        return v << RESERVED_BITS

    @classmethod
    def decode(cls, value: int, n_rb: int) -> "PdcchOrder":
        """Raises MacPduError unless `value` is a DCI 1_0 PDCCH order. Reserved bits are ignored."""
        n, f = size_bits(n_rb), freq_field_bits(n_rb)
        if not isinstance(value, int) or not 0 <= value < (1 << n):
            raise MacPduError(f"DCI value does not fit in {n} bits")
        pos = n

        def take(bits):
            nonlocal pos
            pos -= bits
            return (value >> pos) & ((1 << bits) - 1)

        if take(1) != 1:
            raise MacPduError("identifier for DCI formats is 0: not a DL DCI")
        if take(f) != (1 << f) - 1:
            raise MacPduError("frequency domain resource assignment is not all ones: not a PDCCH order")
        preamble, ul_sul, ssb, mask = take(6), take(1), take(6), take(4)
        if preamble == 0:                                # the other fields are reserved
            return cls(0)
        return cls(preamble, ul_sul, ssb, mask)

    def to_bytes(self, n_rb: int) -> bytes:
        n = size_bits(n_rb)
        pad = -n % 8
        return (self.encode(n_rb) << pad).to_bytes((n + pad) // 8, "big")

    @classmethod
    def from_bytes(cls, b: bytes, n_rb: int) -> "PdcchOrder":
        n = size_bits(n_rb)
        pad = -n % 8
        if len(b) != (n + pad) // 8:
            raise MacPduError(f"PDCCH order for {n_rb} PRBs is {(n + pad) // 8} bytes, got {len(b)}")
        return cls.decode(int.from_bytes(b, "big") >> pad, n_rb)
