"""KBClient interface + implementations (design sec. 8: kb-rag access).

StubKB   - curated snippets (paraphrased from the user's knowledge base, with file + page). Offline/deterministic.
CachedKB - replays/records kb-rag answers in a JSONL file (a Claude session can fill it via the kb-rag MCP tool).
CommandKB- runs a user-provided command:  <cmd> "<query>" "<technology>"  -> prints JSON [{file,page,text}, ...]
"""
from __future__ import annotations
import json
import shlex
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Chunk:
    file: str
    page: str
    text: str

    def cite(self) -> str:
        return f"{self.file} p.{self.page}"


class KBClient:
    def search(self, query: str, technology: str | None = None, topic: str | None = None) -> list:
        raise NotImplementedError


_MAC = "5G-NR/5G_DL_UL_MAC_Scheduling_1769365262.pdf"
_PERF = "5gPHY/3GPP_5G_Scheduler_Performance_1721973681.pdf"
_SSB = "LTE/SSB_Beamwidth_Adjustments_for_3GPP_Release_19_1724132592.pdf"
_BLER = "5gPHY/BLER_in_LTE_1751420051.pdf"
_HARQ = "L2-Layer/Understanding_HARQ_1749572180.pdf"

_FAIR = Chunk(_MAC, "23", "If fairness is too low, cell-edge users starve; if it is too high, total cell throughput drops.")
STUB = {
    "STARVATION": [_FAIR, Chunk(_PERF, "1", "Transport block size grows with CQI, the number of allocated RBs and the MCS, so a UE with poor CQI gets small blocks for the same resources.")],
    "CQI_DROP": [Chunk(_SSB, "7", "The scheduler estimates channel quality from the reported CQI; a higher CQI allows a larger transport block, so a CQI drop shrinks it."),
                 Chunk(_SSB, "8-9", "CQI is derived at the UE from measured SINR; link adaptation maps it to an MCS, and the SINR behind it is what interference lowers.")],
    "HIGH_BLER": [Chunk(_MAC, "23", "BLER above about 20% overloads HARQ and throughput collapses; about 10% gives the best net throughput after HARQ combining."),
                  Chunk(_MAC, "26", "Aggressive scheduling pushes observed BLER far above the 10% target and loads HARQ heavily.")],
    "LOW_BLER": [Chunk(_MAC, "23", "BLER below about 5% means the scheduler is too conservative and wastes spectrum."),
                 Chunk(_BLER, "4", "High BLER triggers a lower MCS; low BLER allows a higher MCS to raise throughput (target around 10%).")],
    "HARQ_EXHAUSTED": [Chunk(_MAC, "23", "The scheduler must avoid exhausting HARQ processes within the HARQ round-trip window."),
                       Chunk(_HARQ, "4", "5G HARQ allows up to 16 processes with configurable feedback timing.")],
    "QUEUE_GROWING": [Chunk(_MAC, "2", "The MAC scheduler picks UEs every slot, allocates PRBs, selects MCS and enforces QoS such as delay budgets.")],
    "LOW_FAIRNESS": [_FAIR],
    "PRB_UNDERUSE": [Chunk(_SSB, "8-9", "The number of RBs allocated to a UE directly sets its transport block size; more RBs mean a larger block.")],
}


class StubKB(KBClient):
    def search(self, query, technology=None, topic=None):
        return list(STUB.get(topic, []))


class CachedKB(KBClient):
    def __init__(self, path, backend: KBClient | None = None):
        self.path = Path(path)
        self.backend = backend
        self.cache = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    self.cache[(d["query"], d.get("technology") or "")] = [Chunk(**c) for c in d["chunks"]]

    def search(self, query, technology=None, topic=None):
        key = (query, technology or "")
        if key in self.cache:
            return self.cache[key]
        if self.backend is None:
            return []
        res = self.backend.search(query, technology, topic)
        if not res:                      # backend down / no hits: don't cache the failure
            return res
        self.cache[key] = res
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"query": query, "technology": technology, "chunks": [asdict(c) for c in res]}) + "\n")
        return res


class CommandKB(KBClient):
    def __init__(self, command: str, timeout: float = 180.0):   # first call loads models (~20 s)
        self.command, self.timeout = command, timeout

    def search(self, query, technology=None, topic=None):
        cmd = shlex.split(self.command) + [query, technology or ""]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=True).stdout
            return [Chunk(str(d["file"]), str(d.get("page", "?")), str(d["text"])) for d in json.loads(out)]
        except Exception:
            return []     # kb-rag unavailable -> agent still works, without citations


def make_kb(cfg) -> KBClient:
    if cfg.kb == "stub":
        return StubKB()
    if cfg.kb == "cached":
        return CachedKB(cfg.kb_cache or "kb_cache.jsonl", StubKB())
    if cfg.kb == "command":
        return CachedKB(cfg.kb_cache, CommandKB(cfg.kb_command)) if cfg.kb_cache else CommandKB(cfg.kb_command)
    raise ValueError(f"unknown kb {cfg.kb!r}")


QUERIES = {
    "STARVATION": "proportional fair scheduler metric starvation low CQI",
    "CQI_DROP": "CQI drop causes interference SINR MCS selection",
    "HIGH_BLER": "high BLER HARQ overload link adaptation MCS",
    "LOW_BLER": "target BLER 10% link adaptation outer loop",
    "HARQ_EXHAUSTED": "HARQ processes round trip time stall",
    "QUEUE_GROWING": "scheduler QoS priority buffer status report",
    "LOW_FAIRNESS": "fairness index cell edge users scheduler",
    "PRB_UNDERUSE": "PRB allocation bits per PRB buffer status",
}
