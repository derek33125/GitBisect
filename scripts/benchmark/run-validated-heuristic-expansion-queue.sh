#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-validated-heuristic-expansion-queue.sh [--heuristic-version tuned|general] LANE ISSUE [ISSUE...]

Waits for one of the three remote run-online slots, revalidates the recorded
good/bad endpoints, and runs the selected heuristic control only when the
preflight still reports a usable interval. `tuned` is the issue-specific
control; `general` is the weak-general control.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

HEURISTIC_VERSION="${HEURISTIC_VERSION:-tuned}"
if [[ "${1:-}" == "--heuristic-version" ]]; then
  HEURISTIC_VERSION="${2:-}"
  shift 2
fi
case "${HEURISTIC_VERSION}" in
  tuned|general) ;;
  *)
    echo "error: unsupported heuristic version: ${HEURISTIC_VERSION}" >&2
    exit 2
    ;;
esac

if (( $# < 2 )); then
  usage
  exit 0
fi

LANE="$1"
shift
ISSUES=("$@")
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
BASE_REPO="${BASE_REPO:-${ROOT}/../llvm-project}"
WORK_ROOT="${WORK_ROOT:-$(dirname "${BASE_REPO}")/worktrees}"
PY="${PY:-${ROOT}/.venv/bin/python}"
LOG="${ROOT}/results/issues/server-jobs/${LANE}.log"
RESERVATION_DIR="${WORK_ROOT}/.run-online-reservations"
LOCK_FILE="${WORK_ROOT}/.run-online-lane.lock"
RESERVATION_PATH=""

export ROOT BASE_REPO WORK_ROOT
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS}}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"
RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}"

if [[ ! "${RUN_ONLINE_MAX_LANES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be a positive integer" >&2
  exit 2
fi

if [[ ! -x "${PY}" ]]; then
  echo "error: Python executable not found: ${PY}" >&2
  exit 2
fi
if ! git -C "${BASE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: LLVM repository not found: ${BASE_REPO}" >&2
  exit 2
fi

mkdir -p "${WORK_ROOT}" "${RESERVATION_DIR}" "$(dirname "${LOG}")"

active_run_online_pids() {
  ps -eo pid=,args= \
    | awk '/tools\/lm_bisect.py run-online/ && !/run-validated-heuristic-expansion-queue/ {print $1}'
}

active_lane_reservations() {
  find "${RESERVATION_DIR}" -type f -name '*.slot' 2>/dev/null | wc -l
}

reclaim_dead_lane_reservations() {
  local reservation pid
  while IFS= read -r reservation; do
    pid="$(awk -F= '$1 == "pid" {print $2; exit}' "${reservation}" 2>/dev/null || true)"
    if [[ -z "${pid}" || ! "${pid}" =~ ^[0-9]+$ ]] || ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${reservation}"
    fi
  done < <(find "${RESERVATION_DIR}" -type f -name '*.slot' -print 2>/dev/null)
}

release_lane_reservation() {
  if [[ -n "${RESERVATION_PATH}" ]]; then
    rm -f "${RESERVATION_PATH}"
    RESERVATION_PATH=""
  fi
}

reservation_owns_process() {
  local process_pid="$1"
  local current_pid="${process_pid}"
  local parent_pid reservation_pid

  # A controller retains its reservation while its preflight and run-online
  # child execute. Walk the child ancestry so the pair consumes one lane.
  while [[ -n "${current_pid}" && "${current_pid}" != "1" ]]; do
    while IFS= read -r reservation_pid; do
      if [[ "${current_pid}" == "${reservation_pid}" ]]; then
        return 0
      fi
    done < <(awk -F= '$1 == "pid" {print $2}' "${RESERVATION_DIR}"/*.slot 2>/dev/null)

    parent_pid="$(ps -o ppid= -p "${current_pid}" 2>/dev/null | tr -d '[:space:]')"
    if [[ -z "${parent_pid}" || "${parent_pid}" == "${current_pid}" ]]; then
      return 1
    fi
    current_pid="${parent_pid}"
  done
  return 1
}

active_unreserved_run_online_lanes() {
  local process_pid
  local count=0

  while IFS= read -r process_pid; do
    if ! reservation_owns_process "${process_pid}"; then
      ((count += 1))
    fi
  done < <(active_run_online_pids)
  printf '%s\n' "${count}"
}

acquire_lane_reservation() {
  while true; do
    local unreserved reserved
    exec 9>"${LOCK_FILE}"
    flock -x 9
    # Endpoint preflight can take longer than ten minutes. A reservation lives
    # until its owning controller exits, not until a wall-clock timeout.
    reclaim_dead_lane_reservations
    reserved="$(active_lane_reservations)"
    unreserved="$(active_unreserved_run_online_lanes)"
    if (( unreserved + reserved < RUN_ONLINE_MAX_LANES )); then
      RESERVATION_PATH="${RESERVATION_DIR}/${LANE}-$$-$(date +%s).slot"
      printf 'lane=%s\npid=%s\ncreated=%s\n' "${LANE}" "$$" "$(date -Iseconds)" >"${RESERVATION_PATH}"
      flock -u 9
      exec 9>&-
      return
    fi
    flock -u 9
    exec 9>&-
    echo "[${LANE}] $(date -Iseconds) waiting: ${unreserved} unreserved run-online lanes active, ${reserved} controller reservations" | tee -a "${LOG}"
    sleep 600
  done
}

preflight_issue() {
  local issue="$1"
  local run_id="${LANE}-profile-preflight-${issue}"
  local issue_dir="${ROOT}/results/issues/${issue}"
  local summary="${issue_dir}/${issue}-profile-endpoint-preflight-${run_id}.tsv"
  local preflight_log="${issue_dir}/${issue}-profile-endpoint-preflight-${run_id}.log"
  local good bad runner wt good_rc bad_rc

  echo "[${LANE}] $(date -Iseconds) preflight ${issue}" | tee -a "${LOG}"
  if ! read -r good bad runner < <("${PY}" - "${ROOT}" "${issue}" <<'PY'
import json
import sys

root, issue = sys.argv[1:]
profile = json.load(open(f"{root}/tools/lm_bisect_profiles.json"))[issue]
print(profile["good_commit"], profile["bad_commit"], profile["runner"])
PY
  ); then
    echo "[${LANE}] missing profile for ${issue}" | tee -a "${LOG}"
    return 1
  fi

  runner="${ROOT}/${runner}"
  wt="${WORK_ROOT}/${issue}-${run_id}"
  mkdir -p "${issue_dir}"
  printf 'issue\tgood_commit\tgood_rc\tbad_commit\tbad_rc\tusable_state\tnote\n' >"${summary}"

  if [[ ! -x "${runner}" ]]; then
    printf '%s\t%s\tNA\t%s\tNA\tinvalid\trunner_missing\n' "${issue}" "${good}" "${bad}" >>"${summary}"
    echo "[${LANE}] missing runner: ${runner}" | tee -a "${LOG}"
    return 1
  fi

  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || true
  rm -rf "${wt}"
  if ! git -C "${BASE_REPO}" worktree add --detach "${wt}" "${bad}" >>"${preflight_log}" 2>&1; then
    printf '%s\t%s\tNA\t%s\tNA\tinvalid\tworktree_create_failed\n' "${issue}" "${good}" "${bad}" >>"${summary}"
    cleanup_preflight_worktree "${wt}"
    return 1
  fi

  if ! git -C "${wt}" checkout -q "${good}" >>"${preflight_log}" 2>&1; then
    printf '%s\t%s\tNA\t%s\tNA\tinvalid\tgood_checkout_failed\n' "${issue}" "${good}" "${bad}" >>"${summary}"
    cleanup_preflight_worktree "${wt}"
    return 1
  fi
  set +e
  ROOT="${ROOT}" BASE_REPO="${wt}" WORK_ROOT="${WORK_ROOT}" RUN_ID="${run_id}-good" "${runner}" "${wt}" >>"${preflight_log}" 2>&1
  good_rc=$?
  set -e

  if ! git -C "${wt}" checkout -q "${bad}" >>"${preflight_log}" 2>&1; then
    printf '%s\t%s\t%s\t%s\tNA\tinvalid\tbad_checkout_failed\n' "${issue}" "${good}" "${good_rc}" "${bad}" >>"${summary}"
    cleanup_preflight_worktree "${wt}"
    return 1
  fi
  set +e
  ROOT="${ROOT}" BASE_REPO="${wt}" WORK_ROOT="${WORK_ROOT}" RUN_ID="${run_id}-bad" "${runner}" "${wt}" >>"${preflight_log}" 2>&1
  bad_rc=$?
  set -e

  if [[ "${good_rc}" -eq 0 && "${bad_rc}" -eq 1 ]]; then
    printf '%s\t%s\t%s\t%s\t%s\tusable\tprofile_runner_endpoints_validated\n' "${issue}" "${good}" "${good_rc}" "${bad}" "${bad_rc}" >>"${summary}"
    cleanup_preflight_worktree "${wt}"
    return 0
  fi

  printf '%s\t%s\t%s\t%s\t%s\tinvalid\tendpoint_verdict_mismatch\n' "${issue}" "${good}" "${good_rc}" "${bad}" "${bad_rc}" >>"${summary}"
  cleanup_preflight_worktree "${wt}"
  return 1
}

cleanup_preflight_worktree() {
  local wt="$1"

  # Endpoint preflight writes durable TSV/log artifacts beneath results/. Reclaim
  # only its temporary checkout and build output after endpoint classification.
  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || true
  rm -rf "${wt}"
}

run_heuristic() {
  local issue="$1"
  local bad wt observations
  bad="$("${PY}" - "${ROOT}" "${issue}" <<'PY'
import json
import sys

root, issue = sys.argv[1:]
print(json.load(open(f"{root}/tools/lm_bisect_profiles.json"))[issue]["bad_commit"])
PY
)"
  wt="${WORK_ROOT}/${issue}-${LANE}"
  observations="results/lm_bisect_observations/${issue}-${LANE}.json"

  echo "[${LANE}] $(date -Iseconds) heuristic ${issue} bad=${bad}" | tee -a "${LOG}"
  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || true
  rm -rf "${wt}"
  git -C "${BASE_REPO}" worktree add --detach "${wt}" "${bad}" >>"${LOG}" 2>&1
  if RUN_ID="${LANE}" "${PY}" "${ROOT}/tools/lm_bisect.py" run-online \
    --issue "${issue}" \
    --llvm-dir "${wt}" \
    --scorer heuristic \
    --heuristic-version "${HEURISTIC_VERSION}" \
    --search-policy calibrated-posterior \
    --observations "${observations}" \
    --run-label "${LANE}" \
    --max-steps 30 >>"${LOG}" 2>&1; then
    return 0
  fi
  return $?
}

cleanup_issue_worktree() {
  local issue="$1"
  local wt="${WORK_ROOT}/${issue}-${LANE}"

  # Results are outside the worktree. Remove only generated checkout/build data
  # after the runner has exited so a long queue cannot consume the server disk.
  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || true
  rm -rf "${wt}"
}

trap release_lane_reservation EXIT

for issue in "${ISSUES[@]}"; do
  acquire_lane_reservation
  if preflight_issue "${issue}"; then
    if run_heuristic "${issue}"; then
      echo "[${LANE}] $(date -Iseconds) done ${issue}" | tee -a "${LOG}"
    else
      echo "[${LANE}] $(date -Iseconds) failed ${issue}; preserving artifacts and continuing" | tee -a "${LOG}"
    fi
    cleanup_issue_worktree "${issue}"
  else
    echo "[${LANE}] $(date -Iseconds) skip heuristic ${issue}: preflight not usable" | tee -a "${LOG}"
  fi
  release_lane_reservation
done
