"""PHY abstraction: SINR thresholds, CQI/MCS selection, TBS, BLER (see design sec. 6.2)."""
from __future__ import annotations
import bisect
import math
from .tables import CQI_EFF, MCS_TABLE1

ALPHA = 0.75          # Shannon attenuation used for the 10%-BLER threshold
SLOPE_DB = 0.8        # logistic BLER slope
N_RE_PER_PRB = 12 * 14 - 12   # 156: 14 symbols, 12 DMRS REs removed
LN9 = math.log(9.0)


def thr10(eff: float) -> float:
    """SINR (dB) at which a format with spectral efficiency `eff` has 10% BLER."""
    return 10.0 * math.log10(2.0 ** (eff / ALPHA) - 1.0)


CQI_THR = [-math.inf] + [thr10(e) for e in CQI_EFF[1:]]
MCS_EFF = [qm * r / 1024.0 for qm, r in MCS_TABLE1]
MCS_THR = [thr10(e) for e in MCS_EFF]
N_MCS = len(MCS_TABLE1)


def sinr_to_cqi(sinr_db: float) -> int:
    """Highest CQI whose 10%-BLER threshold is <= SINR; 0 if none."""
    return bisect.bisect_right(CQI_THR[1:], sinr_db)


def select_mcs(cqi: int, offset_db: float = 0.0) -> int:
    """Highest MCS whose threshold is <= threshold(CQI) + offset; floor at MCS 0."""
    if cqi <= 0:
        raise ValueError("CQI 0 is not schedulable")
    target = CQI_THR[cqi] + offset_db
    best = 0
    for m in range(N_MCS):
        if MCS_THR[m] <= target:
            best = m
    return best


def bits_per_prb(mcs: int, layers: int = 1) -> float:
    qm, r = MCS_TABLE1[mcs]
    return N_RE_PER_PRB * qm * r / 1024.0 * layers


def tbs_bits(mcs: int, n_prb: int, layers: int = 1, qm_r: tuple[int, int] | None = None) -> int:
    qm, r = qm_r if qm_r is not None else MCS_TABLE1[mcs]
    n_info = N_RE_PER_PRB * n_prb * qm * r / 1024.0 * layers
    return max(24, 8 * int(n_info // 8))


def bler(sinr_db: float, mcs: int, retx_count: int = 0) -> float:
    """Logistic BLER; exactly 10% at thr10(mcs) for a first transmission."""
    thr_mid = MCS_THR[mcs] - SLOPE_DB * LN9
    sinr_eff = sinr_db + 10.0 * math.log10(1 + retx_count)   # chase-combining gain
    x = (sinr_eff - thr_mid) / SLOPE_DB
    x = max(-60.0, min(60.0, x))
    return 1.0 / (1.0 + math.exp(x))
