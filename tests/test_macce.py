import random
import pytest
from gnbsim import bsr_tables as B
from gnbsim.macce import (CE_LENGTH, DL, UL, ConfiguredGrantConfirmation, ContentionResolution, CRnti, DRXCommand,
                          DuplicationActivation, LongBSR, LongDRXCommand, LongTruncatedBSR, MacPduError,
                          MultiEntryPHR, MultiEntryPHR4, PhrEntry, RawCE, RecommendedBitRate,
                          RecommendedBitRateQuery, SCellActivation, SCellActivation4, ShortBSR, ShortTruncatedBSR,
                          SingleEntryPHR, TACommand, decode_ce, padding_bsr, regular_bsr)
from gnbsim.macpdu import MacSdu, Padding, build_tb, dispatch, max_payload_for, pack, pack_subpdu, unpack

# (CE, complete subPDU in hex) - bytes written out by hand from the field layouts in TS 38.321 6.1.3
DL_CES = [
    (TACommand(0, 33), "3d 21"), (TACommand(3, 63), "3d ff"),
    (SCellActivation({1, 2}), "3a 06"),
    (SCellActivation4({1, 8, 31}), "39 02 01 00 80"),
    (DuplicationActivation({0, 7}), "38 81"),
    (DRXCommand(), "3c"), (LongDRXCommand(), "3b"),
    (ContentionResolution(bytes(range(6))), "3e 00 01 02 03 04 05"),
    (RecommendedBitRate(4, True, 33), "2f 13 08"),         # 000100 1 1|00001 000
    (RawCE(DL, 52, b"\x12\x34"), "34 12 34"),              # fixed length, no L
    (RawCE(DL, 53, b"\x01\x02\x03"), "35 03 01 02 03"),    # variable length, L=3
]
UL_CES = [
    (CRnti(0x4601), "3a 46 01"),
    (ConfiguredGrantConfirmation(), "37"),
    (RecommendedBitRateQuery(4, False, 1), "35 10 08"),
    (ShortBSR(2, 17), "3d 51"),                            # 010 10001
    (ShortTruncatedBSR(7, 31), "3b ff"),
    (LongBSR({2: 5, 0: 1}), "3e 03 05 01 05"),             # LCG0 then LCG2
    (LongBSR({}), "3e 01 00"),
    (LongTruncatedBSR({0, 1, 7}, (9,)), "3c 02 83 09"),
    (LongTruncatedBSR({0, 1}, ()), "3c 01 03"),
    (SingleEntryPHR(40, 50), "39 28 32"),
    (MultiEntryPHR(PhrEntry(40, 50)), "38 03 00 28 32"),
    (MultiEntryPHR(PhrEntry(40, 50), {3: PhrEntry(7), 1: PhrEntry(1, 2, True)}), "38 06 0a 28 32 81 02 47"),
    (MultiEntryPHR4(PhrEntry(1), {9: PhrEntry(2, 3)}), "36 07 00 02 00 00 41 02 03"),
]
ALL = [(DL, *x) for x in DL_CES] + [(UL, *x) for x in UL_CES]


@pytest.mark.parametrize("d, ce, raw", ALL)
def test_ce_worked_bytes_both_ways(d, ce, raw):
    raw = bytes.fromhex(raw)
    assert pack([ce], direction=d) == raw
    assert unpack(raw, d) == [ce]
    assert decode_ce(d, ce.lcid, ce.encode()) == ce


def test_every_typed_ce_and_both_raw_kinds_are_covered_above():
    seen = {(d, ce.lcid) for d, ce, _ in ALL}
    assert set(CE_LENGTH) - seen == {(DL, n) for n in (48, 49, 50, 51, 54, 55)}


@pytest.mark.parametrize("lcid, n", [(48, 2), (49, 3), (51, 2), (52, 2)])
def test_fixed_raw_ce_does_not_swallow_the_next_subpdu(lcid, n):
    assert unpack(bytes([lcid]) + b"\x55" * n + b"\x3d\x21") == [RawCE(DL, lcid, b"\x55" * n), TACommand(0, 33)]


@pytest.mark.parametrize("lcid, dl, ul", [(57, SCellActivation4, SingleEntryPHR), (58, SCellActivation, CRnti),
                                          (60, DRXCommand, LongTruncatedBSR), (61, TACommand, ShortBSR)])
def test_same_lcid_is_a_different_ce_in_each_direction(lcid, dl, ul):
    raw = {57: ("3900000000", "390102"), 58: ("3a00", "3a0000"), 60: ("3c", "3c0100"), 61: ("3d00", "3d00")}[lcid]
    assert type(unpack(bytes.fromhex(raw[0]), DL)[0]) is dl and type(unpack(bytes.fromhex(raw[1]), UL)[0]) is ul


def test_wrong_direction_raises():
    with pytest.raises(MacPduError, match="UL CE, not DL"):
        pack([ShortBSR(0, 1)], direction=DL)
    with pytest.raises(MacPduError, match="DL CE, not UL"):
        pack([TACommand(0, 1)], direction=UL)
    with pytest.raises(MacPduError, match="direction must be"):
        unpack(b"", "dl")


def test_reserved_bits_ignored():
    assert unpack(bytes.fromhex("39e8f2"), UL) == [SingleEntryPHR(40, 50)]
    assert unpack(bytes.fromhex("3903010080")) == [SCellActivation4({1, 8, 31})]
    assert unpack(bytes.fromhex("2f130f")) == [RecommendedBitRate(4, True, 33)]


@pytest.mark.parametrize("raw", ["3e00", "3e0103", "3e020305", "3e04030105ff", "3e0201ff",      # long BSR
                                 "3c00", "3c020001",                                            # long truncated
                                 "3800", "380100", "38020028", "380400283299"])                 # multi-entry PHR
def test_malformed_variable_ul_ce_raises(raw):
    with pytest.raises(MacPduError):
        unpack(bytes.fromhex(raw), UL)


@pytest.mark.parametrize("d, ce, raw", ALL)
def test_truncated_ce_raises(d, ce, raw):
    raw = bytes.fromhex(raw)
    for cut in range(1, len(raw)):
        with pytest.raises(MacPduError):
            unpack(raw[:cut], d)


@pytest.mark.parametrize("make", [
    lambda: SCellActivation4({32}), lambda: SCellActivation4({0}), lambda: DuplicationActivation({8}),
    lambda: ContentionResolution(b"12345"), lambda: RecommendedBitRate(64, True, 0),
    lambda: RecommendedBitRate(0, True, 64), lambda: CRnti(0x10000), lambda: CRnti(-1),
    lambda: ShortBSR(8, 0), lambda: ShortBSR(0, 32), lambda: LongBSR({8: 0}), lambda: LongBSR({0: 255}),
    lambda: LongTruncatedBSR({0}, (1, 2)), lambda: LongTruncatedBSR({0}, (255,)), lambda: LongTruncatedBSR({8}, ()),
    lambda: SingleEntryPHR(64, 0), lambda: SingleEntryPHR(0, 64), lambda: PhrEntry(64), lambda: PhrEntry(0, 64),
    lambda: MultiEntryPHR(PhrEntry(0), {8: PhrEntry(0)}), lambda: MultiEntryPHR(PhrEntry(0), {0: PhrEntry(0)}),
    lambda: RawCE(DL, 52, b"1"), lambda: RawCE(DL, 53, b""), lambda: RawCE(DL, 61, b"1"), lambda: RawCE(UL, 52, b"12")])
def test_out_of_range_ce_fields_raise(make):
    with pytest.raises(MacPduError):
        make()


def test_decode_ce_checks_lcid_and_length():
    with pytest.raises(MacPduError, match="unsupported UL CE LCID 40"):
        decode_ce(UL, 40, b"")
    with pytest.raises(MacPduError, match="expected 1 bytes"):
        decode_ce(DL, 61, b"12")


# ---------------------------------------------------------------- UL PDUs
def test_ul_order_is_sdus_then_ces_then_padding():
    subs = [MacSdu(4, b"abc"), ShortBSR(2, 5), SingleEntryPHR(1, 2)]
    raw = pack(subs, 20, UL)
    assert raw == bytes.fromhex("0403616263 3d45 390102 3f") + bytes(9)
    assert unpack(raw, UL) == subs + [Padding(9)]
    with pytest.raises(MacPduError, match="UL order"):
        pack(subs[::-1], direction=UL)


@pytest.mark.parametrize("lcid, n", [(0, 8), (52, 6)])
def test_ul_ccch_has_fixed_size_and_no_length_field(lcid, n):
    s = MacSdu(lcid, b"\x1e" * n)
    raw = pack([s, ShortBSR(0, 0)], direction=UL)
    assert raw == bytes([lcid]) + b"\x1e" * n + b"\x3d\x00"
    assert unpack(raw, UL) == [s, ShortBSR(0, 0)]
    with pytest.raises(MacPduError, match="UL CCCH"):
        pack([MacSdu(lcid, b"x")], direction=UL)


def test_dl_ccch_has_a_length_field_and_lcid_52_sdu_is_not_dl():
    assert pack([MacSdu(0, b"ab")]) == bytes.fromhex("00026162")
    with pytest.raises(MacPduError, match="not a DL SDU"):
        pack([MacSdu(52, bytes(6))])


@pytest.mark.parametrize("d, ces", [(DL, [c for c, _ in DL_CES]), (UL, [c for c, _ in UL_CES])])
def test_random_pdus_round_trip(d, ces):
    rng = random.Random(3)
    for _ in range(300):
        c = rng.sample(ces, rng.randrange(4))
        s = [MacSdu(rng.randrange(1, 33), rng.randbytes(rng.choice([1, 40, 255, 256, 900]))) for _ in range(rng.randrange(4))]
        subs = c + s if d == DL else s + c
        pad = rng.randrange(4)
        raw = pack(subs, len(pack(subs, direction=d)) + pad, d)
        assert unpack(raw, d) == subs + ([Padding(pad - 1)] if pad else [])


@pytest.mark.parametrize("d", [DL, UL])
def test_random_bytes_only_raise_macpduerror_and_accepted_input_reencodes(d):
    rng, ok = random.Random(11), 0
    for _ in range(20000):
        try:
            subs = unpack(rng.randbytes(rng.randrange(1, 10)), d)
        except MacPduError:
            continue
        ok += 1
        for s in subs:
            assert unpack(pack_subpdu(s, d), d) == [s]
    assert ok > 100


# ---------------------------------------------------------------- build_tb / dispatch
def puller(queues, calls=None):
    def pull(lcid, n):
        if calls is not None:
            calls.append((lcid, n))
        data, queues[lcid] = queues[lcid][:n], queues[lcid][n:]
        return data
    return pull


def test_build_tb_reproduces_the_worked_dl_example():
    q = {1: bytes(range(20)), 4: b"\xaa" * 200}
    tb = build_tb(100, [TACommand(0, 33), SCellActivation({1, 2})], [(1, 22), (4, 62)], puller(q))
    assert len(tb) == 100 and tb[:6].hex() == "3d213a060114" and tb[26:28].hex() == "043c" and tb[88] == 0x3F
    assert len(q[4]) == 140


@pytest.mark.parametrize("d, ce", [(DL, TACommand(0, 1)), (UL, ShortBSR(0, 1))])
def test_build_tb_always_returns_exactly_the_tb_size(d, ce):
    for size in [2, 3, 4, 5, *range(255, 266), 3000]:
        q = {4: b"x" * 5000}
        tb = build_tb(size, [ce], [(4, size)], puller(q), d)
        assert len(tb) == size
        got = sum(len(s.data) for s in unpack(tb, d) if isinstance(s, MacSdu))
        assert got + len(q[4]) == 5000                     # nothing pulled is lost


def test_build_tb_does_not_pull_data_when_ces_do_not_fit():
    calls = []
    with pytest.raises(MacPduError, match="do not fit"):
        build_tb(1, [TACommand(0, 1)], [(4, 10)], puller({4: b"x" * 10}, calls))
    assert calls == []


def test_build_tb_skips_empty_queue_and_tiny_allocation():
    assert build_tb(10, [], [(1, 8), (4, 2)], puller({1: b"", 4: b"x"})) == b"\x3f" + bytes(9)


def test_max_payload_for_fits_and_is_maximal():
    for space in range(1, 600):
        n = max_payload_for(space)
        assert n == max([k for k in range(1, space) if k + (2 if k <= 255 else 3) <= space], default=0)


def test_dispatch_routes_by_type():
    sdus, ces = [], []
    dispatch([TACommand(0, 1), MacSdu(4, b"x"), Padding(3)], lambda l, d: sdus.append((l, d)), ces.append)
    assert sdus == [(4, b"x")] and ces == [TACommand(0, 1)]


# ---------------------------------------------------------------- BSR
def test_bsr_tables_shape_and_anchor_values():
    assert len(B.SHORT_LEVELS) == 31 and len(B.LONG_LEVELS) == 254
    for t in (B.SHORT_LEVELS, B.LONG_LEVELS):
        assert t[0] == 0 and t[1] == 10 and list(t) == sorted(set(t))
    assert B.SHORT_LEVELS[18] == 2806 and B.SHORT_LEVELS[-1] == 150000 and B.LONG_LEVELS[-1] == 81338368


@pytest.mark.parametrize("n, s, l", [(0, 0, 0), (1, 1, 1), (10, 1, 1), (11, 2, 2), (14, 2, 5), (15, 3, 6),
                                     (150000, 30, 153), (150001, 31, 153), (81338368, 31, 253), (81338369, 31, 254)])
def test_bsr_index(n, s, l):
    assert (B.short_index(n), B.long_index(n)) == (s, l)


def test_bsr_level_never_under_reports_within_range():
    for n in range(0, 150001, 37):
        assert B.short_bytes(B.short_index(n)) >= n and B.long_bytes(B.long_index(n)) >= n


@pytest.mark.parametrize("f, bad", [(B.short_bytes, 32), (B.short_bytes, -1), (B.long_bytes, 255), (B.long_bytes, -1)])
def test_bsr_bytes_rejects_bad_index(f, bad):
    with pytest.raises(ValueError):
        f(bad)


def test_regular_bsr_format():
    assert regular_bsr({}) == ShortBSR(0, 0)
    assert regular_bsr({1: 0, 2: 100}) == ShortBSR(2, 8)
    assert regular_bsr({1: 10, 2: 100}) == LongBSR.from_bytes({1: 10, 2: 100})


def test_padding_bsr_format_by_space():
    q = {1: 10, 2: 100, 5: 11}                             # long BSR needs 2 + 1 + 3 = 6 bytes
    assert padding_bsr(q, 1) is None
    assert padding_bsr(q, 2, [5, 1, 2]) == ShortTruncatedBSR(5, 2)
    assert padding_bsr(q, 3) == LongTruncatedBSR({1, 2, 5}, ())
    assert padding_bsr(q, 5, [5, 1, 2]) == LongTruncatedBSR({1, 2, 5}, (2, 1))
    assert padding_bsr(q, 6) == LongBSR.from_bytes(q)
    assert padding_bsr({2: 100}, 2) == ShortBSR(2, 8)
    for space in range(2, 10):
        assert len(pack_subpdu(padding_bsr(q, space), UL)) <= space
