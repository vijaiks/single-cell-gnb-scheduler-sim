from .base import BaseScheduler


class RoundRobin(BaseScheduler):
    name = "rr"

    def __init__(self, n_ues: int, **_):
        self.n = n_ues
        self.pointer = 0

    def rank(self, ctx, cands):
        return sorted(cands, key=lambda c: ((c.ue_id - self.pointer) % self.n, c.ue_id))

    def on_slot_end(self, ctx, served_bits):
        if ctx.new_served_order:
            self.pointer = (ctx.new_served_order[-1] + 1) % self.n
