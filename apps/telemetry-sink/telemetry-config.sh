#!/usr/bin/env bash
#
# Points the Weaviate under test at the local telemetry sink. TELEMETRY_URL only
# landed in v1.36.0; older versions cannot be redirected, so telemetry is turned
# off for them instead. An unparseable version counts as old, so it never leaks.
#
#   eval "$(apps/telemetry-sink/telemetry-config.sh 1.36.0)"

set -eu

version="${1:-}"

url='http://telemetry-sink:8080/weaviate-telemetry'

major="${version%%.*}"
rest="${version#*.}"
minor="${rest%%.*}"

if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$minor" =~ ^[0-9]+$ ]] &&
  { ((major > 1)) || ((major == 1 && minor >= 36)); }; then
  echo "telemetry: enabled for '$version', pushing to $url" >&2
  echo "unset DISABLE_TELEMETRY; export TELEMETRY_URL='$url'"
else
  echo "telemetry: disabled for '$version', TELEMETRY_URL needs v1.36.0 or newer" >&2
  echo "unset TELEMETRY_URL; export DISABLE_TELEMETRY=true"
fi
