#!/usr/bin/env bash
#
# Points the Weaviate under test at the local telemetry sink. TELEMETRY_URL only
# landed in v1.36.0; older versions cannot be redirected, so telemetry is turned
# off for them instead.
#
#   eval "$(apps/telemetry-sink/telemetry-config.sh 1.36.0)"
#
# WEAVIATE_VERSION in CI is a Docker tag, not a version. When the argument does
# not parse and WEAVIATE_REAL_VERSION is set, fall back to it so nightly/preview
# builds resolve; an argument that is already a version is used as-is.

set -eu

# strip a leading v so this agrees with the Go gate (hashicorp/go-version, which
# accepts v1.36.0) on the same inputs
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
