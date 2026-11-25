"""
UL scheduling loop: SR -> grant -> PUSCH with BSR -> sized grants -> HARQ retx, exchanging real MAC PDUs.
Run:  python -m gnbsim.ul_sim

Simplifications:
- FDD-like, one PUSCH per UE per slot, fixed MCS, one shared PRB pool per slot.
- gNB decode takes PROC slots, DCI to PUSCH is K2 slots.
- No DL, no DRX, no PHR, no PBR/BSD in LCP (strict priority by LCID), no RLC retransmission of dropped TBs.
"""
from __future__ import annotations
import random
from collections import defaultdict

from .macce import UL, LongBSR, ShortBSR, regular_bsr
from .macpdu import MacSdu, build_tb, pack_subpdu, unpack
from .ulgrant import grant_for_bsr, prbs_for_bytes, split_budget

K2, PROC = 4, 2                 # DCI -> PUSCH slots, gNB decode/schedule delay
MAX_PRB, NUM_HARQ, MAX_TX = 106, 16, 4
MIN_GRANT = 56                  # bytes given after an SR when the buffer size is unknown
RETX_BSR_SLOTS = 80             # retxBSR-Timer: ask again if data waits this long without a grant
LCG_OF = {1: 1, 4: 2}           # logical channel -> logical channel group
MCS = dict(n_symb=12, n_dmrs_per_prb=12, n_oh_per_prb=0, qm=4, rate=490 / 1024)
RV_SEQ = (0, 2, 3, 1)


class UE:
    def __init__(self, uid, sr_period, sr_offset):
        self.uid, self.sr_period, self.sr_offset = uid, sr_period, sr_offset
        self.q = {1: 0, 4: 0}                    # pending bytes per LCID
        self.bsr_pending = self.sr_pending = False
        self.sr_prohibit_until = 0
        self.harq = {}                           # pid -> {tb, idx}
        self.tb_seq = 0
        self.last_grant = 0

    def total(self):
        return sum(self.q.values())

    def on_data(self, lcid, n, slot):
        had = [l for l, v in self.q.items() if v > 0]
        before = self.total()
        self.q[lcid] += n
        if before == 0:
            self.last_grant = max(self.last_grant, slot)
        if before == 0 or lcid < min(had):       # regular BSR trigger (simplified)
            self.bsr_pending = self.sr_pending = True

    def sr_due(self, slot):
        if self.total() > 0 and slot - self.last_grant >= RETX_BSR_SLOTS:
            self.bsr_pending = self.sr_pending = True
        return (self.sr_pending and slot >= self.sr_prohibit_until and slot >= self.sr_offset
                and (slot - self.sr_offset) % self.sr_period == 0)

    def rlc_pull(self, lcid, n):
        k = min(n, self.q[lcid])
        self.q[lcid] -= k
        return bytes([lcid]) * k                 # filler payload tagged with its LCID

    def per_lcg(self, pending):
        out = defaultdict(int)
        for lcid, n in pending.items():
            out[LCG_OF[lcid]] += n
        return out

    def build_new_tb(self, tbs_bytes, slot):
        self.last_grant = slot
        wants = [(l, self.q[l]) for l in sorted(self.q) if self.q[l] > 0]
        # the BSR sent can only be as long as, or shorter than, one covering everything waiting now
        reserve = len(pack_subpdu(regular_bsr(self.per_lcg(self.q)), UL))
        plan, _ = split_budget(tbs_bytes, reserve, wants)
        remaining = dict(self.q)
        for l, p, _ in plan:
            remaining[l] -= p
        include_bsr = self.bsr_pending or sum(remaining.values()) > 0
        if not include_bsr:
            plan, _ = split_budget(tbs_bytes, 0, wants)
        ces = []
        if include_bsr:
            ces.append(regular_bsr(self.per_lcg(remaining)))
            self.bsr_pending = self.sr_pending = False
        return build_tb(tbs_bytes, ces, [(l, p + h) for l, p, h in plan], self.rlc_pull, UL)


class GNB:
    def __init__(self, sim):
        self.sim = sim
        self.est = defaultdict(int)              # estimated UE buffer (bytes)
        self.sr_flag = set()
        self.harq = defaultdict(dict)            # uid -> pid -> state
        self.grants = defaultdict(list)          # uid -> [(pusch_slot, bytes)] for BSR correction
        self.prb_used = defaultdict(int)
        self.ue_used = defaultdict(set)          # pusch_slot -> uids already scheduled
        self.retx = []
        self.rr = 0
        self.bsr_built = defaultdict(lambda: -1)     # build slot of the newest BSR applied per UE

    def schedule(self):
        s, sim = self.sim.slot, self.sim
        ps = s + K2

        # 1) HARQ retransmissions first, same TBS and PRBs as the first attempt
        for uid, pid in list(self.retx):
            st = self.harq[uid][pid]
            if uid in self.ue_used[ps] or MAX_PRB - self.prb_used[ps] < st["n_prb"]:
                continue
            self.retx.remove((uid, pid))
            st["attempt"] += 1
            self.prb_used[ps] += st["n_prb"]
            self.ue_used[ps].add(uid)
            sim.log(f"gNB DCI UE{uid} pid{pid} NDI{st['ndi']} (same) RV{RV_SEQ[st['attempt'] % 4]} "
                    f"{st['tbs']}B -> PUSCH slot {ps}")
            sim.at(ps, lambda u=uid, p=pid: sim.ue_tx(u, p, retx=True))

        # 2) new grants, round robin, sized from the BSR estimate
        uids = sorted(sim.ues)
        order = uids[self.rr % len(uids):] + uids[:self.rr % len(uids)]
        self.rr += 1
        for uid in order:
            if uid in self.ue_used[ps] or not (self.est[uid] > 0 or uid in self.sr_flag):
                continue
            free = MAX_PRB - self.prb_used[ps]
            pid = next((p for p in range(NUM_HARQ) if p not in self.harq[uid]), None)
            if free <= 0 or pid is None:
                continue
            if self.est[uid] > 0:
                g = grant_for_bsr(self.est[uid], 0, free, **MCS)
                n_prb, tbs_b = g["n_prb"], g["tbs_bytes"]
            else:
                n_prb, tbs_b = prbs_for_bytes(MIN_GRANT, free, **MCS)
            if n_prb == 0:
                continue
            self.sr_flag.discard(uid)
            self.prb_used[ps] += n_prb
            self.ue_used[ps].add(uid)
            self.grants[uid].append((ps, tbs_b))
            self.est[uid] = max(0, self.est[uid] - (tbs_b - 5))
            ndi = 1 - self.harq_last_ndi(uid, pid)
            self.harq[uid][pid] = dict(tbs=tbs_b, n_prb=n_prb, attempt=0, ndi=ndi)
            sim.log(f"gNB DCI UE{uid} pid{pid} NDI{ndi} (new) {tbs_b}B ({n_prb} PRB) -> PUSCH slot {ps}")
            sim.at(ps, lambda u=uid, p=pid: sim.ue_tx(u, p, retx=False))

    def harq_last_ndi(self, uid, pid):
        d = self.sim.last_ndi
        return d.get((uid, pid), 0)

    def decode_done(self, uid, pid, tb, failed, built):
        sim, st = self.sim, self.harq[uid][pid]
        self.sim.last_ndi[(uid, pid)] = st["ndi"]
        if failed:
            if st["attempt"] + 1 >= MAX_TX:
                sim.log(f"gNB UE{uid} pid{pid} CRC fail, max retx, drop (RLC ARQ takes over)")
                del self.harq[uid][pid]
            else:
                sim.log(f"gNB UE{uid} pid{pid} CRC fail -> retx needed")
                self.retx.append((uid, pid))
            return
        for sub in unpack(tb, UL):
            if isinstance(sub, (ShortBSR, LongBSR)):
                # a TB keeps its BSR through HARQ retx, so judge it by when it was built, not decoded
                if built < self.bsr_built[uid]:
                    continue
                self.bsr_built[uid] = built
                reported = sub.n_bytes() if isinstance(sub, ShortBSR) else sum(sub.n_bytes().values())
                later = sum(b for (p, b) in self.grants[uid] if p > built)
                self.est[uid] = max(0, reported - later)      # drop bytes already granted since
                sim.log(f"gNB UE{uid} {type(sub).__name__} = {reported}B, "
                        f"already granted later {later}B -> est {self.est[uid]}B")
            elif isinstance(sub, MacSdu):
                sim.delivered[uid] += len(sub.data)
        if sim.delivered[uid] >= sim.injected[uid] and uid not in sim.done_slot:
            sim.done_slot[uid] = sim.slot
        del self.harq[uid][pid]


class Sim:
    def __init__(self, ues, arrivals, fail_hook=None, seed=1, trace_until=40):
        self.ues = {u.uid: u for u in ues}
        self.arrivals, self.trace_until = arrivals, trace_until
        self.ev = defaultdict(list)
        self.rng = random.Random(seed)
        self.fail_hook = fail_hook or self.random_fail
        self.last_ndi = {}
        self.injected, self.delivered, self.done_slot = defaultdict(int), defaultdict(int), {}
        self.gnb, self.slot = GNB(self), 0

    def at(self, slot, fn):
        self.ev[slot].append(fn)

    def log(self, msg):
        if self.slot <= self.trace_until:
            print(f"[slot {self.slot:3d}] {msg}")

    def random_fail(self, uid, tb_idx, attempt):
        return self.rng.random() < (0.10, 0.03, 0.01, 0.01)[min(attempt, 3)]

    def ue_tx(self, uid, pid, retx):
        ue, st = self.ues[uid], self.gnb.harq[uid][pid]
        if retx:
            tb, idx = ue.harq[pid]["tb"], ue.harq[pid]["idx"]
        else:
            tb, idx = ue.build_new_tb(st["tbs"], self.slot), ue.tb_seq
            ue.harq[pid] = dict(tb=tb, idx=idx, built=self.slot)
            ue.tb_seq += 1
        failed = self.fail_hook(uid, idx, st["attempt"])
        self.log(f"UE{uid} PUSCH pid{pid} TB#{idx} {len(tb)}B attempt {st['attempt']} "
                 f"{'(CRC will fail)' if failed else ''}")
        self.at(self.slot + PROC, lambda: self.gnb.decode_done(uid, pid, tb, failed, ue.harq[pid]["built"]))

    def run(self, n_slots):
        for self.slot in range(n_slots):
            for fn in self.ev.pop(self.slot, []):
                fn()
            for uid, lcid, n in self.arrivals.get(self.slot, []):
                self.ues[uid].on_data(lcid, n, self.slot)
                self.injected[uid] += n
                self.log(f"UE{uid} {n}B arrive on LCID {lcid}")
            for ue in self.ues.values():
                if ue.sr_due(self.slot):
                    ue.sr_prohibit_until = self.slot + 20
                    self.log(f"UE{ue.uid} sends SR")
                    self.at(self.slot + PROC, lambda u=ue.uid: self.gnb.sr_flag.add(u))
            self.gnb.schedule()
        assert all(v <= MAX_PRB for v in self.gnb.prb_used.values()), "PRB pool over-subscribed"


def main():
    ues = [UE(0, sr_period=4, sr_offset=1), UE(1, sr_period=20, sr_offset=5)]
    arrivals = {0: [(0, 4, 3000)], 10: [(1, 4, 1500)]}
    # Force the second TB of UE0 to fail on its first attempt, as in the timeline diagram
    hook = lambda uid, idx, attempt: (uid, idx, attempt) == (0, 1, 0)
    sim = Sim(ues, arrivals, fail_hook=hook, trace_until=45)
    sim.run(80)
    print()
    for uid in sorted(sim.ues):
        print(f"UE{uid}: injected {sim.injected[uid]} B, delivered {sim.delivered[uid]} B, "
              f"completed at slot {sim.done_slot.get(uid)}")
    assert all(sim.delivered[u] == sim.injected[u] for u in sim.ues)


if __name__ == "__main__":
    main()
