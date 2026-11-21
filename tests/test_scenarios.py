import pytest
from gnbsim.engine import SimEngine
from conftest import make


@pytest.fixture(scope="module")
def results():
    import yaml
    from pathlib import Path
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "mixed5_l1.yaml").read_text())
    out = {}
    for a in ("rr", "maxcqi", "pf"):
        raw2 = dict(raw); raw2["algo"] = {"name": a, "params": {"tc": 100} if a == "pf" else {}}
        out[a] = SimEngine(make(raw2)).run()
    return out


def test_cell_throughput_order(results):
    assert results["maxcqi"]["cell_throughput_mbps"] > results["pf"]["cell_throughput_mbps"] > results["rr"]["cell_throughput_mbps"]


def test_weak_ue_share_by_algorithm(results):
    ue4 = {a: r["ues"][4]["prb_share"] for a, r in results.items()}
    assert ue4["maxcqi"] < 0.01
    assert abs(ue4["rr"] - 0.2) < 0.01
    assert 0.1 < ue4["pf"] < 0.3


def test_jain_resource_share_order(results):
    j = {a: r["jain_resource_share"] for a, r in results.items()}
    assert j["rr"] >= j["pf"] > j["maxcqi"] and j["rr"] > 0.99 and j["maxcqi"] < 0.3


def test_starvation_flags(results):
    assert not any(u["starved"] for u in results["rr"]["ues"])
    assert not any(u["starved"] for u in results["pf"]["ues"])
    assert sum(u["starved"] for u in results["maxcqi"]["ues"]) >= 3


def test_layer2_config_runs_and_reports_harq(raw_l2):
    s = SimEngine(make(raw_l2, slots=1500)).run()
    assert s["prb_utilization"] > 0.9
    bl = [u["first_tx_bler"] for u in s["ues"]]
    assert 0.02 < sum(bl) / len(bl) < 0.30            # around the 10% target, not 0 and not collapsing
