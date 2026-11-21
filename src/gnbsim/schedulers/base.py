from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Candidate:
    ue_id: int
    cqi: int
    mcs: int
    bits_per_prb: float
    buffer_bits: float      # as reported to the gNB (BSR view); may be inf
    weight: float = 1.0
    is_retx: bool = False


@dataclass
class SlotContext:
    slot: int
    n_prb: int
    n_ues: int
    slot_s: float
    candidate_ids: list = field(default_factory=list)   # filled before on_slot_end
    new_served_order: list = field(default_factory=list)


class BaseScheduler:
    name = "base"

    def rank(self, ctx: SlotContext, cands: list) -> list:
        raise NotImplementedError

    def on_slot_end(self, ctx: SlotContext, served_bits: dict) -> None:
        pass

    def set_param(self, ue_id, key: str, value: float) -> None:
        raise KeyError(f"{self.name}: unknown parameter {key!r}")

    def inst_rate(self, ctx: SlotContext, c: Candidate) -> float:
        return c.bits_per_prb * ctx.n_prb
