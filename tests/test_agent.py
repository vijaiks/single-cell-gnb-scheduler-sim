import numpy as np
import pytest
from gnbsim.agent.actions import Action, ActionPlanner, apply_action, rollback
from gnbsim.agent.analyzer import analyze, wilson
from gnbsim.agent.explainer import TemplateExplainer
from gnbsim.agent.findings import Finding
from gnbsim.agent.kb import CachedKB, Chunk, StubKB
from gnbsim.agent.loop import AgentLoop
from gnbsim.engine import SimEngine
from gnbsim.metrics import MetricsCollector
from conftest import make

A = 100


def healthy(n=2, slots=4 * A):
    m = MetricsCollector(n, slots, 1e-3, 50)
    m.backlogged[:] = True
    m.prbs[:] = 25
    m.cqi[:] = 10
    m.delivered[:] = 1000.0
    m.buffer[:] = 1e12
    return m


def kinds(fs):
    return sorted((f.kind, f.ue) for f in fs)


def run(m, t=4 * A, **kw):
    return analyze(m, t, A, bler_enabled=True, full_buffer=[True] * m.n, **kw)


def test_healthy_log_is_silent():
    m = healthy()
    m.first_ok[::10, :] = -1   # exactly 10% NACK
    m.first_ok[5::10, :] = 1
    m.first_ok[1::10, :] = 1; m.first_ok[2::10, :] = 1; m.first_ok[3::10, :] = 1
    m.first_ok[4::10, :] = 1; m.first_ok[6::10, :] = 1; m.first_ok[7::10, :] = 1
    m.first_ok[8::10, :] = 1; m.first_ok[9::10, :] = 1
    assert run(m) == []


def test_starvation_detected():
    m = healthy(); m.prbs[:, 1] = 0; m.prbs[:, 0] = 50
    ks = kinds(run(m))
    assert ("STARVATION", 1) in ks and ("STARVATION", 0) not in ks and ("LOW_FAIRNESS", None) in ks


def test_cqi_drop_detected():
    m = healthy(); m.cqi[3 * A:, 0] = 5
    assert ("CQI_DROP", 0) in kinds(run(m))


def test_bler_rules_use_confidence_interval():
    m = healthy(slots=2 * A); m.first_ok[:, :] = 1
    m.first_ok[::2, 0] = -1                      # 50% on UE0
    ks = kinds(run(m, t=2 * A))
    assert ("HIGH_BLER", 0) in ks and ("LOW_BLER", 1) in ks      # 0 failures in 100 -> upper bound < 5%
    m2 = healthy(slots=2 * A); m2.first_ok[:, :] = 1
    m2.first_ok[::6, :] = -1                     # ~17%: above 10% but not significantly above 20%
    assert not [k for k in kinds(run(m2, t=2 * A)) if k[0] in ("HIGH_BLER", "LOW_BLER")]


def test_wilson_bounds_sane():
    lo, hi = wilson(10, 100)
    assert lo < 0.10 < hi and 0 <= lo and hi <= 1
    assert wilson(0, 0) == (0.0, 1.0)


def test_harq_exhausted_and_underuse():
    m = healthy(slots=2 * A); m.no_proc[150, 1] = True; m.prbs[:] = 10
    ks = kinds(run(m, t=2 * A))
    assert ("HARQ_EXHAUSTED", 1) in ks and ("PRB_UNDERUSE", None) in ks


def test_queue_growing():
    m = healthy(slots=3 * A); m.backlogged[:] = True
    for w in range(3):
        m.buffer[w * A:(w + 1) * A, 1] = 20000 * (w + 1)
    fs = analyze(m, 3 * A, A, full_buffer=[True, False])
    assert ("QUEUE_GROWING", 1) in kinds(fs)


# ---------------------------------------------------------------- planner / guardrails
def small_engine(raw_agent, algo="pf", slots=400):
    cfg = make(raw_agent, slots=slots, algo={"name": algo})
    cfg.agent.enabled = False
    return SimEngine(cfg)


def test_raise_weight_respects_bounds(raw_agent):
    eng = small_engine(raw_agent)
    a = Action("RAISE_WEIGHT", 2, {"factor": 1.5})
    seen = []
    for _ in range(8):
        a = Action("RAISE_WEIGHT", 2, {"factor": 1.5})
        seen.append(apply_action(eng, a))
    assert eng.ues[2].priority == 4.0 and seen[-1] is False


def test_switch_algo_and_rollback(raw_agent):
    eng = small_engine(raw_agent, algo="maxcqi")
    a = Action("SWITCH_ALGO", None, {"name": "pf"})
    assert apply_action(eng, a) and eng.algo_name == "pf"
    rollback(eng, a)
    assert eng.algo_name == "maxcqi" and not a.applied
    assert not apply_action(eng, Action("SWITCH_ALGO", None, {"name": "maxcqi"}))


def test_offset_bounds(raw_agent):
    eng = small_engine(raw_agent)
    for _ in range(10):
        apply_action(eng, Action("ADJUST_MCS_OFFSET", 1, {"delta_db": -1.0}))
    assert eng.ues[1].manual_db == -6.0


def test_advice_is_never_applied(raw_agent):
    eng = small_engine(raw_agent)
    a = Action("FLAG_INTERFERENCE", 1, advice_only=True)
    assert apply_action(eng, a) is False and not a.applied


def test_one_action_per_window_and_cooldown(raw_agent):
    eng = small_engine(raw_agent, algo="pf")
    cfg = eng.cfg.agent
    loop = AgentLoop(eng, cfg)
    f = [Finding("STARVATION", 3, {}), Finding("STARVATION", 4, {})]
    a1 = loop._plan_and_apply(100, f, f)
    assert a1 is not None and a1.applied
    assert loop._plan_and_apply(110, f, f) is None            # evaluation pending -> no second action


def test_rollback_when_cell_throughput_collapses(raw_agent, monkeypatch):
    eng = small_engine(raw_agent, algo="pf")
    loop = AgentLoop(eng, eng.cfg.agent)
    a = Action("RAISE_WEIGHT", 2, {"factor": 1.5}, motive=["QUEUE_GROWING"], targets=[2])
    apply_action(eng, a)
    base = {"cell_tput_mbps": 10.0, "jain_share": 1.0, "ues": [{"tput_mbps": 1.0, "first_tx_bler": 0.1}] * 5}
    cur = {"cell_tput_mbps": 4.0, "jain_share": 1.0, "ues": [{"tput_mbps": 2.0, "first_tx_bler": 0.1}] * 5}
    loop.pending.append({"action": a, "base": base, "due": 100, "evals": 0})
    monkeypatch.setattr(eng.metrics, "window", lambda a_, b_: cur)
    out = loop._evaluate_pending(100)
    assert out[0]["verdict"].startswith("rolled_back") and eng.ues[2].priority == 1.0


def test_fairness_motivated_action_tolerates_bigger_cell_drop(raw_agent, monkeypatch):
    eng = small_engine(raw_agent, algo="maxcqi")
    loop = AgentLoop(eng, eng.cfg.agent)
    a = Action("SWITCH_ALGO", None, {"name": "pf"}, motive=["STARVATION"], targets=[4])
    apply_action(eng, a)
    base = {"cell_tput_mbps": 40.0, "jain_share": 0.2, "ues": [{"tput_mbps": 0.0, "first_tx_bler": 0.1}] * 5}
    cur = {"cell_tput_mbps": 26.0, "jain_share": 0.99, "ues": [{"tput_mbps": 3.0, "first_tx_bler": 0.1}] * 5}
    loop.pending.append({"action": a, "base": base, "due": 100, "evals": 0})
    monkeypatch.setattr(eng.metrics, "window", lambda a_, b_: cur)
    assert loop._evaluate_pending(100)[0]["verdict"].startswith("kept")      # -35% allowed (limit 50%)


# ---------------------------------------------------------------- kb / explainer
def test_stub_kb_returns_cited_chunks_and_explanation_cites_them():
    f = Finding("STARVATION", 4, {"prb_share": 0.0, "fair_share": 0.2, "max_unserved_run": 100, "mean_cqi": 6.0,
                                   "window_slots": 100})
    chunks = StubKB().search("q", "NR", topic="STARVATION")
    assert chunks and all(c.file.endswith(".pdf") for c in chunks)
    text = TemplateExplainer().explain(f, chunks, {"algo": "maxcqi"})
    assert "UE-4" in text and ".pdf p." in text and "Max-CQI" in text


def test_cached_kb_records_and_replays(tmp_path):
    class B:
        calls = 0
        def search(self, q, t=None, topic=None):
            B.calls += 1
            return [Chunk("x.pdf", "1", "hello")]
    kb = CachedKB(tmp_path / "c.jsonl", B())
    assert kb.search("q", "NR")[0].text == "hello" and kb.search("q", "NR")[0].text == "hello"
    assert B.calls == 1
    kb2 = CachedKB(tmp_path / "c.jsonl")                    # new object, no backend: served from file
    assert kb2.search("q", "NR")[0].file == "x.pdf" and kb2.search("other", "NR") == []


# ---------------------------------------------------------------- end to end
def test_end_to_end_agent_rescues_starved_ues(raw_agent, tmp_path):
    base_cfg = make(raw_agent, slots=1000); base_cfg.agent.enabled = False
    base = SimEngine(base_cfg).run()
    cfg = make(raw_agent, slots=1000)
    eng = SimEngine(cfg)
    loop = AgentLoop(eng, cfg.agent, log_path=tmp_path / "log.jsonl")
    s = eng.run()
    assert base["ues"][4]["throughput_mbps"] < 0.05
    assert eng.algo_name == "pf"
    assert s["ues"][4]["throughput_mbps"] > 1.0 and s["jain_resource_share"] > base["jain_resource_share"] + 0.4
    first = loop.records[0]
    assert first["slot"] == 100 and first["action"]["kind"] == "SWITCH_ALGO"
    assert all(e["citations"] for e in first["explanations"])
    assert (tmp_path / "log.jsonl").read_text().count("\n") == len(loop.records)


def test_interference_event_triggers_cqi_drop_advice(raw_agent):
    d = raw_agent
    d["ues"][3]["channel"]["events"] = [{"slot": 600, "delta_db": -10}]
    cfg = make(d, slots=1000)
    eng = SimEngine(cfg)
    loop = AgentLoop(eng, cfg.agent)
    eng.run()
    adv = [r for r in loop.records if r["action"] and r["action"]["kind"] == "FLAG_INTERFERENCE" and r["action"]["ue"] == 3]
    assert adv and 600 < adv[0]["slot"] <= 800
    assert not adv[0]["action"]["applied"]


def test_command_kb_via_wrapper_contract_and_failure_not_cached(tmp_path):
    import sys
    from gnbsim.agent.kb import CommandKB
    ok = tmp_path / "ok.py"
    ok.write_text('import json,sys; print(json.dumps([{"file":"a.pdf","page":"3","text":sys.argv[1]+"|"+sys.argv[2]}]))')
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.exit(1)")
    c = CommandKB(f"{sys.executable} {ok}").search("q", "NR")
    assert (c[0].file, c[0].page, c[0].text) == ("a.pdf", "3", "q|NR")
    cache = tmp_path / "c.jsonl"
    kb = CachedKB(cache, CommandKB(f"{sys.executable} {bad}"))
    assert kb.search("q", "NR") == [] and not cache.exists()      # failure -> no cache poisoning
