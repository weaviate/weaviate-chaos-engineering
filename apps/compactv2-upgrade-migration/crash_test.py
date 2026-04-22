#!/usr/bin/env python3
"""
compactv2 migration crash-recovery test.

Ingests on the old image, then repeatedly boots the target image and kills
it mid-startup at different delays (covering different points of the
migrator + compactor pipeline). After each kill, boots the target image
again and asserts the golden set is still recoverable via nearVector.

Intended to exercise:
  * compact.Loader.Load() partial execution (snapshot moved but
    condensed not yet converted)
  * Compactor SafeFileWriter interrupted mid-commit (.migrating files
    left behind)
  * MarkMigrationComplete not yet written (if/when wired up)

Run:
    python3 apps/compactv2-upgrade-migration/crash_test.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repro as R  # noqa: E402  (imported as library)


KILL_DELAYS = [0.1, 0.3, 0.6, 1.0, 1.5]


def main() -> int:
    R.log("=== compactv2 migration crash-recovery test ===")
    R.log(f"    old image: {R.OLD_IMAGE}")
    R.log(f"    new image: {R.NEW_IMAGE}")
    R.ensure_image(R.OLD_IMAGE)
    R.ensure_image(R.NEW_IMAGE)
    R.teardown()
    R.setup_infra()

    label = "crash"
    R.log("--- phase 1: ingest on old image ---")
    R.docker_run(R.OLD_IMAGE)
    R.wait_ready()
    R.log(f"version = {R.get_version()}")
    R.create_collection("legacy")
    R.ingest("legacy", label)
    time.sleep(10)

    m, n = R.check_vector_queries("legacy", label,
                                  phase="phase1_pre_crash_baseline")
    if n == 0 or m != n:
        R.log(f"baseline check failed before we even start "
              f"crash-testing: {m}/{n}")
        return 2
    R.stop_container()
    R.dump_volume(f"before_{label}")

    failures: list[str] = []

    for i, delay in enumerate(KILL_DELAYS, start=1):
        R.log(f"--- iteration {i}: kill {delay}s into startup ---")
        R.docker_run(R.NEW_IMAGE)
        time.sleep(delay)
        R.run(["docker", "kill", "-s", "KILL", R.CONTAINER], check=False)
        logs = R.run(["docker", "logs", R.CONTAINER], check=False).stdout or ""
        (R.ROOT / f"crash_iter{i}_killed.log").write_text(logs)
        R.run(["docker", "rm", "-f", R.CONTAINER], check=False)

        R.log(f"--- iteration {i}: recovery boot ---")
        R.docker_run(R.NEW_IMAGE)
        try:
            R.wait_ready(timeout=60)
            m, n = R.check_vector_queries("legacy", label,
                                          phase=f"crash_iter{i}_recovery")
            if m != n:
                failures.append(
                    f"iter{i} kill@{delay}s: only {m}/{n} queries "
                    f"matched after recovery")
        except Exception as e:  # noqa: BLE001
            failures.append(
                f"iter{i} kill@{delay}s: recovery boot failed: {e}")
        finally:
            logs2 = R.run(["docker", "logs", R.CONTAINER], check=False).stdout or ""
            (R.ROOT / f"crash_iter{i}_recovery.log").write_text(logs2)
            R.stop_container()
            R.dump_volume(f"after_crash_iter{i}_{label}")

    R.log("=== CRASH TEST SUMMARY ===")
    if failures:
        for f in failures:
            R.log("  FAIL: " + f)
        return 1
    R.log(f"  all {len(KILL_DELAYS)} crash-recovery iterations passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
