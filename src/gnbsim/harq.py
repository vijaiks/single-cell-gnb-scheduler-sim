"""HARQ process table per UE (design sec. 6.3)."""
from __future__ import annotations
from dataclasses import dataclass, field

IDLE, WAIT_FB, PENDING_RETX = "IDLE", "WAIT_FB", "PENDING_RETX"


@dataclass
class HarqProcess:
    pid: int
    state: str = IDLE
    tbs_bits: int = 0
    payload_bits: float = 0.0
    mcs: int = 0
    n_prb: int = 0
    retx_count: int = 0
    feedback_slot: int = -1
    will_fail: bool = False


@dataclass
class HarqEntity:
    n_proc: int
    procs: list = field(default_factory=list)

    def __post_init__(self):
        self.procs = [HarqProcess(i) for i in range(self.n_proc)]

    def free_process(self):
        for p in self.procs:
            if p.state == IDLE:
                return p
        return None

    def pending_retx(self):
        return [p for p in self.procs if p.state == PENDING_RETX]

    def busy(self) -> int:
        return sum(1 for p in self.procs if p.state != IDLE)
