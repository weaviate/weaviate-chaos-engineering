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
run_step "Create Books collection and import data"
go test -count 1 -v -run '^TestCreateBooksCollectionAndFilter$' ./... -timeout 600s

# Step 2
run_step "Check property index buckets existence for Books collection"
SHARDS=$(kubectl -n weaviate exec -i weaviate-0 -c weaviate -- ls /var/lib/weaviate/books/)
for SHARD in $SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" books "$SHARD" exists \
    property_title property_title_searchable \
    property_author property_author_searchable \
    property_description property_description_searchable
done

# Step 3
run_step "Create Books multi-tenant collection and import data"
go test -count 1 -v -run '^TestCreateBooksMTCollectionAndFilter$' ./... -timeout 600s

# Step 4
run_step "Check property index buckets existence for BooksMT tenant1"
"$SCRIPT_DIR/check_folder_existence.sh" booksmt tenant1 exists \
  property_title property_title_searchable \
  property_author property_author_searchable \
  property_description property_description_searchable

# Step 5
run_step "Check property index buckets existence for BooksMT tenant2"
"$SCRIPT_DIR/check_folder_existence.sh" booksmt tenant2 exists \
  property_title property_title_searchable \
  property_author property_author_searchable \
  property_description property_description_searchable

# Step 6
run_step "Drop property indexes from Books collection"
go test -count 1 -v -run '^TestDropPropertyIndexesBooks$' ./... -timeout 600s

# Step 7
run_step "Check property index buckets removed for Books collection"
SHARDS=$(kubectl -n weaviate exec -i weaviate-0 -c weaviate -- ls /var/lib/weaviate/books/)
for SHARD in $SHARDS; do
  echo "Checking shard: $SHARD"
  "$SCRIPT_DIR/check_folder_existence.sh" books "$SHARD" absent \
    property_title property_title_searchable \
    property_author property_author_searchable \
    property_description property_description_searchable
done

# Step 8
run_step "Drop property indexes from BooksMT collection"
go test -count 1 -v -run '^TestDropPropertyIndexesBooksMT$' ./... -timeout 600s

# Step 9
run_step "Check property index buckets removed for BooksMT tenant1"
"$SCRIPT_DIR/check_folder_existence.sh" booksmt tenant1 absent \
  property_title property_title_searchable \
  property_author property_author_searchable \
  property_description property_description_searchable

# Step 10
run_step "Check property index buckets still exist for BooksMT tenant2"
"$SCRIPT_DIR/check_folder_existence.sh" booksmt tenant2 exists \
  property_title property_title_searchable \
  property_author property_author_searchable \
  property_description property_description_searchable

# Step 11
run_step "Activate tenant2 and verify filtering not working"
go test -count 1 -v -run '^TestActivateTenant2AndVerifyNoFiltering$' ./... -timeout 600s

# Step 12
run_step "Check property index buckets removed for BooksMT tenant2"
"$SCRIPT_DIR/check_folder_existence.sh" booksmt tenant2 absent \
  property_title property_title_searchable \
  property_author property_author_searchable \
  property_description property_description_searchable

# Step 13
run_step "Drop property indexes from empty BookEmpty collection and verify schema"
go test -count 1 -v -run '^TestDropPropertyIndexesEmptyCollection$' ./... -timeout 600s

echo ""
echo "======================================"
echo "All $step steps passed!"
echo "======================================"
