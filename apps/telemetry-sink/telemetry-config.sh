#!/usr/bin/env bash
#
# Points the Weaviate under test at the local telemetry sink, or off below
# v1.36.0 where TELEMETRY_URL does not exist. Resolve via BASH_SOURCE, not cwd:
#
#   eval "$("$(dirname "${BASH_SOURCE[0]}")/apps/telemetry-sink/telemetry-config.sh" 1.36.0)"
#
# A tag that does not parse (nightly, preview-...) falls back to
# WEAVIATE_REAL_VERSION; an argument that is already a version is used as-is.

set -eu

# strip a leading v so v1.36.0 agrees with the Go gate
version="${1:-}"
version="${version#v}"

url='http://telemetry-sink:8080/weaviate-telemetry'

if ! [[ "$version" =~ ^[0-9]+\.[0-9]+ ]] && [ -n "${WEAVIATE_REAL_VERSION:-}" ]; then
  version="$WEAVIATE_REAL_VERSION"
fi

major="${version%%.*}"
rest="${version#*.}"
minor="${rest%%.*}"

if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$minor" =~ ^[0-9]+$ ]] &&
  { ((major > 1)) || ((major == 1 && minor >= 36)); }; then
  echo "telemetry: enabled for '$version', pushing to $url" >&2
  echo "export DISABLE_TELEMETRY=false; export TELEMETRY_URL='$url'"
else
  echo "telemetry: disabled for '$version', TELEMETRY_URL needs v1.36.0 or newer" >&2
  echo "unset TELEMETRY_URL; export DISABLE_TELEMETRY=true"
fi
