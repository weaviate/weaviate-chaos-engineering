"""
plot_results.py
===============

Render the client-side latency distribution captured by bench.py
(client_latency_samples_ms) into a histogram PNG for the CI artifacts:

    hist.png  — overlaid latency histograms (baseline vs candidate), per CL/phase

Usage:
    python3 plot_results.py results.json [baseline.json] --outdir charts/

Reads and writes are plotted on separate axes (very different scales). When no
baseline is given, only the candidate is drawn.
"""

import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

CAND_COLOR = "#1f77b4"
BASE_COLOR = "#999999"
PHASES = ("write", "read")


def samples_index(res: Optional[dict]) -> Dict[Tuple[str, str], List[float]]:
    out: Dict[Tuple[str, str], List[float]] = {}
    if not res:
        return out
    for lvl in res.get("levels", []):
        for ph in PHASES:
            s = lvl[ph].get("client_latency_samples_ms")
            if s:
                out[(lvl["consistency_level"], ph)] = s
    return out


def levels_of(res: dict) -> List[str]:
    return [lvl["consistency_level"] for lvl in res.get("levels", [])]


def plot_hist(cur, base, levels, label_cur, label_base, outdir):
    rows = [lv for lv in levels if any((lv, ph) in cur for ph in PHASES)]
    if not rows:
        return None
    fig, axes = plt.subplots(len(rows), 2, figsize=(11, 3.1 * len(rows)), squeeze=False)
    for r, lv in enumerate(rows):
        for c, ph in enumerate(PHASES):
            ax = axes[r][c]
            series = [s for s in (cur.get((lv, ph)), base.get((lv, ph))) if s]
            if not series:
                ax.set_visible(False)
                continue
            lo = max(1e-3, min(min(s) for s in series))
            hi = max(max(s) for s in series)
            # 30 log-spaced bins so the long write tail and sub-ms reads both resolve
            bins = [lo * (hi / lo) ** (k / 30) for k in range(31)] if hi > lo else 30
            if cur.get((lv, ph)):
                ax.hist(
                    cur[(lv, ph)],
                    bins=bins,
                    color=CAND_COLOR,
                    alpha=0.55,
                    label=label_cur,
                    density=True,
                )
            if base.get((lv, ph)):
                ax.hist(
                    base[(lv, ph)],
                    bins=bins,
                    color=BASE_COLOR,
                    alpha=0.6,
                    label=label_base,
                    density=True,
                    histtype="step",
                    lw=1.6,
                )
            ax.set_title(f"CL={lv}  {ph}", fontsize=10)
            ax.set_xlabel("latency (ms)")
            ax.set_ylabel("density")
            ax.set_xscale("log")
            ax.grid(True, which="both", alpha=0.25)
            if r == 0 and c == 0:
                ax.legend(fontsize=8)
    fig.suptitle("Client latency histogram (log-x, density)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p = os.path.join(outdir, "hist.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("baseline", nargs="?", default="")
    ap.add_argument("--outdir", default="charts")
    args = ap.parse_args()

    with open(args.results) as f:
        res = json.load(f)
    base = None
    if args.baseline:
        try:
            with open(args.baseline) as f:
                base = json.load(f)
        except OSError:
            base = None

    cur = samples_index(res)
    if not cur:
        print("no client_latency_samples_ms in results — nothing to plot")
        return 0
    os.makedirs(args.outdir, exist_ok=True)
    label_cur = res.get("weaviate_version", "candidate")
    label_base = base.get("weaviate_version", "baseline") if base else "baseline"

    p = plot_hist(cur, samples_index(base), levels_of(res), label_cur, label_base, args.outdir)
    print(f"wrote: {p}" if p else "no chart produced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
