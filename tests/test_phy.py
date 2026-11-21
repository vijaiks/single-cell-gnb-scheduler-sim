import math
from gnbsim import phy
from gnbsim.tables import CQI_EFF, MCS_TABLE1


def test_table_sizes():
    assert len(CQI_EFF) == 16 and len(MCS_TABLE1) == 29


def test_cqi_thresholds_strictly_increasing():
    t = phy.CQI_THR[1:]
    assert all(a < b for a, b in zip(t, t[1:]))


def test_cqi_mapping_edges():
    assert phy.sinr_to_cqi(-30) == 0
    assert phy.sinr_to_cqi(40) == 15
    for c in range(1, 16):
        assert phy.sinr_to_cqi(phy.CQI_THR[c] + 1e-6) == c


def test_mcs_non_decreasing_in_cqi():
    mcs = [phy.select_mcs(c) for c in range(1, 16)]
    assert mcs == sorted(mcs) and mcs[0] == 0 and mcs[-1] <= 28


def test_offset_lowers_mcs():
    assert phy.select_mcs(10, -3.0) < phy.select_mcs(10, 0.0)
    assert phy.select_mcs(10, 3.0) >= phy.select_mcs(10, 0.0)


def test_tbs_monotonic_in_prbs_and_mcs_within_modulation():
    assert all(phy.tbs_bits(10, n) < phy.tbs_bits(10, n + 1) for n in range(1, 60))
    for lo, hi in ((0, 9), (10, 16), (17, 28)):
        t = [phy.tbs_bits(m, 50) for m in range(lo, hi + 1)]
        assert t == sorted(t)


def test_tbs_sanity_vs_knowledge_base_value():
    # kb-rag (CRC_in_5G.pdf p.2): max TBS, MCS27, 275 RBs = 319784 bits (256QAM table: Qm=8, R=948/1024)
    got = phy.tbs_bits(0, 275, qm_r=(8, 948))
    assert abs(got - 319784) / 319784 < 0.01


def test_bler_is_10_percent_at_threshold_and_monotonic():
    for m in (0, 10, 20, 28):
        assert abs(phy.bler(phy.MCS_THR[m], m) - 0.10) < 1e-9
    b = [phy.bler(s, 15) for s in range(-5, 30)]
    assert all(x >= y for x, y in zip(b, b[1:]))


def test_retx_combining_reduces_bler():
    s = phy.MCS_THR[15] - 1
    assert phy.bler(s, 15, 1) < phy.bler(s, 15, 0)
    assert phy.bler(s, 15, 3) < phy.bler(s, 15, 1)


def test_cqi0_not_schedulable():
    import pytest
    with pytest.raises(ValueError):
        phy.select_mcs(0)
