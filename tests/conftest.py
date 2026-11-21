import copy
from pathlib import Path
import pytest
import yaml
from gnbsim import config as C

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def raw_l1():
    return yaml.safe_load((ROOT / "configs" / "mixed5_l1.yaml").read_text())


@pytest.fixture
def raw_l2():
    return yaml.safe_load((ROOT / "configs" / "mixed5_l2.yaml").read_text())


@pytest.fixture
def raw_agent():
    return yaml.safe_load((ROOT / "configs" / "mixed5_agent.yaml").read_text())


def make(raw, **over):
    d = copy.deepcopy(raw)
    for k, v in over.items():
        d[k] = v
    return C.from_dict(d)
