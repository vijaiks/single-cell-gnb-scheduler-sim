import math

import pytest
from gnbsim.dci import PdcchOrder, freq_field_bits, size_bits
from gnbsim.macce import MacPduError


@pytest.mark.parametrize("n_rb", [1, 2, 24, 48, 51, 52, 106, 273, 275])
def test_freq_field_width_matches_formula(n_rb):
    x = n_rb * (n_rb + 1) // 2
    assert freq_field_bits(n_rb) == (math.ceil(math.log2(x)) if x > 1 else 0)


def test_known_sizes():
    assert freq_field_bits(51) == 11 and size_bits(51) == 39      # 1 + 11 + 6 + 1 + 6 + 4 + 10
    assert freq_field_bits(106) == 13 and freq_field_bits(273) == 16
    for bad in (0, 276, -1):
        with pytest.raises(MacPduError):
            freq_field_bits(bad)


def test_bit_layout():
    v = PdcchOrder(preamble=56, ul_sul=1, ssb=5, prach_mask=3).encode(51)
    bits = format(v, "039b")
    assert bits[0] == "1"                      # DL DCI identifier
    assert bits[1:12] == "1" * 11              # frequency assignment all ones = PDCCH order
    assert bits[12:18] == format(56, "06b")
    assert bits[18] == "1"
    assert bits[19:25] == format(5, "06b")
    assert bits[25:29] == format(3, "04b")
    assert bits[29:] == "0" * 10               # reserved


def test_bytes_left_aligned():
    b = PdcchOrder(56).to_bytes(51)
    assert b == bytes([0xFF, 0xFE, 0x00, 0x00, 0x00]) and len(b) == 5    # 39 bits + 1 pad bit
    assert PdcchOrder.from_bytes(b, 51) == PdcchOrder(56)


@pytest.mark.parametrize("n_rb", [24, 51, 106, 273])
def test_round_trip(n_rb):
    for o in (PdcchOrder(0), PdcchOrder(1), PdcchOrder(63, 1, 63, 15), PdcchOrder(60, 0, 2, 7)):
        assert PdcchOrder.decode(o.encode(n_rb), n_rb) == o
        assert PdcchOrder.from_bytes(o.to_bytes(n_rb), n_rb) == o


def test_preamble_zero_means_cbra_and_reserved_fields():
    assert not PdcchOrder(0).contention_free and PdcchOrder(1).contention_free
    with pytest.raises(MacPduError):
        PdcchOrder(0, ssb=3)
    # a receiver ignores whatever is in the reserved fields when the preamble index is 0
    v = PdcchOrder(0).encode(51) | (0b1111 << 10) | (1 << 20)
    assert PdcchOrder.decode(v, 51) == PdcchOrder(0)
    assert PdcchOrder.decode(PdcchOrder(9).encode(51) | 0x3FF, 51) == PdcchOrder(9)   # reserved bits ignored


@pytest.mark.parametrize("kw", [dict(preamble=64), dict(preamble=-1), dict(preamble=1, ul_sul=2),
                                dict(preamble=1, ssb=64), dict(preamble=1, prach_mask=16)])
def test_field_ranges(kw):
    with pytest.raises(MacPduError):
        PdcchOrder(**kw)


def test_decode_rejects_non_orders():
    v = PdcchOrder(5).encode(51)
    with pytest.raises(MacPduError):
        PdcchOrder.decode(v & ~(1 << 38), 51)          # identifier 0: UL DCI
    with pytest.raises(MacPduError):
        PdcchOrder.decode(v & ~(1 << 30), 51)          # one frequency bit 0: a normal DL assignment
    with pytest.raises(MacPduError):
        PdcchOrder.decode(1 << 39, 51)                 # too wide
    with pytest.raises(MacPduError):
        PdcchOrder.from_bytes(bytes(4), 51)            # wrong length
