"""Scenario configuration: dataclasses, YAML loading, validation."""
from __future__ import annotations
import copy
from dataclasses import dataclass, field, fields, is_dataclass, asdict
from pathlib import Path
import yaml


@dataclass
class ChannelSpec:
    mean_sinr_db: float = 15.0
    ramp_to_db: float | None = None
    noise_std_db: float = 1.0
    shadow_std_db: float = 0.0
    shadow_corr: float = 0.98
    events: list = field(default_factory=list)   # [{slot, delta_db, duration?}]


@dataclass
class TrafficSpec:
    type: str = "full_buffer"          # full_buffer | poisson | periodic
    rate_pps: float = 0.0
    pkt_bytes: int = 1500
    every_n_slots: int = 0
    bytes: int = 0


@dataclass
class UEConfig:
    id: int = 0
    priority: float = 1.0
    channel: ChannelSpec = field(default_factory=ChannelSpec)
    traffic: TrafficSpec = field(default_factory=TrafficSpec)


@dataclass
class PhyConfig:
    harq_enabled: bool = True
    k1: int = 4
    n_harq: int = 16
    max_retx: int = 3
    bler_model: str = "logistic"       # logistic | none
    olla_enabled: bool = False
    olla_step_db: float = 0.05
    olla_target_bler: float = 0.1
    layers: int = 1


@dataclass
class ReportsConfig:
    p_cqi: int = 5
    d_cqi: int = 2
    p_bsr: int = 10
    bsr_granularity_bits: int = 800


@dataclass
class MetricsConfig:
    warmup_slots: int = 200
    starve_run_slots: int = 100


@dataclass
class AlgoConfig:
    name: str = "pf"
    params: dict = field(default_factory=dict)


@dataclass
class AgentConfig:
    enabled: bool = False
    interval: int = 10
    analysis_window: int = 100
    kb: str = "stub"                   # stub | cached | command
    kb_cache: str | None = None
    kb_command: str | None = None
    rules: dict = field(default_factory=dict)


@dataclass
class SimConfig:
    slots: int = 2000
    seed: int = 1
    mu: int = 0
    n_prb: int = 50
    max_ues_per_slot: int = 1
    prb_policy: str = "split"
    algo: AlgoConfig = field(default_factory=AlgoConfig)
    phy: PhyConfig = field(default_factory=PhyConfig)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    ues: list = field(default_factory=list)


def _build(cls, data):
    """Recursively build dataclass `cls` from dict, rejecting unknown keys."""
    if data is None:
        data = {}
    names = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(names)
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown key(s) {sorted(unknown)}")
    kwargs = {}
    for k, v in data.items():
        t = {"ChannelSpec": ChannelSpec, "TrafficSpec": TrafficSpec, "AlgoConfig": AlgoConfig,
             "PhyConfig": PhyConfig, "ReportsConfig": ReportsConfig, "MetricsConfig": MetricsConfig,
             "AgentConfig": AgentConfig}.get(str(names[k].type).replace(" | None", ""))
        kwargs[k] = _build(t, v) if t is not None else v
    obj = cls(**kwargs)
    return obj


def validate(cfg: SimConfig) -> SimConfig:
    from .schedulers import REGISTRY
    if cfg.mu not in (0, 1):
        raise ValueError("mu must be 0 or 1")
    if cfg.slots < 1 or cfg.n_prb < 1:
        raise ValueError("slots and n_prb must be >= 1")
    if not 1 <= cfg.max_ues_per_slot:
        raise ValueError("max_ues_per_slot must be >= 1")
    if cfg.algo.name not in REGISTRY:
        raise ValueError(f"unknown algo {cfg.algo.name!r}; choose from {sorted(REGISTRY)}")
    if not cfg.ues:
        raise ValueError("at least one UE is required")
    if [u.id for u in cfg.ues] != list(range(len(cfg.ues))):
        raise ValueError("UE ids must be 0..N-1 in order")
    for u in cfg.ues:
        if u.traffic.type not in ("full_buffer", "poisson", "periodic"):
            raise ValueError(f"UE {u.id}: unknown traffic type {u.traffic.type!r}")
        if u.priority <= 0:
            raise ValueError(f"UE {u.id}: priority must be > 0")
    r = cfg.reports
    if min(r.p_cqi, r.p_bsr) < 1 or r.d_cqi < 0:
        raise ValueError("report periods must be >= 1 and delay >= 0")
    if cfg.phy.bler_model not in ("logistic", "none"):
        raise ValueError("bler_model must be 'logistic' or 'none'")
    if cfg.phy.n_harq < 1 or cfg.phy.k1 < 1:
        raise ValueError("n_harq and k1 must be >= 1")
    if cfg.agent.analysis_window < cfg.agent.interval:
        raise ValueError("agent.analysis_window must be >= agent.interval")
    return cfg


def from_dict(d: dict) -> SimConfig:
    d = copy.deepcopy(d)
    ues = d.pop("ues", [])
    cfg = _build(SimConfig, d)
    cfg.ues = []
    for i, u in enumerate(ues):
        u = dict(u)
        u.setdefault("id", i)
        cfg.ues.append(_build(UEConfig, u))
    return validate(cfg)


def load(path) -> SimConfig:
    with open(path, "r", encoding="utf-8") as fh:
        return from_dict(yaml.safe_load(fh) or {})


def to_dict(cfg: SimConfig) -> dict:
    return asdict(cfg)
