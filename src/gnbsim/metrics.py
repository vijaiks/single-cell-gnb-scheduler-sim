"""Per-slot metric arrays, aggregates, Jain index, window statistics for the agent."""
from __future__ import annotations
import csv
import numpy as np


def jain(x) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) == 0 or np.sum(x ** 2) == 0:
        return 0.0
    return float(np.sum(x) ** 2 / (len(x) * np.sum(x ** 2)))


class MetricsCollector:
    def __init__(self, n_ues: int, slots: int, slot_s: float, n_prb: int):
        self.n, self.slots, self.slot_s, self.n_prb = n_ues, slots, slot_s, n_prb
        z = lambda dt=float: np.zeros((slots, n_ues), dtype=dt)
        self.prbs = z(int); self.tbs = z(int); self.mcs = z(int) - 1
        self.cqi = z(int); self.sinr = z(); self.buffer = z()
        self.delivered = z(); self.lost = z()
        self.backlogged = z(bool); self.is_retx = z(bool); self.no_proc = z(bool)
        self.retx_blocked = z(bool); self.out_of_range = z(bool)
        self.first_ok = z(int)         # +1 first-tx ACK, -1 first-tx NACK (at feedback slot)
        self.any_nack = z(bool); self.drop = z(bool); self.harq_busy = z(int)

    # ---------------- aggregates ----------------
    def summary(self, warmup: int = 0, starve_run: int = 20, upto: int | None = None) -> dict:
        end = self.slots if upto is None else upto
        a = min(warmup, max(0, end - 1))
        sl = slice(a, end)
        T = (end - a)
        secs = T * self.slot_s
        prbs = self.prbs[sl].sum(axis=0)
        total_prbs = max(1, prbs.sum())
        slots_served = (self.prbs[sl] > 0).sum(axis=0)
        deliv = self.delivered[sl].sum(axis=0)
        tput = deliv / secs / 1e6
        first_ok = self.first_ok[sl]
        n_first = (first_ok != 0).sum(axis=0)
        n_nack = (first_ok == -1).sum(axis=0)
        drops = self.drop[sl].sum(axis=0)
        n_back = self.backlogged[sl].mean(axis=0)
        active = [u for u in range(self.n) if n_back[u] > 0.5]
        fair = 1.0 / max(1, len(active))
        ues = []
        for u in range(self.n):
            run = self._max_run(self.backlogged[sl, u] & (self.prbs[sl, u] == 0))
            share = float(prbs[u] / total_prbs)
            cq = self.cqi[sl, u]
            mc = self.mcs[sl, u]
            starved = bool(u in active and (share < 0.5 * fair or run >= starve_run))
            ues.append({
                "ue": u, "throughput_mbps": float(tput[u]), "prb_share": share,
                "slot_share": float(slots_served[u] / T),
                "avg_cqi": float(cq[cq > 0].mean()) if (cq > 0).any() else 0.0,
                "avg_mcs": float(mc[mc >= 0].mean()) if (mc >= 0).any() else 0.0,
                "first_tx_bler": float(n_nack[u] / n_first[u]) if n_first[u] else 0.0,
                "dropped_blocks": int(drops[u]),
                "mean_buffer_kB": float(self.buffer[sl, u].mean() / 8000.0),
                "max_unserved_run": int(run), "starved": starved,
            })
        shares = [x["prb_share"] for x in ues]
        return {
            "slots_measured": int(T), "warmup_slots": int(a),
            "cell_throughput_mbps": float(tput.sum()),
            "prb_utilization": float(self.prbs[sl].sum() / (self.n_prb * T)),
            "jain_throughput": jain(tput), "jain_resource_share": jain(shares),
            "retx_blocked_slots": int(self.retx_blocked[sl].sum()),
            "out_of_range_slots": int(self.out_of_range[sl].sum()),
            "ues": ues,
        }

    @staticmethod
    def _max_run(mask) -> int:
        best = cur = 0
        for v in mask:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best

    # ---------------- window statistics (agent input) ----------------
    def window(self, a: int, b: int) -> dict:
        """Facts over slots [a, b) that a real operator could observe (no true SINR)."""
        a = max(0, a)
        sl = slice(a, b)
        T = b - a
        prbs = self.prbs[sl].sum(axis=0)
        tot = max(1, prbs.sum())
        back = self.backlogged[sl]
        active = [u for u in range(self.n) if back[:, u].mean() > 0.5]
        fair = 1.0 / max(1, len(active))
        fo = self.first_ok[sl]
        out = {"a": a, "b": b, "slots": T, "fair_share": fair, "active": active,
               "cell_tput_mbps": float(self.delivered[sl].sum() / (T * self.slot_s) / 1e6),
               "utilization": float(self.prbs[sl].sum() / (self.n_prb * T)),
               "any_backlog": bool(back.any(axis=1).mean() > 0.5),
               "ues": []}
        shares = []
        for u in range(self.n):
            cq = self.cqi[sl, u]
            n_first = int((fo[:, u] != 0).sum())
            run = self._max_run(back[:, u] & (self.prbs[sl, u] == 0))
            share = float(prbs[u] / tot)
            shares.append(share)
            out["ues"].append({
                "ue": u, "prb_share": share, "backlog_frac": float(back[:, u].mean()),
                "max_unserved_run": int(run),
                "mean_cqi": float(cq[cq > 0].mean()) if (cq > 0).any() else 0.0,
                "first_tx_n": n_first,
                "first_tx_bler": float((fo[:, u] == -1).sum() / n_first) if n_first else 0.0,
                "no_proc_slots": int(self.no_proc[sl, u].sum()),
                "mean_buffer_bits": float(self.buffer[sl, u].mean()),
                "tput_mbps": float(self.delivered[sl, u].sum() / (T * self.slot_s) / 1e6),
            })
        out["jain_share"] = jain([shares[u] for u in active]) if active else 1.0
        return out

    # ---------------- csv ----------------
    def write_csv(self, path):
        cols = ["slot", "ue", "sinr_db", "cqi", "mcs", "prbs", "tbs_bits", "delivered_bits", "lost_bits",
                "is_retx", "first_ok", "buffer_bits", "harq_busy", "no_proc", "retx_blocked"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(cols)
            for t in range(self.slots):
                for u in range(self.n):
                    w.writerow([t, u, f"{self.sinr[t,u]:.2f}", self.cqi[t, u], self.mcs[t, u], self.prbs[t, u],
                                self.tbs[t, u], f"{self.delivered[t,u]:.0f}", f"{self.lost[t,u]:.0f}",
                                int(self.is_retx[t, u]), self.first_ok[t, u], f"{self.buffer[t,u]:.0f}",
                                self.harq_busy[t, u], int(self.no_proc[t, u]), int(self.retx_blocked[t, u])])
