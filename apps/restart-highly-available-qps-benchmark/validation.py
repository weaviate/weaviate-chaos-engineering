"""
Pass/fail analysis for the restart benchmark CSVs.

Free of the weaviate and k8s dependencies the benchmark needs, so the thresholds
can be tested against recorded CSVs without a cluster. See test_validation.py.

On what is measured
-------------------
Each CSV row covers one second of queries, so a row's "p99" is really the
maximum over that second's samples and swings wildly between runs -- the same
build has produced 32 per-request timeouts in one run and 0 in the next. A
single row therefore cannot support a verdict.

Production found weaviate/weaviate#13396 by rolling the cluster and watching how
*long* latency stayed bad: p999 of 46s over 5-minute windows, from histograms
with thousands of samples. The equivalent available here is the share of the
post-restart window spent degraded, which over a long run is stable even though
each row is not. That, plus the count of queries that outran the per-request
timeout, is what gets judged; peak values are reported but never gate.
"""

import csv as csv_module
import glob
import logging
import os
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid int for %s=%r; using default %s", name, value, default)
        return default


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, value, default)
        return default


def _is_restart_sentinel(row: Dict[str, str]) -> bool:
    name = row.get("phase_name", "")
    return name == "restart_event" or name.endswith("_restart_event")


def _column(rows: List[Dict[str, str]], key: str) -> List[float]:
    out: List[float] = []
    for r in rows:
        try:
            out.append(float(r[key]))
        except (ValueError, KeyError):
            pass
    return out


def _sustained_breach(
    values: List[float], breached: Callable[[float], bool], window: int
) -> Optional[Tuple[int, List[float]]]:
    """First run of *window* consecutive values all satisfying *breached*."""
    for i in range(max(1, len(values) - window + 1)):
        chunk = values[i : i + window]
        if len(chunk) < window:
            break
        if all(breached(v) for v in chunk):
            return i, chunk
    return None


def annotate_csvs_with_restart(
    restart_time: float,
    collection_names: List[str],
    test_start_time: float,
    event_name: str = "restart_event",
) -> None:
    """
    Insert a sentinel row with phase_name=*event_name* into each collection's CSV
    at the position matching restart_time, so the pre/post split is unambiguous.
    """
    for name in collection_names:
        pattern = f"benchmark_results_{name}_*.csv"
        candidates = [
            p for p in sorted(glob.glob(pattern)) if os.path.getmtime(p) >= test_start_time
        ]
        if not candidates:
            logger.warning("No benchmark CSV for collection %s, skipping annotation", name)
            continue
        csv_path = candidates[-1]

        with open(csv_path, newline="") as f:
            reader = csv_module.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        if not rows or not fieldnames:
            logger.warning("CSV %s is empty, skipping annotation", csv_path)
            continue

        insert_at = len(rows)
        for i, row in enumerate(rows):
            try:
                if float(row["timestamp"]) >= restart_time:
                    insert_at = i
                    break
            except (ValueError, KeyError):
                continue

        sentinel: Dict[str, str] = {k: "" for k in fieldnames}
        sentinel["timestamp"] = f"{restart_time:.6f}"
        sentinel["phase_name"] = event_name
        sentinel["actual_qps"] = "0"
        sentinel["total_queries"] = "0"
        rows.insert(insert_at, sentinel)

        with open(csv_path, "w", newline="") as f:
            writer = csv_module.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Annotated %s with %s at row %s", csv_path, event_name, insert_at + 1)


def validate_benchmark_csv(
    csv_path: str,
    target_qps: float,
    max_drop_ratio: float = 0.6,
    sustained_window: int = 3,
    baseline_skip_rows: int = 3,
    degraded_ms: float = 1000.0,
    max_degraded_fraction: float = 0.15,
    min_rows_for_fraction: int = 120,
    min_baseline_qps_ratio: float = 0.75,
) -> Tuple[bool, str]:
    """
    Validate a restart-annotated benchmark CSV.

    Fails when, after the restart, either
      * QPS stays below baseline * (1 - ``max_drop_ratio``) for
        ``sustained_window`` consecutive rows, or
      * more than ``max_degraded_fraction`` of the window has a p99 above
        ``degraded_ms``.

    The second check needs ``min_rows_for_fraction`` rows to mean anything; below
    that the window is too short to tell a regression from one slow second, and
    the fraction is reported without gating. The run is also not judged on
    latency at all when the baseline could not hold ``min_baseline_qps_ratio`` of
    the target, since a saturated runner says nothing about the build.

    Returns:
        (passed, reason)
    """
    with open(csv_path, newline="") as f:
        rows = list(csv_module.DictReader(f))

    if len(rows) < 10:
        return False, f"Too few rows ({len(rows)}) to validate"

    restart_idx: Optional[int] = None
    for i, row in enumerate(rows):
        if _is_restart_sentinel(row):
            restart_idx = i
            break
    if restart_idx is None:
        return False, "No restart sentinel found — annotation may have failed"

    pre_rows = [r for r in rows[:restart_idx] if not _is_restart_sentinel(r)]
    stable_pre = pre_rows[baseline_skip_rows:] or pre_rows

    measured_baseline = len(stable_pre) >= 3
    if measured_baseline:
        samples = _column(stable_pre, "actual_qps")
        baseline_qps = sum(samples) / len(samples) if samples else target_qps
    else:
        baseline_qps = target_qps
        logger.warning("No stable pre-restart rows; using target_qps=%s", target_qps)

    post_rows = [
        r
        for r in rows[restart_idx + 1 :]
        if not _is_restart_sentinel(r) and r.get("actual_qps") not in ("", "0")
    ]
    if not post_rows:
        return False, "No post-restart data rows found"

    qps_values = _column(post_rows, "actual_qps")
    if not qps_values:
        return False, "Could not parse any actual_qps values after the restart"

    threshold_qps = baseline_qps * (1.0 - max_drop_ratio)
    breach = _sustained_breach(qps_values, lambda v: v < threshold_qps, sustained_window)
    if breach is not None:
        i, window = breach
        return (
            False,
            f"QPS sustained below threshold for {sustained_window}+ consecutive seconds "
            f"starting at post-restart row {i + 1}: "
            f"window={[round(v, 2) for v in window]}, threshold={threshold_qps:.2f} "
            f"(baseline={baseline_qps:.2f}, max_drop={max_drop_ratio * 100:.0f}%)",
        )

    post_p99 = _column(post_rows, "p99_latency")
    if not post_p99:
        return (
            False,
            "No p99 samples after the restart. A query that outran the per-request "
            "timeout records no latency, so a window of nothing but timeouts looks "
            "like this — see the timeout count for what happened",
        )

    degraded = [v for v in post_p99 if v > degraded_ms]
    fraction = len(degraded) / len(post_p99)
    shape = (
        f"baseline={baseline_qps:.2f} QPS, min_observed={min(qps_values):.2f} QPS, "
        f"{len(degraded)}/{len(post_p99)} post-restart seconds above {degraded_ms:.0f}ms "
        f"({fraction * 100:.1f}%), peak_p99={max(post_p99):.0f}ms"
    )

    if measured_baseline and baseline_qps < target_qps * min_baseline_qps_ratio:
        return (
            True,
            f"OK on throughput; latency not judged — the baseline held only "
            f"{baseline_qps:.1f} of {target_qps:.0f} QPS "
            f"({baseline_qps / target_qps * 100:.0f}%) before any restart, so this run "
            f"was saturated and says nothing about the build. {shape}",
        )

    if len(post_p99) < min_rows_for_fraction:
        return (
            True,
            f"OK; latency not judged — only {len(post_p99)} post-restart seconds, "
            f"fewer than the {min_rows_for_fraction} needed before a degraded share "
            f"is meaningful at this query rate. {shape}",
        )

    if fraction > max_degraded_fraction:
        return (
            False,
            f"latency degraded for {fraction * 100:.1f}% of the post-restart window, "
            f"above the {max_degraded_fraction * 100:.0f}% allowed: {shape}",
        )

    return True, f"OK — {shape}"
