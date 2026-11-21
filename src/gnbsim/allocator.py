"""PRB allocator: equal split over the top-K ranked UEs, capped by need (design D10).

K (max_ues_per_slot) is a hard limit (think PDCCH capacity). PRBs freed by a capped UE flow to the other
UEs inside the top-K; if none of them can use them, the PRBs stay idle. The analyzer's PRB_UNDERUSE finding
and the RAISE_MAX_UES action exist for exactly that case.
"""
from __future__ import annotations
import math


def need_prbs(cand, n_prb: int) -> int:
    if math.isinf(cand.buffer_bits):
        return n_prb
    return max(1, min(n_prb, math.ceil(cand.buffer_bits / cand.bits_per_prb)))


def split_prbs(ranked, n_prb: int, k: int):
    """Return [(candidate, n_prb)] in rank order. Total <= n_prb."""
    if n_prb <= 0 or not ranked:
        return []
    k = max(1, k)
    need = {c.ue_id: need_prbs(c, n_prb) for c in ranked}
    alloc: dict[int, int] = {}
    order = []
    active = list(ranked[:k])
    for c in active:
        alloc[c.ue_id] = 0
        order.append(c)
    remaining = n_prb
    while remaining > 0 and active:
        per, extra = divmod(remaining, len(active))
        nxt = []
        for i, c in enumerate(active):
            give = per + (1 if i < extra else 0)
            g = min(give, need[c.ue_id] - alloc[c.ue_id])
            if g > 0:
                alloc[c.ue_id] += g
                remaining -= g
            if alloc[c.ue_id] < need[c.ue_id]:
                nxt.append(c)
        active = nxt
    return [(c, alloc[c.ue_id]) for c in order if alloc[c.ue_id] > 0]
