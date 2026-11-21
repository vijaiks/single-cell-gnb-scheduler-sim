import math
from gnbsim.allocator import split_prbs
from gnbsim.schedulers import Candidate


def cand(i, buf=math.inf, bpp=100.0):
    return Candidate(i, 10, 15, bpp, buf)


def test_k1_gives_everything_to_top():
    r = split_prbs([cand(0), cand(1)], 50, 1)
    assert [(c.ue_id, n) for c, n in r] == [(0, 50)]


def test_k2_equal_split_within_one():
    r = split_prbs([cand(0), cand(1), cand(2)], 51, 2)
    assert [n for _, n in r] == [26, 25]
    assert sum(n for _, n in r) == 51


def test_need_cap_and_leftover_flows_within_top_k():
    # UE0 needs ceil(1000/100)=10 PRBs, UE1 full buffer; UE2 is outside the top-2 and gets nothing
    r = split_prbs([cand(0, buf=1000), cand(1), cand(2)], 50, 2)
    d = {c.ue_id: n for c, n in r}
    assert d[0] == 10 and d[1] == 40 and 2 not in d


def test_k_is_a_hard_limit_idle_prbs_stay_idle():
    r = split_prbs([cand(0, 500), cand(1, 500), cand(2)], 50, 2)
    d = {c.ue_id: n for c, n in r}
    assert d == {0: 5, 1: 5}            # 40 PRBs idle: this is what PRB_UNDERUSE detects


def test_never_exceeds_cell():
    for k in (1, 2, 3, 5):
        for n in (1, 7, 50):
            r = split_prbs([cand(i) for i in range(5)], n, k)
            assert sum(x for _, x in r) <= n
