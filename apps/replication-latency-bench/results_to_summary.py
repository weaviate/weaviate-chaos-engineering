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

# Writes and reads are both single-object (one request each), so every request
# pays its own replica round-trip — the per-request cost the short-circuit removes.
PHASES = ("write", "read")
PHASE_LABEL = {"write": "write", "read": "read"}


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.3f}"


def _client_index(res: dict) -> dict:
    """(CL, phase) -> client_latency_ms (end-to-end, as the bench client sees it)."""
    out = {}
    for lvl in res.get("levels", []):
        for phase in PHASES:
            if lvl.get(phase):
                out[(lvl["consistency_level"], phase)] = lvl[phase]["client_latency_ms"]
    return out


def _server_index(res: dict) -> dict:
    """(CL, phase) -> the highest-count server-side request-duration row (the
    Weaviate-internal gRPC/HTTP request duration, above the replica fan-out).
    avg_ms is exact (_sum/_count); p99 is bucket-limited unless the image carries
    the finer RequestLatencyBuckets."""
    grpc = "grpc_server_request_duration_seconds"
    out = {}
    for lvl in res.get("levels", []):
        for phase in PHASES:
            p = lvl.get(phase)
            if not p:
                continue
            srv = p.get("server_request_latency", {})
            # The bench's ops are gRPC (BatchObjects / Search); prefer those rows so
            # unrelated HTTP noise (readiness, schema POSTs) can't win on count.
            pool = [(grpc, r) for r in srv.get(grpc, [])] or [
                (m, r) for m, rs in srv.items() for r in rs
            ]
            best = None
            for metric, r in pool:
                if best is None or r.get("count", 0) > best.get("count", 0):
                    best = {**r, "metric": metric}
            if best:
                out[(lvl["consistency_level"], phase)] = best
    return out


def _pct(cur: Optional[float], base: Optional[float]) -> str:
    if not cur or not base:
        return "-"
    return f"{(cur - base) / base * 100.0:+.0f}%"


def _avg_table(res, baseline, index_fn, heading, note):
    """Render one avg-latency table (baseline → candidate → change, or absolute)."""
    lines = [heading, "", f"_{note}_", ""]
    cur = index_fn(res)
    if baseline:
        old = index_fn(baseline)
        lines.append("| CL | op | baseline | candidate | change |")
        lines.append("|----|----|----------|-----------|--------|")
        for lvl in res.get("levels", []):
            name = lvl["consistency_level"]
            for phase in PHASES:
                c, b = cur.get((name, phase)), old.get((name, phase))
                if not c or not b:
                    continue
                lines.append(
                    f"| {name} | {PHASE_LABEL.get(phase, phase)} | "
                    f"{_fmt(b.get('avg_ms'))} | {_fmt(c.get('avg_ms'))} | "
                    f"{_pct(c.get('avg_ms'), b.get('avg_ms'))} |"
                )
    else:
        lines.append("| CL | op | avg |")
        lines.append("|----|----|-----|")
        for lvl in res.get("levels", []):
            name = lvl["consistency_level"]
            for phase in PHASES:
                c = cur.get((name, phase))
                if c:
                    lines.append(
                        f"| {name} | {PHASE_LABEL.get(phase, phase)} | {_fmt(c.get('avg_ms'))} |"
                    )
    lines.append("")
    return lines


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
        f"{res.get('nodes', '?')} nodes · rf={res.get('replication_factor', '?')} · "
        f"single-object ops · median of {iters} runs. Lower = faster."
    )
    lines.append("")

    # server-side table (the headline / attributable metric)
    lines.extend(
        _avg_table(
            res,
            baseline,
            _server_index,
            "### Server-side avg latency (ms)",
            "inside Weaviate: request duration above the replica fan-out (avg is exact)",
        )
    )
    # client-side table (context: does the saving reach the caller?)
    lines.extend(
        _avg_table(
            res,
            baseline,
            _client_index,
            "### Client-side avg latency (ms)",
            "end-to-end as the benchmark client sees it (incl. client + transport)",
        )
    )
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
