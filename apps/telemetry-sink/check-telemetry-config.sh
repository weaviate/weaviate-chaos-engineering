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

    # 1. every weaviate service declares telemetry itself, so a node copied into
    #    a file that already mentions it elsewhere is still caught
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
            if "TELEMETRY" not in yaml.safe_dump(svc):
                bad.append(f"{path}: service '{name}' declares no telemetry setting")

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
