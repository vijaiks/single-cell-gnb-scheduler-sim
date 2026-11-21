"""AgentLoop: wakes every `interval` slots, judges the last `analysis_window` slots."""
from __future__ import annotations
import json
from pathlib import Path

from .actions import Action, ActionPlanner, apply_action, rollback, FAIRNESS_MOTIVE
from .analyzer import analyze
from .explainer import TemplateExplainer
from .kb import make_kb, QUERIES


class AgentLoop:
    def __init__(self, engine, cfg, log_path=None, kb=None, explainer=None):
        self.engine, self.cfg = engine, cfg
        self.A, self.N = cfg.analysis_window, cfg.interval
        self.kb = kb or make_kb(cfg)
        self.explainer = explainer or TemplateExplainer()
        self.planner = ActionPlanner(self.A)
        self.log_path = Path(log_path) if log_path else None
        self.records: list = []
        self.seen: set = set()
        self.pending: list = []        # [{action, baseline, due, evals}]
        engine.agent = self

    # ------------------------------------------------------------------ hook
    def on_slot_end(self, t: int):
        if t % self.N != 0 or t < self.A:
            return
        self.step(t)

    def _emit(self, rec):
        self.records.append(rec)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    def step(self, t: int):
        eng = self.engine
        outcomes = self._evaluate_pending(t)
        findings = analyze(eng.metrics, t, self.A, self.cfg.rules,
                           bler_enabled=eng.cfg.phy.bler_model != "none",
                           full_buffer=[u.full_buffer for u in eng.ues])
        keys = {f.key for f in findings}
        new = [f for f in findings if f.key not in self.seen]
        self.seen = keys
        explanations = []
        for f in new:
            chunks = self.kb.search(QUERIES[f.kind], "NR", topic=f.kind)
            text = self.explainer.explain(f, chunks, {"algo": eng.algo_name})
            explanations.append({"kind": f.kind, "ue": f.ue, "text": text,
                                 "citations": [c.cite() for c in chunks], "query": QUERIES[f.kind]})
        action = self._plan_and_apply(t, findings, new)
        if explanations or action or outcomes:
            self._emit({"slot": t, "findings": [f.to_dict() for f in new], "explanations": explanations,
                        "action": action.to_dict() if action else None, "outcomes": outcomes})

    # ------------------------------------------------------------------ planning / guardrails
    def _plan_and_apply(self, t, findings, new):
        eng = self.engine
        new_keys = {f.key for f in new}
        for a in self.planner.candidates(findings, eng, t):
            if a.advice_only:
                if any((k, a.ue) in new_keys for k in a.motive):       # advise once per new finding
                    a.slot = t
                    return a
                continue
            if self.pending or t - self.planner.last_any < self.A:     # one action per window, none while evaluating
                continue
            if t - self.planner.last_by_kind.get(a.kind, -10 ** 9) < 2 * self.A:
                continue
            base = eng.metrics.window(t - self.A, t)
            if apply_action(eng, a):
                a.slot = t
                self.planner.last_any = t
                self.planner.last_by_kind[a.kind] = t
                self.pending.append({"action": a, "base": base, "due": t + self.A, "evals": 0})
                return a
        return None

    def _evaluate_pending(self, t):
        out = []
        for p in list(self.pending):
            if t < p["due"]:
                continue
            a, base = p["action"], p["base"]
            cur = self.engine.metrics.window(t - self.A, t)
            tol = 0.5 if set(a.motive) & FAIRNESS_MOTIVE else 0.15
            drop = 1 - cur["cell_tput_mbps"] / base["cell_tput_mbps"] if base["cell_tput_mbps"] > 0 else 0.0
            if a.targets:
                b = sum(base["ues"][u]["tput_mbps"] for u in a.targets)
                c = sum(cur["ues"][u]["tput_mbps"] for u in a.targets)
            else:
                b, c = base["cell_tput_mbps"], cur["cell_tput_mbps"]
            if a.metric == "bler_down":          # MCS action: did BLER move toward the target?
                b, c = base["ues"][a.ue]["first_tx_bler"], cur["ues"][a.ue]["first_tx_bler"]
                improved = c <= b - 0.02
            else:
                improved = (c > 0.01) if b <= 0.01 else (c >= 1.03 * b)
            res = {"action": a.kind, "ue": a.ue, "applied_slot": a.slot, "target_before": b, "target_after": c,
                   "cell_before": base["cell_tput_mbps"], "cell_after": cur["cell_tput_mbps"],
                   "jain_share_before": base["jain_share"], "jain_share_after": cur["jain_share"]}
            if drop > tol:
                rollback(self.engine, a)
                res["verdict"] = f"rolled_back: cell throughput fell {drop:.0%} (limit {tol:.0%})"
                self.pending.remove(p)
            elif improved:
                res["verdict"] = "kept: target improved"
                self.pending.remove(p)
            else:
                p["evals"] += 1
                p["due"] = t + self.A
                if p["evals"] >= 2:
                    rollback(self.engine, a)
                    res["verdict"] = "rolled_back: no improvement after 2 windows"
                    self.pending.remove(p)
                else:
                    res["verdict"] = "waiting: no improvement yet"
            out.append(res)
        return out
