"""Standalone 4-step contention-based random access (CBRA) demo: `python -m gnbsim.rach_sim`.

    Msg1 preamble -> Msg2 RAR (rar.py) -> Msg3 UL CCCH SDU (macpdu, UL) -> Msg4 Contention Resolution CE (macpdu, DL)

Procedure follows TS 38.321 sec. 5.1: RA-RNTI per PRACH occasion, ra-ResponseWindow, backoff from the BI
(PREAMBLE_BACKOFF = 0 when the RAR has no BI), ra-ContentionResolutionTimer, preambleTransMax.

Simplifications: 30 kHz SCS (0.5 ms slots) and PRACH at the same SCS, one PRACH occasion every RO_PERIOD slots
(symbol 0, frequency index 0), fixed Msg3 TB size, no HARQ on Msg3/Msg4, no power ramping (the detection
probability rises with each attempt instead), Msg4 carries only the Contention Resolution CE (no RRCSetup).
Not connected to the DL engine: like ul_sim, it is a separate demo of the MAC procedures.
"""
from __future__ import annotations
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .macce import DL, UL, ContentionResolution
from .macpdu import LCID_UL_CCCH48, MacSdu, pack, unpack
from .rar import RarEntry, backoff_ms, build_rar_pdu, pack_ul_grant, parse_rar_pdu, ra_rnti

SLOT_MS = 0.5
SLOTS_PER_FRAME = 20
RO_PERIOD = 20       # slots between PRACH occasions
RAR_DELAY = 4        # slots from the occasion to the RAR PDSCH (gNB processing)
RAR_WINDOW = 10      # ra-ResponseWindow, slots counted from the occasion
MSG3_DELAY = 6       # slots from the RAR to Msg3 PUSCH
MSG4_DELAY = 4       # slots from Msg3 decoding to Msg4
CR_TIMER = 32        # ra-ContentionResolutionTimer, slots counted from Msg3
MAX_TX = 4           # preambleTransMax
MSG4_TBS = 16        # bytes


@dataclass
class Cfg:
    num_preambles: int = 64
    bi_idx: int | None = 2                            # BI index in every RAR (2 = 20 ms); None = no BI subPDU
    detect: tuple = (0.80, 0.92, 0.98, 0.99)          # preamble detection probability per attempt
    msg3_ok: float = 0.95                             # single-UE Msg3 decode probability
    capture: float = 0.80                             # chance the gNB decodes one Msg3 when UEs collide
    msg3_tbs: int = 11                                # Msg3 transport block, bytes
    forced: dict = field(default_factory=dict)        # uid -> preamble per attempt, for scripted examples


class UE:
    def __init__(self, uid, rng):
        self.uid = uid
        self.ident = bytes(rng.randrange(256) for _ in range(6))   # 48-bit UL CCCH SDU (RRCSetupRequest)
        self.ta = rng.randrange(4, 200)                             # what the gNB will measure from the preamble
        self.attempt, self.state, self.token = 0, "idle", 0
        self.preamble = self.ra_rnti = self.tc_rnti = None
        self.backoff_ms = 0                                         # PREAMBLE_BACKOFF
        self.start_slot = self.done_slot = None


class Sim:
    def __init__(self, n_ues, cfg, starts, seed=1, trace=True, trace_until=400):
        self.cfg, self.trace, self.trace_until = cfg, trace, trace_until
        self.rng = random.Random(seed)
        self.ues = {u: UE(u, self.rng) for u in range(n_ues)}
        self.ev = defaultdict(list)
        self.prach, self.msg3 = defaultdict(list), defaultdict(list)
        self.rar_scheduled, self.next_tc = set(), 0x4601
        self.collisions, self.slot = 0, 0
        self.pdus = []                                   # (slot, name, rnti, bytes): every PDU sent, for tests
        for uid, s in starts.items():
            self.at(s, lambda u=uid: self.start_ra(u))

    # ----- plumbing -----
    def at(self, slot, fn):
        if slot < self.slot:
            raise ValueError(f"event for past slot {slot} (now {self.slot})")
        self.ev[slot].append(fn)

    def log(self, msg):
        if self.trace and self.slot <= self.trace_until:
            print(f"[slot {self.slot:3d} | {self.slot * SLOT_MS:6.1f} ms] {msg}")

    def run(self, max_slot=1500):
        for self.slot in range(self.slot, max_slot):
            while self.ev.get(self.slot):                # events added for the current slot run too
                for fn in self.ev.pop(self.slot):
                    fn()

    # ----- UE side -----
    def start_ra(self, uid):
        ue = self.ues[uid]
        ue.start_slot, ue.backoff_ms = self.slot, 0
        self.log(f"UE{uid} RACH triggered (data arrived, no uplink resources)")
        self.schedule_preamble(uid, self.slot)

    def schedule_preamble(self, uid, earliest):
        ro = -(-earliest // RO_PERIOD) * RO_PERIOD          # next PRACH occasion at or after `earliest`
        self.at(ro, lambda: self.send_preamble(uid))

    def send_preamble(self, uid):
        ue = self.ues[uid]
        ue.token += 1
        ue.state = "wait_rar"
        ue.ra_rnti = ra_rnti(0, self.slot % SLOTS_PER_FRAME, 0)
        forced = self.cfg.forced.get(uid, [])
        ue.preamble = forced[ue.attempt] if ue.attempt < len(forced) else self.rng.randrange(self.cfg.num_preambles)
        det = self.rng.random() < self.cfg.detect[min(ue.attempt, len(self.cfg.detect) - 1)]
        self.prach[self.slot].append((uid, ue.preamble, det))
        self.log(f"UE{uid} Msg1 preamble {ue.preamble}, attempt {ue.attempt + 1}" + ("" if det else " (not detected)"))
        if self.slot not in self.rar_scheduled:
            self.rar_scheduled.add(self.slot)
            self.at(self.slot + RAR_DELAY, lambda ro=self.slot: self.gnb_send_rar(ro))
        tok = ue.token
        self.at(self.slot + RAR_WINDOW, lambda: self.rar_window_end(uid, tok))

    def ue_rx_rar(self, uid, rnti, pdu):
        ue = self.ues[uid]
        if ue.state != "wait_rar" or rnti != ue.ra_rnti:
            return
        bi, entries = parse_rar_pdu(pdu)
        ue.backoff_ms = backoff_ms(bi) if bi is not None else 0
        mine = [e for e in entries if e.rapid == ue.preamble]
        if not mine:
            self.log(f"UE{uid} RAR has no RAPID {ue.preamble}, waiting for the window to end")
            return
        e = mine[0]
        ue.state, ue.tc_rnti = "msg3_wait", e.tc_rnti
        ue.token += 1                                        # cancels the RAR window timer
        self.log(f"UE{uid} RAR matched RAPID {e.rapid}: TA={e.ta}, TC-RNTI=0x{e.tc_rnti:04X}")
        self.at(self.slot + MSG3_DELAY, lambda: self.send_msg3(uid))

    def send_msg3(self, uid):
        ue = self.ues[uid]
        tb = pack([MacSdu(LCID_UL_CCCH48, ue.ident)], self.cfg.msg3_tbs, UL)
        ue.state = "wait_cr"
        ue.token += 1
        tok = ue.token
        self.msg3[self.slot].append((uid, ue.tc_rnti, tb))
        self.pdus.append((self.slot, "msg3", ue.tc_rnti, tb))
        self.log(f"UE{uid} Msg3 on TC-RNTI 0x{ue.tc_rnti:04X}: {tb.hex(' ')}")
        if len(self.msg3[self.slot]) == 1:
            self.at(self.slot + 1, lambda s=self.slot: self.gnb_process_msg3(s))
        self.at(self.slot + CR_TIMER, lambda: self.cr_timeout(uid, tok))

    def ue_rx_msg4(self, uid, pdu):
        ue = self.ues[uid]
        if ue.state != "wait_cr":
            return
        ce = next((s for s in unpack(pdu, DL) if isinstance(s, ContentionResolution)), None)
        if ce and ce.identity == ue.ident:                 # first 48 bits of the CCCH SDU it sent
            ue.state, ue.done_slot = "done", self.slot
            self.log(f"UE{uid} contention resolution OK, TC-RNTI 0x{ue.tc_rnti:04X} becomes its C-RNTI")
        else:
            self.log(f"UE{uid} contention resolution identity does not match, lost the contention")
            self.retry(uid)

    def rar_window_end(self, uid, tok):
        ue = self.ues[uid]
        if ue.token == tok and ue.state == "wait_rar":
            self.log(f"UE{uid} RAR window ended with no usable RAR")
            self.retry(uid)

    def cr_timeout(self, uid, tok):
        ue = self.ues[uid]
        if ue.token == tok and ue.state == "wait_cr":
            self.log(f"UE{uid} contention resolution timer expired")
            self.retry(uid)

    def retry(self, uid):
        ue = self.ues[uid]
        ue.attempt += 1
        ue.tc_rnti = None
        if ue.attempt >= MAX_TX:
            ue.state = "failed"
            self.log(f"UE{uid} reached preambleTransMax, random access problem reported to RRC")
            return
        wait = self.rng.randint(0, int(ue.backoff_ms / SLOT_MS))   # uniform in 0..PREAMBLE_BACKOFF
        ue.state = "backoff"
        self.log(f"UE{uid} backoff {wait * SLOT_MS:.1f} ms (PREAMBLE_BACKOFF {ue.backoff_ms} ms), then new preamble")
        self.schedule_preamble(uid, self.slot + 1 + wait)

    # ----- gNB side -----
    def gnb_send_rar(self, ro):
        sent = self.prach.pop(ro)
        by_p = defaultdict(list)
        for uid, p, det in sent:
            by_p[p].append((uid, det))
        self.collisions += sum(1 for lst in by_p.values() if len(lst) > 1)
        entries = []
        for p in sorted(p for p, lst in by_p.items() if any(d for _, d in lst)):
            first = next(uid for uid, d in by_p[p] if d)       # the gNB measures one TA per detected preamble
            entries.append(RarEntry(p, self.ues[first].ta, pack_ul_grant(0, 0x100 + p, 2, 4, 3, 0), self.next_tc))
            self.next_tc += 1
        if not entries:
            self.log(f"gNB heard no preamble in the occasion at slot {ro}")
            return
        rnti = ra_rnti(0, ro % SLOTS_PER_FRAME, 0)
        pdu = build_rar_pdu(entries, bi_idx=self.cfg.bi_idx)
        self.pdus.append((self.slot, "rar", rnti, pdu))
        self.log(f"gNB RAR on RA-RNTI {rnti}: RAPIDs {[e.rapid for e in entries]}, {len(pdu)} B: {pdu.hex(' ')}")
        for uid, _, _ in sent:
            self.ue_rx_rar(uid, rnti, pdu)

    def gnb_process_msg3(self, tx_slot):
        groups = defaultdict(list)
        for uid, tc, tb in self.msg3.pop(tx_slot, []):
            groups[tc].append((uid, tb))
        for tc, lst in groups.items():
            if len(lst) == 1:
                winner = lst[0] if self.rng.random() < self.cfg.msg3_ok else None
            else:
                self.log(f"gNB sees {len(lst)} UEs transmitting Msg3 on TC-RNTI 0x{tc:04X} (collision)")
                winner = self.rng.choice(lst) if self.rng.random() < self.cfg.capture else None
            if winner is None:
                self.log(f"gNB could not decode Msg3 on TC-RNTI 0x{tc:04X}")
                continue
            ccch = next(s for s in unpack(winner[1], UL) if isinstance(s, MacSdu) and s.lcid == LCID_UL_CCCH48)
            pdu4 = pack([ContentionResolution(ccch.data[:6])], MSG4_TBS, DL)
            self.pdus.append((self.slot, "msg4", tc, pdu4))
            self.log(f"gNB decoded Msg3, Msg4 on TC-RNTI 0x{tc:04X} echoes {ccch.data.hex()}: {pdu4.hex(' ')}")
            for uid, _ in lst:                                 # every UE using this TC-RNTI hears Msg4
                self.at(self.slot + MSG4_DELAY, lambda u=uid, p=pdu4: self.ue_rx_msg4(u, p))


def summary(sim) -> dict:
    ok = [u for u in sim.ues.values() if u.state == "done"]
    lat = sorted((u.done_slot - u.start_slot) * SLOT_MS for u in ok)
    return {"ues": len(sim.ues), "success": len(ok), "failed": len(sim.ues) - len(ok),
            "collisions": sim.collisions,
            "setup_ms": {"mean": sum(lat) / len(lat), "median": lat[len(lat) // 2], "max": lat[-1]} if lat else None,
            "attempts": dict(sorted(Counter(u.attempt + (u.state == "done") for u in sim.ues.values()).items()))}


def main():
    print("=== Example: 3 UEs, UE0 and UE1 pick the same preamble (23), UE2 picks 7 ===")
    cfg = Cfg(detect=(1.0,), msg3_ok=1.0, capture=1.0, forced={0: [23], 1: [23], 2: [7]})
    sim = Sim(3, cfg, starts={0: 3, 1: 5, 2: 8}, seed=4)
    sim.run(300)
    print()
    for uid, u in sim.ues.items():
        print(f"UE{uid}: {u.state}, attempts {u.attempt + 1}, setup time {(u.done_slot - u.start_slot) * SLOT_MS:.1f} ms")
    assert all(u.state == "done" for u in sim.ues.values()) and sim.collisions >= 1

    print("\n=== Load test: 40 UEs start within 8 ms, 64 preambles, default probabilities ===")
    rng = random.Random(7)
    big = Sim(40, Cfg(), starts={u: rng.randrange(16) for u in range(40)}, seed=7, trace=False)
    big.run(3000)
    s = summary(big)
    print(f"UEs {s['ues']}: success {s['success']}, failed {s['failed']}, preamble collisions {s['collisions']}")
    if s["setup_ms"]:
        print("setup time ms: mean {mean:.1f}, median {median:.1f}, max {max:.1f}".format(**s["setup_ms"]))
    print("attempts used:", s["attempts"])


if __name__ == "__main__":
    main()
