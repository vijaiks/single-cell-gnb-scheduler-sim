import random

import pytest
from gnbsim.macce import MacPduError
from gnbsim.rar import (BI_MS, RarEntry, backoff_ms, build_rar_pdu, decode_mac_rar, encode_mac_rar,
                        pack_ul_grant, parse_rar_pdu, ra_rnti, unpack_ul_grant)

G1 = pack_ul_grant(0, 0x123, 2, 4, 3, 0)
G2 = pack_ul_grant(1, 0x3FFF, 15, 15, 7, 1)
ES = [RarEntry(7, 100, G1, 0x4601), RarEntry(23, 31, G2, 0xFFFF)]


# ---------------------------------------------------------------- RA-RNTI, BI table
def test_ra_rnti_formula_and_extremes():
    assert ra_rnti(0, 0, 0) == 1
    assert ra_rnti(0, 20, 0) == 281
    assert ra_rnti(3, 5, 2, 1) == 1 + 3 + 14 * 5 + 14 * 80 * 2 + 14 * 80 * 8
    assert ra_rnti(13, 79, 7, 1) == 17920                     # largest RA-RNTI, 0x4600
    for bad in ((14, 0, 0), (0, 80, 0), (0, 0, 8), (0, 0, 0, 2), (-1, 0, 0)):
        with pytest.raises(MacPduError):
            ra_rnti(*bad)


def test_bi_table():
    assert len(BI_MS) == 14 and BI_MS[0] == 5 and BI_MS[2] == 20 and BI_MS[12] == 960 and BI_MS[13] == 1920
    assert list(BI_MS) == sorted(BI_MS)
    for reserved in (14, 15, -1):
        with pytest.raises(MacPduError):
            backoff_ms(reserved)


# ---------------------------------------------------------------- UL grant, MAC RAR
def test_ul_grant_fields_and_bit_positions():
    assert unpack_ul_grant(G1) == {"hop": 0, "freq": 0x123, "time": 2, "mcs": 4, "tpc": 3, "csi": 0}
    assert G2 == (1 << 27) - 1
    assert pack_ul_grant(1, 0, 0, 0, 0, 0) == 1 << 26
    assert pack_ul_grant(0, 1, 0, 0, 0, 0) == 1 << 12
    assert pack_ul_grant(0, 0, 0, 0, 0, 1) == 1


@pytest.mark.parametrize("args", [(2, 0, 0, 0, 0, 0), (0, 1 << 14, 0, 0, 0, 0), (0, 0, 16, 0, 0, 0),
                                  (0, 0, 0, 16, 0, 0), (0, 0, 0, 0, 8, 0), (0, 0, 0, 0, 0, 2), (0, -1, 0, 0, 0, 0)])
def test_ul_grant_rejects_out_of_range(args):
    with pytest.raises(MacPduError):
        pack_ul_grant(*args)


def test_mac_rar_layout():
    # TA = 0xABC: R=0 + 7 high bits = 0x55, low 5 bits 11100 then grant's top 3 bits
    b = encode_mac_rar(0xABC, 0x5123456, 0x4601)
    assert b == bytes([0x55, 0xE5, 0x12, 0x34, 0x56, 0x46, 0x01])
    assert decode_mac_rar(b) == (0xABC, 0x5123456, 0x4601)
    assert decode_mac_rar(encode_mac_rar(4095, (1 << 27) - 1, 0xFFFF)) == (4095, (1 << 27) - 1, 0xFFFF)
    assert decode_mac_rar(bytes([b[0] | 0x80]) + b[1:]) == decode_mac_rar(b)       # R bit ignored


@pytest.mark.parametrize("args", [(4096, 0, 0), (-1, 0, 0), (0, 1 << 27, 0), (0, 0, 0x10000)])
def test_mac_rar_rejects_out_of_range(args):
    with pytest.raises(MacPduError):
        encode_mac_rar(*args)


def test_decode_mac_rar_needs_seven_bytes():
    with pytest.raises(MacPduError):
        decode_mac_rar(bytes(6))


def test_rar_entry_validates():
    with pytest.raises(MacPduError):
        RarEntry(64, 0, 0, 1)
    with pytest.raises(MacPduError):
        RarEntry(0, 5000, 0, 1)


# ---------------------------------------------------------------- PDU build / parse
def test_worked_example_bytes():
    pdu = build_rar_pdu(ES, bi_idx=2, tb_size=40)
    assert len(pdu) == 40
    assert pdu[0] == 0x82                      # E=1 T=0 BI=2
    assert pdu[1] == 0xC7                      # E=1 T=1 RAPID=7
    assert pdu[2:9] == encode_mac_rar(100, G1, 0x4601)
    assert pdu[9] == 0x57                      # E=0 (last) T=1 RAPID=23
    assert pdu[17:] == bytes(23)               # padding
    assert parse_rar_pdu(pdu) == (2, ES)


def test_single_entry_no_bi_no_padding():
    one = build_rar_pdu(ES[:1])
    assert len(one) == 8 and one[0] == 0x47
    assert parse_rar_pdu(one) == (None, ES[:1])


def test_bi_only_pdu():
    assert build_rar_pdu([], bi_idx=13) == bytes([13])
    assert parse_rar_pdu(bytes([13, 0xFF, 0xFF])) == (13, [])    # padding content ignored


def test_build_errors():
    with pytest.raises(MacPduError):
        build_rar_pdu([])
    with pytest.raises(MacPduError):
        build_rar_pdu(ES, bi_idx=14)
    with pytest.raises(MacPduError):
        build_rar_pdu(ES, tb_size=15)          # needs 16
    assert len(build_rar_pdu(ES, tb_size=16)) == 16


def test_parse_errors():
    pdu = build_rar_pdu(ES, bi_idx=2)
    for cut in (0, 1, 5, 9, len(pdu) - 1):
        with pytest.raises(MacPduError):
            parse_rar_pdu(pdu[:cut])
    with pytest.raises(MacPduError):
        parse_rar_pdu(bytes([0x82, 0x03]))     # second BI subPDU
    with pytest.raises(MacPduError):
        parse_rar_pdu(bytes([0xC7]) + bytes(7) + bytes([0x02]))   # BI after a RAPID subPDU


def test_parse_returns_reserved_bi_as_is():
    assert parse_rar_pdu(bytes([0x0F])) == (15, [])


def test_random_round_trip():
    rng = random.Random(1)
    for _ in range(300):
        es = [RarEntry(rng.randrange(64), rng.randrange(4096), rng.randrange(1 << 27), rng.randrange(1 << 16))
              for _ in range(rng.randrange(0, 6))]
        bi = rng.choice([None] + list(range(14))) if es else rng.randrange(14)
        pdu = build_rar_pdu(es, bi_idx=bi, tb_size=rng.choice([None, 64]))
        assert parse_rar_pdu(pdu) == (bi, es)
