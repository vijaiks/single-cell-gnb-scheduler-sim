"""Traffic arrivals per UE, precomputed in bits per slot."""
from __future__ import annotations
import numpy as np


def build_arrivals(spec, slots: int, slot_s: float, rng: np.random.Generator):
    """Return (full_buffer, arrivals_bits[slots])."""
    if spec.type == "full_buffer":
        return True, np.zeros(slots)
    if spec.type == "poisson":
        counts = rng.poisson(spec.rate_pps * slot_s, slots)
        return False, counts * spec.pkt_bytes * 8.0
    if spec.type == "periodic":
        arr = np.zeros(slots)
        if spec.every_n_slots > 0:
            arr[::spec.every_n_slots] = spec.bytes * 8.0
        return False, arr
    raise ValueError(f"unknown traffic type {spec.type}")
