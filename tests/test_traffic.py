import numpy as np
import pytest
from gnbsim.config import TrafficSpec
from gnbsim.engine import SimEngine
from gnbsim.traffic import build_arrivals
from conftest import make


def build(slots=100, slot_s=1e-3, seed=1, **spec):
    return build_arrivals(TrafficSpec(**spec), slots, slot_s, np.random.default_rng(seed))


# ---------------------------------------------------------------- full buffer
def test_full_buffer_flag_and_no_explicit_arrivals():
    fb, arr = build(type="full_buffer")
    assert fb is True and arr.shape == (100,) and not arr.any()


# ---------------------------------------------------------------- periodic
def test_periodic_exact_slots_and_bytes_to_bits():
    fb, arr = build(slots=25, type="periodic", every_n_slots=10, bytes=100)
    assert fb is False
    assert np.flatnonzero(arr).tolist() == [0, 10, 20]
    assert arr[[0, 10, 20]].tolist() == [800.0] * 3


def test_periodic_every_slot():
    _, arr = build(slots=5, type="periodic", every_n_slots=1, bytes=1)
    assert arr.tolist() == [8.0] * 5


def test_periodic_longer_than_run_gives_single_arrival_at_slot_0():
    _, arr = build(slots=10, type="periodic", every_n_slots=1000, bytes=50)
    assert np.flatnonzero(arr).tolist() == [0]


@pytest.mark.parametrize("n", [0, -5])
def test_periodic_non_positive_period_means_no_traffic(n):
    fb, arr = build(type="periodic", every_n_slots=n, bytes=100)
    assert fb is False and not arr.any()


def test_periodic_does_not_depend_on_rng():
    a = build(seed=1, type="periodic", every_n_slots=7, bytes=10)[1]
    b = build(seed=2, type="periodic", every_n_slots=7, bytes=10)[1]
    assert np.array_equal(a, b)


# ---------------------------------------------------------------- poisson
def test_poisson_arrivals_are_whole_packets():
    fb, arr = build(slots=1000, type="poisson", rate_pps=2000, pkt_bytes=500)
    assert fb is False and arr.shape == (1000,) and arr.any()
    assert (arr >= 0).all() and (arr % 4000 == 0).all()


@pytest.mark.parametrize("slot_s", [1e-3, 0.5e-3])
def test_poisson_mean_rate_matches_pps_for_each_slot_length(slot_s):
    n = 100_000
    _, arr = build(slots=n, slot_s=slot_s, type="poisson", rate_pps=800, pkt_bytes=1500)
    assert arr.sum() / (n * slot_s) == pytest.approx(800 * 1500 * 8, rel=0.03)


def test_poisson_variance_equals_mean():
    _, arr = build(slots=100_000, type="poisson", rate_pps=2000, pkt_bytes=1)
    counts = arr / 8
    assert counts.var() == pytest.approx(counts.mean(), rel=0.05)


def test_poisson_zero_rate_means_no_traffic():
    assert not build(type="poisson", rate_pps=0)[1].any()


def test_poisson_same_seed_repeats_and_other_seed_differs():
    kw = dict(slots=500, type="poisson", rate_pps=1000)
    assert np.array_equal(build(seed=5, **kw)[1], build(seed=5, **kw)[1])
    assert not np.array_equal(build(seed=5, **kw)[1], build(seed=6, **kw)[1])


# ---------------------------------------------------------------- errors
def test_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown traffic type bursty"):
        build(type="bursty")


def test_poisson_negative_rate_raises():
    with pytest.raises(ValueError):
        build(type="poisson", rate_pps=-1)


# ---------------------------------------------------------------- engine integration
def test_engine_counts_exactly_the_generated_arrivals(raw_l2):
    ues = [{"id": 0, "channel": {"mean_sinr_db": 20}, "traffic": {"type": "poisson", "rate_pps": 500}},
           {"id": 1, "channel": {"mean_sinr_db": 20}, "traffic": {"type": "periodic", "every_n_slots": 20, "bytes": 300}},
           {"id": 2, "channel": {"mean_sinr_db": 20}, "traffic": {"type": "full_buffer"}}]
    eng = SimEngine(make(raw_l2, slots=200, ues=ues))
    eng.run()
    assert eng.arrivals[0].any()
    assert eng.ues[0].arrived_bits == pytest.approx(eng.arrivals[0].sum())
    assert eng.ues[1].arrived_bits == pytest.approx(10 * 300 * 8)
    assert eng.ues[2].full_buffer and eng.ues[2].arrived_bits == 0


def test_engine_gives_identical_ues_independent_arrivals(raw_l2):
    tr = {"type": "poisson", "rate_pps": 1000}
    ues = [{"id": i, "channel": {"mean_sinr_db": 20}, "traffic": dict(tr)} for i in range(2)]
    eng = SimEngine(make(raw_l2, slots=200, ues=ues))
    assert not np.array_equal(eng.arrivals[0], eng.arrivals[1])
