import random

import pytest
from gnbsim import rach_sim
from gnbsim.macce import DL, UL, ContentionResolution
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
    assert s["success"] == 40 and s["failed"] == 0 and s["collisions"] > 0
    assert sum(s["attempts"].values()) == 40


def test_past_event_rejected():
    sim = run(1, Cfg(**SURE), {0: 3}, slots=50)
    with pytest.raises(ValueError):
        sim.at(10, lambda: None)
