#!/usr/bin/env bash
#
# Fails if anything in the repo starts Weaviate without saying where its
# telemetry goes. Telemetry is on by default, so a service or container that
# says nothing reports to the production endpoint - easy to reintroduce by
# copying an existing block. Each one must either point at the local sink
# (TELEMETRY_URL, usually via the DISABLE_TELEMETRY pass-through) or opt out
# explicitly with DISABLE_TELEMETRY.
#
# Covers compose services and Go testcontainers. Skips vendor/ and the
# multi-tenancy-load-test submodule, which is a separate repo.

set -eu

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

python3 - <<'PY'
import re
import subprocess
import sys

SKIP = ("vendor/", ".git/", "multi-tenancy-load-test/")
bad = []


def tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], capture_output=True, text=True).stdout
    return [f for f in out.split("\n") if f and not any(s in f for s in SKIP)]


# --- compose services -------------------------------------------------------
for path in tracked("*.yml", "*.yaml"):
    try:
        lines = open(path).read().split("\n")
    except OSError:
        continue
    if not any(l.startswith("services:") for l in lines):
        continue

    blocks, name, start, in_services = [], None, None, False
    for i, line in enumerate(lines):
        if re.match(r"^\S+:", line):
            if name:
                blocks.append((name, start, i))
                name = None
            in_services = line.startswith("services:")
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([A-Za-z0-9._-]+):", line)
        if m:
            if name:
                blocks.append((name, start, i))
            name, start = m.group(1), i
    if name:
        blocks.append((name, start, len(lines)))

    for name, start, end in blocks:
        body = "\n".join(lines[start:end])
        runs_weaviate = "semitechnologies/weaviate" in body or "service: weaviate" in body
        if runs_weaviate and "TELEMETRY" not in body:
            bad.append(f"{path}: service '{name}' runs weaviate with no telemetry setting")

# --- go testcontainers ------------------------------------------------------
for path in tracked("*.go"):
    src = open(path).read()
    if "semitechnologies/weaviate" not in src:
        continue
    # check the enclosing function, so one gated helper cannot cover a second
    # ungated container in the same file
    for m in re.finditer(r"^func .*?^}", src, re.S | re.M):
        fn = m.group(0)
        if "semitechnologies/weaviate" in fn and "TELEMETRY" not in fn and "telemetryFor" not in fn:
            sig = fn.split("\n", 1)[0].strip()
            bad.append(f"{path}: {sig} starts weaviate with no telemetry setting")

for line in bad:
    print(line)

if bad:
    print()
    print("Point it at the local sink with TELEMETRY_URL, or opt out with")
    print("DISABLE_TELEMETRY. See apps/telemetry-sink.")
    sys.exit(1)

print("all weaviate services and containers declare where telemetry goes")
PY
