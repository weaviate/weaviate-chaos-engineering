#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

step=0
run_step() {
  step=$((step + 1))
  echo ""
  echo "======================================"
  echo "Step $step: $1"
  echo "======================================"
}

# Step 1
run_step "Create Movies collection with various vector indexes and import data"
go test -count 1 -v -run '^TestCreateMoviesCollectionAndSearch$' ./... -timeout 600s

echo ""
echo "======================================"
echo "All $step steps passed!"
echo "======================================"
