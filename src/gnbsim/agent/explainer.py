"""Plain-language explanations (deterministic template explainer; an LLM explainer can implement the same method)."""
from __future__ import annotations


class TemplateExplainer:
    def explain(self, f, chunks: list, ctx: dict) -> str:
        e, u, algo = f.evidence, f.ue, ctx.get("algo", "?")
        k = f.kind
        if k == "STARVATION":
            why = {
                "maxcqi": "Max-CQI always gives the slot to the UE with the best channel, so a UE with a weaker channel loses every time.",
                "pf": "PF compares each UE's current rate with its own recent average; this UE's average is high compared with what its channel gives now, so others win.",
                "rr": "Round Robin should not starve anyone, so check for HARQ blocking or empty reports.",
            }.get(algo, "")
            t = (f"UE-{u} is waiting too long. In the last {e['window_slots']} slots it got {e['prb_share']:.0%} of the PRBs "
                 f"(fair share is {e['fair_share']:.0%}) and went {e['max_unserved_run']} slots in a row without a grant. "
                 f"Its average CQI is {e['mean_cqi']:.1f}. {why}")
        elif k == "CQI_DROP":
            t = (f"UE-{u}'s channel got worse: average CQI fell from {e['cqi_before']:.1f} to {e['cqi_now']:.1f}. "
                 f"A lower CQI means a lower MCS and a smaller transport block per PRB, so its throughput falls even with the same PRBs. "
                 f"A sudden drop like this usually means interference or the UE moving to the cell edge - check interference near this UE.")
        elif k == "HIGH_BLER":
            t = (f"UE-{u} loses {e['bler']:.0%} of first transmissions ({e['samples']} samples, 95% range {e['ci95'][0]:.0%}-{e['ci95'][1]:.0%}); the target is about 10%. "
                 f"Too many retransmissions eat capacity. The MCS is too aggressive for what the channel really gives, so use a safer MCS.")
        elif k == "LOW_BLER":
            t = (f"UE-{u} loses only {e['bler']:.0%} of first transmissions ({e['samples']} samples, 95% range up to {e['ci95'][1]:.0%}), below the 10% target. "
                 f"The MCS is too careful and wastes spectrum; a slightly higher MCS would give more throughput.")
        elif k == "HARQ_EXHAUSTED":
            t = (f"UE-{u} had data but all HARQ processes were busy in {e['slots_without_free_process']} slot(s), so it could not be scheduled. "
                 f"Feedback takes K1 slots, so many blocks in flight can use up all processes.")
        elif k == "QUEUE_GROWING":
            t = f"UE-{u}'s buffer keeps growing over three windows: it gets less capacity than its traffic needs."
        elif k == "LOW_FAIRNESS":
            t = (f"Resources are shared very unevenly (fairness index {e['jain_share']:.2f}, 1.0 is perfectly even). "
                 f"Some UEs get almost nothing while others get most PRBs.")
        elif k == "PRB_UNDERUSE":
            t = (f"Only {e['utilization']:.0%} of the PRBs were used although UEs had data. "
                 f"Letting more UEs share each slot can fill the idle PRBs.")
        else:
            t = f"{k}: {e}"
        cites = "; ".join(c.cite() for c in chunks[:2])
        ref = " ".join(c.text for c in chunks[:1])
        return t + (f" Reference: {ref} [{cites}]" if chunks else " (no knowledge-base reference available)")
