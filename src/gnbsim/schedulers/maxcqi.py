from .base import BaseScheduler


class MaxCQI(BaseScheduler):
    name = "maxcqi"

    def __init__(self, n_ues: int = 0, **_):
        pass

    def rank(self, ctx, cands):
        return sorted(cands, key=lambda c: (-self.inst_rate(ctx, c), c.ue_id))
