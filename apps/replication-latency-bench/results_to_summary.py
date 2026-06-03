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
    lines.append("### Client-side latency (ms)")
    lines.append("")
    lines.append("| CL | phase | reqs | avg | p50 | p95 | p99 | max | throughput |")
    lines.append("|----|-------|------|-----|-----|-----|-----|-----|------------|")
    for lvl in res.get("levels", []):
        name = lvl["consistency_level"]
        for phase in ("write", "read"):
            p = lvl[phase]
            c = p["client_latency_ms"]
            if phase == "write":
                tput = p.get("objects_per_second")
                tput_s = f"{tput} obj/s" if tput is not None else "-"
            else:
                tput = p.get("reads_per_second")
                tput_s = f"{tput} reads/s" if tput is not None else "-"
            lines.append(
                f"| {name} | {phase} | {c.get('count', 0)} | "
                f"{_fmt(c.get('avg_ms'))} | {_fmt(c.get('p50_ms'))} | "
                f"{_fmt(c.get('p95_ms'))} | {_fmt(c.get('p99_ms'))} | "
                f"{_fmt(c.get('max_ms'))} | {tput_s} |"
            )
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
            "— client-side p99"
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
