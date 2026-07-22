#!/usr/bin/env bash
set -euo pipefail

LANE_PREFIX="${1:?lane prefix required}"
shift
ISSUES=("$@")
if [[ ${#ISSUES[@]} -eq 0 ]]; then
  echo "error: provide at least one scoped issue" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
if [[ ! -x "${QUEUE}" && ! -f "${QUEUE}" ]]; then
  echo "error: comparison queue not found: ${QUEUE}" >&2
  exit 2
fi

export ROOT
export MODEL_TOP_K=12

run_variant() {
  local mode="$1"
  local name="$2"
  local lane="${LANE_PREFIX}-${name}-20260722a"
  local namespace="k12-${name}-${LANE_PREFIX}-20260722"

  echo "[${lane}] $(date -Iseconds) start ${mode}: ${ISSUES[*]}"
  MODEL_CACHE_NAMESPACE="${namespace}" bash "${QUEUE}" "${mode}" "${lane}" "${ISSUES[@]}"
  echo "[${lane}] $(date -Iseconds) complete ${mode}"
}

# Each mode owns its observations, history labels, and model-cache namespace.
run_variant evidence-diverse-k12 evidence-diverse
run_variant causal-parent-k12 causal
run_variant observation-posterior-parent-k12 observation-posterior
run_variant confidence-parent-k12 confidence
