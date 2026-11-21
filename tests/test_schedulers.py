import numpy as np
from gnbsim.engine import SimEngine
from conftest import make


def served(eng):
    return (eng.metrics.prbs > 0)


def test_round_robin_slot_counts_differ_by_at_most_one(raw_l1):
    eng = SimEngine(make(raw_l1, slots=503, algo={"name": "rr"}))
    eng.run()
    counts = served(eng).sum(axis=0)
    assert counts.max() - counts.min() <= 1


def test_maxcqi_always_picks_best_channel(raw_l1):
    eng = SimEngine(make(raw_l1, slots=400, algo={"name": "maxcqi"}))
    eng.run()
    m = eng.metrics
    for t in range(400):
        u = int(np.argmax(m.prbs[t]))
        assert m.prbs[t, u] > 0
        assert m.cqi[t, u] == m.cqi[t].max()   # L1: reports are perfect and immediate


def test_pf_equal_ues_get_equal_share(raw_l1):
    ues = [{"id": i, "channel": {"mean_sinr_db": 15, "noise_std_db": 2.0}} for i in range(2)]
    eng = SimEngine(make(raw_l1, slots=3000, ues=ues, algo={"name": "pf", "params": {"tc": 100}}))
    s = eng.run()
    shares = [u["prb_share"] for u in s["ues"]]
    assert abs(shares[0] - shares[1]) < 0.05


def test_pf_never_starves_weak_ue(raw_l1):
    ues = [{"id": 0, "channel": {"mean_sinr_db": 22, "noise_std_db": 2.0}},
           {"id": 1, "channel": {"mean_sinr_db": 3, "noise_std_db": 2.0}}]
    eng = SimEngine(make(raw_l1, slots=2000, ues=ues, algo={"name": "pf", "params": {"tc": 100}}))
    s = eng.run()
    assert s["ues"][1]["prb_share"] > 0.30 and not s["ues"][1]["starved"]


def test_pf_weight_increases_share(raw_l1):
    def share(w):
        ues = [{"id": i, "priority": (w if i == 1 else 1.0), "channel": {"mean_sinr_db": 12, "noise_std_db": 2.0}}
               for i in range(2)]
        return SimEngine(make(raw_l1, slots=2000, ues=ues, algo={"name": "pf"})).run()["ues"][1]["prb_share"]
    assert share(2.0) > share(1.0) + 0.05
