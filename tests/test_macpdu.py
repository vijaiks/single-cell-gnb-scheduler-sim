import random

import pytest
from gnbsim import phy
from gnbsim.macpdu import MacPduError, MacSdu, Padding, SCellActivation, TACommand, pack, unpack

SRB1, DRB = MacSdu(1, b"\xaa" * 20), MacSdu(4, b"\xbb" * 60)

# worked examples: (subPDU, expected bytes)
EXAMPLES = [
    (TACommand(0, 33), bytes.fromhex("3d21")),
    (SCellActivation({1, 2}), bytes.fromhex("3a06")),
    (SRB1, bytes.fromhex("0114") + b"\xaa" * 20),
    (DRB, bytes.fromhex("043c") + b"\xbb" * 60),
    (Padding(3), bytes.fromhex("3f000000")),
]


@pytest.mark.parametrize("sub, raw", EXAMPLES)
def test_worked_example_both_ways(sub, raw):
    assert pack([sub]) == raw
    assert unpack(raw) == [sub]


def test_full_pdu_padded_to_transport_block():
    subs = [s for s, _ in EXAMPLES[:4]]
    pdu = pack(subs, tb_bytes=100)
    assert pdu == b"".join(r for _, r in EXAMPLES[:4]) + b"\x3f" + bytes(11)      # 88 + 1 + 11
    assert unpack(pdu) == subs + [Padding(11)]


def test_exact_fit_has_no_padding_and_one_spare_byte_is_a_bare_subheader():
    assert pack([SRB1], tb_bytes=22) == pack([SRB1])
    assert pack([SRB1], tb_bytes=23)[-1] == 0x3F
    assert unpack(pack([SRB1], tb_bytes=23)) == [SRB1, Padding(0)]


def test_fits_a_simulator_tbs():
    tb = phy.tbs_bits(10, 5) // 8
    pdu = pack([TACommand(1, 5), SRB1], tb_bytes=tb)
    assert len(pdu) == tb and unpack(pdu)[:2] == [TACommand(1, 5), SRB1]


@pytest.mark.parametrize("n, head", [(1, "0401"), (255, "04ff"), (256, "440100"), (65535, "44ffff")])
def test_length_field_switches_to_16_bits_above_255(n, head):
    s = MacSdu(4, bytes(n))
    raw = pack([s])
    assert raw[:-n].hex() == head and unpack(raw) == [s]


def test_16_bit_length_is_accepted_for_a_short_sdu():
    assert unpack(bytes.fromhex("440002abcd")) == [MacSdu(4, b"\xab\xcd")]


@pytest.mark.parametrize("tag", range(4))
@pytest.mark.parametrize("ta", [0, 1, 31, 63])
def test_ta_round_trip(tag, ta):
    assert unpack(pack([TACommand(tag, ta)])) == [TACommand(tag, ta)]


@pytest.mark.parametrize("active, byte", [(set(), 0x00), ({1}, 0x02), ({7}, 0x80), (set(range(1, 8)), 0xFE)])
def test_scell_bitmap(active, byte):
    raw = pack([SCellActivation(active)])
    assert raw[1] == byte and unpack(raw) == [SCellActivation(active)]


def test_reserved_bits_and_padding_contents_are_ignored():
    assert unpack(bytes.fromhex("fd21")) == [TACommand(0, 33)]           # both R bits set
    assert unpack(bytes.fromhex("3a07")) == [SCellActivation({1, 2})]    # R bit in the bitmap
    assert unpack(bytes.fromhex("8101aa")) == [MacSdu(1, b"\xaa")]
    assert unpack(bytes.fromhex("3f3d21ff")) == [Padding(3)]


def test_empty():
    assert pack([]) == b"" and unpack(b"") == []


@pytest.mark.parametrize("raw", ["3d", "3a", "01", "0114aa", "4401", "440100aa", "3d2101"])
def test_truncated_pdu_raises(raw):
    with pytest.raises(MacPduError, match="truncated"):
        unpack(bytes.fromhex(raw))


@pytest.mark.parametrize("lcid", [33, 34, 46])
def test_unsupported_lcid_raises(lcid):
    with pytest.raises(MacPduError, match=f"unsupported DL LCID {lcid} at byte 2"):
        unpack(bytes([0x3D, 0x21, lcid, 0]))


def test_zero_length_sdu_raises():
    with pytest.raises(MacPduError):
        unpack(bytes.fromhex("0100"))


@pytest.mark.parametrize("make", [
    lambda: TACommand(4, 0), lambda: TACommand(-1, 0), lambda: TACommand(0, 64), lambda: TACommand(0, -1),
    lambda: SCellActivation({0}), lambda: SCellActivation({8}),
    lambda: MacSdu(33, b"x"), lambda: MacSdu(-1, b"x"), lambda: MacSdu(1, b""), lambda: MacSdu(1, bytes(65536)),
    lambda: Padding(-1)])
def test_out_of_range_fields_raise(make):
    with pytest.raises(MacPduError):
        make()


@pytest.mark.parametrize("subs", [[SRB1, TACommand(0, 1)], [Padding(1), SRB1], [Padding(1), Padding(1)]])
def test_wrong_order_raises(subs):
    with pytest.raises(MacPduError, match="order"):
        pack(subs)


def test_does_not_fit_or_double_padding_or_unknown_type_raises():
    with pytest.raises(MacPduError, match="does not fit"):
        pack([SRB1], tb_bytes=21)
    with pytest.raises(MacPduError, match="not both"):
        pack([SRB1, Padding(1)], tb_bytes=50)
    with pytest.raises(MacPduError, match="cannot pack"):
        pack(["x"])


# ---------------------------------------------------------------- whole PDUs
@pytest.mark.parametrize("lcid", [0, 1, 2, 3, 4, 32])
def test_sdu_lcid_is_preserved(lcid):
    raw = pack([MacSdu(lcid, b"\x5a")])
    assert raw == bytes([lcid, 1, 0x5A]) and unpack(raw) == [MacSdu(lcid, b"\x5a")]


def test_several_sdus_keep_order_including_same_lcid():
    subs = [MacSdu(4, b"first"), MacSdu(1, b"srb"), MacSdu(4, b"second")]
    assert unpack(pack(subs)) == subs


def test_sdu_payload_that_looks_like_subheaders_is_not_parsed():
    subs = [MacSdu(4, bytes.fromhex("3f3d213a06")), MacSdu(5, b"\x3f")]
    assert unpack(pack(subs)) == subs


def test_ce_order_is_preserved_and_same_ce_may_repeat():
    subs = [SCellActivation({3}), TACommand(0, 1), TACommand(1, 2)]
    assert unpack(pack(subs)) == subs


def test_mixed_8_and_16_bit_lengths():
    subs = [MacSdu(4, b"a" * 255), MacSdu(5, b"b" * 256), MacSdu(6, b"c")]
    assert unpack(pack(subs)) == subs


def test_padding_only_transport_block():
    assert pack([], tb_bytes=4) == bytes.fromhex("3f000000")
    assert unpack(pack([], tb_bytes=4)) == [Padding(3)]
    assert pack([], tb_bytes=0) == b""


def test_explicit_padding_that_fills_the_block_exactly():
    assert len(pack([SRB1, Padding(7)], tb_bytes=30)) == 30


def test_pack_accepts_a_generator_and_unpack_accepts_bytearray_and_memoryview():
    raw = pack(s for s in [TACommand(0, 33), SRB1])
    assert unpack(bytearray(raw)) == unpack(memoryview(raw)) == [TACommand(0, 33), SRB1]


def test_unpack_tolerates_ce_after_sdu():
    assert unpack(bytes.fromhex("0101aa3d21")) == [MacSdu(1, b"\xaa"), TACommand(0, 33)]


# ---------------------------------------------------------------- randomized / robustness (fixed seeds)
def random_subpdus(rng):
    ces = [TACommand(rng.randrange(4), rng.randrange(64)) if rng.random() < 0.5
           else SCellActivation(rng.sample(range(1, 8), rng.randrange(8))) for _ in range(rng.randrange(4))]
    sdus = [MacSdu(rng.randrange(33), rng.randbytes(rng.choice([1, 2, 20, 255, 256, 700])))
            for _ in range(rng.randrange(5))]
    return ces + sdus


@pytest.mark.parametrize("seed", range(20))
def test_random_pdu_round_trip_with_every_small_padding(seed):
    subs = random_subpdus(random.Random(seed))
    n = len(pack(subs))
    for extra in range(6):
        raw = pack(subs, tb_bytes=n + extra)
        assert len(raw) == n + extra
        assert unpack(raw) == subs + ([Padding(extra - 1)] if extra else [])


def test_pdu_cut_at_every_byte_decodes_a_prefix_or_raises():
    subs = [TACommand(0, 33), SCellActivation({1, 2}), SRB1, DRB]
    raw = pack(subs)
    boundaries = {0: 0, 2: 1, 4: 2, 26: 3, 88: 4}          # cut position -> complete subPDUs before it
    for cut in range(len(raw) + 1):
        if cut in boundaries:
            assert unpack(raw[:cut]) == subs[:boundaries[cut]]
        else:
            with pytest.raises(MacPduError, match="truncated"):
                unpack(raw[:cut])


def test_random_bytes_only_ever_raise_macpduerror():
    rng, decoded = random.Random(7), 0
    for _ in range(20000):
        try:
            subs = unpack(rng.randbytes(rng.randrange(1, 12)))
        except MacPduError:
            continue
        decoded += 1
        for s in subs:                                     # anything accepted must be encodable again
            assert unpack(pack([s])) == [s]
    assert decoded > 100
