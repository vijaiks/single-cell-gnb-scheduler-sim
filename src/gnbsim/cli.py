from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path

import yaml

from . import config as cfgmod
from .engine import SimEngine
from .plots import run_plot, compare_plot


def _load(args):
    cfg = cfgmod.load(args.config)
    if getattr(args, "algo", None):
        cfg.algo.name = args.algo
        cfg.algo.params = {}
    if getattr(args, "no_agent", False):
        cfg.agent.enabled = False
    if getattr(args, "slots", None):
        cfg.slots = args.slots
    if getattr(args, "seed", None) is not None:
        cfg.seed = args.seed
    return cfgmod.validate(cfg)


def _outdir(args, cfg, tag):
    base = Path(args.out) if args.out else Path("runs")
    d = base / f"{Path(args.config).stem}_{tag}_{cfg.seed}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_summary(s):
    print(f"algo={s['algo']}  cell={s['cell_throughput_mbps']:.2f} Mbps  PRB util={s['prb_utilization']:.2f}  "
          f"Jain(tput)={s['jain_throughput']:.3f}  Jain(share)={s['jain_resource_share']:.3f}")
    print(f"{'UE':>3} {'Mbps':>8} {'PRB%':>6} {'avgCQI':>7} {'avgMCS':>7} {'BLER1':>6} {'drops':>5} {'maxwait':>7}  flag")
    for u in s["ues"]:
        print(f"{u['ue']:>3} {u['throughput_mbps']:8.2f} {100*u['prb_share']:6.1f} {u['avg_cqi']:7.1f} "
              f"{u['avg_mcs']:7.1f} {u['first_tx_bler']:6.2f} {u['dropped_blocks']:5d} {u['max_unserved_run']:7d}  "
              f"{'STARVED' if u['starved'] else ''}")


def cmd_run(args, agent=False):
    cfg = _load(args)
    if agent:
        cfg.agent.enabled = True
    eng = SimEngine(cfg)
    loop = None
    if cfg.agent.enabled:
        from .agent.loop import AgentLoop
        loop = AgentLoop(eng, cfg.agent, log_path=None)
    d = _outdir(args, cfg, cfg.algo.name + ("_agent" if loop else ""))
    if loop:
        loop.log_path = d / "agent_log.jsonl"
        loop.log_path.write_text("", encoding="utf-8")
    s = eng.run()
    (d / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (d / "config.yaml").write_text(yaml.safe_dump(cfgmod.to_dict(cfg)), encoding="utf-8")
    eng.metrics.write_csv(d / "slots.csv")
    run_plot(eng, d / "plots.png")
    _print_summary(s)
    if loop:
        print(f"\nagent: {len(loop.records)} log records, actions applied: "
              f"{[(r['slot'], r['action']['kind']) for r in loop.records if r.get('action') and r['action'].get('applied')]}")
    print(f"\noutput: {d}")
    return s


def cmd_compare(args):
    cfg0 = _load(args)
    results = {}
    for algo in ("rr", "maxcqi", "pf"):
        cfg = copy.deepcopy(cfg0)
        cfg.algo.name = algo
        cfg.algo.params = cfg0.algo.params if algo == cfg0.algo.name else {}
        cfg.agent.enabled = False
        eng = SimEngine(cfg)
        results[algo] = eng.run()
    d = _outdir(args, cfg0, "compare")
    (d / "compare.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    compare_plot(results, d / "compare.png")
    print(f"{'algo':>8} {'cell Mbps':>10} {'Jain(tput)':>11} {'Jain(share)':>12}   per-UE Mbps")
    for a, s in results.items():
        print(f"{a:>8} {s['cell_throughput_mbps']:10.2f} {s['jain_throughput']:11.3f} {s['jain_resource_share']:12.3f}   "
              + " ".join(f"{u['throughput_mbps']:6.2f}" + ("*" if u["starved"] else " ") for u in s["ues"]))
    print("(* = starved)\noutput:", d)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gnbsim", description="Single-cell gNB scheduler simulator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "compare", "agent-run"):
        p = sub.add_parser(name)
        p.add_argument("config")
        p.add_argument("--out", default=None)
        p.add_argument("--slots", type=int)
        p.add_argument("--seed", type=int)
        if name != "compare":
            p.add_argument("--algo", choices=["rr", "maxcqi", "pf"])
        if name == "run":
            p.add_argument("--no-agent", action="store_true", help="ignore agent.enabled in the config (baseline run)")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "agent-run":
        cmd_run(args, agent=True)
    else:
        cmd_compare(args)


if __name__ == "__main__":
    main()
