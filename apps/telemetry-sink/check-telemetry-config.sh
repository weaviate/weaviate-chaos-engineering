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

import yaml

SKIP = ("vendor/", ".git/", "multi-tenancy-load-test/")
bad = []


def tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], capture_output=True, text=True).stdout
    return [f for f in out.split("\n") if f and not any(s in f for s in SKIP)]


def is_weaviate_image(image):
    """True for weaviate itself, but not for siblings like weaviate-benchmarker."""
    repo = str(image).split(":")[0]
    return repo == "weaviate" or repo.endswith("/weaviate")


SINK = "telemetry-sink"


def declares_telemetry(node):
    return "TELEMETRY" in yaml.safe_dump(node, default_flow_style=False)


def env_of(svc):
    """compose env is either a mapping or a list of KEY=VALUE / KEY entries"""
    env = svc.get("environment")
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    if isinstance(env, list):
        out = {}
        for item in env:
            k, _, v = str(item).partition("=")
            out[k] = v
        return out
    return {}


def points_elsewhere(url):
    """a literal url that is not the sink; a ${VAR} reference cannot be judged"""
    return url and "$" not in url and SINK not in url


# --- compose services -------------------------------------------------------
# parsed rather than pattern-matched, so indentation style cannot hide a service
for path in tracked("*.yml", "*.yaml"):
    try:
        doc = yaml.safe_load(open(path))
    except yaml.YAMLError:
        continue
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        continue

    for name, svc in doc["services"].items():
        if not isinstance(svc, dict):
            continue
        extended = (svc.get("extends") or {}).get("service", "") if isinstance(svc.get("extends"), dict) else ""
        runs_weaviate = is_weaviate_image(svc.get("image", "")) or extended.startswith("weaviate")
        if not runs_weaviate:
            continue

        if not declares_telemetry(svc):
            bad.append(f"{path}: service '{name}' runs weaviate with no telemetry setting")
            continue

        env = env_of(svc)
        url = env.get("TELEMETRY_URL", "")
        disabled = env.get("DISABLE_TELEMETRY", "").strip("'\"").lower()

        if points_elsewhere(url):
            bad.append(f"{path}: service '{name}' sends telemetry to {url}, not the local sink")
        elif disabled in ("false", "0", "off") and not url:
            bad.append(f"{path}: service '{name}' enables telemetry without pointing at the local sink")

# --- go testcontainers ------------------------------------------------------
IMAGE_LITERAL = re.compile(r'"([^"\s]*weaviate:[^"\s]*)"')

for path in tracked("*.go"):
    src = open(path).read()
    if "weaviate:" not in src:
        continue

    # identifiers holding a weaviate image, so a const declared outside the
    # function still counts as starting weaviate
    holders = {
        m.group(1)
        for m in re.finditer(r"^\s*(?:const|var)?\s*(\w+)\s*(?::?=)\s*.*?" + IMAGE_LITERAL.pattern, src, re.M)
        if is_weaviate_image(m.group(2))
    }
    holders |= {
        m.group(1)
        for m in re.finditer(r"^\s*(\w+)\s*(?::?=)\s*fmt\.Sprintf\(\s*" + IMAGE_LITERAL.pattern, src, re.M)
        if is_weaviate_image(m.group(2).replace("%s", "x"))
    }

    for m in re.finditer(r"^func .*?^}", src, re.S | re.M):
        fn = m.group(0)
        if "ContainerRequest" not in fn and "GenericContainer" not in fn:
            continue
        literal = any(is_weaviate_image(i) for i in IMAGE_LITERAL.findall(fn))
        via_holder = any(re.search(rf"\b{h}\b", fn) for h in holders)
        if (literal or via_holder) and "TELEMETRY" not in fn and "telemetryFor" not in fn:
            bad.append(f"{path}: {fn.splitlines()[0].strip()} starts weaviate with no telemetry setting")

# --- CI workflows ------------------------------------------------------------
# weaviate-local-k8s sets DISABLE_TELEMETRY=true itself and appends values-inline
# after it, so the danger here is a job turning telemetry back on without saying
# where it should go
OFF = re.compile(r"DISABLE_TELEMETRY\s*[:=]\s*[\"']?(false|0|off)[\"']?", re.I)

for path in tracked(".github/**"):
    try:
        lines = open(path).read().split("\n")
    except (OSError, UnicodeDecodeError):
        continue
    for i, line in enumerate(lines):
        if not OFF.search(line):
            continue
        # a values-override block spans lines, so look around the enabling line
        window = "\n".join(lines[max(0, i - 20):i + 20])
        if "TELEMETRY_URL" not in window:
            bad.append(f"{path}:{i + 1}: re-enables telemetry without setting TELEMETRY_URL")

for line in sorted(bad):
    print(line)

if bad:
    print()
    print("Telemetry is on by default, so anything running weaviate must say where")
    print("it goes. Either:")
    print()
    print("  DISABLE_TELEMETRY: 'true'                 opt out entirely, or")
    print("  TELEMETRY_URL: 'http://telemetry-sink:8080/weaviate-telemetry'")
    print("  DISABLE_TELEMETRY: '${DISABLE_TELEMETRY:-}'   report to the local sink")
    print()
    print("Leaving telemetry on without TELEMETRY_URL, or pointing it anywhere other")
    print("than the local sink, reports to the production endpoint. See")
    print("apps/telemetry-sink.")
    sys.exit(1)

print("all weaviate services and containers declare where telemetry goes")
PY
