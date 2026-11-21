from .base import BaseScheduler


class ProportionalFair(BaseScheduler):
    """metric = weight * inst_rate / avg_rate**beta ; EMA window tc (design sec. 5)."""
    name = "pf"

    def __init__(self, n_ues: int = 0, tc: float = 100.0, beta: float = 1.0,
                 update_idle: bool = False, **_):
        self.tc = float(tc)
        self.beta = float(beta)
        self.update_idle = bool(update_idle)
        self.avg: dict[int, float] = {}

    def rank(self, ctx, cands):
        for c in cands:                       # warm start: first observation
            if c.ue_id not in self.avg:
                self.avg[c.ue_id] = max(1.0, self.inst_rate(ctx, c))

        def metric(c):
            return c.weight * self.inst_rate(ctx, c) / (self.avg[c.ue_id] ** self.beta)
        return sorted(cands, key=lambda c: (-metric(c), c.ue_id))

    def on_slot_end(self, ctx, served_bits):
        ids = range(ctx.n_ues) if self.update_idle else ctx.candidate_ids
        for u in ids:
            if u not in self.avg:
                continue
            self.avg[u] = (1 - 1 / self.tc) * self.avg[u] + served_bits.get(u, 0) / self.tc

    def set_param(self, ue_id, key, value):
        if key == "tc":
            if not 5 <= value <= 5000:
                raise ValueError("tc out of range")
            self.tc = float(value)
        elif key == "beta":
            self.beta = float(value)
        else:
            super().set_param(ue_id, key, value)
