#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: run-ceg-master50-remaining30-queue.sh LANE_PREFIX

Run CEG-Bisect v6 for the 30 master-50 issues outside the completed scoped-10
study and master50-next10 expansion. Exactly three physical queues are used.

Required environment:
  CEG_INPUT_ROOT  Directory containing the endpoint manifest for the selected
                  partition.

Optional environment:
  CEG_QUEUE_PARTITION
                  all30 (default), edu15, or aws15. The 15-case partitions match
                  the distributed capture partitions and may run as soon as that
                  host's validated endpoint bundle is ready.
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
CEG_QUEUE_PARTITION="${CEG_QUEUE_PARTITION:-all30}"

ISSUES=(
  pr121365 pr156249 pr165246
  pr165445 pr167514 pr168912
  pr170421 pr173943 pr190445
  pr194000 pr194590 pr196450
  pr197067 pr198339 pr199162
  pr199526 pr200330 pr200648
  pr201855 pr202003 pr202043
  pr203261 pr203278 pr203519
  pr204178 pr204561 pr205971
  pr206007 pr50655 pr65982
)
LANE_A_ISSUES=(
  pr121365 pr165445 pr170421 pr194000 pr197067
  pr199526 pr201855 pr203261 pr204178 pr206007
)
LANE_B_ISSUES=(
  pr156249 pr167514 pr173943 pr194590 pr198339
  pr200330 pr202003 pr203278 pr204561 pr50655
)
LANE_C_ISSUES=(
  pr165246 pr168912 pr190445 pr196450 pr199162
  pr200648 pr202043 pr203519 pr205971 pr65982
)
declare -A COMPAT=(
  [pr121365]=1
  [pr173943]=1
  [pr204178]=1
)

case "${CEG_QUEUE_PARTITION}" in
  all30)
    ;;
  edu15)
    ISSUES=(
      pr121365 pr170421 pr197067 pr201855 pr204178
      pr156249 pr173943 pr198339 pr202003 pr204561
      pr165246 pr190445 pr199162 pr202043 pr205971
    )
    LANE_A_ISSUES=(pr121365 pr170421 pr197067 pr201855 pr204178)
    LANE_B_ISSUES=(pr156249 pr173943 pr198339 pr202003 pr204561)
    LANE_C_ISSUES=(pr165246 pr190445 pr199162 pr202043 pr205971)
    ;;
  aws15)
    ISSUES=(
      pr165445 pr194000 pr199526 pr203261 pr206007
      pr167514 pr194590 pr200330 pr203278 pr50655
      pr168912 pr196450 pr200648 pr203519 pr65982
    )
    LANE_A_ISSUES=(pr165445 pr194000 pr199526 pr203261 pr206007)
    LANE_B_ISSUES=(pr167514 pr194590 pr200330 pr203278 pr50655)
    LANE_C_ISSUES=(pr168912 pr196450 pr200648 pr203519 pr65982)
    ;;
  *)
    echo "error: unsupported CEG_QUEUE_PARTITION=${CEG_QUEUE_PARTITION}" >&2
    exit 2
    ;;
esac

if [[ ! "${RUN_ONLINE_MAX_LANES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be a positive integer" >&2
  exit 2
fi
if [[ ! "${MIN_FREE_GB}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: MIN_FREE_GB must be a positive integer" >&2
  exit 2
fi
if [[ ! -x "${PY}" || ! -x "${QUEUE}" ]]; then
  echo "error: CEG queue runtime is incomplete" >&2
  exit 2
fi
if [[ ! -f "${CEG_INPUT_ROOT}/manifest.json" ]]; then
  echo "error: CEG input manifest is missing" >&2
  exit 2
fi

"${PY}" - "${CEG_INPUT_ROOT}/manifest.json" \
  "${ROOT}/tools/lm_bisect_profiles.json" "${#ISSUES[@]}" "${ISSUES[@]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
root = manifest_path.parent
manifest = json.loads(manifest_path.read_text())
profiles = json.loads(Path(sys.argv[2]).read_text())
expected_count = int(sys.argv[3])
expected = sys.argv[4:]
cases = manifest.get("cases", [])
actual = [str(case.get("issue", "")) for case in cases if isinstance(case, dict)]
if manifest.get("protocol") != "ceg-bad-endpoint-v1":
    raise SystemExit("CEG endpoint protocol mismatch")
if (
    len(expected) != expected_count
    or len(set(expected)) != expected_count
    or sorted(actual) != sorted(expected)
):
    raise SystemExit(
        "CEG input manifest must match the selected remaining30 partition"
    )
for case in cases:
    if case.get("capture_commit") != profiles[case["issue"]]["bad_commit"]:
        raise SystemExit(f"bad-endpoint commit mismatch for {case['issue']}")
    artifact = root / case["crash_artifact"]
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != case["crash_artifact_sha256"]:
        raise SystemExit(f"crash artifact hash mismatch for {case['issue']}")
    for relative, digest in case["reproducer_sha256"].items():
        path = root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"reproducer hash mismatch for {case['issue']}: {relative}")
PY

mkdir -p "${STATUS_ROOT}"
printf 'started_at=%s\nmax_server_lanes=%s\nceg_physical_lanes=3\nexpected_cases=%s\npartition=%s\nmin_free_gb=%s\n' \
  "$(date -Iseconds)" "${RUN_ONLINE_MAX_LANES}" "${#ISSUES[@]}" \
  "${CEG_QUEUE_PARTITION}" "${MIN_FREE_GB}" > "${STATUS_ROOT}/run.meta"

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

  if case_completed "${status_file}" "${issue}"; then
    return 0
  fi
  if [[ -n "${COMPAT[${issue}]:-}" ]]; then
    mode=causal-evidence-guided-k12-compat
  fi

  wait_for_free_space "${issue}"
  printf '%s\t%s\tstarted\t%s\t%s\n' \
    "$(date -Iseconds)" "${issue}" "${lane}" "${mode}" >> "${status_file}"
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

run_lane() {
  local physical_lane="$1"
  shift
  local failed=0
  local issue
  for issue in "$@"; do
    run_case "${physical_lane}" "${issue}" || failed=1
  done
  return "${failed}"
}

run_lane lane-a "${LANE_A_ISSUES[@]}" > "${STATUS_ROOT}/lane-a.controller.log" 2>&1 &
pid_a=$!
run_lane lane-b "${LANE_B_ISSUES[@]}" > "${STATUS_ROOT}/lane-b.controller.log" 2>&1 &
pid_b=$!
run_lane lane-c "${LANE_C_ISSUES[@]}" > "${STATUS_ROOT}/lane-c.controller.log" 2>&1 &
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
