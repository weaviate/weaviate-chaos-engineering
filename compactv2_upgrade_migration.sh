#!/bin/bash
# compactv2 upgrade-migration sweep: runs all collection-shape scenarios
# upgrading from Weaviate 1.37.1 (fixed baseline, last release with the
# legacy split HNSW directory layout) to $WEAVIATE_VERSION (default: nightly).
#
# Exits non-zero if any scenario fails (HNSW data loss post-upgrade or
# across reboots).
set -eou pipefail

cd "$(dirname "$0")"

export WEAVIATE_VERSION="${WEAVIATE_VERSION:-nightly}"
export OLD_IMAGE="${OLD_IMAGE:-semitechnologies/weaviate:1.37.1}"
export NEW_IMAGE="${NEW_IMAGE:-semitechnologies/weaviate:${WEAVIATE_VERSION}}"
export REPRO_ROOT="${REPRO_ROOT:-$(pwd)/workdir/compactv2-upgrade-migration}"
mkdir -p "${REPRO_ROOT}"

echo "compactv2 upgrade-migration test"
echo "  OLD_IMAGE = ${OLD_IMAGE}"
echo "  NEW_IMAGE = ${NEW_IMAGE}"
echo "  REPRO_ROOT = ${REPRO_ROOT}"

bash apps/compactv2-upgrade-migration/run_sweep.sh
