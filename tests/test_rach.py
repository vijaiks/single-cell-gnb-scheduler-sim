import random

import pytest
from gnbsim import rach_sim
from gnbsim.dci import PdcchOrder
from gnbsim.macce import DL, UL, ContentionResolution, CRnti
from gnbsim.macpdu import MacSdu, Padding, unpack
from gnbsim.rach_sim import Cfg, Sim, summary
from gnbsim.rar import parse_rar_pdu

SURE = dict(detect=(1.0,), msg3_ok=1.0, capture=1.0)


def run(n, cfg, starts, seed=1, slots=600):
    sim = Sim(n, cfg, starts, seed=seed, trace=False)
    sim.run(slots)
    return sim


def test_demo_runs(capsys):
    rach_sim.main()
    out = capsys.readouterr().out
    assert "lost the contention" in out and "UE0: done, attempts 2" in out


def test_single_ue_timeline():
    sim = run(1, Cfg(**SURE), {0: 3})
    ue = sim.ues[0]
    # trigger 3 -> PRACH 20 -> RAR 24 -> Msg3 30 -> Msg4 35
    assert ue.state == "done" and ue.attempt == 0 and ue.done_slot == 20 + 4 + 6 + 1 + 4
    names = [(s, n) for s, n, _, _ in sim.pdus]
    assert names == [(24, "rar"), (30, "msg3"), (31, "msg4")]


def test_trigger_on_a_prach_slot_is_not_lost():
    # regression: an event added for the current slot used to be dropped, so the UE stayed idle forever
    sim = run(2, Cfg(**SURE), {0: 0, 1: 20})
    assert all(u.state == "done" for u in sim.ues.values())
    assert not sim.ev


def test_msg3_is_ul_ccch48_without_length_field():
    sim = run(1, Cfg(**SURE, msg3_tbs=11), {0: 3})
    _, _, tc, tb = next(p for p in sim.pdus if p[1] == "msg3")
    ident = sim.ues[0].ident
    assert len(tb) == 11 and tb[0] == 52 and tb[1:7] == ident and tb[7] == 63
    assert unpack(tb, UL) == [MacSdu(52, ident), Padding(3)]
    assert tc == sim.ues[0].tc_rnti


def test_msg4_echoes_the_ccch_sdu():
    sim = run(1, Cfg(**SURE), {0: 3})
    _, _, _, pdu = next(p for p in sim.pdus if p[1] == "msg4")
    assert pdu[0] == 62 and unpack(pdu, DL)[0] == ContentionResolution(sim.ues[0].ident)


def test_rar_content_and_ra_rnti():
    sim = run(1, Cfg(**SURE, bi_idx=5), {0: 25})     # PRACH at slot 40 = slot 0 of the frame
    _, _, rnti, pdu = next(p for p in sim.pdus if p[1] == "rar")
    bi, entries = parse_rar_pdu(pdu)
    assert rnti == 1 and bi == 5
    assert [e.rapid for e in entries] == [sim.ues[0].preamble] and entries[0].ta == sim.ues[0].ta


def test_collision_one_winner_then_retry():
    sim = run(2, Cfg(**SURE, forced={0: [9], 1: [9]}), {0: 1, 1: 2}, seed=3)
    assert sim.collisions == 1
    assert all(u.state == "done" for u in sim.ues.values())
    assert sorted(u.attempt for u in sim.ues.values()) == [0, 1]


def test_no_bi_means_zero_backoff():
    # collision with capture: the loser retries at the very next PRACH occasion when the RAR has no BI
    sim = run(2, Cfg(**SURE, bi_idx=None, forced={0: [9], 1: [9]}), {0: 1, 1: 2}, seed=3)
    loser = next(u for u in sim.ues.values() if u.attempt == 1)
    assert loser.backoff_ms == 0
    assert loser.done_slot == 40 + 15          # Msg4 lost at 35 -> next occasion 40 -> done 15 slots later


def test_backoff_stays_within_bi():
    # lost at slot 35; a 5 ms BI allows 0..10 slots of backoff -> earliest slot 36..46 -> occasion 40 or 60
    seen = set()
    for seed in range(30):
        sim = run(2, Cfg(**SURE, bi_idx=0, forced={0: [9], 1: [9]}), {0: 1, 1: 2}, seed=seed)
        loser = next(u for u in sim.ues.values() if u.attempt == 1)
        seen.add(loser.done_slot)
    assert seen == {40 + 15, 60 + 15}


def test_preamble_trans_max():
    sim = run(1, Cfg(detect=(0.0,)), {0: 3}, slots=3000)
    assert sim.ues[0].state == "failed" and sim.ues[0].attempt == rach_sim.MAX_TX
    assert not [p for p in sim.pdus if p[1] == "rar"]


def test_msg3_lost_then_cr_timer_expires():
    sim = run(1, Cfg(detect=(1.0,), msg3_ok=0.0), {0: 3}, slots=3000)
    assert sim.ues[0].state == "failed"
    assert len([p for p in sim.pdus if p[1] == "msg3"]) == rach_sim.MAX_TX
    assert not [p for p in sim.pdus if p[1] == "msg4"]


def test_load_is_deterministic_and_completes():
    def once():
        rng = random.Random(7)
        sim = Sim(40, Cfg(), {u: rng.randrange(16) for u in range(40)}, seed=7, trace=False)
        sim.run(3000)
        return summary(sim), sim.pdus

    a, b = once(), once()
    assert a == b
    s = a[0]
    assert s["success"] + s["failed"] == 40 and s["success"] >= 36 and s["collisions"] > 0
    assert sum(s["attempts"].values()) == 40
    assert max(s["attempts"]) <= rach_sim.MAX_TX
    assert set(s["by_kind"]) == {"cbra"}


def test_past_event_rejected():
    sim = run(1, Cfg(**SURE), {0: 3}, slots=50)
    with pytest.raises(ValueError):
        sim.at(10, lambda: None)


# ---------------------------------------------------------------- preamble split, CFRA by PDCCH order
def test_cbra_never_picks_a_dedicated_preamble():
    rng = random.Random(3)
    sim = Sim(60, Cfg(), {u: rng.randrange(40) for u in range(60)}, seed=3, trace=False)
    picked = []
    orig = sim.send_preamble

    def spy(uid):
        orig(uid)
        picked.append(sim.ues[uid].preamble)
    sim.send_preamble = spy
    sim.run(4000)
    assert picked and max(picked) < 56


def test_cfg_validates_preamble_split():
    with pytest.raises(ValueError):
        Cfg(num_cb_preambles=0)
    with pytest.raises(ValueError):
        Cfg(num_cb_preambles=65, num_preambles=64)


def test_ue_in_both_starts_and_orders_rejected():
    with pytest.raises(ValueError):
        Sim(1, Cfg(), {0: 3}, orders={0: 5})


def test_cfra_timeline_no_msg3_no_msg4():
    sim = Sim(1, Cfg(**SURE), {}, orders={0: 5}, trace=False)
    sim.run(300)
    ue = sim.ues[0]
    # order 5 -> earliest PRACH 7 -> occasion 20 -> RAR 24 = done
    assert ue.kind == "cfra" and ue.state == "done" and ue.done_slot == 24 and ue.attempt == 0
    assert [(s, n) for s, n, _, _ in sim.pdus] == [(5, "pdcch_order"), (24, "rar")]
    assert ue.preamble == 56


def test_pdcch_order_bytes_and_c_rnti_kept():
    sim = Sim(1, Cfg(**SURE), {}, orders={0: 5}, trace=False)
    sim.run(300)
    ue = sim.ues[0]
    _, _, rnti, dci = sim.pdus[0]
    assert rnti == ue.c_rnti == rach_sim.C_RNTI_BASE
    assert PdcchOrder.from_bytes(dci, 51) == PdcchOrder(56)
    _, _, _, rar = sim.pdus[1]
    (entry,) = parse_rar_pdu(rar)[1]
    assert entry.rapid == 56 and entry.ta == ue.ta == ue.ta_applied
    assert ue.c_rnti == rach_sim.C_RNTI_BASE and ue.tc_rnti is None        # TC-RNTI in the RAR ignored


def test_order_close_to_an_occasion_waits_for_the_next_one():
    sim = Sim(1, Cfg(**SURE), {}, orders={0: 19}, trace=False)              # 19 + 2 > 20 -> occasion 40
    sim.run(300)
    assert sim.ues[0].done_slot == 44
    sim = Sim(1, Cfg(**SURE), {}, orders={0: 18}, trace=False)              # 18 + 2 = 20 -> occasion 20
    sim.run(300)
    assert sim.ues[0].done_slot == 24


def test_cfra_and_cbra_on_same_occasion_do_not_collide():
    sim = Sim(2, Cfg(**SURE), {0: 3}, orders={1: 5}, seed=4, trace=False)
    sim.run(300)
    assert sim.collisions == 0 and all(u.state == "done" for u in sim.ues.values())
    assert sim.ues[1].done_slot < sim.ues[0].done_slot


def test_cfra_retry_without_backoff_then_preamble_trans_max():
    sim = Sim(1, Cfg(detect=(0.0,), bi_idx=12), {}, orders={0: 5}, trace=False)
    sends = []
    orig = sim.send_preamble
    sim.send_preamble = lambda uid: (sends.append(sim.slot), orig(uid))
    sim.run(1000)
    assert sends == [20, 40, 60, 80]                    # every occasion, same preamble, 960 ms BI ignored
    assert sim.ues[0].state == "failed" and sim.ues[0].attempt == rach_sim.MAX_TX


def test_cfra_success_on_retry():
    sim = Sim(1, Cfg(detect=(0.0, 1.0), msg3_ok=1.0), {}, orders={0: 5}, trace=False)
    sim.run(300)
    assert sim.ues[0].state == "done" and sim.ues[0].attempt == 1 and sim.ues[0].done_slot == 44


def test_pool_exhaustion_falls_back_to_cbra_with_c_rnti_ce():
    orders = {u: 2 + u for u in range(9)}               # 9 orders, 8 dedicated preambles
    sim = Sim(9, Cfg(**SURE), {}, orders=orders, trace=False)
    sim.run(600)
    kinds = [sim.ues[u].kind for u in range(9)]
    assert kinds == ["cfra"] * 8 + ["cfra_fallback"]
    assert [PdcchOrder.from_bytes(d, 51).preamble for _, n, _, d in sim.pdus if n == "pdcch_order"] == \
        list(range(56, 64)) + [0]
    assert all(u.state == "done" for u in sim.ues.values())
    ue = sim.ues[8]
    (_, _, _, msg3), = [p for p in sim.pdus if p[1] == "msg3"]
    assert unpack(msg3, UL)[0] == CRnti(ue.c_rnti) and msg3[0] == 58        # C-RNTI CE, not a CCCH SDU
    assert not [p for p in sim.pdus if p[1] == "msg4"]
    assert [p[2] for p in sim.pdus if p[1] == "pdcch_crnti"] == [ue.c_rnti]
    assert ue.c_rnti == rach_sim.C_RNTI_BASE + 8


def test_preamble_released_after_rar_and_reused():
    sim = Sim(2, Cfg(**SURE), {}, orders={0: 2, 1: 30}, trace=False)
    sim.run(300)
    assert sim.ues[0].preamble == 56 and sim.ues[1].preamble == 56     # released at slot 24, reused at 30
    assert not sim.cf_owner and len(sim.cf_free) == 8


def test_preamble_released_after_guard_on_failure():
    sim = Sim(1, Cfg(detect=(0.0,)), {}, orders={0: 5}, trace=False)
    sim.run(5 + rach_sim.CF_GUARD - 1)
    assert 56 in sim.cf_owner and sim.ues[0].state == "failed"
    sim.run(5 + rach_sim.CF_GUARD + 1)
    assert not sim.cf_owner and 56 in sim.cf_free


def test_fallback_ue_loses_to_idle_ue_waits_for_cr_timer():
    # connected fallback UE (C-RNTI CE) and idle UE (CCCH) collide; the gNB picks the idle UE's Msg3
    cfg = Cfg(**SURE, num_cb_preambles=64, forced={0: [9], 1: [9]})           # no dedicated preambles
    for seed in range(40):
        sim = Sim(2, cfg, {0: 3}, orders={1: 4}, seed=seed, trace=False)
        retries = []
        orig = sim.retry
        sim.retry = lambda uid, orig=orig, r=retries, sim=sim: (r.append((uid, sim.slot)), orig(uid))
        sim.run(130)
        if [p for p in sim.pdus if p[1] == "msg4"][:1] and sim.ues[0].done_slot == 35:
            break
    else:
        pytest.fail("no seed where the idle UE wins")
    ue = sim.ues[1]
    assert ue.kind == "cfra_fallback" and ue.attempt >= 1
    assert retries[0] == (1, 30 + rach_sim.CR_TIMER)               # ignored the Msg4, gave up only at the CR timer
    assert ue.state == "done"


def test_mixed_load_summary_by_kind():
    rng = random.Random(7)
    sim = Sim(40, Cfg(), {u: rng.randrange(16) for u in range(30)},
              orders={u: rng.randrange(16) for u in range(30, 40)}, seed=7, trace=False)
    sim.run(3000)
    s = summary(sim)
    assert s["by_kind"]["cfra"]["ues"] == 8 and s["by_kind"]["cfra_fallback"]["ues"] == 2
    assert s["by_kind"]["cbra"]["ues"] == 30
    assert sum(k["ues"] for k in s["by_kind"].values()) == s["ues"] == 40


def test_cfra_ignores_bi_heard_in_a_rar():
    # UE1's dedicated preamble is missed once; the RAR it hears (for CBRA UE0) carries a 960 ms BI
    sim = Sim(2, Cfg(detect=(1.0,), msg3_ok=1.0, bi_idx=12), {0: 3}, orders={1: 5}, trace=False)
    orig = sim.send_preamble

    def miss_first(uid):
        orig(uid)
        if uid == 1 and sim.ues[1].attempt == 0:
            u, p, _ = sim.prach[sim.slot][-1]
            sim.prach[sim.slot][-1] = (u, p, False)
    sim.send_preamble = miss_first
    sim.run(300)
    ue = sim.ues[1]
    assert ue.backoff_ms == 960                         # it did hear the BI
    assert ue.state == "done" and ue.attempt == 1 and ue.done_slot == 44     # yet retried at the next occasion
