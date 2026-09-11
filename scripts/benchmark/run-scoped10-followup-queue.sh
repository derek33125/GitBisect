#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-scoped10-followup-queue.sh LANE

Run the scoped-10 heuristic follow-up experiment only after the master-50
runtime-generation refill records 29 terminal cases. The queue intentionally
contains no master-50 benchmark jobs.

The controller first runs deterministic repeat checks, then the API-free
heuristic negative controls. `RUNTIME_GATE_FILE` must name a JSON record with:
  {"scope":"master50-runtime-generation","status":"complete",
   "expected_cases":29,"terminal_cases":29}
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

LANE="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
RUNTIME_GATE_FILE="${RUNTIME_GATE_FILE:-${ROOT}/results/issues/server-jobs/master50-runtime-generation-20260903.complete.json}"
FOLLOWUP_COHORT="${FOLLOWUP_COHORT:-all}"
FOLLOWUP_ARMS="${FOLLOWUP_ARMS:-all}"
PY="${PY:-}"

if [[ -z "${PY}" ]]; then
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    PY="${ROOT}/.venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi

if [[ ! -x "${PY}" ]]; then
  echo "error: Python executable not found: ${PY}" >&2
  exit 2
fi
if [[ ! -x "${QUEUE}" ]]; then
  echo "error: comparison queue is not executable: ${QUEUE}" >&2
  exit 2
fi
case "${FOLLOWUP_COHORT}" in
  standard|compat|all) ;;
  *)
    echo "error: FOLLOWUP_COHORT must be standard, compat, or all" >&2
    exit 2
    ;;
esac

if [[ "${FOLLOWUP_ARMS}" != "all" ]]; then
  IFS=',' read -r -a REQUESTED_ARMS <<< "${FOLLOWUP_ARMS}"
  if [[ ${#REQUESTED_ARMS[@]} -eq 0 ]]; then
    echo "error: FOLLOWUP_ARMS must not be empty" >&2
    exit 2
  fi
  for arm in "${REQUESTED_ARMS[@]}"; do
    case "${arm}" in
      repeat-a|repeat-b|only-keywords|only-relevant-paths|only-high-risk-paths|only-risky-words|shuffle-signals) ;;
      *)
        echo "error: unsupported FOLLOWUP_ARMS entry: ${arm}" >&2
        exit 2
        ;;
    esac
  done
fi

require_runtime_generation() {
  if [[ ! -f "${RUNTIME_GATE_FILE}" ]]; then
    echo "waiting for runtime generation: missing completion record ${RUNTIME_GATE_FILE}" >&2
    return 75
  fi
  "${PY}" - "${RUNTIME_GATE_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    record = json.loads(path.read_text())
except (OSError, json.JSONDecodeError) as exc:
    print(f"waiting for runtime generation: unreadable completion record: {exc}", file=sys.stderr)
    raise SystemExit(75)

expected = record.get("expected_cases")
terminal = record.get("terminal_cases")
if (
    record.get("scope") != "master50-runtime-generation"
    or record.get("status") != "complete"
    or expected != 29
    or terminal != expected
):
    print(
        "waiting for runtime generation: expected a complete 29/29 terminal record "
        f"but got scope={record.get('scope')!r} status={record.get('status')!r} "
        f"terminal={terminal!r} expected={expected!r}",
        file=sys.stderr,
    )
    raise SystemExit(75)
PY
}

require_runtime_generation

ISSUES=(
  pr204559 pr204589 pr201444 pr193164 pr50304
  pr50585 pr48154 pr49535 pr52635 pr200987
)
COMPAT_ISSUES=(pr50304 pr50585 pr48154 pr49535 pr52635)
STANDARD_ISSUES=(pr204559 pr204589 pr201444 pr193164 pr200987)

run_arm() {
  local mode="$1"
  local suffix="$2"
  local factor="${3:-}"
  shift 3 || true
  local -a issues=("$@")
  local lane_label="${LANE}-${suffix}"
  local -a env=(
    "ROOT=${ROOT}"
    "PY=${PY}"
    "RUN_ONLINE_MAX_LANES=${RUN_ONLINE_MAX_LANES:-3}"
  )
  if [[ -n "${factor}" ]]; then
    env+=("HEURISTIC_ABLATION_FACTOR=${factor}")
  fi
  if [[ -n "${EXTRA_CMAKE_CXX_FLAGS:-}" ]]; then
    env+=("EXTRA_CMAKE_CXX_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}")
  fi
  env "${env[@]}" bash "${QUEUE}" "${mode}" "${lane_label}" "${issues[@]}"
}

run_standard_arm() {
  if [[ "${FOLLOWUP_COHORT}" == "compat" ]]; then
    return
  fi
  run_arm "$@" "${STANDARD_ISSUES[@]}"
}

run_compat_arm() {
  if [[ "${FOLLOWUP_COHORT}" == "standard" ]]; then
    return
  fi
  EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}" \
    run_arm "$@" "${COMPAT_ISSUES[@]}"
}

arm_requested() {
  local requested="$1"
  local arm
  if [[ "${FOLLOWUP_ARMS}" == "all" ]]; then
    return 0
  fi
  for arm in "${REQUESTED_ARMS[@]}"; do
    if [[ "${arm}" == "${requested}" ]]; then
      return 0
    fi
  done
  return 1
}

# Study 2: the heuristic has no model seed. These independent full reruns test
# runner/build reproducibility and produce a paired per-issue step delta.
if arm_requested repeat-a; then
  run_standard_arm heuristic "repeat-a-standard" ""
  run_compat_arm heuristic "repeat-a-compat" ""
fi
if arm_requested repeat-b; then
  run_standard_arm heuristic "repeat-b-standard" ""
  run_compat_arm heuristic "repeat-b-compat" ""
fi

# Study 4: only-one-signal inclusions isolate the contribution of each semantic
# family. Shuffle is a negative control that preserves signal-count scale but
# removes the issue-to-signal association. Buildability and feedback remain on.
for factor in only-keywords only-relevant-paths only-high-risk-paths only-risky-words shuffle-signals; do
  if ! arm_requested "${factor}"; then
    continue
  fi
  run_standard_arm heuristic-ablation "${factor}-standard" "${factor}"
  run_compat_arm heuristic-ablation-compat "${factor}-compat" "${factor}"
done
