#!/usr/bin/env python3
"""
compactv2 upgrade-migration test.

Ingests objects into Weaviate 1.37.1 (old on-disk HNSW layout), stops it,
starts a target Weaviate image on the SAME volume (expected to contain the
compactv2 migration code), waits for startup, and verifies the HNSW index
survived by running nearVector queries against a deterministic golden set
captured during ingest. Reboots the target image one or more times to
exercise load-from-disk and assert the migration is durable.

Variants:  legacy | named | multishard | multitenant | pq | bq | sq
Env var toggles:
  WEAVIATE_VERSION (default: nightly) - used to build NEW_IMAGE
  OLD_IMAGE        (default: semitechnologies/weaviate:1.37.1)
  NEW_IMAGE        (default: semitechnologies/weaviate:$WEAVIATE_VERSION)
  REPRO_ROOT       (default: ./workdir/compactv2-upgrade-migration)
  REPRO_VECTOR_DIM (default: 16)
  REPRO_NUM_OBJECTS (default: 80000)
  REPRO_REBOOTS    (default: 1)
  REPRO_GOLDEN_QUERIES (default: 50)
  REPRO_DISABLE_PHASE1_SNAPSHOTS=1   skip snapshot creation under 1.37.1
  REPRO_LAZY_LOAD=1                   force lazy shard loading under NEW_IMAGE
  REPRO_SKIP_QUERY=1                  do not query post-upgrade
  REPRO_SKIP_PHASE3=1                 skip the reboot phase
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _default_root() -> Path:
    return Path(os.environ.get("REPRO_ROOT",
                               "./workdir/compactv2-upgrade-migration")).resolve()


ROOT = _default_root()
ROOT.mkdir(parents=True, exist_ok=True)
LOG = (ROOT / "run.log").open("w")

DEFAULT_WEAVIATE_VERSION = os.environ.get("WEAVIATE_VERSION", "nightly")
OLD_IMAGE = os.environ.get("OLD_IMAGE", "semitechnologies/weaviate:1.37.1")
NEW_IMAGE = os.environ.get(
    "NEW_IMAGE", f"semitechnologies/weaviate:{DEFAULT_WEAVIATE_VERSION}")

NETWORK = "compactv2-upgrade-net"
SUBNET = "172.30.78.0/24"   # Distinct from any existing chaos-engineering subnet
NODE_IP = "172.30.78.10"
NODE_HOSTNAME = "weaviate-node-0"
CONTAINER = "compactv2-upgrade-weaviate"
VOLUME = "compactv2-upgrade-data"

HTTP_PORT = 18080
GRPC_PORT = 18443
READY_URL = f"http://127.0.0.1:{HTTP_PORT}/v1/.well-known/ready"
META_URL = f"http://127.0.0.1:{HTTP_PORT}/v1/meta"
SCHEMA_URL = f"http://127.0.0.1:{HTTP_PORT}/v1/schema"
OBJECTS_BATCH_URL = f"http://127.0.0.1:{HTTP_PORT}/v1/batch/objects"

COLLECTION = "CompactV2UpgradeMigration"
VECTOR_DIM = int(os.environ.get("REPRO_VECTOR_DIM", "16"))
NUM_OBJECTS = int(os.environ.get("REPRO_NUM_OBJECTS", "80000"))
BATCH_SIZE = 500
INGEST_WAVES = 3

LAZY_LOAD = os.environ.get("REPRO_LAZY_LOAD") == "1"
SKIP_QUERY = os.environ.get("REPRO_SKIP_QUERY") == "1"
DISABLE_PHASE1_SNAPSHOTS = os.environ.get("REPRO_DISABLE_PHASE1_SNAPSHOTS") == "1"
REBOOTS = int(os.environ.get("REPRO_REBOOTS", "1"))
GOLDEN_QUERIES = int(os.environ.get("REPRO_GOLDEN_QUERIES", "50"))

WEAVIATE_ENV = {
    "QUERY_DEFAULTS_LIMIT": "25",
    "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true",
    "PERSISTENCE_DATA_PATH": "/var/lib/weaviate",
    "DEFAULT_VECTORIZER_MODULE": "none",
    "CLUSTER_HOSTNAME": NODE_HOSTNAME,
    "RAFT_BOOTSTRAP_EXPECT": "1",
    "RAFT_JOIN": NODE_HOSTNAME,
    "PERSISTENCE_HNSW_MAX_LOG_SIZE": "5242880",
    "PERSISTENCE_HNSW_SNAPSHOT_INTERVAL_SECONDS": "5",
    "PERSISTENCE_HNSW_SNAPSHOT_ON_STARTUP": "true",
    "PERSISTENCE_HNSW_SNAPSHOT_MIN_DELTA_COMMITLOGS_NUMBER": "1",
    "PERSISTENCE_HNSW_SNAPSHOT_MIN_DELTA_COMMITLOGS_SIZE_PERCENTAGE": "0",
    "PERSISTENCE_HNSW_DISABLE_SNAPSHOTS": "false",
    "LOG_LEVEL": "debug",
}
if LAZY_LOAD:
    WEAVIATE_ENV["LAZY_LOAD_SHARD_COUNT_THRESHOLD"] = "0"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def run(cmd: list[str], check: bool = True,
        capture: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(shlex.quote(c) for c in cmd))
    res = subprocess.run(cmd, check=False, text=True,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.STDOUT if capture else None)
    if capture and res.stdout:
        for line in res.stdout.splitlines():
            LOG.write("    " + line + "\n")
        LOG.flush()
    if check and res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {cmd}")
    return res


def ensure_image(image: str) -> None:
    have = run(["docker", "image", "inspect", image], check=False).returncode == 0
    if not have:
        log(f"pulling {image}")
        run(["docker", "pull", image])


def teardown() -> None:
    log("tearing down any prior state")
    run(["docker", "rm", "-f", CONTAINER], check=False)
    run(["docker", "network", "rm", NETWORK], check=False)
    run(["docker", "volume", "rm", VOLUME], check=False)


def setup_infra() -> None:
    run(["docker", "network", "create", "--driver", "bridge",
         "--subnet", SUBNET, NETWORK])
    run(["docker", "volume", "create", VOLUME])


def docker_run(image: str, extra_env: dict | None = None) -> None:
    env_args: list[str] = []
    merged = dict(WEAVIATE_ENV)
    if extra_env:
        merged.update(extra_env)
    for k, v in merged.items():
        env_args += ["-e", f"{k}={v}"]
    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER,
        "--hostname", NODE_HOSTNAME,
        "--network", NETWORK,
        "--ip", NODE_IP,
        "-p", f"{HTTP_PORT}:8080",
        "-p", f"{GRPC_PORT}:50051",
        "-v", f"{VOLUME}:/var/lib/weaviate",
        *env_args,
        image,
        "--host", "0.0.0.0",
        "--port", "8080",
        "--scheme", "http",
    ]
    run(cmd)


def stop_container() -> None:
    run(["docker", "stop", "-t", "30", CONTAINER], check=False)
    run(["docker", "rm", "-f", CONTAINER], check=False)


def wait_ready(timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(READY_URL, timeout=2) as r:
                if r.status == 200:
                    log("weaviate ready")
                    return
        except Exception as e:  # noqa: BLE001 -- retry loop swallows all errors
            last_err = e
        time.sleep(1.0)
    run(["docker", "logs", "--tail", "200", CONTAINER], check=False)
    raise RuntimeError(f"weaviate not ready after {timeout}s: {last_err}")


def get_version() -> str:
    with urllib.request.urlopen(META_URL, timeout=5) as r:
        meta = json.loads(r.read())
    return meta.get("version", "?") + " (" + meta.get("gitHash", "?")[:10] + ")"


def http_json(method: str, url: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} on {method} {url}: {body_txt}") from e


HNSW_CFG = {
    "distance": "l2-squared",
    "efConstruction": 64,
    "maxConnections": 16,
    "skip": False,
    "dynamicEfMin": 100,
    "dynamicEfMax": 500,
    "dynamicEfFactor": 8,
}


def create_collection(variant: str) -> None:
    props = [{"name": "seq", "dataType": ["int"]}]
    if variant == "legacy":
        schema = {"class": COLLECTION, "vectorizer": "none",
                  "vectorIndexType": "hnsw",
                  "vectorIndexConfig": HNSW_CFG,
                  "properties": props}
    elif variant == "named":
        schema = {"class": COLLECTION, "properties": props,
                  "vectorConfig": {
                      "v_a": {"vectorizer": {"none": {}},
                              "vectorIndexType": "hnsw",
                              "vectorIndexConfig": HNSW_CFG},
                      "v_b": {"vectorizer": {"none": {}},
                              "vectorIndexType": "hnsw",
                              "vectorIndexConfig": HNSW_CFG}}}
    elif variant == "multishard":
        schema = {"class": COLLECTION, "vectorizer": "none",
                  "vectorIndexType": "hnsw",
                  "vectorIndexConfig": HNSW_CFG, "properties": props,
                  "shardingConfig": {"desiredCount": 3}}
    elif variant == "multitenant":
        schema = {"class": COLLECTION, "vectorizer": "none",
                  "vectorIndexType": "hnsw",
                  "vectorIndexConfig": HNSW_CFG, "properties": props,
                  "multiTenancyConfig": {"enabled": True,
                                          "autoTenantCreation": True}}
    elif variant in ("pq", "bq", "sq"):
        cfg = dict(HNSW_CFG)
        if variant == "pq":
            cfg["pq"] = {"enabled": True, "segments": 4, "centroids": 256,
                         "trainingLimit": 20000, "bitCompression": False}
        elif variant == "bq":
            cfg["bq"] = {"enabled": True}
        elif variant == "sq":
            cfg["sq"] = {"enabled": True, "trainingLimit": 20000,
                         "rescoreLimit": 20}
        schema = {"class": COLLECTION, "vectorizer": "none",
                  "vectorIndexType": "hnsw",
                  "vectorIndexConfig": cfg, "properties": props}
    else:
        raise ValueError(f"unknown variant {variant}")
    http_json("POST", SCHEMA_URL, schema)
    log(f"created collection {COLLECTION} (variant={variant})")


def make_obj(variant: str, seq: int, rnd: random.Random, tenant,
             goldset: list | None = None) -> dict:
    obj = {"class": COLLECTION, "properties": {"seq": seq}}
    if tenant is not None:
        obj["tenant"] = tenant
    if variant == "named":
        va = [(rnd.random() * 2 - 1) for _ in range(VECTOR_DIM)]
        vb = [(rnd.random() * 2 - 1) for _ in range(VECTOR_DIM)]
        obj["vectors"] = {"v_a": va, "v_b": vb}
        if goldset is not None:
            goldset.append({"seq": seq, "tenant": tenant,
                            "vector_v_a": va, "vector_v_b": vb})
    else:
        v = [(rnd.random() * 2 - 1) for _ in range(VECTOR_DIM)]
        obj["vector"] = v
        if goldset is not None:
            goldset.append({"seq": seq, "tenant": tenant, "vector": v})
    return obj


def ensure_tenants(tenants: list[str]) -> None:
    url = f"{SCHEMA_URL}/{COLLECTION}/tenants"
    http_json("POST", url, [{"name": t} for t in tenants])


def ingest(variant: str, label: str) -> list:
    rnd = random.Random(42)
    tenants = ["tenantA", "tenantB", "tenantC"] if variant == "multitenant" else [None]
    if variant == "multitenant":
        ensure_tenants([t for t in tenants if t])
    per_wave = NUM_OBJECTS // INGEST_WAVES
    total = 0
    all_vectors: list = []
    for wave in range(INGEST_WAVES):
        wave_end = per_wave * (wave + 1) if wave < INGEST_WAVES - 1 else NUM_OBJECTS
        for start in range(total, wave_end, BATCH_SIZE):
            end = min(start + BATCH_SIZE, wave_end)
            batch = []
            for i in range(start, end):
                tenant = tenants[i % len(tenants)]
                batch.append(make_obj(variant, i, rnd, tenant, all_vectors))
            http_json("POST", OBJECTS_BATCH_URL, {"objects": batch})
            total = end
        log(f"wave {wave + 1}/{INGEST_WAVES} done, total ingested = {total}")
        log("pausing 8s for commit-log roll/condense cycle")
        time.sleep(8)

    rnd_pick = random.Random(1337)
    sample = rnd_pick.sample(all_vectors, min(GOLDEN_QUERIES, len(all_vectors)))
    goldpath = ROOT / f"goldset_{label}.json"
    goldpath.write_text(json.dumps(sample))
    log(f"saved golden set of {len(sample)} items to {goldpath}")
    return sample


def load_goldset(label: str) -> list:
    p = ROOT / f"goldset_{label}.json"
    return json.loads(p.read_text()) if p.exists() else []


def check_vector_queries(variant: str, label: str, phase: str) -> tuple[int, int]:
    sample = load_goldset(label)
    if not sample:
        log(f"[{phase}] no golden set to verify")
        return (0, 0)
    matched = 0
    mismatches: list[dict] = []
    for item in sample:
        tenant_clause = f', tenant: "{item["tenant"]}"' if item.get("tenant") else ""
        if variant == "named":
            vec = item["vector_v_a"]
            near = f'nearVector: {{vector: {vec}, targetVectors: ["v_a"]}}'
        else:
            vec = item["vector"]
            near = f'nearVector: {{vector: {vec}}}'
        query = (f'{{Get{{{COLLECTION}({near}, limit: 1{tenant_clause})'
                 f'{{seq}}}}}}')
        try:
            r = http_json("POST", f"http://127.0.0.1:{HTTP_PORT}/v1/graphql",
                          {"query": query})
            got = r.get("data", {}).get("Get", {}).get(COLLECTION, [])
            if got and got[0].get("seq") == item["seq"]:
                matched += 1
            else:
                mismatches.append({"expected_seq": item["seq"], "got": got})
        except Exception as e:  # noqa: BLE001
            mismatches.append({"expected_seq": item["seq"], "error": str(e)})
    total = len(sample)
    log(f"[{phase}] nearVector check: {matched}/{total} matched")
    if mismatches:
        log(f"[{phase}] first 3 mismatches: "
            f"{json.dumps(mismatches[:3])[:500]}")
    return matched, total


def dump_volume(label: str) -> Path:
    out = ROOT / f"{label}.txt"
    script = (
        'cd /v && echo "---FILES---"; '
        'find . -type f 2>/dev/null | sort | while read f; do '
        '  printf "%s\\t%s\\n" "$f" "$(stat -c %s "$f" 2>/dev/null)"; '
        'done; echo "---DIRS---"; find . -type d 2>/dev/null | sort'
    )
    res = run(["docker", "run", "--rm", "-v", f"{VOLUME}:/v:ro",
               "alpine", "sh", "-c", script])
    text = res.stdout or ""
    idx = text.find("---FILES---")
    text = text[idx:] if idx >= 0 else text
    out.write_text(text)
    log(f"wrote {out} ({len(text)} bytes)")
    return out


def main() -> int:
    global OLD_IMAGE, NEW_IMAGE
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="legacy",
                    choices=["legacy", "named", "multishard", "multitenant",
                             "pq", "bq", "sq"])
    ap.add_argument("--label", default=None,
                    help="suffix for output files (before_{label}.txt etc)")
    ap.add_argument("--old-image", default=OLD_IMAGE,
                    help=f"Docker image to run phase 1 ingest on "
                         f"(default: {OLD_IMAGE})")
    ap.add_argument("--new-image", default=NEW_IMAGE,
                    help=f"Docker image to run the upgrade against "
                         f"(default: {NEW_IMAGE})")
    args = ap.parse_args()
    label = args.label or args.variant
    OLD_IMAGE = args.old_image
    NEW_IMAGE = args.new_image

    log(f"=== compactv2 upgrade migration test ===")
    log(f"    old image: {OLD_IMAGE}")
    log(f"    new image: {NEW_IMAGE}")
    log(f"    variant:   {args.variant}")
    log(f"    root:      {ROOT}")

    ensure_image(OLD_IMAGE)
    ensure_image(NEW_IMAGE)
    teardown()
    setup_infra()

    log("--- phase 1: ingest on old image ---")
    env_override: dict[str, str] = {}
    if DISABLE_PHASE1_SNAPSHOTS:
        env_override["PERSISTENCE_HNSW_DISABLE_SNAPSHOTS"] = "true"
    docker_run(OLD_IMAGE, extra_env=env_override)
    wait_ready()
    log(f"version = {get_version()}")
    create_collection(args.variant)
    ingest(args.variant, label)
    log("sleeping to allow snapshot interval (+fsync)…")
    time.sleep(12)

    m, n = check_vector_queries(args.variant, label, phase="phase1_pre_upgrade")
    log(f"PRE-UPGRADE BASELINE: {m}/{n} — if low, test config is wrong (not a migration issue)")

    logs_res = run(["docker", "logs", CONTAINER], check=False)
    (ROOT / f"phase1_{label}.log").write_text(logs_res.stdout or "")
    stop_container()
    before = dump_volume(f"before_{label}")

    log("--- phase 2: upgrade to new image ---")
    docker_run(NEW_IMAGE)
    wait_ready()
    log(f"version = {get_version()}")
    log("sleeping to allow migration to settle…")
    time.sleep(15)

    reproduced_vec_loss = False
    if not SKIP_QUERY:
        try:
            q = http_json("POST", f"http://127.0.0.1:{HTTP_PORT}/v1/graphql",
                          {"query": "{Aggregate{" + COLLECTION + "{meta{count}}}}"})
            log(f"aggregate count (LSM): {json.dumps(q)[:200]}")
        except Exception as e:  # noqa: BLE001
            log(f"aggregate query FAILED: {e}")
        m, n = check_vector_queries(args.variant, label, phase="phase2")
        if m != n and n > 0:
            reproduced_vec_loss = True
            log(f"!!! HNSW BROKEN after phase 2: only {m}/{n} queries matched")
    else:
        log("SKIPPING post-upgrade queries")

    logs_res = run(["docker", "logs", CONTAINER], check=False)
    (ROOT / f"phase2_{label}.log").write_text(logs_res.stdout or "")

    # Live filesystem dump while the new image is still running.
    live_res = run([
        "docker", "exec", CONTAINER, "sh", "-c",
        'cd /var/lib/weaviate && echo "---FILES---"; '
        'find . -type f | sort | while read f; do '
        '  printf "%s\\t%s\\n" "$f" "$(stat -c %s "$f" 2>/dev/null)"; '
        'done; echo "---DIRS---"; find . -type d | sort',
    ], check=False)
    live_out = live_res.stdout or ""
    idx = live_out.find("---FILES---")
    (ROOT / f"live_{label}.txt").write_text(live_out[idx:] if idx >= 0 else live_out)
    stop_container()
    after = dump_volume(f"after_{label}")

    if os.environ.get("REPRO_SKIP_PHASE3") == "1":
        log("skipping phase 3 by REPRO_SKIP_PHASE3=1")
        return 1 if reproduced_vec_loss else 0

    for reboot_i in range(1, REBOOTS + 1):
        log(f"--- phase 3.{reboot_i}: reboot new image ({reboot_i}/{REBOOTS}) ---")
        docker_run(NEW_IMAGE)
        try:
            wait_ready(timeout=60)
            q = http_json("POST", f"http://127.0.0.1:{HTTP_PORT}/v1/graphql",
                          {"query": "{Aggregate{" + COLLECTION + "{meta{count}}}}"})
            log(f"post-reboot {reboot_i} count = {json.dumps(q)[:200]}")
            try:
                count = q["data"]["Aggregate"][COLLECTION][0]["meta"]["count"]
                if count != NUM_OBJECTS:
                    log(f"!!! LSM DATA LOSS on reboot {reboot_i}: "
                        f"expected {NUM_OBJECTS}, got {count}")
                    reproduced_vec_loss = True
            except Exception:  # noqa: BLE001
                pass
            m, n = check_vector_queries(args.variant, label,
                                        phase=f"phase3.{reboot_i}")
            if m != n and n > 0:
                log(f"!!! HNSW DATA LOSS on reboot {reboot_i}: "
                    f"only {m}/{n} vector queries matched")
                reproduced_vec_loss = True
        except Exception as e:  # noqa: BLE001
            log(f"post-reboot {reboot_i} check failed: {e}")
            reproduced_vec_loss = True
        finally:
            logs_res = run(["docker", "logs", CONTAINER], check=False)
            (ROOT / f"phase3.{reboot_i}_{label}.log").write_text(logs_res.stdout or "")
            stop_container()
            dump_volume(f"after_reboot{reboot_i}_{label}")

    return 1 if reproduced_vec_loss else 0


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        log("interrupted")
        rc = 130
    except Exception as e:  # noqa: BLE001 -- top-level; rethrow after logging
        log(f"FAILED: {e}")
        raise
    sys.exit(rc)
