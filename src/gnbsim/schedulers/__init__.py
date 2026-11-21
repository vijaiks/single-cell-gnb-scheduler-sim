from .base import BaseScheduler, Candidate, SlotContext
from .rr import RoundRobin
from .maxcqi import MaxCQI
from .pf import ProportionalFair

REGISTRY = {"rr": RoundRobin, "maxcqi": MaxCQI, "pf": ProportionalFair}


def make_scheduler(name: str, n_ues: int, params: dict | None = None):
    if name not in REGISTRY:
        raise ValueError(f"unknown algorithm {name!r}; choose from {sorted(REGISTRY)}")
    return REGISTRY[name](n_ues=n_ues, **(params or {}))
