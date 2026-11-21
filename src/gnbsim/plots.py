from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def run_plot(engine, path):
    m, n = engine.metrics, engine.n
    t = np.arange(engine.t)
    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    s = engine.summary()
    ax[0, 0].bar(range(n), [u["throughput_mbps"] for u in s["ues"]])
    ax[0, 0].set(title=f"Throughput per UE (Mbps) - {engine.algo_name}", xlabel="UE")
    for u in range(n):
        ax[0, 1].plot(t, np.cumsum(m.delivered[:engine.t, u]) / 8e6, label=f"UE{u}")
        ax[1, 0].plot(t, m.cqi[:engine.t, u], lw=0.6, label=f"UE{u}")
    ax[0, 1].set(title="Cumulative delivered (MB)", xlabel="slot"); ax[0, 1].legend(fontsize=7)
    ax[1, 0].set(title="Reported CQI", xlabel="slot")
    ax[1, 1].bar(range(n), [u["prb_share"] for u in s["ues"]], color="tab:orange")
    ax[1, 1].set(title="PRB share", xlabel="UE", ylim=(0, 1))
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def compare_plot(results: dict, path):
    algos = list(results)
    n = len(results[algos[0]]["ues"])
    w = 0.8 / len(algos)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    for i, a in enumerate(algos):
        ax[0].bar(np.arange(n) + i * w, [u["throughput_mbps"] for u in results[a]["ues"]], w, label=a)
        ax[1].bar(np.arange(n) + i * w, [u["prb_share"] for u in results[a]["ues"]], w, label=a)
    ax[0].set(title="Throughput per UE (Mbps)", xlabel="UE"); ax[0].legend()
    ax[1].set(title="PRB share per UE", xlabel="UE", ylim=(0, 1))
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
