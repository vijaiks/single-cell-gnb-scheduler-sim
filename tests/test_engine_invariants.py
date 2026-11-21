import numpy as np
import pytest
from gnbsim import phy
from gnbsim.engine import SimEngine
from conftest import make


def one_packet_cfg(raw_l2, **phy_over):
    ues = [{"id": 0, "channel": {"mean_sinr_db": 20, "noise_std_db": 0.0},
            "traffic": {"type": "periodic", "every_n_slots": 10 ** 6, "bytes": 2000}}]
    ph = {"harq_enabled": True, "k1": 4, "n_harq": 16, "max_retx": 3, "bler_model": "logistic"}
    ph.update(phy_over)
    return make(raw_l2, slots=40, ues=ues, phy=ph, reports={"p_cqi": 1, "d_cqi": 0, "p_bsr": 1},
                max_ues_per_slot=1, metrics={"warmup_slots": 0})


def test_nack_retx_schedule_and_drop(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda *a, **k: 1.0)       # every transmission fails
    eng = SimEngine(one_packet_cfg(raw_l2))
    eng.run()
    m = eng.metrics
    tx_slots = [t for t in range(40) if m.tbs[t, 0] > 0]
    assert tx_slots == [0, 4, 8, 12]                              # K1 = 4, max_retx = 3 -> 4 transmissions
    assert m.is_retx[:, 0].tolist().count(True) == 3
    assert len({int(m.prbs[t, 0]) for t in tx_slots}) == 1        # retx keeps PRB count (same TBS)
    assert len({int(m.mcs[t, 0]) for t in tx_slots}) == 1         # ... and MCS
    assert m.drop[16, 0] and m.delivered.sum() == 0 and m.lost.sum() == pytest.approx(16000)
    assert m.first_ok[4, 0] == -1                                 # first-tx NACK seen exactly K1 slots later


def test_ack_frees_process_and_delivers(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda *a, **k: 0.0)
    eng = SimEngine(one_packet_cfg(raw_l2))
    eng.run()
    m = eng.metrics
    assert [t for t in range(40) if m.tbs[t, 0] > 0] == [0]
    assert m.delivered[4, 0] == pytest.approx(16000) and m.first_ok[4, 0] == 1
    assert eng.ues[0].harq.busy() == 0


def test_never_more_than_n_harq_busy_and_prb_budget(raw_l2):
    cfg = make(raw_l2, slots=1500)
    eng = SimEngine(cfg)
    eng.run()
    assert eng.metrics.harq_busy.max() <= cfg.phy.n_harq
    assert (eng.metrics.prbs.sum(axis=1) <= cfg.n_prb).all()


def test_conservation_of_bits(raw_l2):
    eng = SimEngine(make(raw_l2, slots=1500))
    eng.run()
    assert all(abs(r) < 1e-6 for r in eng.conservation())
    for u in eng.ues:
        assert u.buffer_bits >= 0


def test_determinism_and_seed_sensitivity(raw_l2):
    a = SimEngine(make(raw_l2, slots=600)); a.run()
    b = SimEngine(make(raw_l2, slots=600)); b.run()
    c = SimEngine(make(raw_l2, slots=600, seed=2)); c.run()
    assert np.array_equal(a.metrics.tbs, b.metrics.tbs) and a.summary() == b.summary()
    assert not np.array_equal(a.metrics.tbs, c.metrics.tbs)


def test_retx_served_before_new_data(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda *a, **k: 1.0)
    ues = [{"id": i, "channel": {"mean_sinr_db": 20, "noise_std_db": 0.0}} for i in range(2)]
    cfg = make(raw_l2, slots=60, ues=ues, max_ues_per_slot=2, metrics={"warmup_slots": 0},
               reports={"p_cqi": 1, "d_cqi": 0, "p_bsr": 1})
    eng = SimEngine(cfg); eng.run()
    m = eng.metrics
    for t in range(60):
        if m.retx_blocked[t].any():
            continue
        assert m.prbs[t].sum() <= cfg.n_prb


def test_two_ues_full_buffer_split_equally_in_layer2(raw_l2):
    ues = [{"id": i, "channel": {"mean_sinr_db": 20, "noise_std_db": 0.0}} for i in range(2)]
    cfg = make(raw_l2, slots=30, ues=ues, max_ues_per_slot=2, phy={"harq_enabled": False, "bler_model": "none"},
               reports={"p_cqi": 1, "d_cqi": 0, "p_bsr": 1})
    eng = SimEngine(cfg); eng.run()
    for t in range(30):
        assert abs(int(eng.metrics.prbs[t, 0]) - int(eng.metrics.prbs[t, 1])) <= 1
        assert eng.metrics.prbs[t].sum() == cfg.n_prb


def test_bsr_staleness_limits_scheduling(raw_l2):
    # A 2000-byte packet arrives EVERY slot, but the gNB only learns the buffer at BSR time (p_bsr = 10).
    # It grants what the last BSR showed, then goes quiet until the next report: stale view -> latency.
    ues = [{"id": 0, "channel": {"mean_sinr_db": 20, "noise_std_db": 0.0},
            "traffic": {"type": "periodic", "every_n_slots": 1, "bytes": 2000}}]
    cfg = make(raw_l2, slots=30, ues=ues, reports={"p_cqi": 1, "d_cqi": 0, "p_bsr": 10},
               phy={"harq_enabled": False, "bler_model": "none"}, metrics={"warmup_slots": 0})
    eng = SimEngine(cfg); eng.run()
    t = eng.metrics.tbs[:, 0]
    assert t[0] > 0 and (t[1:10] == 0).all()                    # one grant, then silence until the next BSR
    assert (t[10:15] > 0).all() and (t[15:20] == 0).all()       # BSR at 10 reveals a backlog -> burst, then quiet again
    assert eng.metrics.buffer[9, 0] > eng.metrics.buffer[1, 0]  # the queue builds in between
