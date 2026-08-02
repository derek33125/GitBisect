#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?mode required: heuristic|general-heuristic|no-keyword-heuristic|neutral-heuristic|oracle-first-bad-heuristic|oracle-major-keyword-heuristic|oracle-major-tuned-keyword-heuristic|oracle-anchor-major-tuned-keyword-heuristic|parent-extract|causal-parent-extract|causal-parent-impl-k12|evidence-diverse-k12|causal-parent-k12|adaptive-parent-extract|confidence-parent-extract|confidence-parent-k12|observation-posterior-parent-extract|observation-posterior-parent-k12|lastdiff-extract}"
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
cd "${ROOT}"
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
MODEL_NAME="${MODEL_NAME:-}"
MODEL_REASONING_EFFORT="${MODEL_REASONING_EFFORT:-}"
export ADAPTIVE_TOP_K_THRESHOLD="${ADAPTIVE_TOP_K_THRESHOLD:-5000}"
export ADAPTIVE_TOP_K_LARGE="${ADAPTIVE_TOP_K_LARGE:-12}"
export ADAPTIVE_TOP_K_SMALL="${ADAPTIVE_TOP_K_SMALL:-3}"
export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-adaptive-${ADAPTIVE_TOP_K_THRESHOLD}-${ADAPTIVE_TOP_K_LARGE}-${ADAPTIVE_TOP_K_SMALL}}"
export CONFIDENCE_FRONTIER_THRESHOLD="${CONFIDENCE_FRONTIER_THRESHOLD:-0.35}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"

MODEL_ARGS=()
if [[ -n "${MODEL_NAME}" ]]; then
  MODEL_ARGS+=(--model-name "${MODEL_NAME}")
fi
if [[ -n "${MODEL_REASONING_EFFORT}" ]]; then
  MODEL_ARGS+=(--model-reasoning-effort "${MODEL_REASONING_EFFORT}")
fi

mkdir -p "${WORK_ROOT}" "${ROOT}/results/issues/server-jobs"
LOG="${ROOT}/results/issues/server-jobs/${LANE}.log"
RESERVATION_DIR="${WORK_ROOT}/.run-online-reservations"
LOCK_FILE="${WORK_ROOT}/.run-online-lane.lock"
RESERVATION_PATH=""
RUN_ISSUE_EXIT_CODE=0
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

oracle_first_bad_commit() {
  local issue="$1"
  case "${issue}" in
    pr204559|pr204589) echo "5a5d0fb1e471b3a1e842aee1f993e885c8d19713" ;;
    pr201444) echo "6bcdd843e302063c4f0d36204686155149a6bb0a" ;;
    pr193164) echo "cac7fe50e0fbedfb14028c170d83386efeb1265b" ;;
    pr50304) echo "c9c05a91c4843c243d508c39bdfbc5e26f311af2" ;;
    pr50585) echo "e38b7e894808ec2a0c976ab01e44364f167508d3" ;;
    pr48154) echo "20e989e9de6abcf9a684978a2688acc4ea01036f" ;;
    pr49535) echo "be20eae25f50f5ef648aeefa1143e1c31e4410fc" ;;
    pr52635) echo "10bc12588dac532fad044b2851dde8e7b9121e88" ;;
    pr200987) echo "329ef60f3e21fd6845e8e8b0da405cae7eb27267" ;;
    *)
      echo "missing canonical first-bad SHA for oracle diagnostic: ${issue}" >&2
      return 2
      ;;
  esac
}

run_bisect_cmd() {
  RUN_ID="${LANE}" "$@" &
  local child_pid=$!
  sleep 5
  release_lane_reservation
  if wait "${child_pid}"; then
    RUN_ISSUE_EXIT_CODE=0
  else
    RUN_ISSUE_EXIT_CODE=$?
  fi
}

run_issue() {
  local issue="$1"
  local bad
  local oracle_bad=""
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
    no-keyword-heuristic)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version none \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    neutral-heuristic)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version neutral \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    oracle-first-bad-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad \
        --oracle-first-bad-sha "${oracle_bad}" \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    oracle-major-keyword-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad-major \
        --oracle-first-bad-sha "${oracle_bad}" \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    oracle-major-tuned-keyword-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad-major-tuned \
        --oracle-first-bad-sha "${oracle_bad}" \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    oracle-anchor-major-tuned-keyword-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad-major-tuned-anchor \
        --oracle-first-bad-sha "${oracle_bad}" \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 1
      ;;
    parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
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
    causal-parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 3 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    evidence-diverse-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier evidence-diverse \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-impl-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-impl \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    adaptive-parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k "${MODEL_TOP_K}" \
        --adaptive-top-k-threshold "${ADAPTIVE_TOP_K_THRESHOLD}" \
        --adaptive-top-k-large "${ADAPTIVE_TOP_K_LARGE}" \
        --adaptive-top-k-small "${ADAPTIVE_TOP_K_SMALL}" \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-frontier topk \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    confidence-parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 3 \
        --model-frontier topk \
        --confidence-adaptive-frontier-threshold "${CONFIDENCE_FRONTIER_THRESHOLD}" \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    confidence-parent-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --confidence-adaptive-frontier-threshold "${CONFIDENCE_FRONTIER_THRESHOLD}" \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    observation-posterior-parent-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 3 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observation-conditioned-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    observation-posterior-parent-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction llm \
        --observation-prompt-mode trace-only \
        --observation-conditioned-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    lastdiff-extract)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        "${MODEL_ARGS[@]}" \
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
  if (( RUN_ISSUE_EXIT_CODE == 0 )); then
    echo "[${LANE}] $(date -Iseconds) done ${issue}" | tee -a "${LOG}"
  else
    echo "[${LANE}] $(date -Iseconds) failed ${issue} exit=${RUN_ISSUE_EXIT_CODE}" | tee -a "${LOG}"
  fi
}

trap release_lane_reservation EXIT

for issue in "${ISSUES[@]}"; do
  wait_for_lane
  RUN_ISSUE_EXIT_CODE=0
  run_issue "${issue}"
  if (( RUN_ISSUE_EXIT_CODE != 0 )); then
    if [[ "${MODE}" == "oracle-anchor-major-tuned-keyword-heuristic" ]]; then
      # A non-bad anchor is a retained diagnostic result, not a reason to drop
      # the remaining independent anchor validations in this queue.
      echo "[${LANE}] $(date -Iseconds) retained failed direct-anchor validation for ${issue} exit=${RUN_ISSUE_EXIT_CODE}" | tee -a "${LOG}"
      continue
    fi
    exit "${RUN_ISSUE_EXIT_CODE}"
  fi
done
