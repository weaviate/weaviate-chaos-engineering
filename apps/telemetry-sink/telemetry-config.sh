#!/usr/bin/env bash
#
# Points the Weaviate under test at the local telemetry sink. TELEMETRY_URL only
# landed in v1.36.0; older versions cannot be redirected, so telemetry is turned
# off for them instead. An unparseable version counts as old, so it never leaks.
#
#   eval "$(apps/telemetry-sink/telemetry-config.sh env 1.36.0)"   # docker compose
#   apps/telemetry-sink/telemetry-config.sh helm 1.24.15           # helm --set flags

set -eu

format="${1:?usage: telemetry-config.sh <env|helm> <version>}"
version="${2:-}"

url='http://telemetry-sink:8080/weaviate-telemetry'

major="${version%%.*}"
rest="${version#*.}"
minor="${rest%%.*}"

redirect=false
if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$minor" =~ ^[0-9]+$ ]] &&
  { ((major > 1)) || ((major == 1 && minor >= 36)); }; then
  redirect=true
fi

if [ "$redirect" = true ]; then
  echo "telemetry: enabled for '$version', pushing to $url" >&2
  case "$format" in
  env) echo "unset DISABLE_TELEMETRY; export TELEMETRY_URL='$url'" ;;
  helm) echo "--set env.TELEMETRY_URL=$url" ;;
  *) echo "unknown format '$format'" >&2 && exit 1 ;;
  esac
else
  echo "telemetry: disabled for '$version', TELEMETRY_URL needs v1.36.0 or newer" >&2
  case "$format" in
  env) echo "unset TELEMETRY_URL; export DISABLE_TELEMETRY=true" ;;
  helm) echo "--set env.DISABLE_TELEMETRY=true" ;;
  *) echo "unknown format '$format'" >&2 && exit 1 ;;
  esac
fi
