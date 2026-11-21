#!/usr/bin/env python3
"""CLI wrapper around the kb-rag index (same retrieval as the kb-rag MCP `search_kb` tool).

    kb_rag_search.py "<query>" ["<technology>"]   ->  stdout: JSON [{"file","page","text"}, ...]
    kb_rag_search.py --prefetch kb_cache.jsonl     ->  runs every agent query once (models load once, ~1 min total)
                                                       and writes the JSONL cache that `kb: cached` / `kb_cache:` reads

Plug into gnbsim with:  agent: {kb: command, kb_command: "python tools/kb_rag_search.py"}
It re-launches itself with kb-rag's own virtualenv (torch, lancedb, ...), so gnbsim needs none of those.
Env: KB_RAG_HOME (default ~/projects/kb-rag-starter). Diagnostics go to stderr; stdout is JSON only.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("KB_RAG_HOME", "~/projects/kb-rag-starter")).expanduser()
MAX_CHARS = 700


def _search(query: str, tech: str) -> list:
    sys.path.insert(0, str(HOME))
    os.chdir(HOME)                                   # kb-rag resolves config.yaml relative to its root
    real_stdout, sys.stdout = sys.stdout, sys.stderr  # libraries print progress; keep stdout clean
    try:
        from common import chunks_table, load_config, tech_filter
        from query.ask import retrieve
        cfg = load_config()
        rows = retrieve(cfg, chunks_table(cfg), query, tech_filter(tech))
    finally:
        sys.stdout = real_stdout
    out = []
    for c in rows:
        a, b = c["page_start"], c["page_end"]
        out.append({"file": c["file_path"], "page": str(a) if a == b else f"{a}-{b}",
                    "text": " ".join(c["text"].split())[:MAX_CHARS]})
    return out


def _prefetch(path: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from gnbsim.agent.kb import QUERIES
    path = os.path.abspath(path)                     # _search() chdirs into kb-rag
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["query"] for l in open(path, encoding="utf-8") if l.strip()}
    with open(path, "a", encoding="utf-8") as fh:
        for kind, q in QUERIES.items():
            if q in done:
                continue
            chunks = _search(q, "NR")
            if chunks:
                fh.write(json.dumps({"query": q, "technology": "NR", "chunks": chunks}) + "\n")
                fh.flush()
            print(f"prefetch {kind}: {len(chunks)} chunks", file=sys.stderr)
    return 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "--prefetch":
        query, tech = argv[2], ""
    elif len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2
    else:
        query, tech = argv[1], (argv[2] if len(argv) > 2 else "")
    py = HOME / ".venv" / "bin" / "python"
    if os.environ.get("_KB_INNER") != "1" and py.exists() and Path(sys.executable) != py:
        env = dict(os.environ, _KB_INNER="1")
        return subprocess.call([str(py), os.path.abspath(__file__)] + argv[1:], env=env)
    try:
        if argv[1] == "--prefetch":
            return _prefetch(argv[2])
        res = _search(query, tech)
    except Exception as e:                           # non-zero exit -> gnbsim falls back, nothing cached
        print(f"kb_rag_search: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    json.dump(res, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
