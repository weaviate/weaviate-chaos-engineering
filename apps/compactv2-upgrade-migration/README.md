# compactv2 upgrade migration test

End-to-end test that upgrades Weaviate **1.37.1 → `$WEAVIATE_VERSION`** on the
same on-disk data and verifies the HNSW vector index survives migration.

The 1.37.1 baseline is fixed — it is the last release with the legacy
split-directory HNSW layout (`*.hnsw.snapshot.d/` + `*.hnsw.commitlog.d/`).
The upgrade target is variable via `WEAVIATE_VERSION` so this test is
re-run on every CI dispatch against the current candidate image.

## What it does

For each scenario variant:

1. Ingest 80 000 objects with random 16-dim vectors against
   `semitechnologies/weaviate:1.37.1`. Vectors are centered around 0 so
   that `sign()`-based quantization (BQ) is not pathologically degenerate.
   Capture a deterministic 50-item golden set of `(seq, vector)` pairs.
2. **Pre-upgrade baseline**: `nearVector` each golden-set vector against
   1.37.1 itself. Any low hit-rate here is a test-config problem, not a
   migration bug.
3. Stop the 1.37.1 container, keep the volume.
4. Start the target image on the same volume, static hostname + IP so
   node identity survives the swap.
5. `nearVector` each golden-set vector post-upgrade. Each query must
   return its own `seq`.
6. Reboot the target image N times (default 2). Vector queries must keep
   returning the expected seq — this proves migration is durable, not a
   side-effect of the in-memory graph that was loaded before the migrator
   ran.

Variants: `legacy`, `named` (two named vectors), `multishard`, `multitenant`,
`pq`, `bq`, `sq`, plus a `condensed_only` scenario that disables snapshots
on 1.37.1 so the migrator sees only `.condensed` files, and a `many_reboots`
scenario that reboots five times.

## Running

Host python + docker (not inside a container; the harness orchestrates
docker itself). From the repo root:

```bash
WEAVIATE_VERSION=nightly ./compactv2_upgrade_migration.sh
```

Individual variant:

```bash
cd apps/compactv2-upgrade-migration
python3 repro.py --variant legacy --label my-run
```

Override images directly:

```bash
python3 repro.py \
    --old-image semitechnologies/weaviate:1.37.1 \
    --new-image semitechnologies/weaviate:nightly \
    --variant multishard
```

Crash-during-migration:

```bash
python3 apps/compactv2-upgrade-migration/crash_test.py
```

## History

Found two bugs while building this:

* **createSnapshot output-collision data loss** — fixed in
  [weaviate#9988 `d6403a902d`](https://github.com/weaviate/weaviate/commit/d6403a902d).
  When a shard came out of 1.37.1 with a single `.condensed` file sharing
  its timestamp with an existing `.snapshot`, the compactor would write
  the merged snapshot to `{ts}.snapshot` (overwriting the input) and then
  `Remove()` every input path — deleting its own just-written output.
  This test's multishard variant reproduces the bug on the pre-fix code.

* **Misattributed "migration never runs"** QA report, resolved in the
  same investigation: the QA docker image (`hfresh-improve-search` tag)
  did not contain the compactv2 code at all; the image was a
  pre-compactv2 build with a `1.38.0-dev` version string. On a correctly
  built image, migration runs unconditionally at shard init.
