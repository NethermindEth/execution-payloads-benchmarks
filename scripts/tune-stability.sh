#!/bin/bash
set -euo pipefail

# Tune benchmark stability by running multiple configs and comparing variance.
# Usage: ./tune-stability.sh <config-file> [runs_per_config]
#
# Example:
#   ./tune-stability.sh /mnt/sda/expb-data/github-action-compressed-mainnet-flat.yaml 3

CONFIG_FILE="${1:?Usage: $0 <config-file> [runs_per_config]}"
RUNS="${2:-3}"
RESULTS_DIR="/tmp/expb-tune-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${RESULTS_DIR}"

echo "=== EXPB Stability Tuning ==="
echo "Config: ${CONFIG_FILE}"
echo "Runs per config: ${RUNS}"
echo "Results: ${RESULTS_DIR}"
echo ""

extract_times() {
    local log_file="$1"
    # Extract processing_ms from client_metric lines
    grep "client_metric.*processing_ms=" "${log_file}" \
        | sed 's/.*processing_ms=//' \
        | sed 's/[^0-9.].*//' \
        | grep -v '^$'
}

compute_stats() {
    local dir="$1"
    local label="$2"
    local run_files=("${dir}"/run-*.times)

    if [[ "${#run_files[@]}" -lt 2 ]]; then
        echo "  ${label}: not enough runs to compare"
        return
    fi

    # Compute pairwise variance between consecutive runs
    local total_pct=0
    local pairs=0
    local max_pct=0

    for (( i=0; i<${#run_files[@]}-1; i++ )); do
        local f1="${run_files[$i]}"
        local f2="${run_files[$((i+1))]}"

        local stats
        stats=$(paste "${f1}" "${f2}" | awk '
        {
            a=$1; b=$2
            if (a > 0 && b > 0) {
                diff = (a-b) > 0 ? (a-b) : (b-a)
                avg = (a+b)/2
                pct = (diff/avg)*100
                sum += pct
                n++
                if (pct > max) max = pct
            }
        }
        END {
            if (n > 0) printf "%.2f %.2f %d", sum/n, max, n
            else printf "0 0 0"
        }')

        local mean_pct max_single n
        read -r mean_pct max_single n <<< "${stats}"
        total_pct=$(awk "BEGIN {printf \"%.2f\", ${total_pct} + ${mean_pct}}")
        pairs=$((pairs + 1))

        if awk "BEGIN {exit !(${max_single} > ${max_pct})}"; then
            max_pct="${max_single}"
        fi
    done

    local avg_variance
    avg_variance=$(awk "BEGIN {printf \"%.2f\", ${total_pct} / ${pairs}}")

    echo "  ${label}: mean_variance=${avg_variance}% max_single_block=${max_pct}% (${pairs} pairs compared)"
}

run_config() {
    local label="$1"
    local stable_cpu_flag="$2"
    local config_mods="$3"  # unused for now, reserved for config tweaks
    local out_dir="${RESULTS_DIR}/${label}"
    mkdir -p "${out_dir}"

    echo "--- Testing: ${label} ---"

    for (( run=1; run<=RUNS; run++ )); do
        echo "  Run ${run}/${RUNS}..."
        local log_file="${out_dir}/run-${run}.log"

        expb execute-scenarios \
            --config-file "${CONFIG_FILE}" \
            ${stable_cpu_flag} \
            --per-payload-metrics \
            --per-payload-metrics-logs \
            > "${log_file}" 2>&1 || true

        extract_times "${log_file}" > "${out_dir}/run-${run}.times"

        local count
        count=$(wc -l < "${out_dir}/run-${run}.times")
        echo "    Extracted ${count} payload times"

        # Cooldown between runs
        if [[ "${run}" -lt "${RUNS}" ]]; then
            echo "    Cooling down 15s..."
            sleep 15
        fi
    done

    compute_stats "${out_dir}" "${label}"
    echo ""
}

# ---- Config A: No stabilizers (baseline) ----
run_config "A-no-stabilizers" "--no-stable-cpu" ""

# ---- Config B: Stable CPU (governor only, no freq cap) ----
# Temporarily remove cpu_max_frequency_khz from config
ORIG_CONFIG=$(cat "${CONFIG_FILE}")
sed -i 's/^cpu_max_frequency_khz:.*/# cpu_max_frequency_khz: disabled/' "${CONFIG_FILE}"
sed -i 's/^offline_cpus:.*/# offline_cpus: disabled/' "${CONFIG_FILE}"
run_config "B-governor-only" "--stable-cpu" ""
echo "${ORIG_CONFIG}" > "${CONFIG_FILE}"

# ---- Config C: Governor + freq cap (no SMT offline) ----
ORIG_CONFIG=$(cat "${CONFIG_FILE}")
sed -i 's/^offline_cpus:.*/# offline_cpus: disabled/' "${CONFIG_FILE}"
run_config "C-governor-freqcap" "--stable-cpu" ""
echo "${ORIG_CONFIG}" > "${CONFIG_FILE}"

# ---- Config D: Full stabilizers ----
run_config "D-full-stabilizers" "--stable-cpu" ""

echo "========================================="
echo "=== SUMMARY ==="
echo "========================================="
for dir in "${RESULTS_DIR}"/*/; do
    label=$(basename "${dir}")
    compute_stats "${dir}" "${label}"
done

echo ""
echo "Raw data in: ${RESULTS_DIR}"
echo "To inspect: paste ${RESULTS_DIR}/<config>/run-1.times ${RESULTS_DIR}/<config>/run-2.times | head"
