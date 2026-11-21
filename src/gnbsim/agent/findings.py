from __future__ import annotations
from dataclasses import dataclass, field

KINDS = ("STARVATION", "CQI_DROP", "HIGH_BLER", "LOW_BLER", "HARQ_EXHAUSTED",
         "QUEUE_GROWING", "LOW_FAIRNESS", "PRB_UNDERUSE")


@dataclass
class Finding:
    kind: str
    ue: int | None                      # None = cell-level
    evidence: dict = field(default_factory=dict)

    @property
    def key(self):
        return (self.kind, self.ue)

    def to_dict(self):
        return {"kind": self.kind, "ue": self.ue, "evidence": self.evidence}
