import pytest
from gnbsim import phy
from gnbsim.engine import SimEngine
from gnbsim.harq import HarqEntity, HarqProcess, IDLE, PENDING_RETX, WAIT_FB
from conftest import make


# ---------------------------------------------------------------- HarqProcess / HarqEntity
def test_new_process_defaults():
    p = HarqProcess(3)
    assert (p.pid, p.state, p.retx_count, p.payload_bits, p.will_fail) == (3, IDLE, 0, 0.0, False)
    assert p.feedback_slot == -1


@pytest.mark.parametrize("n", [1, 8, 16])
def test_entity_creates_n_idle_processes_with_sequential_ids(n):
    h = HarqEntity(n)
    assert [p.pid for p in h.procs] == list(range(n))
    assert all(p.state == IDLE for p in h.procs)
    assert h.busy() == 0 and h.pending_retx() == []


def test_entities_do_not_share_processes():
    a, b = HarqEntity(2), HarqEntity(2)
    a.procs[0].state = WAIT_FB
    assert a.busy() == 1 and b.busy() == 0


def test_free_process_returns_lowest_idle_without_reserving_it():
    h = HarqEntity(3)
    assert h.free_process() is h.procs[0]
    assert h.free_process() is h.procs[0]               # a lookup, not an allocation
    h.procs[0].state, h.procs[1].state = WAIT_FB, PENDING_RETX
    assert h.free_process() is h.procs[2]


def test_free_process_none_when_exhausted_then_reuses_released():
    h = HarqEntity(3)
    for p in h.procs:
        p.state = WAIT_FB
    assert h.free_process() is None and h.busy() == 3
    h.procs[1].state = IDLE
    assert h.free_process() is h.procs[1] and h.busy() == 2


def test_process_awaiting_retx_is_not_free():
    h = HarqEntity(1)
    h.procs[0].state = PENDING_RETX
    assert h.free_process() is None


def test_busy_and_pending_retx_count_the_right_states():
    h = HarqEntity(4)
    h.procs[0].state, h.procs[1].state, h.procs[3].state = PENDING_RETX, WAIT_FB, PENDING_RETX
    assert h.pending_retx() == [h.procs[0], h.procs[3]]  # pid order
    assert h.busy() == 3                                 # WAIT_FB and PENDING_RETX both occupy a process


# ---------------------------------------------------------------- state transitions (driven by the engine)
def engine(raw_l2, traffic, slots=30, **phy_over):
    ues = [{"id": 0, "channel": {"mean_sinr_db": 20, "noise_std_db": 0.0}, "traffic": traffic}]
    ph = {"harq_enabled": True, "k1": 4, "n_harq": 16, "max_retx": 3, "bler_model": "logistic", **phy_over}
    return SimEngine(make(raw_l2, slots=slots, ues=ues, phy=ph, max_ues_per_slot=1,
                          reports={"p_cqi": 1, "d_cqi": 0, "p_bsr": 1}, metrics={"warmup_slots": 0}))


ONE_PACKET = {"type": "periodic", "every_n_slots": 10 ** 6, "bytes": 2000}
FULL = {"type": "full_buffer"}


def tx_slots(eng):
    return [t for t in range(eng.cfg.slots) if eng.metrics.tbs[t, 0] > 0]


@pytest.mark.parametrize("k1", [1, 4, 8])
def test_process_is_held_until_feedback_k1_slots_later(raw_l2, monkeypatch, k1):
    monkeypatch.setattr(phy, "bler", lambda *a: 0.0)
    eng = engine(raw_l2, ONE_PACKET, k1=k1)
    for _ in range(k1):
        eng.step()
        assert eng.ues[0].harq.procs[0].state == WAIT_FB
        assert eng.metrics.delivered.sum() == 0
    eng.step()                                           # slot t = k1
    assert eng.ues[0].harq.procs[0].state == IDLE
    assert eng.metrics.delivered[k1, 0] == pytest.approx(16000)


def test_recovers_after_failures_and_delivers_once(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda sinr, mcs, retx: 1.0 if retx < 2 else 0.0)
    eng = engine(raw_l2, ONE_PACKET)
    eng.run()
    m = eng.metrics
    assert tx_slots(eng) == [0, 4, 8]
    assert m.delivered[12, 0] == pytest.approx(16000) and m.delivered.sum() == pytest.approx(16000)
    assert m.lost.sum() == 0 and not m.drop.any()
    assert m.first_ok[:, 0].tolist().count(-1) == 1      # only the first tx counts for first-tx BLER
    assert m.first_ok[:, 0].tolist().count(1) == 0


def test_success_on_last_allowed_retx_is_not_dropped(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda sinr, mcs, retx: 1.0 if retx < 3 else 0.0)
    eng = engine(raw_l2, ONE_PACKET, max_retx=3)
    eng.run()
    assert tx_slots(eng) == [0, 4, 8, 12]
    assert not eng.metrics.drop.any() and eng.metrics.delivered.sum() == pytest.approx(16000)


def test_max_retx_zero_drops_on_first_nack(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda *a: 1.0)
    eng = engine(raw_l2, ONE_PACKET, max_retx=0)
    eng.run()
    assert tx_slots(eng) == [0] and eng.metrics.drop[4, 0]
    assert eng.metrics.lost.sum() == pytest.approx(16000)
    assert eng.ues[0].harq.busy() == 0 and eng.ues[0].inflight_bits() == 0


def test_ue_stalls_when_all_processes_wait_for_feedback(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda *a: 0.0)
    eng = engine(raw_l2, FULL, slots=16, n_harq=2, k1=8)
    eng.run()
    assert tx_slots(eng) == [0, 1, 8, 9]
    assert [t for t in range(16) if eng.metrics.no_proc[t, 0]] == [2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15]


def test_retransmission_has_priority_over_new_data(raw_l2, monkeypatch):
    monkeypatch.setattr(phy, "bler", lambda sinr, mcs, retx: 0.0 if retx else 1.0)
    eng = engine(raw_l2, FULL, slots=8)
    eng.run()
    assert eng.metrics.is_retx[:, 0].tolist() == [False] * 4 + [True] * 4


def test_reused_process_resets_retx_count(raw_l2, monkeypatch):
    seen = []
    # fails at retx 0, succeeds at retx 1; a single process is reused for every block
    monkeypatch.setattr(phy, "bler", lambda sinr, mcs, retx: seen.append(retx) or (0.0 if retx else 1.0))
    engine(raw_l2, FULL, slots=16, n_harq=1).run()
    assert seen == [0, 1, 0, 1]
