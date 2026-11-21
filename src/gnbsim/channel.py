"""Synthetic per-UE SINR traces (precomputed, independent of scheduling)."""
from __future__ import annotations
import numpy as np


def build_trace(spec, slots: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(slots, dtype=float)
    mean = np.full(slots, float(spec.mean_sinr_db))
    if spec.ramp_to_db is not None:
        mean = spec.mean_sinr_db + (spec.ramp_to_db - spec.mean_sinr_db) * t / max(1, slots - 1)
    shadow = np.zeros(slots)
    if spec.shadow_std_db > 0:
        rho = spec.shadow_corr
        innov = rng.normal(0.0, spec.shadow_std_db * np.sqrt(1 - rho ** 2), slots)
        for i in range(1, slots):
            shadow[i] = rho * shadow[i - 1] + innov[i]
    noise = rng.normal(0.0, spec.noise_std_db, slots) if spec.noise_std_db > 0 else np.zeros(slots)
    ev = np.zeros(slots)
    for e in spec.events:
        start = int(e["slot"])
        end = start + int(e["duration"]) if e.get("duration") else slots
        ev[start:end] += float(e["delta_db"])
    return mean + shadow + noise + ev
