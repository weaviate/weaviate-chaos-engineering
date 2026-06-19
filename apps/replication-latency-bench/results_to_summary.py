"""
results_to_summary.py
=====================

Render results.json (and an optional baseline.json) from bench.py into a
GitHub-flavoured Markdown step summary: a replication-performance A/B between two
Weaviate images, to catch regressions or prove improvements.

For each consistency level and op it compares two metrics, baseline -> candidate:
  - replication latency (ms, lower is better) — server-side coordinator duration
  - throughput (ops/sec, higher is better)

Both are timings, so each per-(CL, op) change is gated by a two-sided
Mann-Whitney U test over the interleaved rounds: only changes that are
statistically distinguishable from round-to-round jitter are shown as an
improvement/regression; anything within noise is omitted.

Usage:
    python3 results_to_summary.py results.json [baseline.json] > summary.md

Stdlib only (no pip install in CI).
"""

import json
import math
import statistics
import sys
from typing import List, Optional

PHASES = ("write", "read")
ALPHA = 0.05  # significance threshold for the verdict gate


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _band(series: List[float]) -> str:
    if not series:
        return "-"
    return f"{_median(series):.2f} [{min(series):.2f}-{max(series):.2f}]"


def _pct(cur: Optional[float], base: Optional[float]) -> str:
    if cur is None or not base:  # allow cur == 0 (e.g. self-calls → 0 = -100%)
        return "-"
    return f"{(cur - base) / base * 100.0:+.0f}%"


def mann_whitney_p(a: List[float], b: List[float]) -> Optional[float]:
    """Two-sided Mann-Whitney U p-value via the normal approximation with tie and
    continuity correction. Dependency-free (math.erf); None if either sample is
    empty. Appropriate for the small, non-normal per-run samples here."""
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return None
    tagged = sorted([(v, 0) for v in a] + [(v, 1) for v in b], key=lambda t: t[0])
    ranks = [0.0] * len(tagged)
    i = 0
    while i < len(tagged):
        j = i
        while j + 1 < len(tagged) and tagged[j + 1][0] == tagged[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based average rank over the tie group
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    r1 = sum(rk for rk, (_, g) in zip(ranks, tagged) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = min(u1, n1 * n2 - u1)
    mu = n1 * n2 / 2.0
    n = n1 + n2
    counts: dict = {}
    for v, _ in tagged:
        counts[v] = counts.get(v, 0) + 1
    ties = sum(t**3 - t for t in counts.values())
    var = n1 * n2 / 12.0 * ((n + 1) - ties / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return 1.0
    z = (u - mu + 0.5) / math.sqrt(var)  # continuity correction toward the mean
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    return max(0.0, min(1.0, p))


def _signal(base: List[float], cand: List[float], lower_is_better: bool) -> str:
    """Human-readable verdict (no p-values / noise jargon). The Mann-Whitney test
    still decides whether a move is real: changes within round-to-round jitter read
    as "≈ same"; only statistically real moves are called faster/slower."""
    bm, cm = _median(base), _median(cand)
    p = mann_whitney_p(base, cand)
    if p is None or bm is None or cm is None:
        return "—"
    if p >= ALPHA:
        return "≈ same"
    better = (cm < bm) if lower_is_better else (cm > bm)
    return "▲ faster" if better else "▼ slower"


def _bars(triples: List[tuple]) -> List[str]:
    """A Grafana-style horizontal bar chart (monospace) of baseline vs candidate
    medians. triples: [(label, baseline_median, candidate_median)]."""
    vals = [v for _, b, c in triples for v in (b, c) if v is not None]
    if not vals:
        return []
    mx = max(vals) or 1.0
    width = 30
    out = ["```"]
    for label, b, c in triples:
        for tag, v in (("baseline ", b), ("candidate", c)):
            if v is None:
                continue
            n = max(1, round(v / mx * width))
            out.append(f"{label:13} {tag} {'█' * n}{'·' * (width - n)} {v:.2f}")
        out.append("")
    out.append("```")
    return out


def _triples(res, baseline, metric) -> List[tuple]:
    cur, old = _series(res, metric), (_series(baseline, metric) if baseline else {})
    out = []
    for name in _ordered_levels(res):
        for ph in PHASES:
            c = cur.get((name, ph))
            if not c:
                continue
            b = old.get((name, ph))
            out.append((f"{name} {ph}", _median(b) if b else None, _median(c)))
    return out


def _per_node(doc: dict, key: str) -> dict:
    """node -> [per-round value of a per-node write metric], pooled across CLs."""
    out: dict = {}
    for rnd in doc.get("rounds", []):
        for lvl in rnd.get("levels", []):
            for node, v in ((lvl.get("write") or {}).get(key) or {}).items():
                if v is not None:
                    out.setdefault(node, []).append(v)
    return out


def _replica_calls(doc: dict) -> dict:
    return _per_node(doc, "replica_http_per_op")


def _impact_section(res: dict, baseline: Optional[dict]) -> List[str]:
    """Coordinator CPU + allocations per write, baseline vs candidate — the
    downstream cost of the self-call the PR removes."""
    if not baseline:
        return []
    rows = []
    for key, unit in (
        ("cpu_ms_per_write", "CPU ms/write"),
        ("alloc_kb_per_write", "alloc KB/write"),
    ):
        cur, old = _per_node(res, key), _per_node(baseline, key)
        if not cur or not old:
            continue
        coord = list(cur.keys())[0]
        b, c = old.get(coord), cur.get(coord)
        if b and c:
            rows.append(
                f"| coordinator {unit} | {_band(b)} | {_band(c)} | "
                f"{_pct(_median(c), _median(b))} | {_signal(b, c, True)} |"
            )
    if not rows:
        return []
    return [
        "### Coordinator work per write — the PR's downstream impact",
        "",
        "_The self-call serializes the object (CPU + memory) and loops it back. If removing "
        "it helps, the coordinator's CPU and allocations per write drop. Counters over the "
        "whole window — low noise._",
        "",
        "| metric | baseline (median [min-max]) | candidate (median [min-max]) | Δ | |",
        "|----|----|----|----|----|",
        *rows,
        "",
    ]


def _replica_section(res: dict, baseline: Optional[dict]) -> List[str]:
    cur = _replica_calls(res)
    if not cur:
        return []
    nodes = list(cur.keys())
    coord = nodes[0]
    lines = [
        "### Self-directed replica HTTP calls per write — the PR's effect",
        "",
        f"_Inbound replica-write HTTP calls each node receives, per write. The PR stops a node "
        f"from calling itself, so **{coord}** (coordinator) drops to ~0 while the other replicas "
        "are unchanged. A deterministic count — no run-to-run noise._",
        "",
    ]
    label = {n: (f"{n} (coord)" if n == coord else n) for n in nodes}
    if baseline:
        old = _replica_calls(baseline)
        lines.append("| node | baseline (median [min-max]) | candidate (median [min-max]) | Δ |")
        lines.append("|----|----|----|----|")
        for n in nodes:
            b, c = old.get(n), cur.get(n)
            if not c or not b:
                continue
            lines.append(
                f"| {label[n]} | {_band(b)} | {_band(c)} | {_pct(_median(c), _median(b))} |"
            )
        triples = [
            (label[n], _median(old.get(n)) if old.get(n) else None, _median(cur[n])) for n in nodes
        ]
        lines += ["", "Replica HTTP calls per write — baseline vs candidate:", ""] + _bars(triples)
    else:
        lines.append("| node | per write (median [min-max]) |")
        lines.append("|----|----|")
        for n in nodes:
            lines.append(f"| {label[n]} | {_band(cur[n])} |")
    lines.append("")
    return lines


def _series(doc: dict, metric: str) -> dict:
    """(CL, phase) -> [one value per round] for the given per-op metric, gathered
    across all interleaved rounds."""
    out: dict = {}
    for rnd in doc.get("rounds", []):
        for lvl in rnd.get("levels", []):
            cl = lvl.get("consistency_level")
            for ph in PHASES:
                v = (lvl.get(ph) or {}).get(metric)
                if v is not None:
                    out.setdefault((cl, ph), []).append(v)
    return out


def _ordered_levels(doc: dict) -> List[str]:
    seen: List[str] = []
    for rnd in doc.get("rounds", []):
        for lvl in rnd.get("levels", []):
            cl = lvl.get("consistency_level")
            if cl and cl not in seen:
                seen.append(cl)
    return seen


def _table(res, baseline, metric, lower_is_better, heading, note) -> List[str]:
    lines = [heading, "", f"_{note}_", ""]
    cur = _series(res, metric)
    levels = _ordered_levels(res)
    if baseline:
        old = _series(baseline, metric)
        rows = []
        for name in levels:
            for ph in PHASES:
                c, b = cur.get((name, ph)), old.get((name, ph))
                if not c or not b:
                    continue
                rows.append(
                    f"| {name} | {ph} | {_band(b)} | {_band(c)} | "
                    f"{_pct(_median(c), _median(b))} | {_signal(b, c, lower_is_better)} |"
                )
        if rows:
            lines.append(
                "| CL | op | baseline (median [min-max]) | candidate (median [min-max]) | Δ median | |"
            )
            lines.append("|----|----|----|----|----|----|")
            lines.extend(rows)
        else:
            lines.append("_No data._")
    else:
        lines.append("| CL | op | median [min-max] |")
        lines.append("|----|----|----|")
        for name in levels:
            for ph in PHASES:
                c = cur.get((name, ph))
                if c:
                    lines.append(f"| {name} | {ph} | {_band(c)} |")
    lines.append("")
    return lines


def render(res: dict, baseline: Optional[dict]) -> str:
    rounds = len(res.get("rounds", []))
    conc = res.get("concurrency", "?")
    window = res.get("duration_seconds", "?")
    lines = ["## Replication performance — image A/B", ""]
    if baseline:
        lines.append(
            f"`{baseline.get('weaviate_version', 'baseline')}` (baseline) "
            f"→ `{res.get('weaviate_version', 'candidate')}` (candidate)"
        )
    else:
        lines.append(f"`{res.get('weaviate_version', 'unknown')}`")
    lines.append(
        f"{res.get('nodes', '?')} nodes · rf={res.get('replication_factor', '?')} · "
        f"{conc} concurrent clients · {window}s windows · median of {rounds} interleaved "
        "rounds with [min-max] spread."
    )
    if baseline:
        lines.append("")
        lines.append(
            "_▲ faster / ▼ slower mark statistically real moves; ≈ same = within "
            "round-to-round jitter. Lower latency / higher throughput is better._"
        )
    lines.append("")

    lines.extend(_replica_section(res, baseline))
    lines.extend(_impact_section(res, baseline))
    lines.extend(
        _table(
            res,
            baseline,
            "internal_p50_ms",
            True,
            "### Weaviate internal replication latency p50 (ms) — lower is better",
            "server-side coordinator write/read duration (p50 from Weaviate's histogram), "
            "measured inside Weaviate above the replica fan-out — the headline; CL=ONE write "
            "is where the local-replica short-circuit shows",
        )
    )
    if baseline:
        chart = _bars(_triples(res, baseline, "internal_p50_ms"))
        if chart:
            lines.append("Internal replication latency p50 — baseline vs candidate:")
            lines.append("")
            lines.extend(chart)
    lines.extend(
        _table(
            res,
            baseline,
            "latency_p50_ms",
            True,
            "### Client per-op latency p50 (ms) — lower is better",
            "end-to-end as the low-concurrency client sees it (cross-check on the internal metric)",
        )
    )
    lines.extend(
        _table(
            res,
            baseline,
            "throughput_ops",
            False,
            "### Throughput (ops/sec) — higher is better",
            "sustained ops/sec under saturating concurrent load; general capacity",
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
