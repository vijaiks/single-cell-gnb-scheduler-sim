"""Deterministic rule-based window analyzer (design sec. 8)."""
from __future__ import annotations
import math
from .findings import Finding


def wilson(k: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a proportion (k failures out of n)."""
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return c - h, c + h

DEFAULT_RULES = dict(
    starve_share_factor=0.5, starve_run=50, cqi_drop=3.0,
    bler_high=0.20, bler_low=0.05, min_bler_samples=20,
    jain_low=0.6, util_low=0.9, queue_windows=3, queue_min_bits=8000,
)


def analyze(metrics, t_end: int, A: int, rules: dict | None = None, bler_enabled: bool = True,
            full_buffer: list | None = None) -> list:
    r = {**DEFAULT_RULES, **(rules or {})}
    w = metrics.window(t_end - A, t_end)
    prev = metrics.window(t_end - 2 * A, t_end - A) if t_end - 2 * A >= 0 else None
    out = []
    fair = w["fair_share"]
    for u in w["ues"]:
        i = u["ue"]
        if i not in w["active"]:
            continue
        if u["prb_share"] < r["starve_share_factor"] * fair or u["max_unserved_run"] >= r["starve_run"]:
            out.append(Finding("STARVATION", i, {
                "prb_share": u["prb_share"], "fair_share": fair, "max_unserved_run": u["max_unserved_run"],
                "mean_cqi": u["mean_cqi"], "window_slots": A}))
        if prev is not None:
            pc = prev["ues"][i]["mean_cqi"]
            if pc > 0 and u["mean_cqi"] <= pc - r["cqi_drop"]:
                out.append(Finding("CQI_DROP", i, {"cqi_now": u["mean_cqi"], "cqi_before": pc, "window_slots": A}))
        if bler_enabled and u["first_tx_n"] >= r["min_bler_samples"]:
            lo, hi = wilson(round(u["first_tx_bler"] * u["first_tx_n"]), u["first_tx_n"])
            ev = {"bler": u["first_tx_bler"], "samples": u["first_tx_n"], "ci95": [round(lo, 3), round(hi, 3)]}
            if lo > r["bler_high"]:                 # statistically above target band (no flapping on noise)
                out.append(Finding("HIGH_BLER", i, ev))
            elif hi < r["bler_low"]:
                out.append(Finding("LOW_BLER", i, ev))
        if u["no_proc_slots"] >= 1:
            out.append(Finding("HARQ_EXHAUSTED", i, {"slots_without_free_process": u["no_proc_slots"]}))
    # queue growth (non full-buffer UEs): buffer mean rising over 3 consecutive windows
    k = r["queue_windows"]
    if t_end - k * A >= 0:
        ws = [metrics.window(t_end - (k - j) * A, t_end - (k - j - 1) * A) for j in range(k)]
        for i in w["active"]:
            if full_buffer and full_buffer[i]:
                continue
            seq = [x["ues"][i]["mean_buffer_bits"] for x in ws]
            if all(seq[j] < seq[j + 1] for j in range(k - 1)) and seq[-1] > r["queue_min_bits"]:
                out.append(Finding("QUEUE_GROWING", i, {"buffer_bits_seq": seq}))
    if len(w["active"]) >= 2 and w["jain_share"] < r["jain_low"]:
        out.append(Finding("LOW_FAIRNESS", None, {"jain_share": w["jain_share"]}))
    if w["any_backlog"] and w["utilization"] < r["util_low"]:
        out.append(Finding("PRB_UNDERUSE", None, {"utilization": w["utilization"]}))
    return out
