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

# Phases in display order; labels shown in tables. single_write = one PutObject
# per request (no batching), which exposes the per-request short-circuit saving.
PHASES = ("write", "single_write", "read")
PHASE_LABEL = {"write": "write(batch)", "single_write": "write(1-obj)", "read": "read"}
PHASE_SHORT = {"write": "wr-batch", "single_write": "wr-1obj", "read": "read"}


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


def render(res: dict, baseline: Optional[dict]) -> str:
    lines = []
    lines.append("## Replication latency benchmark")
    lines.append("")
    lines.append(f"- **weaviate version:** `{res.get('weaviate_version', 'unknown')}`")
    lines.append(
        f"- **cluster:** {res.get('nodes', '?')} nodes, "
        f"rf={res.get('replication_factor', '?')}, dim={res.get('dim', '?')}, "
        f"coordinator=node-1 (always a local replica)"
    )
    lines.append("")

    # ── client-side latency (the headline numbers) ──
    # Values are the median across timed iterations; p99 range shows the
    # per-run spread so reviewers can see how noisy the measurement is.
    lines.append("### Client-side latency (ms, median of timed runs)")
    lines.append("")
    lines.append(
        "| CL | phase | iters | reqs/run | avg | p50 | p95 | p99 | p99 range | throughput |"
    )
    lines.append(
        "|----|-------|-------|----------|-----|-----|-----|-----|-----------|------------|"
    )
    for lvl in res.get("levels", []):
        name = lvl["consistency_level"]
        for phase in PHASES:
            p = lvl.get(phase)
            if not p:
                continue
            c = p["client_latency_ms"]
            iters = c.get("iterations", lvl.get("iterations", 1))
            if phase == "read":
                tput = p.get("reads_per_second")
                tput_s = f"{tput} reads/s" if tput is not None else "-"
            else:
                tput = p.get("objects_per_second")
                tput_s = f"{tput} obj/s" if tput is not None else "-"
            lo, hi = c.get("p99_ms_min"), c.get("p99_ms_max")
            p99_range = f"{_fmt(lo)}–{_fmt(hi)}" if lo is not None and hi is not None else "-"
            lines.append(
                f"| {name} | {PHASE_LABEL.get(phase, phase)} | {iters} | {c.get('count', 0)} | "
                f"{_fmt(c.get('avg_ms'))} | {_fmt(c.get('p50_ms'))} | "
                f"{_fmt(c.get('p95_ms'))} | {_fmt(c.get('p99_ms'))} | "
                f"{p99_range} | {tput_s} |"
            )
    lines.append("")

    # ── latency impact graph (Mermaid, renders inline in the step summary) ──
    lines.extend(_mermaid_graph(res, baseline))

    # ── server-side request latency (above the replica fan-out) ──
    lines.append("### Server-side request latency (ms, above the replica fan-out)")
    lines.append("")
    lines.append("| CL | phase | metric | method/route | n | p50 | p95 | p99 |")
    lines.append("|----|-------|--------|--------------|---|-----|-----|-----|")
    any_server = False
    for lvl in res.get("levels", []):
        name = lvl["consistency_level"]
        for phase in PHASES:
            p = lvl.get(phase)
            if not p:
                continue
            srv = p.get("server_request_latency", {})
            for metric, rows in srv.items():
                for r in rows:
                    any_server = True
                    labels = r.get("labels", {})
                    mr = labels.get("method") or labels.get("route") or "?"
                    lines.append(
                        f"| {name} | {PHASE_LABEL.get(phase, phase)} | {metric} | {mr} | "
                        f"{r.get('count', 0)} | {_fmt(r.get('p50_ms'))} | "
                        f"{_fmt(r.get('p95_ms'))} | {_fmt(r.get('p99_ms'))} |"
                    )
    if not any_server:
        lines.append("| - | - | _no server-side series scraped_ | - | - | - | - | - |")
    lines.append("")

    # ── optional delta vs a baseline run ──
    if baseline is not None:
        cur, old = _client_index(res), _client_index(baseline)
        lines.append(
            f"### Delta vs baseline (`{baseline.get('weaviate_version', 'unknown')}`) "
            "— client-side median p99"
        )
        lines.append("")
        lines.append("negative = faster on this build")
        lines.append("")
        lines.append("| CL | phase | baseline p99 | current p99 | delta | delta % |")
        lines.append("|----|-------|--------------|-------------|-------|---------|")
        for key in sorted(cur):
            c, b = cur[key], old.get(key, {})
            cp, bp = c.get("p99_ms"), b.get("p99_ms")
            if cp is None or bp is None:
                continue
            d = cp - bp
            pct = (d / bp * 100.0) if bp else 0.0
            lines.append(
                f"| {key[0]} | {key[1]} | {_fmt(bp)} | {_fmt(cp)} | " f"{d:+.3f} | {pct:+.1f}% |"
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
