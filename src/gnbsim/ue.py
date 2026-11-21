from __future__ import annotations
import math
from dataclasses import dataclass, field
from .harq import HarqEntity


@dataclass
class UE:
    id: int
    priority: float
    full_buffer: bool
    harq: HarqEntity
    buffer_bits: float = 0.0          # true buffer (payload not yet scheduled)
    cqi: int = 0                      # CQI as known by gNB (stale)
    bsr_bits: float = 0.0             # buffer as believed by gNB
    cqi_pending: list = field(default_factory=list)
    olla_db: float = 0.0
    manual_db: float = 0.0
    # accounting
    arrived_bits: float = 0.0
    delivered_bits: float = 0.0
    lost_bits: float = 0.0

    def offset_db(self) -> float:
        return max(-6.0, min(3.0, self.olla_db + self.manual_db))

    def backlogged(self) -> bool:
        return self.full_buffer or self.buffer_bits > 0

    def inflight_bits(self) -> float:
        from .harq import IDLE
        return sum(p.payload_bits for p in self.harq.procs if p.state != IDLE)
