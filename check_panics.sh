#!/bin/bash
#
# Fails when Weaviate logged a panic or a fatal runtime error.
#
# Usage:
#   check_panics.sh LOG_FILE...   scan these files (stern's /tmp/weaviate_pods.log, a tee'd
#                                 journey log); a missing file is an error, since a check
#                                 that silently reads nothing is the failure this guards
#   check_panics.sh               scan `docker logs` of every container whose image or name
#                                 contains "weaviate"; none present is a pass with a note
#
# Exit 0 when no marker was found, 1 when one was, 2 when a named file is missing.
#
# This is the one place the markers live. common.sh runs it from shutdown(), before the
# containers and their logs are removed, and the k8s jobs run it on the stern capture.
# Chaos tests restart Weaviate by SIGKILL and stop it by SIGTERM, neither of which logs a
# goroutine dump, so a dump here is a crash. "Recovered from panic" is included because
# most compose files do not set DISABLE_RECOVERY_ON_PANIC, so a panic there is caught by
# Weaviate and logged rather than exiting the process.

set -u

PANIC_MARKERS='panic:|fatal error:|runtime error:|SIGSEGV|SIGBUS|goroutine [0-9]+ \[running\]|Recovered from panic'
CONTEXT_LINES=40
MAX_LINES_PER_SOURCE=400

found=0
scanned=0

scan() {
  local label="$1" file="$2"
  scanned=$((scanned + 1))
  if grep -qE "$PANIC_MARKERS" "$file"; then
    found=$((found + 1))
    echo "======================================"
    echo "PANIC MARKERS IN: $label"
    echo "======================================"
    grep -nE -A "$CONTEXT_LINES" "$PANIC_MARKERS" "$file" | head -n "$MAX_LINES_PER_SOURCE"
    echo ""
  fi
}

if [ "$#" -gt 0 ]; then
  for file in "$@"; do
    if [ ! -f "$file" ]; then
      echo "::error::check_panics: log file not found: $file"
      exit 2
    fi
    scan "$file" "$file"
  done
else
  tmp=$(mktemp) || exit 2
  trap 'rm -f "$tmp"' EXIT
  if ! containers=$(docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Image}}'); then
    echo "::error::check_panics: could not enumerate containers"
    exit 2
  fi
  # ID, name and image per container, including exited ones: a crashed node is exited.
  while IFS=$'\t' read -r id name image; do
    [ -n "$id" ] || continue
    case "${image}${name}" in
      *weaviate*) ;;
      *) continue ;;
    esac
    if ! docker logs "$id" > "$tmp" 2>&1; then
      echo "::error::check_panics: could not read logs for container $name ($id)"
      cat "$tmp"
      exit 2
    fi
    scan "container $name ($image)" "$tmp"
  done <<< "$containers"
  if [ "$scanned" -eq 0 ]; then
    echo "check_panics: no weaviate containers to scan"
    exit 0
  fi
fi

if [ "$found" -gt 0 ]; then
  echo "::error::Found panic in weaviate logs ($found of $scanned source(s))"
  exit 1
fi
echo "check_panics: no panic markers in $scanned source(s)"
exit 0
