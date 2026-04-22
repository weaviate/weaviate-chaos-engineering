#!/usr/bin/env bash
# Sweep driver: runs the compactv2 upgrade-migration test across a set of
# collection shapes (legacy, named vectors, multi-shard, multi-tenant,
# PQ/BQ/SQ quantization, condensed-only, long reboot chain).
#
# Each scenario: ingest on OLD_IMAGE, upgrade to NEW_IMAGE, run nearVector
# golden checks pre-upgrade, post-upgrade, and across N reboots.
#
# Env vars forwarded to repro.py:
#   OLD_IMAGE / NEW_IMAGE / WEAVIATE_VERSION
#   REPRO_VECTOR_DIM / REPRO_NUM_OBJECTS / REPRO_REBOOTS / REPRO_ROOT
set -eo pipefail
cd "$(dirname "$0")"

run_scenario() {
    local name="$1" extra_env="$2" args="$3"
    echo
    echo "########################################"
    echo "### SCENARIO: $name"
    echo "########################################"
    # shellcheck disable=SC2086
    env REPRO_REBOOTS="${REPRO_REBOOTS:-2}" $extra_env \
        python3 repro.py $args 2>&1 | tee "sweep_${name}.log"
    local matched data_loss
    matched=$(grep -E "nearVector check: [0-9]+/[0-9]+ matched" \
              "sweep_${name}.log" || true)
    data_loss=$(grep -cE "DATA LOSS|HNSW BROKEN" "sweep_${name}.log" || true)
    if [[ "${data_loss:-0}" -gt 0 ]]; then
        RESULTS+=("$name: FAIL ($data_loss data-loss hits)")
    else
        RESULTS+=("$name: pass | $matched")
    fi
}

RESULTS=()
run_scenario legacy          ""                                "--variant legacy      --label sweep_legacy"
run_scenario named           ""                                "--variant named       --label sweep_named"
run_scenario multishard      ""                                "--variant multishard  --label sweep_multishard"
run_scenario multitenant     ""                                "--variant multitenant --label sweep_multitenant"
run_scenario bq              ""                                "--variant bq          --label sweep_bq"
run_scenario sq              ""                                "--variant sq          --label sweep_sq"
run_scenario pq              ""                                "--variant pq          --label sweep_pq"
run_scenario condensed_only  "REPRO_DISABLE_PHASE1_SNAPSHOTS=1" "--variant legacy     --label sweep_condensed_only"
run_scenario many_reboots    "REPRO_REBOOTS=5"                  "--variant legacy     --label sweep_many_reboots"

echo
echo "########################################"
echo "### SWEEP SUMMARY"
echo "########################################"
for r in "${RESULTS[@]}"; do
    echo "  $r"
done

# Exit 1 if any scenario failed.
for r in "${RESULTS[@]}"; do
    [[ "$r" == *FAIL* ]] && exit 1
done
exit 0
