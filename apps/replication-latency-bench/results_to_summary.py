"""
results_to_summary.py
======================

Render the results.json produced by bench.py into a GitHub-flavoured Markdown
summary, so the CI job can surface the replication latency numbers in the
workflow run's Step Summary (and, optionally, a delta against a baseline).

Usage:
    python3 results_to_summary.py results.json [baseline.json] > summary.md

Kept dependency-free (stdlib only) so the CI step needs no pip install.
"""

import json
import math
import sys
from typing import Optional

# Writes and reads are both single-object (one request each), so every request
# pays its own replica round-trip — the per-request cost the short-circuit removes.
PHASES = ("write", "read")
PHASE_LABEL = {"write": "write", "read": "read"}
PHASE_SHORT = {"write": "write", "read": "read"}


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.3f}"


def _client_index(res: dict) -> dict:
    """(consistency_level, phase) -> client_latency_ms dict."""
    out = {}
    for lvl in res.get("levels", []):
        for phase in PHASES:
            if lvl.get(phase):
                out[(lvl["consistency_level"], phase)] = lvl[phase]["client_latency_ms"]
    return out


def _mermaid_graph(res: dict, baseline: Optional[dict]) -> list:
    """A Mermaid xychart bar graph (renders inline in the GitHub step summary).
    With a baseline: p50 delta % per CL/phase (negative = candidate faster).
    Without: absolute p50 ms per CL/phase."""
    cur = _client_index(res)
    cats, vals = [], []
    base = _client_index(baseline) if baseline else {}
    for lvl in res.get("levels", []):
        nm = lvl["consistency_level"]
        for ph in PHASES:
            c = cur.get((nm, ph))
            if not c or c.get("p50_ms") is None:
                continue
            if baseline:
                b = base.get((nm, ph))
                bp = b.get("p50_ms") if b else None
                if not bp:
                    continue
                vals.append(round((c["p50_ms"] - bp) / bp * 100.0, 1))
            else:
                vals.append(round(c["p50_ms"], 3))
            cats.append(f"{nm}/{PHASE_SHORT.get(ph, ph)}")
    if not vals:
        return []
    if baseline:
        title = "p50 latency delta % by CL/phase (negative = candidate faster)"
        yaxis = "delta %"
        lo, hi = math.floor(min(vals + [0]) - 5), math.ceil(max(vals + [0]) + 5)
        yrange = f" {lo} --> {hi}"
    else:
        title = "p50 latency by CL/phase (ms)"
        yaxis = "ms"
        yrange = ""
    xs = ", ".join(f'"{c}"' for c in cats)
    ys = ", ".join(str(v) for v in vals)
    return [
        "### Latency impact graph",
        "",
        "```mermaid",
        "xychart-beta",
        f'    title "{title}"',
        f"    x-axis [{xs}]",
        f'    y-axis "{yaxis}"{yrange}',
        f"    bar [{ys}]",
        "```",
        "",
    ]


def _pct(cur: Optional[float], base: Optional[float]) -> str:
    if not cur or not base:
        return "-"
    return f"{(cur - base) / base * 100.0:+.0f}%"


def render(res: dict, baseline: Optional[dict]) -> str:
    iters = (res.get("levels") or [{}])[0].get("iterations", "?")
    lines = ["## Replication latency benchmark", ""]
    if baseline:
        lines.append(
            f"`{baseline.get('weaviate_version', 'baseline')}` (baseline) "
            f"→ `{res.get('weaviate_version', 'candidate')}` (candidate)"
        )
    else:
        lines.append(f"`{res.get('weaviate_version', 'unknown')}`")
    lines.append(
        f"{res.get('nodes', '?')} nodes, rf={res.get('replication_factor', '?')}, "
        f"single-object ops, median of {iters} timed runs/level. "
        "Full numbers in the `results.json` artifact."
    )
    lines.append("")

    # graph first, then one compact table
    lines.extend(_mermaid_graph(res, baseline))

    if baseline:
        cur, old = _client_index(res), _client_index(baseline)
        lines.append("### Latency delta (negative = candidate faster)")
        lines.append("")
        lines.append("| CL | op | Δp50 | Δp99 |")
        lines.append("|----|----|------|------|")
        for lvl in res.get("levels", []):
            name = lvl["consistency_level"]
            for phase in PHASES:
                c, b = cur.get((name, phase)), old.get((name, phase))
                if not c or not b:
                    continue
                lines.append(
                    f"| {name} | {PHASE_LABEL.get(phase, phase)} | "
                    f"{_pct(c.get('p50_ms'), b.get('p50_ms'))} | "
                    f"{_pct(c.get('p99_ms'), b.get('p99_ms'))} |"
                )
    else:
        lines.append("### Latency (ms, median of timed runs)")
        lines.append("")
        lines.append("| CL | op | p50 | p99 |")
        lines.append("|----|----|-----|-----|")
        for lvl in res.get("levels", []):
            name = lvl["consistency_level"]
            for phase in PHASES:
                p = lvl.get(phase)
                if not p:
                    continue
                c = p["client_latency_ms"]
                lines.append(
                    f"| {name} | {PHASE_LABEL.get(phase, phase)} | "
                    f"{_fmt(c.get('p50_ms'))} | {_fmt(c.get('p99_ms'))} |"
                )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: results_to_summary.py results.json [baseline.json]", file=sys.stderr)
        return 2
    with open(sys.argv[1]) as f:
        res = json.load(f)
    baseline = None
    if len(sys.argv) > 2 and sys.argv[2]:
        try:
            with open(sys.argv[2]) as f:
                baseline = json.load(f)
        except OSError as e:
            print(f"baseline {sys.argv[2]!r} not usable: {e}", file=sys.stderr)
    sys.stdout.write(render(res, baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
