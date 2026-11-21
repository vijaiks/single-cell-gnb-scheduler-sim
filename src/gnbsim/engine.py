"""SimEngine: the slot loop in the order documented in the design (Figure 2)."""
from __future__ import annotations
import math
import numpy as np

from . import phy
from .allocator import split_prbs
from .channel import build_trace
from .config import SimConfig
from .harq import HarqEntity, IDLE, WAIT_FB, PENDING_RETX
from .metrics import MetricsCollector
from .numerology import slot_duration_s
from .schedulers import Candidate, SlotContext, make_scheduler
from .traffic import build_arrivals
from .ue import UE

INF = math.inf


class SimEngine:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        n = len(cfg.ues)
        self.n = n
        self.slot_s = slot_duration_s(cfg.mu)
        children = np.random.SeedSequence(cfg.seed).spawn(2 * n + 1)
        self.trace, self.arrivals, self.ues = [], [], []
        for u in cfg.ues:
            self.trace.append(build_trace(u.channel, cfg.slots, np.random.default_rng(children[2 * u.id])))
            fb, arr = build_arrivals(u.traffic, cfg.slots, self.slot_s, np.random.default_rng(children[2 * u.id + 1]))
            self.arrivals.append(arr)
            self.ues.append(UE(id=u.id, priority=u.priority, full_buffer=fb, harq=HarqEntity(cfg.phy.n_harq)))
        self.rng = np.random.default_rng(children[-1])
        self.algo_name = cfg.algo.name
        self.sched = make_scheduler(cfg.algo.name, n, cfg.algo.params)
        self.max_ues = cfg.max_ues_per_slot
        self.metrics = MetricsCollector(n, cfg.slots, self.slot_s, cfg.n_prb)
        self.t = 0
        self.agent = None
        self._init_reports()

    # ------------------------------------------------------------------ control API (used by the agent)
    def set_algo(self, name: str, params: dict | None = None):
        self.sched = make_scheduler(name, self.n, params if params is not None else {})
        self.algo_name = name

    def set_weight(self, ue: int, w: float):
        if not 0.25 <= w <= 4.0:
            raise ValueError("weight out of [0.25, 4]")
        self.ues[ue].priority = w

    def set_max_ues(self, k: int):
        if not 1 <= k <= self.n:
            raise ValueError("max_ues out of range")
        self.max_ues = k

    def set_manual_offset(self, ue: int, db: float):
        if not -6.0 <= db <= 3.0:
            raise ValueError("offset out of [-6, 3]")
        self.ues[ue].manual_db = db

    # ------------------------------------------------------------------ internals
    def _init_reports(self):
        for u in self.ues:
            u.cqi = phy.sinr_to_cqi(self.trace[u.id][0])

    def _resolve(self, u: UE, p, t: int, first: bool, fail: bool):
        """Common outcome handling for a transport block (ACK / NACK)."""
        m, ph = self.metrics, self.cfg.phy
        if first:
            m.first_ok[t, u.id] = -1 if fail else 1
        if fail:
            m.any_nack[t, u.id] = True
            if ph.olla_enabled:
                tgt = ph.olla_target_bler
                u.olla_db -= ph.olla_step_db * (1 - tgt) / tgt
        elif ph.olla_enabled:
            u.olla_db += ph.olla_step_db
        u.olla_db = max(-6.0, min(3.0, u.olla_db))

    def step(self):
        cfg, t, n = self.cfg, self.t, self.n
        m, ph, rep = self.metrics, cfg.phy, cfg.reports

        # 1 environment
        for u in self.ues:
            if not u.full_buffer:
                a = self.arrivals[u.id][t]
                u.buffer_bits += a
                u.arrived_bits += a
        # 2 reports (gNB sees stale CQI / BSR)
        for u in self.ues:
            if t % rep.p_cqi == 0:
                u.cqi_pending.append((t + rep.d_cqi, phy.sinr_to_cqi(self.trace[u.id][t])))
            while u.cqi_pending and u.cqi_pending[0][0] <= t:
                u.cqi = u.cqi_pending.pop(0)[1]
            if t % rep.p_bsr == 0:
                if u.full_buffer:
                    u.bsr_bits = INF
                else:
                    g = rep.bsr_granularity_bits
                    u.bsr_bits = math.ceil(u.buffer_bits / g) * g if u.buffer_bits > 0 else 0.0
        # 3 HARQ feedback due
        if ph.harq_enabled:
            for u in self.ues:
                for p in u.harq.procs:
                    if p.state == WAIT_FB and p.feedback_slot <= t:
                        first = p.retx_count == 0
                        self._resolve(u, p, t, first, p.will_fail)
                        if p.will_fail:
                            if p.retx_count < ph.max_retx:
                                p.state = PENDING_RETX
                            else:
                                u.lost_bits += p.payload_bits
                                m.lost[t, u.id] += p.payload_bits
                                m.drop[t, u.id] = True
                                p.state, p.payload_bits = IDLE, 0.0
                        else:
                            u.delivered_bits += p.payload_bits
                            m.delivered[t, u.id] += p.payload_bits
                            p.state, p.payload_bits = IDLE, 0.0
        # 4-5 link adaptation + candidates
        retx_c, new_c = [], []
        for u in self.ues:
            m.cqi[t, u.id] = u.cqi
            m.sinr[t, u.id] = self.trace[u.id][t]
            m.buffer[t, u.id] = u.buffer_bits if not u.full_buffer else 1e12
            m.backlogged[t, u.id] = u.backlogged()
            m.harq_busy[t, u.id] = u.harq.busy()
            if ph.harq_enabled:
                pend = u.harq.pending_retx()
                if pend:
                    retx_c.append((u, pend[0]))
                    continue
            if u.cqi == 0:
                m.out_of_range[t, u.id] = True
                continue
            if u.bsr_bits <= 0:
                continue
            if ph.harq_enabled and u.harq.free_process() is None:
                m.no_proc[t, u.id] = True
                continue
            mcs = phy.select_mcs(u.cqi, u.offset_db())
            new_c.append(Candidate(u.id, u.cqi, mcs, phy.bits_per_prb(mcs, ph.layers),
                                   u.bsr_bits, u.priority))
        ctx = SlotContext(t, cfg.n_prb, n, self.slot_s)
        # 6 rank
        ranked = self.sched.rank(ctx, new_c) if new_c else []
        # 7 allocate: retx first, then split over top-K
        remaining = cfg.n_prb
        grants = []     # (ue, proc|None, n_prb, mcs, is_retx)
        for u, p in retx_c:
            if p.n_prb <= remaining:
                grants.append((u, p, p.n_prb, p.mcs, True))
                remaining -= p.n_prb
            else:
                m.retx_blocked[t, u.id] = True
        for c, k in split_prbs(ranked, remaining, self.max_ues):
            grants.append((self.ues[c.ue_id], None, k, c.mcs, False))
        # 8 PHY: TBS + BLER draw
        served: dict[int, int] = {}
        for u, p, k, mcs, is_retx in grants:
            sinr = self.trace[u.id][t]
            if is_retx:
                p.retx_count += 1
                tbs, payload = p.tbs_bits, p.payload_bits
            else:
                tbs = phy.tbs_bits(mcs, k, ph.layers)
                payload = tbs if u.full_buffer else min(float(tbs), u.buffer_bits)
                if not u.full_buffer:
                    u.buffer_bits -= payload
                    u.bsr_bits = max(0.0, u.bsr_bits - tbs)
                p = u.harq.free_process() if ph.harq_enabled else None
                if p is not None:
                    p.tbs_bits, p.payload_bits, p.mcs, p.n_prb, p.retx_count = tbs, payload, mcs, k, 0
            fail = (ph.bler_model == "logistic" and
                    self.rng.random() < phy.bler(sinr, mcs, p.retx_count if p else 0))
            if ph.harq_enabled:
                p.will_fail, p.state, p.feedback_slot = fail, WAIT_FB, t + ph.k1
            else:           # no HARQ: resolved immediately, NACK means lost
                self._resolve(u, None, t, True, fail)
                if fail:
                    u.lost_bits += payload; m.lost[t, u.id] += payload; m.drop[t, u.id] = True
                else:
                    u.delivered_bits += payload; m.delivered[t, u.id] += payload
            m.prbs[t, u.id], m.tbs[t, u.id], m.mcs[t, u.id] = k, tbs, mcs
            m.is_retx[t, u.id] = is_retx
            served[u.id] = tbs
            if not is_retx:
                ctx.new_served_order.append(u.id)
        # 9 update scheduler state
        ctx.candidate_ids = [c.ue_id for c in new_c] + [u.id for u, _ in retx_c]
        self.sched.on_slot_end(ctx, served)
        self.t += 1
        # 10 agent hook (actions apply from the next slot)
        if self.agent is not None:
            self.agent.on_slot_end(self.t)

    def run(self) -> dict:
        while self.t < self.cfg.slots:
            self.step()
        return self.summary()

    def summary(self) -> dict:
        s = self.metrics.summary(self.cfg.metrics.warmup_slots, self.cfg.metrics.starve_run_slots, upto=self.t)
        s["algo"] = self.algo_name
        return s

    def conservation(self) -> list:
        """arrived = delivered + lost + in-flight + buffered (non-full-buffer UEs). Returns residuals."""
        return [u.arrived_bits - (u.delivered_bits + u.lost_bits + u.inflight_bits() + u.buffer_bits)
                for u in self.ues if not u.full_buffer]
