"""
5G NR transport block size (TBS) determination, TS 38.214 clause 5.1.3.2 (DL) / 6.1.4.2 (UL).
Result is the MAC PDU size in bits (always a multiple of 8). Excludes the 24-bit TB CRC.

Also: LCP byte-budget split, and BSR -> PRB grant sizing. (phy.tbs_bits is the engine's simpler TBS model.)
"""
from __future__ import annotations
import math

# Table 5.1.3.2-1: TBS values for N_info <= 3824 (93 entries)
TBS_TABLE = (
    list(range(24, 193, 8)) + list(range(208, 385, 16)) + list(range(408, 577, 24)) +
    list(range(608, 769, 32)) + list(range(808, 929, 40)) +
    [984, 1032, 1064, 1128, 1160, 1192, 1224, 1256, 1288, 1320, 1352] +
    list(range(1416, 1929, 64)) + list(range(2024, 2281, 64)) + list(range(2408, 2857, 64)) +
    [2976, 3104, 3240, 3368, 3496, 3624, 3752, 3824]
)
assert len(TBS_TABLE) == 93


def tbs_bits(n_prb: int, n_symb: int, n_dmrs_per_prb: int, n_oh_per_prb: int,
             qm: int, rate: float, layers: int = 1) -> int:
    """
    n_prb          : allocated PRBs
    n_symb         : PDSCH/PUSCH symbols in the slot (1..14)
    n_dmrs_per_prb : DMRS REs per PRB over the allocation
    n_oh_per_prb   : higher-layer overhead per PRB (xOverhead: 0, 6, 12 or 18)
    qm, rate       : modulation order and code rate from the MCS table (rate = R x 1024 / 1024)
    """
    if n_prb <= 0:
        return 0
    n_re_prb = 12 * n_symb - n_dmrs_per_prb - n_oh_per_prb
    n_re = min(156, n_re_prb) * n_prb
    n_info = n_re * rate * qm * layers

    if n_info <= 3824:
        n = max(3, math.floor(math.log2(n_info)) - 6)
        n_info_q = max(24, (2 ** n) * math.floor(n_info / 2 ** n))
        return next(t for t in TBS_TABLE if t >= n_info_q)

    n = math.floor(math.log2(n_info - 24)) - 5
    n_info_q = max(3840, (2 ** n) * math.floor((n_info - 24) / 2 ** n + 0.5))
    if rate <= 0.25:
        c = math.ceil((n_info_q + 24) / 3816)
        return 8 * c * math.ceil((n_info_q + 24) / (8 * c)) - 24
    if n_info_q > 8424:
        c = math.ceil((n_info_q + 24) / 8424)
        return 8 * c * math.ceil((n_info_q + 24) / (8 * c)) - 24
    return 8 * math.ceil((n_info_q + 24) / 8) - 24


def split_budget(tb_bytes: int, ce_bytes: int, sdu_wants: list):
    """
    Turn a TB byte budget into per-LC payloads, the way LCP plus packing sees it.
    ce_bytes  : CE subheaders + payloads already reserved
    sdu_wants : [(lcid, pending_bytes)] in priority order
    Returns [(lcid, payload, hdr)] and the leftover padding bytes.
    """
    left = tb_bytes - ce_bytes
    out = []
    for lcid, pending in sdu_wants:
        if left <= 2:
            break
        if pending <= 0:
            continue
        # largest payload that fits, accounting for a 2- or 3-byte subheader
        net = left - 2 if left - 2 <= 255 else left - 3
        take = min(pending, net)
        hdr = 2 if take <= 255 else 3
        out.append((lcid, take, hdr))
        left -= take + hdr
    return out, left


def prbs_for_bytes(need_bytes: int, max_prb: int, n_symb: int, n_dmrs_per_prb: int,
                   n_oh_per_prb: int, qm: int, rate: float, layers: int = 1):
    """
    Inverse of tbs_bits: smallest PRB count whose TBS holds need_bytes (binary search,
    TBS is non-decreasing in PRBs for a fixed MCS). If even max_prb is too small,
    returns max_prb and the bytes it can carry.
    Returns (n_prb, tbs_bytes).
    """
    def tbs(n):
        return tbs_bits(n, n_symb, n_dmrs_per_prb, n_oh_per_prb, qm, rate, layers)

    if max_prb <= 0:
        return 0, 0
    if tbs(max_prb) < need_bytes * 8:
        return max_prb, tbs(max_prb) // 8
    lo, hi = 1, max_prb
    while lo < hi:
        mid = (lo + hi) // 2
        if tbs(mid) >= need_bytes * 8:
            hi = mid
        else:
            lo = mid + 1
    return lo, tbs(lo) // 8


def bytes_to_request(reported_bytes: int, granted_since_bsr: int, ce_overhead: int = 0) -> int:
    """
    gNB-side estimate of the TB size the UE would need to empty its buffer.
    reported_bytes    : last BSR buffer size converted to bytes (upper bound of its level)
    granted_since_bsr : payload bytes granted since that BSR (avoid double counting)
    ce_overhead       : CE bytes to leave room for (e.g. BSR 2, PHR 3)
    """
    est = max(0, reported_bytes - granted_since_bsr)
    if est == 0:
        return 0
    sdu_hdr = 2 if est <= 255 else 3
    return est + sdu_hdr + ce_overhead


def grant_for_bsr(reported_bytes: int, granted_since_bsr: int, max_prb: int,
                  n_symb: int, n_dmrs_per_prb: int, n_oh_per_prb: int,
                  qm: int, rate: float, layers: int = 1, ce_overhead: int = 2):
    need = bytes_to_request(reported_bytes, granted_since_bsr, ce_overhead)
    if need == 0:
        return {"need": 0, "n_prb": 0, "tbs_bytes": 0, "short_by": 0}
    n_prb, tbs_b = prbs_for_bytes(need, max_prb, n_symb, n_dmrs_per_prb,
                                  n_oh_per_prb, qm, rate, layers)
    return {"need": need, "n_prb": n_prb, "tbs_bytes": tbs_b, "short_by": max(0, need - tbs_b)}
