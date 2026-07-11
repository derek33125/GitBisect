#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?mode required: heuristic|general-heuristic|parent-extract|lastdiff-extract}"
LANE="${2:?lane label required}"
shift 2
ISSUES=("$@")
if [[ ${#ISSUES[@]} -eq 0 ]]; then
  echo "error: provide at least one issue" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ -z "${BASE_REPO:-}" ]]; then
  if [[ -d /home/ubuntu/gitbisect-work/llvm-project ]]; then
    BASE_REPO=/home/ubuntu/gitbisect-work/llvm-project
  elif [[ -d /home/derek/gitbisect-work/llvm-project ]]; then
    BASE_REPO=/home/derek/gitbisect-work/llvm-project
  else
    BASE_REPO="${ROOT}/../gitbisect-work/llvm-project"
  fi
fi
WORK_ROOT="${WORK_ROOT:-$(dirname "${BASE_REPO}")/worktrees}"
PY="${PY:-${ROOT}/.venv/bin/python}"
export ROOT BASE_REPO WORK_ROOT
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS}}"
export MODEL_TOP_K="${MODEL_TOP_K:-2000}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"

mkdir -p "${WORK_ROOT}" "${ROOT}/results/issues/server-jobs"
LOG="${ROOT}/results/issues/server-jobs/${LANE}.log"
RESERVATION_DIR="${WORK_ROOT}/.run-online-reservations"
LOCK_FILE="${WORK_ROOT}/.run-online-lane.lock"
RESERVATION_PATH=""
mkdir -p "${RESERVATION_DIR}"

active_run_online_lanes() {
  ps -eo args= \
    | awk '/tools\/lm_bisect.py run-online/ && !/run-comparison-queue-20260630/ {count++} END {print count + 0}'
}

active_lane_reservations() {
  find "${RESERVATION_DIR}" -type f -name '*.slot' 2>/dev/null | wc -l
}

prune_stale_reservations() {
  find "${RESERVATION_DIR}" -type f -name '*.slot' -mmin +10 -delete 2>/dev/null || true
}

release_lane_reservation() {
  if [[ -n "${RESERVATION_PATH}" ]]; then
    rm -f "${RESERVATION_PATH}"
    RESERVATION_PATH=""
  fi
}

wait_for_lane() {
  while true; do
    local active
    local reserved
    active="$(active_run_online_lanes)"
    exec 9>"${LOCK_FILE}"
    flock -x 9
    prune_stale_reservations
    reserved="$(active_lane_reservations)"
    if (( active + reserved < 3 )); then
      RESERVATION_PATH="${RESERVATION_DIR}/${LANE}-$$-$(date +%s).slot"
      printf 'lane=%s\npid=%s\ncreated=%s\n' "${LANE}" "$$" "$(date -Iseconds)" > "${RESERVATION_PATH}"
      flock -u 9
      exec 9>&-
      break
    fi
    flock -u 9
    exec 9>&-
    echo "[${LANE}] $(date -Iseconds) waiting: ${active} run-online lanes active, ${reserved} startup reservations" | tee -a "${LOG}"
    sleep 600
  done
}

profile_bad_commit() {
  local issue="$1"
  "${PY}" - "$ROOT" "$issue" <<'PY'
import json
import sys

root, issue = sys.argv[1], sys.argv[2]
profiles = json.load(open(f"{root}/tools/lm_bisect_profiles.json"))
print(profiles[issue]["bad_commit"])
PY
}

run_bisect_cmd() {
  RUN_ID="${LANE}" "$@" &
  local child_pid=$!
  sleep 5
  release_lane_reservation
  wait "${child_pid}"
}

run_issue() {
  local issue="$1"
  local bad
  bad="$(profile_bad_commit "${issue}")"
  local wt="${WORK_ROOT}/${issue}-${LANE}"
  local obs="results/lm_bisect_observations/${issue}-${LANE}.json"

  echo "[${LANE}] $(date -Iseconds) prepare ${issue} bad=${bad}" | tee -a "${LOG}"
  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || true
  rm -rf "${wt}"
  git -C "${BASE_REPO}" worktree add --detach "${wt}" "${bad}"

  case "${MODE}" in
    heuristic)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    general-heuristic)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version general \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --search-policy calibrated-posterior \
        --model-top-k "${MODEL_TOP_K}" \
        --model-frontier topk \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    lastdiff-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --search-policy calibrated-posterior \
        --model-top-k "${MODEL_TOP_K}" \
        --model-frontier topk \
        --model-diff-mode last-tested \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    *)
      echo "unknown mode: ${MODE}" >&2
      exit 2
      ;;
  esac
  echo "[${LANE}] $(date -Iseconds) done ${issue}" | tee -a "${LOG}"
}

trap release_lane_reservation EXIT

for issue in "${ISSUES[@]}"; do
  wait_for_lane
  run_issue "${issue}"
done
