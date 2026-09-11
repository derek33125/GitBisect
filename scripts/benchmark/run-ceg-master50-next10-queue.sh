#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: run-ceg-master50-next10-queue.sh LANE_PREFIX

Run the leakage-controlled master-50 CEG expansion on exactly three physical
queues. Each queue runs its assigned issues sequentially and continues after
an individual issue failure.

Required environment:
  CEG_INPUT_ROOT  Directory containing the exact ten-case CEG manifest.
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

LANE_PREFIX="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
PY="${PY:-${ROOT}/.venv/bin/python}"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-$(dirname "${BASE_REPO}")/worktrees}"
RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-6}"
CEG_INPUT_ROOT="${CEG_INPUT_ROOT:?CEG_INPUT_ROOT is required}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${LANE_PREFIX}-controller"
MIN_FREE_GB="${MIN_FREE_GB:-80}"
CEG_KEEP_ISSUE_CACHE="${CEG_KEEP_ISSUE_CACHE:-0}"

ISSUES=(
  pr165039 pr197797 pr120802
  pr195788 pr196244 pr172195
  pr198257 pr200742 pr192829 pr202343
)

if [[ ! "${RUN_ONLINE_MAX_LANES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be a positive integer" >&2
  exit 2
fi
if [[ ! "${MIN_FREE_GB}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: MIN_FREE_GB must be a positive integer" >&2
  exit 2
fi
if [[ ! -x "${PY}" ]]; then
  echo "error: Python executable not found: ${PY}" >&2
  exit 2
fi
if [[ ! -x "${QUEUE}" ]]; then
  echo "error: comparison queue is not executable: ${QUEUE}" >&2
  exit 2
fi
if [[ ! -f "${CEG_INPUT_ROOT}/manifest.json" ]]; then
  echo "error: CEG input manifest is missing: ${CEG_INPUT_ROOT}/manifest.json" >&2
  exit 2
fi

"${PY}" - "${CEG_INPUT_ROOT}/manifest.json" "${ISSUES[@]}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
expected = sys.argv[2:]
actual = [
    str(case.get("issue", ""))
    for case in manifest.get("cases", [])
    if isinstance(case, dict)
]
if len(expected) != 10 or len(set(expected)) != 10:
    raise SystemExit("CEG expansion must define exactly ten unique issues")
if actual != expected:
    raise SystemExit(
        "CEG input manifest must contain the exact queue cohort in queue order; "
        f"expected={expected!r} actual={actual!r}"
    )
PY

mkdir -p "${STATUS_ROOT}"
printf 'started_at=%s\nmax_server_lanes=%s\nceg_physical_lanes=3\nmin_free_gb=%s\n' \
  "$(date -Iseconds)" "${RUN_ONLINE_MAX_LANES}" "${MIN_FREE_GB}" > "${STATUS_ROOT}/run.meta"

wait_for_free_space() {
  local issue="$1"
  local available_kb
  local required_kb=$((MIN_FREE_GB * 1024 * 1024))
  while true; do
    available_kb="$(df -Pk "${WORK_ROOT}" | awk 'NR == 2 {print $4}')"
    if [[ "${available_kb}" =~ ^[0-9]+$ ]] && (( available_kb >= required_kb )); then
      return 0
    fi
    printf '%s\t%s\twaiting-for-disk\tavailable_kb=%s\trequired_kb=%s\n' \
      "$(date -Iseconds)" "${issue}" "${available_kb:-unknown}" "${required_kb}" \
      >> "${STATUS_ROOT}/disk.status"
    sleep 300
  done
}

cleanup_issue_cache() {
  local issue="$1"
  if [[ "${CEG_KEEP_ISSUE_CACHE}" == "1" ]]; then
    return
  fi
  rm -rf \
    "${ROOT}/.ccache/${issue}" \
    "${ROOT}/.ccache/${issue}-git-bisect"
}

case_completed() {
  local status_file="$1"
  local issue="$2"
  awk -F '\t' -v issue="${issue}" \
    '$2 == issue && $3 == "terminal" && $4 == "0" {found=1} END {exit !found}' \
    "${status_file}" 2>/dev/null
}

run_case() {
  local physical_lane="$1"
  local issue="$2"
  local lane="${LANE_PREFIX}-${physical_lane}"
  local status_file="${STATUS_ROOT}/${physical_lane}.status"
  local mode=causal-evidence-guided-k12
  local case_exit=0

  if [[ "${issue}" == "pr120802" ]]; then
    mode=causal-evidence-guided-k12-compat
  fi
  if case_completed "${status_file}" "${issue}"; then
    return 0
  fi

  wait_for_free_space "${issue}"
  printf '%s\t%s\tstarted\t%s\n' \
    "$(date -Iseconds)" "${issue}" "${lane}" >> "${status_file}"
  if env \
    ROOT="${ROOT}" \
    PY="${PY}" \
    RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES}" \
    CEG_INPUT_ROOT="${CEG_INPUT_ROOT}" \
    bash "${QUEUE}" "${mode}" "${lane}" "${issue}"; then
    printf '%s\t%s\tterminal\t0\n' \
      "$(date -Iseconds)" "${issue}" >> "${status_file}"
  else
    local exit_code=$?
    case_exit="${exit_code}"
    printf '%s\t%s\tterminal\t%s\n' \
      "$(date -Iseconds)" "${issue}" "${exit_code}" >> "${status_file}"
  fi
  cleanup_issue_cache "${issue}"
  return "${case_exit}"
}

lane_a() {
  local failed=0
  run_case lane-a pr165039 || failed=1
  run_case lane-a pr195788 || failed=1
  run_case lane-a pr198257 || failed=1
  return "${failed}"
}

lane_b() {
  local failed=0
  run_case lane-b pr197797 || failed=1
  run_case lane-b pr196244 || failed=1
  run_case lane-b pr200742 || failed=1
  return "${failed}"
}

lane_c() {
  local failed=0
  run_case lane-c pr120802 || failed=1
  run_case lane-c pr172195 || failed=1
  run_case lane-c pr192829 || failed=1
  run_case lane-c pr202343 || failed=1
  return "${failed}"
}

lane_a > "${STATUS_ROOT}/lane-a.controller.log" 2>&1 &
pid_a=$!
lane_b > "${STATUS_ROOT}/lane-b.controller.log" 2>&1 &
pid_b=$!
lane_c > "${STATUS_ROOT}/lane-c.controller.log" 2>&1 &
pid_c=$!

printf 'lane_a_pid=%s\nlane_b_pid=%s\nlane_c_pid=%s\n' \
  "${pid_a}" "${pid_b}" "${pid_c}" >> "${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
wait "${pid_b}" || exit_code=1
wait "${pid_c}" || exit_code=1

printf 'finished_at=%s\nexit_code=%s\n' \
  "$(date -Iseconds)" "${exit_code}" >> "${STATUS_ROOT}/run.meta"
exit "${exit_code}"
