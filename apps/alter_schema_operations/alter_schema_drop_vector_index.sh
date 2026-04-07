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

# --- Vectorizer-based named vectors (Movies collection) ---

# HNSW vectors that store compressed vectors in LSM (BQ and RQ only; PQ/SQ are in-memory)
HNSW_COMPRESSED_LSM_VECTORS=(hnsw_bq hnsw_rq1 hnsw_rq8)

# Flat vectors always have vectors bucket in LSM
FLAT_VECTORS=(flat_plain flat_bq flat_rq1)

# Flat vectors that also have a compressed vectors bucket in LSM
FLAT_COMPRESSED_LSM_VECTORS=(flat_bq flat_rq1)

# All HNSW vectors (have commitlog.d and snapshot.d at shard level)
VECTORIZER_HNSW_VECTORS=(
  hnsw_plain
  hnsw_pq hnsw_bq hnsw_sq hnsw_rq1 hnsw_rq8
)

VECTORIZER_LSM_FOLDERS=()
for v in "${HNSW_COMPRESSED_LSM_VECTORS[@]}"; do
  VECTORIZER_LSM_FOLDERS+=("vectors_compressed_${v}")
done
for v in "${FLAT_VECTORS[@]}"; do
  VECTORIZER_LSM_FOLDERS+=("vectors_${v}")
done
for v in "${FLAT_COMPRESSED_LSM_VECTORS[@]}"; do
  VECTORIZER_LSM_FOLDERS+=("vectors_compressed_${v}")
done

VECTORIZER_SHARD_FOLDERS=()
for v in "${VECTORIZER_HNSW_VECTORS[@]}"; do
  VECTORIZER_SHARD_FOLDERS+=("vectors_${v}.hnsw.commitlog.d" "vectors_${v}.hnsw.snapshot.d")
done

# Step 1
run_step "Create Movies collection with vectorizer-based vector indexes and import data"
go test -count 1 -v -run '^TestCreateMoviesCollectionAndSearch$' ./... -timeout 600s

# Step 2
run_step "Check vector index LSM buckets exist for Movies collection"
MOVIES_SHARDS=$(kubectl -n weaviate exec -i weaviate-0 -c weaviate -- ls /var/lib/weaviate/movies/)
for SHARD in $MOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" movies "$SHARD" exists "${VECTORIZER_LSM_FOLDERS[@]}"
done

# Step 3
run_step "Check HNSW commitlog/snapshot dirs exist for Movies collection"
for SHARD in $MOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard movies "$SHARD" exists "${VECTORIZER_SHARD_FOLDERS[@]}"
done

# Step 4
run_step "Drop all vector indexes from Movies collection"
go test -count 1 -v -run '^TestDropVectorIndicesMovies$' ./... -timeout 600s

# Step 5
run_step "Check vector index LSM buckets removed for Movies collection"
for SHARD in $MOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" movies "$SHARD" absent "${VECTORIZER_LSM_FOLDERS[@]}"
done

# Step 6
run_step "Check HNSW commitlog/snapshot dirs removed for Movies collection"
for SHARD in $MOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard movies "$SHARD" absent "${VECTORIZER_SHARD_FOLDERS[@]}"
done

# --- Multi-vector named vectors (MVMovies collection) ---

MV_VECTORS=(
  hnsw_multivec hnsw_multivec_muvera
  hnsw_multivec_muvera_rq1 hnsw_multivec_muvera_rq8
)

# Only RQ-compressed multi-vectors have LSM buckets
MV_COMPRESSED_LSM_VECTORS=(hnsw_multivec_muvera_rq1 hnsw_multivec_muvera_rq8)

MV_LSM_FOLDERS=()
for v in "${MV_COMPRESSED_LSM_VECTORS[@]}"; do
  MV_LSM_FOLDERS+=("vectors_compressed_${v}")
done

MV_SHARD_FOLDERS=()
for v in "${MV_VECTORS[@]}"; do
  MV_SHARD_FOLDERS+=("vectors_${v}.hnsw.commitlog.d" "vectors_${v}.hnsw.snapshot.d")
done

# Step 7
run_step "Create MVMovies collection with multi-vector indexes and import data"
go test -count 1 -v -run '^TestCreateMVMoviesCollectionAndSearch$' ./... -timeout 600s

# Step 8
run_step "Check vector index LSM buckets exist for MVMovies collection"
MVMOVIES_SHARDS=$(kubectl -n weaviate exec -i weaviate-0 -c weaviate -- ls /var/lib/weaviate/mvmovies/)
for SHARD in $MVMOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" mvmovies "$SHARD" exists "${MV_LSM_FOLDERS[@]}"
done

# Step 9
run_step "Check HNSW commitlog/snapshot dirs exist for MVMovies collection"
for SHARD in $MVMOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard mvmovies "$SHARD" exists "${MV_SHARD_FOLDERS[@]}"
done

# Step 10
run_step "Drop all vector indexes from MVMovies collection"
go test -count 1 -v -run '^TestDropVectorIndicesMVMovies$' ./... -timeout 600s

# Step 11
run_step "Check vector index LSM buckets removed for MVMovies collection"
for SHARD in $MVMOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" mvmovies "$SHARD" absent "${MV_LSM_FOLDERS[@]}"
done

# Step 12
run_step "Check HNSW commitlog/snapshot dirs removed for MVMovies collection"
for SHARD in $MVMOVIES_SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard mvmovies "$SHARD" absent "${MV_SHARD_FOLDERS[@]}"
done

# --- Multi-tenant named vectors (MoviesMT collection) ---

# MoviesMT has: hnsw_plain, flat_plain, hnsw_rq8, flat_rq1
MT_LSM_FOLDERS=(
  vectors_flat_plain
  vectors_flat_rq1 vectors_compressed_flat_rq1
  vectors_compressed_hnsw_rq8
)

MT_SHARD_FOLDERS=(
  vectors_hnsw_plain.hnsw.commitlog.d vectors_hnsw_plain.hnsw.snapshot.d
  vectors_hnsw_rq8.hnsw.commitlog.d vectors_hnsw_rq8.hnsw.snapshot.d
)

MT_TENANTS=(tenant1 tenant2 tenant3)

run_step "Create MoviesMT multi-tenant collection and import data"
go test -count 1 -v -run '^TestCreateMoviesMTCollectionAndSearch$' ./... -timeout 600s

run_step "Check vector index LSM buckets exist for MoviesMT tenants"
for TENANT in "${MT_TENANTS[@]}"; do
  echo "Checking tenant: $TENANT"
  "$SCRIPT_DIR/check_folder_existence.sh" moviesmt "$TENANT" exists "${MT_LSM_FOLDERS[@]}"
done

run_step "Check HNSW commitlog/snapshot dirs exist for MoviesMT tenants"
for TENANT in "${MT_TENANTS[@]}"; do
  echo "Checking tenant: $TENANT"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard moviesmt "$TENANT" exists "${MT_SHARD_FOLDERS[@]}"
done

run_step "Deactivate tenant3 in MoviesMT"
go test -count 1 -v -run '^TestDeactivateTenant3MoviesMT$' ./... -timeout 60s

run_step "Drop all vector indexes from MoviesMT and verify search fails for active tenants"
go test -count 1 -v -run '^TestDropVectorIndicesMoviesMT$' ./... -timeout 600s

run_step "Check vector index LSM buckets removed for MoviesMT tenant1 and tenant2"
for TENANT in tenant1 tenant2; do
  echo "Checking tenant: $TENANT"
  "$SCRIPT_DIR/check_folder_existence.sh" moviesmt "$TENANT" absent "${MT_LSM_FOLDERS[@]}"
done

run_step "Check HNSW commitlog/snapshot dirs removed for MoviesMT tenant1 and tenant2"
for TENANT in tenant1 tenant2; do
  echo "Checking tenant: $TENANT"
  "$SCRIPT_DIR/check_folder_existence.sh" --location shard moviesmt "$TENANT" absent "${MT_SHARD_FOLDERS[@]}"
done

run_step "Check vector index LSM buckets still exist for MoviesMT tenant3 (was inactive)"
"$SCRIPT_DIR/check_folder_existence.sh" moviesmt tenant3 exists "${MT_LSM_FOLDERS[@]}"

run_step "Check HNSW commitlog/snapshot dirs still exist for MoviesMT tenant3 (was inactive)"
"$SCRIPT_DIR/check_folder_existence.sh" --location shard moviesmt tenant3 exists "${MT_SHARD_FOLDERS[@]}"

run_step "Activate tenant3 and verify search fails + folders removed"
go test -count 1 -v -run '^TestActivateTenant3MoviesMT$' ./... -timeout 600s

run_step "Check vector index LSM buckets removed for MoviesMT tenant3"
"$SCRIPT_DIR/check_folder_existence.sh" moviesmt tenant3 absent "${MT_LSM_FOLDERS[@]}"

run_step "Check HNSW commitlog/snapshot dirs removed for MoviesMT tenant3"
"$SCRIPT_DIR/check_folder_existence.sh" --location shard moviesmt tenant3 absent "${MT_SHARD_FOLDERS[@]}"

echo ""
echo "======================================"
echo "All $step steps passed!"
echo "======================================"
