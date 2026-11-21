"""Fixed action menu, planner and guardrails (design sec. 8)."""
from __future__ import annotations
from dataclasses import dataclass, field

FAIRNESS_MOTIVE = {"STARVATION", "LOW_FAIRNESS"}


@dataclass
class Action:
    kind: str
    ue: int | None = None
    params: dict = field(default_factory=dict)
    reason: str = ""
    motive: list = field(default_factory=list)       # finding kinds that triggered it
    targets: list = field(default_factory=list)      # UEs expected to improve ([] = cell)
    advice_only: bool = False
    metric: str = "tput"                             # tput | bler_down
    applied: bool = False
    prev: dict = field(default_factory=dict)
    slot: int = -1

    def to_dict(self):
        return {"kind": self.kind, "ue": self.ue, "params": self.params, "reason": self.reason,
                "advice_only": self.advice_only, "applied": self.applied, "targets": self.targets}


def apply_action(engine, a: Action) -> bool:
    """Apply with bound checks; returns False if it would not change anything."""
    if a.advice_only:
        return False
    if a.kind == "RAISE_WEIGHT":
        old = engine.ues[a.ue].priority
        new = min(4.0, old * a.params.get("factor", 1.5))
        if abs(new - old) < 1e-9:
            return False
        a.prev = {"weight": old}
        engine.set_weight(a.ue, new)
    elif a.kind == "SWITCH_ALGO":
        if engine.algo_name == a.params["name"]:
            return False
        a.prev = {"name": engine.algo_name, "sched": engine.sched}
        engine.set_algo(a.params["name"])
    elif a.kind == "ADJUST_MCS_OFFSET":
        old = engine.ues[a.ue].manual_db
        new = max(-6.0, min(3.0, old + a.params["delta_db"]))
        if abs(new - old) < 1e-9:
            return False
        a.prev = {"manual_db": old}
        engine.set_manual_offset(a.ue, new)
    elif a.kind == "SET_TC":
        if engine.algo_name != "pf":
            return False
        old = engine.sched.tc
        new = max(20.0, min(500.0, old * a.params.get("factor", 0.5)))
        if abs(new - old) < 1e-9:
            return False
        a.prev = {"tc": old}
        engine.sched.set_param(None, "tc", new)
    elif a.kind == "RAISE_MAX_UES":
        old = engine.max_ues
        new = min(engine.n, old + 1)
        if new == old:
            return False
        a.prev = {"max_ues": old}
        engine.set_max_ues(new)
    else:
        return False
    a.applied = True
    return True


def rollback(engine, a: Action):
    if a.kind == "RAISE_WEIGHT":
        engine.ues[a.ue].priority = a.prev["weight"]
    elif a.kind == "SWITCH_ALGO":
        engine.sched, engine.algo_name = a.prev["sched"], a.prev["name"]
    elif a.kind == "ADJUST_MCS_OFFSET":
        engine.ues[a.ue].manual_db = a.prev["manual_db"]
    elif a.kind == "SET_TC":
        engine.sched.set_param(None, "tc", a.prev["tc"])
    elif a.kind == "RAISE_MAX_UES":
        engine.max_ues = a.prev["max_ues"]
    a.applied = False


class ActionPlanner:
    """Map findings to ONE action from the menu, honouring cooldown / one-action-per-window."""

    ORDER = ["STARVATION", "LOW_FAIRNESS", "HIGH_BLER", "QUEUE_GROWING", "LOW_BLER", "PRB_UNDERUSE", "CQI_DROP", "HARQ_EXHAUSTED"]  # scheduler-level causes first

    def __init__(self, A: int):
        self.A = A
        self.last_by_kind: dict[str, int] = {}
        self.last_any = -10 ** 9

    def candidates(self, findings, engine, t: int):
        by = {}
        for f in findings:
            by.setdefault(f.kind, []).append(f)
        starving = [f.ue for f in by.get("STARVATION", [])]
        for kind in self.ORDER:
            for f in by.get(kind, []):
                if kind == "HIGH_BLER":
                    yield Action("ADJUST_MCS_OFFSET", f.ue, {"delta_db": -1.0}, "BLER too high: use a safer MCS",
                                 [kind], [f.ue], metric="bler_down")
                elif kind == "STARVATION":
                    if engine.algo_name != "pf":
                        yield Action("SWITCH_ALGO", None, {"name": "pf"},
                                     f"UE(s) {sorted(set(starving))} starve under {engine.algo_name}: switch to Proportional Fair",
                                     ["STARVATION"], sorted(set(starving)))
                    else:
                        cq = [g for g in by.get("CQI_DROP", []) if g.ue == f.ue]
                        if cq:
                            continue          # channel problem, not a scheduling problem -> advice via CQI_DROP
                        yield Action("RAISE_WEIGHT", f.ue, {"factor": 1.5}, "raise PF weight for the starved UE",
                                     [kind], [f.ue])
                elif kind == "LOW_FAIRNESS":
                    if engine.algo_name != "pf":
                        yield Action("SWITCH_ALGO", None, {"name": "pf"}, "fairness is low: switch to Proportional Fair",
                                     [kind], [])
                    else:
                        yield Action("SET_TC", None, {"factor": 0.5}, "fairness is low: shorten the PF averaging window",
                                     [kind], [])
                elif kind == "QUEUE_GROWING":
                    yield Action("RAISE_WEIGHT", f.ue, {"factor": 1.5}, "buffer keeps growing: give the UE more priority",
                                 [kind], [f.ue])
                elif kind == "LOW_BLER":
                    yield Action("ADJUST_MCS_OFFSET", f.ue, {"delta_db": 0.5}, "BLER very low: allow a higher MCS",
                                 [kind], [f.ue])
                elif kind == "PRB_UNDERUSE":
                    yield Action("RAISE_MAX_UES", None, {}, "idle PRBs: let more UEs share each slot", [kind], [])
                elif kind == "CQI_DROP":
                    yield Action("FLAG_INTERFERENCE", f.ue, {}, f"CQI of UE-{f.ue} dropped: check interference (advice only)",
                                 [kind], [], advice_only=True)
                elif kind == "HARQ_EXHAUSTED":
                    yield Action("FLAG_HARQ", f.ue, {}, f"UE-{f.ue} ran out of HARQ processes: check K1 / process count (advice only)",
                                 [kind], [], advice_only=True)
