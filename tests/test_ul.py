import pytest
from gnbsim import ul_sim
from gnbsim.macce import UL, LongBSR, ShortBSR
from gnbsim.macpdu import unpack
from gnbsim.ul_sim import Sim, UE
from gnbsim.ulgrant import TBS_TABLE, bytes_to_request, grant_for_bsr, prbs_for_bytes, split_budget, tbs_bits

MCS = dict(n_symb=12, n_dmrs_per_prb=12, n_oh_per_prb=0, qm=4, rate=490 / 1024)


# ---------------------------------------------------------------- TBS / grant sizing
def test_tbs_worked_example_and_known_maximum():
    assert tbs_bits(20, **MCS) == 4992
    assert tbs_bits(275, 14, 12, 0, 8, 948 / 1024) == 319784      # the maximum quoted in the README, exactly


def test_tbs_zero_prb_and_byte_alignment_and_monotonic():
    assert tbs_bits(0, **MCS) == 0
    v = [tbs_bits(n, **MCS) for n in range(1, 274)]
    assert v == sorted(v) and all(x % 8 == 0 for x in v)
    assert TBS_TABLE[-1] == 3824 and TBS_TABLE == sorted(set(TBS_TABLE))


def test_tbs_rounds_half_up():
    # (N_info - 24) / 128 = 40.5 exactly: the spec rounds ties up to 41, Python's round() gives 40
    assert tbs_bits(4, 9, 12, 0, 8, 434 / 1024, 4) == 41 * 128


def test_prbs_for_bytes_is_the_smallest_that_fits():
    for need in (1, 56, 500, 2953):
        n, b = prbs_for_bytes(need, 273, **MCS)
        assert b >= need and (n == 1 or tbs_bits(n - 1, **MCS) < need * 8)
    assert prbs_for_bytes(10 ** 6, 50, **MCS)[0] == 50
    assert prbs_for_bytes(10, 0, **MCS) == (0, 0)


def test_grant_for_bsr():
    assert grant_for_bsr(2948, 0, 273, **MCS) == {"need": 2953, "n_prb": 95, "tbs_bytes": 3009, "short_by": 0}
    assert grant_for_bsr(2948, 0, 50, **MCS)["short_by"] > 0
    assert grant_for_bsr(2948, 2948, 273, **MCS)["n_prb"] == 0
    assert bytes_to_request(255, 0) == 257 and bytes_to_request(256, 0, 2) == 261


def test_split_budget_accounts_for_every_byte_and_skips_empty():
    for tb in range(3, 700):
        plan, left = split_budget(tb, 2, [(1, 40), (3, 0), (4, 5000)])
        assert sum(p + h for _, p, h in plan) + 2 + left == tb and left >= 0
        assert all(p > 0 for _, p, _ in plan)


# ---------------------------------------------------------------- UE transport blocks
def test_ue_sends_short_bsr_for_one_lcg_and_long_for_two():
    ue = UE(0, 4, 0)
    ue.on_data(4, 3000, 0)
    assert unpack(ue.build_new_tb(56, 0), UL)[-1] == ShortBSR.from_bytes(2, ue.q[4])
    ue = UE(0, 4, 0)
    ue.on_data(1, 500, 0); ue.on_data(4, 3000, 0)
    assert unpack(ue.build_new_tb(56, 0), UL)[-1] == LongBSR.from_bytes({1: ue.q[1], 2: ue.q[4]})


def test_ue_tb_is_exactly_the_grant_for_every_size():
    for size in range(6, 320):
        ue = UE(0, 4, 0)
        ue.on_data(1, 100, 0); ue.on_data(4, 150, 0)
        tb = ue.build_new_tb(size, 0)
        assert len(tb) == size
        assert sum(len(getattr(s, "data", b"")) for s in unpack(tb, UL)) + ue.total() == 250


# ---------------------------------------------------------------- closed loop
def run(arrivals, n=600, **kw):
    sim = Sim([UE(0, 4, 1), UE(1, 20, 5)], arrivals, trace_until=-1, **kw)
    sim.run(n)
    return sim


def test_worked_timeline_with_one_forced_retx_delivers_everything():
    sim = run({0: [(0, 4, 3000)], 10: [(1, 4, 1500)]}, 80, fail_hook=lambda *a: a == (0, 1, 0))
    assert dict(sim.delivered) == {0: 3000, 1: 1500}
    assert sim.done_slot == {0: 21, 1: 39}


@pytest.mark.parametrize("seed", range(10))
def test_random_losses_with_two_lcgs_deliver_everything(seed):
    arr = {s: [(s % 2, 1 + 3 * (s % 3 > 0), 200 + 37 * s)] for s in range(0, 120, 3)}
    hook = None
    sim = run(arr, 900, seed=seed, fail_hook=hook)
    lost = {u: sim.injected[u] - sim.delivered[u] for u in sim.ues}
    assert lost == {0: 0, 1: 0} and all(u.total() == 0 for u in sim.ues.values())


def test_dropped_bsr_does_not_leave_the_ue_stuck():
    # TB 0 carries the only BSR and is dropped after all attempts; the retx-BSR timer must recover the rest
    sim = run({0: [(0, 4, 3000)]}, 400, fail_hook=lambda uid, idx, att: idx == 0)
    assert sim.ues[0].total() == 0 and 2900 <= sim.delivered[0] < 3000


def test_bsr_estimate_subtracts_grants_made_after_the_tb_was_built():
    g = run({}, 1).gnb
    ue = UE(0, 4, 0)
    ue.on_data(4, 3000, 0)
    tb = ue.build_new_tb(56, 10)
    reported = unpack(tb, UL)[-1].n_bytes()
    g.harq[0] = {0: dict(tbs=56, n_prb=2, attempt=0, ndi=1)}
    g.grants[0] = [(9, 500), (10, 56), (12, 1000)]             # only slot 12 is later than the BSR
    g.decode_done(0, 0, tb, False, 10)
    assert g.est[0] == reported - 1000


def test_older_bsr_arriving_late_through_harq_is_ignored():
    sim = run({}, 1)
    g, ue = sim.gnb, sim.ues[0]
    st = dict(tbs=56, n_prb=2, attempt=0, ndi=1)
    ue.on_data(4, 3000, 0)
    old = ue.build_new_tb(56, 5)
    new = ue.build_new_tb(56, 9)
    g.harq[0] = {0: dict(st), 1: dict(st)}
    g.decode_done(0, 1, new, False, 9)
    est = g.est[0]
    g.est[0] = 123
    g.decode_done(0, 0, old, False, 5)
    assert est > 0 and g.est[0] == 123


def test_demo_entry_point_runs(capsys):
    ul_sim.main()
    assert "UE1: injected 1500 B, delivered 1500 B" in capsys.readouterr().out
