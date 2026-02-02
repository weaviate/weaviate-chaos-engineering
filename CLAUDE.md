# Weaviate Chaos Engineering

Chaos engineering tests for Weaviate - testing crash recovery, data integrity, and resilience.

## Structure

- **Root shell scripts** (e.g., `import_while_crashing.sh`) - main test entry points
- **`common.sh`** - shared functions: `wait_weaviate`, `shutdown`, `logs`, `wait_for_indexing`
- **`apps/`** - Docker-based helper apps (importers, chaotic-killer, etc.)
- **`apps/weaviate/docker-compose*.yml`** - various Weaviate cluster configurations

## Running Tests

**Locally:**
```bash
WEAVIATE_VERSION=1.35.6 ./script_name.sh
```

**CI - "Test Matrix" workflow:**
```bash
gh workflow run "Test matrix" -f weaviate_version=1.35.6
```

Note: `weaviate_version` should not have a leading "v" (use `1.35.6`, not `v1.35.6`).

To run a specific test:
```bash
gh workflow run "Test matrix" -f weaviate_version=1.35.6 -f test_to_run=import-while-crashing
```

**Specific Versions:**

If an image does not exist for a specific version under test (e.g. a local branch), build it in the weaviate core repo using `make weaviate-image`.

## Environment Variables

Tests use `WEAVIATE_VERSION` env var; docker-compose files reference `$WEAVIATE_VERSION` for the image tag.
