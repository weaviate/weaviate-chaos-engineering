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
import sys
from typing import Optional


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.3f}"


def _client_index(res: dict) -> dict:
    """(consistency_level, phase) -> client_latency_ms dict."""
    out = {}
    for lvl in res.get("levels", []):
        for phase in ("write", "read"):
            out[(lvl["consistency_level"], phase)] = lvl[phase]["client_latency_ms"]
    return out


def _hist_index(res: dict) -> dict:
    """(consistency_level, phase) -> client_latency_hist dict (or {} if absent)."""
    out = {}
    for lvl in res.get("levels", []):
        for phase in ("write", "read"):
            h = lvl[phase].get("client_latency_hist")
            if h and h.get("counts"):
                out[(lvl["consistency_level"], phase)] = h
    return out


def _bucket_labels(edges: list) -> list:
    """Human labels for len(edges)+1 buckets: '<e0', 'e0-e1', ..., '>=e[-1]'."""

    def f(x):
        return f"{x:g}"

    labels = [f"<{f(edges[0])}"]
    for i in range(1, len(edges)):
        labels.append(f"{f(edges[i-1])}-{f(edges[i])}")
    labels.append(f">={f(edges[-1])}")
    return labels


def _bar(frac: float, width: int = 22) -> str:
    """Unicode block bar for a fraction in [0,1] with 1/8-block resolution."""
    if frac <= 0:
        return ""
    eighths = int(round(frac * width * 8))
    full, rem = divmod(eighths, 8)
    return "█" * full + ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")[rem]


def _ascii_hist(edges: list, cur: list, base: Optional[list]) -> list:
    """Side-by-side baseline/candidate distribution as a fenced ASCII block."""
    labels = _bucket_labels(edges)
    ct = sum(cur) or 1
    bt = sum(base) if base else 0
    bt = bt or 1
    # scale bars to the largest fraction present so the tallest bucket fills width
    fracs = [c / ct for c in cur] + ([b / bt for b in base] if base else [])
    mx = max(fracs) if fracs else 1.0
    mx = mx or 1.0
    out = ["```"]
    if base:
        out.append(f"{'bucket(ms)':>11}  {'baseline':<30}{'candidate':<30}")
        for i, lab in enumerate(labels):
            bf, cf = (base[i] / bt), (cur[i] / ct)
            out.append(
                f"{lab:>11}  {_bar(bf/mx):<22}{bf*100:4.1f}%   {_bar(cf/mx):<22}{cf*100:4.1f}%"
            )
    else:
        out.append(f"{'bucket(ms)':>11}  candidate")
        for i, lab in enumerate(labels):
            cf = cur[i] / ct
            out.append(f"{lab:>11}  {_bar(cf/mx):<22}{cf*100:4.1f}%")
    out.append("```")
    return out


def _mermaid_hist(title: str, edges: list, counts: list) -> list:
    """Collapsible Mermaid xychart bar chart (renders on GitHub web only)."""
    labels = _bucket_labels(edges)
    total = sum(counts) or 1
    pcts = [round(c / total * 100, 1) for c in counts]
    xs = ", ".join(f'"{lab}"' for lab in labels)
    ys = ", ".join(str(p) for p in pcts)
    return [
        "<details><summary>chart (GitHub web)</summary>",
        "",
        "```mermaid",
        "xychart-beta",
        f'    title "{title}"',
        f"    x-axis [{xs}]",
        '    y-axis "% of requests"',
        f"    bar [{ys}]",
        "```",
        "",
        "</details>",
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
        for phase in ("write", "read"):
            p = lvl[phase]
            c = p["client_latency_ms"]
            iters = c.get("iterations", lvl.get("iterations", 1))
            if phase == "write":
                tput = p.get("objects_per_second")
                tput_s = f"{tput} obj/s" if tput is not None else "-"
            else:
                tput = p.get("reads_per_second")
                tput_s = f"{tput} reads/s" if tput is not None else "-"
            lo, hi = c.get("p99_ms_min"), c.get("p99_ms_max")
            p99_range = f"{_fmt(lo)}–{_fmt(hi)}" if lo is not None and hi is not None else "-"
            lines.append(
                f"| {name} | {phase} | {iters} | {c.get('count', 0)} | "
                f"{_fmt(c.get('avg_ms'))} | {_fmt(c.get('p50_ms'))} | "
                f"{_fmt(c.get('p95_ms'))} | {_fmt(c.get('p99_ms'))} | "
                f"{p99_range} | {tput_s} |"
            )
    lines.append("")

    # ── client-side latency distribution (histogram) ──
    cur_h = _hist_index(res)
    base_h = _hist_index(baseline) if baseline else {}
    if cur_h:
        lines.append("### Client-side latency distribution")
        lines.append("")
        lines.append(
            "Pooled over all timed iterations; the shape exposes tails the percentiles hide."
        )
        lines.append("")
        for lvl in res.get("levels", []):
            name = lvl["consistency_level"]
            for phase in ("write", "read"):
                h = cur_h.get((name, phase))
                if not h:
                    continue
                edges, counts = h["edges_ms"], h["counts"]
                bh = base_h.get((name, phase))
                base_counts = bh["counts"] if bh and bh.get("edges_ms") == edges else None
                lines.append(
                    f"**CL={name} {phase}**" + ("  (baseline → candidate)" if base_counts else "")
                )
                lines.extend(_ascii_hist(edges, counts, base_counts))
                lines.extend(_mermaid_hist(f"CL={name} {phase} latency (candidate)", edges, counts))
                lines.append("")

    # ── server-side request latency (above the replica fan-out) ──
    lines.append("### Server-side request latency (ms, above the replica fan-out)")
    lines.append("")
    lines.append("| CL | phase | metric | method/route | n | p50 | p95 | p99 |")
    lines.append("|----|-------|--------|--------------|---|-----|-----|-----|")
    any_server = False
    for lvl in res.get("levels", []):
        name = lvl["consistency_level"]
        for phase in ("write", "read"):
            srv = lvl[phase].get("server_request_latency", {})
            for metric, rows in srv.items():
                for r in rows:
                    any_server = True
                    labels = r.get("labels", {})
                    mr = labels.get("method") or labels.get("route") or "?"
                    lines.append(
                        f"| {name} | {phase} | {metric} | {mr} | "
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
