# TODO

## Next: handover with a dedicated preamble (random access demo)

Extend [`rach_sim.py`](src/gnbsim/rach_sim.py) with contention-free random access for **handover**. The PDCCH-order
case is already done. The simulator plays the **target** cell; the UE arrives from a neighbouring cell.

```
UE              source gNB                       target gNB (this simulator)
 │                  │── HO request ──────────────►  reserves a dedicated preamble, assigns a new C-RNTI
 │◄─ RRCReconfiguration (reconfigurationWithSync: new C-RNTI, dedicated preamble, T304)
 │   starts T304, syncs to the target
 │── Msg1: dedicated preamble ──────────────────────►
 │◄─ Msg2: RAR with its RAPID, TA, UL grant ─────────
 │   RA complete → T304 stopped, handover successful
 │── RRCReconfigurationComplete (SRB1) on the RAR's UL grant ──►
```

How it differs from the PDCCH-order case:

| | PDCCH-order CFRA (done) | Handover CFRA (to do) |
|---|---|---|
| C-RNTI | Keeps its own | Gets a **new** one from the target |
| Preamble given by | PDCCH order (DCI 1_0) | RRCReconfiguration `rach-ConfigDedicated` |
| Supervision | `preambleTransMax` → RA problem | **T304**. RA problems are ignored while it runs (TS 38.331 §5.3.10.3), so the UE keeps retrying |
| Failure | RA problem | T304 expiry → handover failure |
| After the RAR | Done | Done, then RRCReconfigurationComplete |

### Tasks
- [ ] Constants `HO_PREP_DELAY` (20 slots) and `HO_SYNC_DELAY` (10 slots). New `Cfg` fields `t304_ms` (default 100) and
      `ho_beam_ok` (chance the dedicated beam is good enough, otherwise CBRA; default 1.0).
- [ ] Scenario input `Sim(..., handovers={uid: slot})`.
- [ ] gNB `gnb_ho_request`: new C-RNTI, plus a dedicated preamble from the pool shared with PDCCH orders. If the pool
      is empty, the handover uses CBRA. Hold the preamble until RA succeeds or T304 runs out.
- [ ] UE: receive the handover command (a Python object, not ASN.1), start T304, run CFRA. Retry with no backoff,
      including past `preambleTransMax`, until T304 expires (`ho_failed`).
- [ ] After a successful HO CFRA, send RRCReconfigurationComplete as a real UL MAC PDU (SRB1 SDU with placeholder bytes).
- [ ] CBRA handover fallback: Msg3 = C-RNTI MAC CE + RRCReconfigurationComplete; contention resolution = PDCCH on
      the C-RNTI. Mostly reuses the existing PDCCH-order fallback path.
- [ ] `summary()`: handover group with success, T304 failures, CFRA vs CBRA, and handover interruption time.
- [ ] Demo scenario: handover UEs during CBRA load, in three cases: dedicated preamble available, pool exhausted,
      and preamble never detected (ends in `ho_failed` at T304).
- [ ] Tests: HO CFRA timeline (no Msg3/Msg4), new C-RNTI adopted, T304 stopped on success, retries past
      `preambleTransMax` until T304, CBRA HO Msg3 bytes (LCID 58 + SRB1), PDCCH-on-C-RNTI resolution, a handover UE
      colliding with an idle UE, the shared preamble pool and its release, beam fallback.
- [ ] README: random access section, glossary (T304, handover), limitations.

## Ideas, not planned yet
- Random access: beam failure recovery (RAR on C-RNTI), 2-step RA (MsgA/MsgB), power ramping and `ra-Prioritization`,
  HARQ on Msg3, Msg4 with RRCSetup.
- Connect the MAC PDU library and the uplink and random access demos to the downlink engine.
