#!/usr/bin/env bash
#
# Fail if anything in the repo starts Weaviate without saying where its telemetry
# goes. Telemetry is on by default, so a service that says nothing reports to the
# production endpoint - a copy-paste away from being reintroduced, and invisible
# until it shows up in real usage data.

set -eu

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

python3 - <<'PY'
import os
import re
import subprocess
import sys

import yaml

SINK = "telemetry-sink"
SKIP = ("vendor/", "multi-tenancy-load-test/")
# an image reference, not a beacon url such as weaviate://localhost/Thing/id
IMAGE = re.compile(r"""[/"'\s]weaviate:[^/\s"']+""")
URL = re.compile(r"""TELEMETRY_URL['"]?\s*[:=]\s*['"]?(\S*?://\S*?)['"\s,}]""", re.I)
OFF = re.compile(r"""DISABLE_TELEMETRY['"]?\s*[:=]\s*['"]?(false|0|off)\b""", re.I)

bad = []
scanned = 0
service_count = 0
sources = set()


def service_env(svc):
    """compose environment as a dict, from either the mapping or list form"""
    env = svc.get("environment")
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    if isinstance(env, list):
        out = {}
        for item in env:
            k, _, v = str(item).partition("=")
            out[k.strip()] = v
        return out
    return {}


def dotenv(path):
    """KEY=VALUE pairs from a .env file, if it exists"""
    out = {}
    try:
        for line in open(path):
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out

# file types that can actually start a container; docs mention images too
KINDS = (".yml", ".yaml", ".go", ".sh", ".env")

for path in subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n"):
    if not path.endswith(KINDS) or any(s in path for s in SKIP):
        continue
    try:
        src = open(path).read()
    except (OSError, UnicodeDecodeError):
        continue
    scanned += 1

    # 1. per service: a weaviate service must either hard-disable telemetry or
    #    name the sink. A ${DISABLE_TELEMETRY:-...} passthrough can resolve to
    #    enabled at runtime, so a node copy-pasted without its TELEMETRY_URL is
    #    caught even though the block still mentions TELEMETRY.
    had_service = False
    if path.endswith((".yml", ".yaml")):
        try:
            doc = yaml.safe_load(src)
        except yaml.YAMLError:
            doc = None
        services = doc.get("services") if isinstance(doc, dict) else None
        for name, svc in (services if isinstance(services, dict) else {}).items():
            if not isinstance(svc, dict):
                continue
            extends = svc.get("extends") if isinstance(svc.get("extends"), dict) else {}
            starts = IMAGE.search(f" {svc.get('image', '')} ") or str(
                extends.get("service", "")
            ).startswith("weaviate")
            if not starts:
                continue
            service_count += 1
            sources.add(path)
            had_service = True

            env = service_env(svc)
            # a list-form `- DISABLE_TELEMETRY` passes the value through from the
            # sibling .env, so resolve it there
            if "DISABLE_TELEMETRY" in env and not env["DISABLE_TELEMETRY"]:
                env = {**dotenv(os.path.join(os.path.dirname(path), ".env")), **{k: v for k, v in env.items() if v}}
            disable = env.get("DISABLE_TELEMETRY", "").strip("'\"").lower()
            url = env.get("TELEMETRY_URL", "")
            # extends pulls telemetry from the referenced service, not visible here
            if extends:
                continue
            if disable in ("true", "1", "on"):
                continue  # hard-disabled, no destination needed
            if not url:
                bad.append(f"{path}: service '{name}' may enable telemetry but names no TELEMETRY_URL")
            elif "$" not in url and SINK not in url:
                bad.append(f"{path}: service '{name}' sends telemetry to {url}, not the local sink")

    # rule 1 fully covers a compose file's services; the file-level rules below
    # are for the other shapes (Go, shell, .env, non-compose yaml)
    if had_service:
        continue

    # 2. whatever the shape - compose, testcontainer, docker run, an image held in
    #    a const or a shell variable - the file has to mention telemetry
    if IMAGE.search(src):
        sources.add(path)
        if "TELEMETRY" not in src:
            bad.append(f"{path}: names a weaviate image but never mentions telemetry")

    # 3. a literal url must be the local sink
    for url in URL.findall(src):
        if SINK not in url:
            bad.append(f"{path}: sends telemetry to {url}, not the local sink")

    # 4. telemetry switched on needs somewhere to go
    if OFF.search(src) and "TELEMETRY_URL" not in src:
        bad.append(f"{path}: enables telemetry without pointing at the local sink")

summary = (
    f"scanned {scanned} files, found {service_count} weaviate services "
    f"across {len(sources)} files that start weaviate"
)
verdict = f"{len(bad)} problem(s)" if bad else "all declare where telemetry goes"
print(f"{summary}\n{verdict}\n")

for line in sorted(bad):
    print(line)

# annotate the offending file in the PR's Files changed view
if os.environ.get("GITHUB_ACTIONS"):
    for line in sorted(bad):
        path, _, message = line.partition(": ")
        print(f"::error file={path}::{message}")

# surface the same thing on the PR rather than only in the raw log
if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(step_summary, "a") as fh:
        fh.write(f"### Telemetry configuration\n\n{summary}\n\n")
        fh.write("".join(f"- `{line}`\n" for line in sorted(bad)) if bad else "No problems found.\n")

if bad:
    print(
        """
Telemetry is on by default, so anything running weaviate must say where it goes:

  DISABLE_TELEMETRY: 'true'                                  opt out entirely, or
  TELEMETRY_URL: 'http://telemetry-sink:8080/weaviate-telemetry'
  DISABLE_TELEMETRY: '${DISABLE_TELEMETRY:-}'                 report to the local sink

Pointing it anywhere other than the local sink reports to the production
endpoint. See apps/telemetry-sink."""
    )
    sys.exit(1)

PY
