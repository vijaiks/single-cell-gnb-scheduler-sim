def scs_khz(mu: int) -> int:
    return 15 * (2 ** mu)

def slot_duration_s(mu: int) -> float:
    return 1e-3 / (2 ** mu)
