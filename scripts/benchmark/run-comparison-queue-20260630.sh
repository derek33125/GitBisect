#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?mode required: heuristic|heuristic-ablation|heuristic-ablation-compat|general-heuristic|no-keyword-heuristic|neutral-heuristic|oracle-first-bad-heuristic|oracle-major-keyword-heuristic|oracle-major-tuned-keyword-heuristic|oracle-major-tuned-semantic-heuristic|oracle-major-tuned-patch-heuristic|oracle-anchor-major-tuned-keyword-heuristic|parent-extract|causal-parent-extract|causal-parent-impl-k12|causal-parent-human-k12|causal-parent-human-pool-k3|causal-parent-human-pool-k3-compat|causal-parent-human-prior-k3|causal-parent-human-prior-k3-compat|causal-parent-human-frontier|causal-parent-human-frontier-compat|causal-parent-human-dynamic-k12|causal-parent-human-dynamic-k12-compat|causal-parent-crash-aware-k12|causal-parent-crash-aware-k12-compat|causal-parent-deterministic-facts-k12|causal-parent-deterministic-facts-artifact-k12|causal-parent-deterministic-facts-artifact-k12-compat|causal-parent-deterministic-facts-artifact-range-k12|causal-parent-deterministic-facts-artifact-range-k12-compat|causal-evidence-guided-k12|causal-evidence-guided-k12-compat|evidence-diverse-k12|causal-parent-k12|terra-causal-parent-range2-k12|terra-causal-parent-range2-k12-compat|terra-causal-parent-range4-k12|terra-causal-parent-range4-k12-compat|terra-causal-parent-range-k12|terra-causal-parent-range-k12-compat|terra-causal-parent-expanded-window3-k12|terra-causal-parent-expanded-window3-k12-compat|adaptive-parent-extract|confidence-parent-extract|confidence-parent-k12|observation-posterior-parent-extract|observation-posterior-parent-k12|lastdiff-extract}"
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
export STARTUP_GRACE_SECONDS="${STARTUP_GRACE_SECONDS:-5}"
export MODEL_TOP_K="${MODEL_TOP_K:-2000}"
MODEL_NAME="${MODEL_NAME:-}"
MODEL_REASONING_EFFORT="${MODEL_REASONING_EFFORT:-}"
export ADAPTIVE_TOP_K_THRESHOLD="${ADAPTIVE_TOP_K_THRESHOLD:-5000}"
export ADAPTIVE_TOP_K_LARGE="${ADAPTIVE_TOP_K_LARGE:-12}"
export ADAPTIVE_TOP_K_SMALL="${ADAPTIVE_TOP_K_SMALL:-3}"

default_model_cache_namespace() {
  # Keep cache entries isolated by experiment semantics.  Callers can still
  # provide MODEL_CACHE_NAMESPACE explicitly to reproduce an intentional run.
  case "${MODE}" in
    terra-causal-parent-range2-k12) echo "terra-bcr-parent-range2-k12" ;;
    terra-causal-parent-range2-k12-compat) echo "terra-bcr-parent-range2-k12-compat" ;;
    terra-causal-parent-range4-k12) echo "terra-bcr-parent-range4-k12" ;;
    terra-causal-parent-range4-k12-compat) echo "terra-bcr-parent-range4-k12-compat" ;;
    terra-causal-parent-range-k12) echo "terra-bcr-parent-range5-k12" ;;
    terra-causal-parent-range-k12-compat) echo "terra-bcr-parent-range5-k12-compat" ;;
    terra-causal-parent-expanded-window3-k12) echo "terra-bcr-expanded-evidence-window3-k12" ;;
    terra-causal-parent-expanded-window3-k12-compat) echo "terra-bcr-expanded-evidence-window3-k12-compat" ;;
    causal-parent-human-k12) echo "terra-bcr-human-crash-v1-k12" ;;
    causal-parent-human-pool-k3) echo "terra-human-signal-pool-v1-k3" ;;
    causal-parent-human-pool-k3-compat) echo "terra-human-signal-pool-v1-k3-compat" ;;
    causal-parent-human-prior-k3) echo "terra-human-soft-prior-v1-k3" ;;
    causal-parent-human-prior-k3-compat) echo "terra-human-soft-prior-v1-k3-compat" ;;
    causal-parent-human-frontier) echo "terra-human-staged-frontier-v1" ;;
    causal-parent-human-frontier-compat) echo "terra-human-staged-frontier-v1-compat" ;;
    causal-parent-human-dynamic-k12) echo "terra-human-dynamic-evidence-v4-k12" ;;
    causal-parent-human-dynamic-k12-compat) echo "terra-human-dynamic-evidence-v4-k12-compat" ;;
    causal-parent-crash-aware-k12) echo "terra-bcr-crash-aware-v12-k12" ;;
    causal-parent-crash-aware-k12-compat) echo "terra-bcr-crash-aware-v12-k12-compat" ;;
    causal-parent-deterministic-facts-k12) echo "terra-bcr-deterministic-facts-v15-k12" ;;
    causal-parent-deterministic-facts-artifact-k12) echo "terra-bcr-deterministic-facts-v16-artifacts-k12" ;;
    causal-parent-deterministic-facts-artifact-k12-compat) echo "terra-bcr-deterministic-facts-v16-artifacts-k12-compat" ;;
    causal-parent-deterministic-facts-artifact-range-k12) echo "terra-bcr-deterministic-facts-v16-artifacts-range5-k12" ;;
    causal-parent-deterministic-facts-artifact-range-k12-compat) echo "terra-bcr-deterministic-facts-v16-artifacts-range5-k12-compat" ;;
    causal-evidence-guided-k12) echo "terra-ceg-bisect-v6-bad-endpoint-merged-selector-range5-k12" ;;
    causal-evidence-guided-k12-compat) echo "terra-ceg-bisect-v6-bad-endpoint-merged-selector-range5-k12-compat" ;;
    *) echo "adaptive-${ADAPTIVE_TOP_K_THRESHOLD}-${ADAPTIVE_TOP_K_LARGE}-${ADAPTIVE_TOP_K_SMALL}" ;;
  esac
}

export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-$(default_model_cache_namespace)}"
export CONFIDENCE_FRONTIER_THRESHOLD="${CONFIDENCE_FRONTIER_THRESHOLD:-0.35}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"
RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}"
HEURISTIC_ABLATION_FACTOR="${HEURISTIC_ABLATION_FACTOR:-}"

if [[ ! "${RUN_ONLINE_MAX_LANES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be a positive integer" >&2
  exit 2
fi

if [[ "${MODE}" == "heuristic-ablation" || "${MODE}" == "heuristic-ablation-compat" ]]; then
  if [[ -z "${HEURISTIC_ABLATION_FACTOR}" ]]; then
    echo "error: HEURISTIC_ABLATION_FACTOR is required for heuristic-ablation mode" >&2
    exit 2
  fi
  case "${HEURISTIC_ABLATION_FACTOR}" in
    keywords|relevant-paths|high-risk-paths|risky-words|buildability|feedback|only-keywords|only-relevant-paths|only-high-risk-paths|only-risky-words|shuffle-signals) ;;
    *)
      echo "error: unsupported heuristic ablation factor: ${HEURISTIC_ABLATION_FACTOR}" >&2
      exit 2
      ;;
  esac
fi

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
CURRENT_WORKTREE=""
mkdir -p "${RESERVATION_DIR}"

active_run_online_pids() {
  ps -eo pid=,args= \
    | awk '$2 ~ /(^|\/)python([0-9.]*)?$/ && $3 == "tools/lm_bisect.py" && $4 == "run-online" {print $1}'
}

active_lane_reservations() {
  find "${RESERVATION_DIR}" -type f -name '*.slot' 2>/dev/null | wc -l
}

reclaim_dead_lane_reservations() {
  local reservation
  local pid
  local owner
  while IFS= read -r reservation; do
    pid="$(awk -F= '$1 == "pid" {print $2; exit}' "${reservation}" 2>/dev/null || true)"
    owner="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    case "${owner}" in
      *run-comparison-queue-20260630.sh*|*run-validated-heuristic-expansion-queue.sh*)
        ;;
      *)
        rm -f "${reservation}"
        ;;
    esac
  done < <(find "${RESERVATION_DIR}" -type f -name '*.slot' -print 2>/dev/null)
}

release_lane_reservation() {
  if [[ -n "${RESERVATION_PATH}" ]]; then
    rm -f "${RESERVATION_PATH}"
    RESERVATION_PATH=""
  fi
}

cleanup_issue_worktree() {
  local wt="${1:-}"
  if [[ -z "${wt}" || ! -e "${wt}" ]]; then
    return
  fi

  # Results and observations are written under ROOT before this runs. The
  # detached source/build tree is disposable and must not accumulate across a
  # long server queue.
  git -C "${BASE_REPO}" worktree remove --force "${wt}" >/dev/null 2>&1 || rm -rf "${wt}"
  if [[ "${CURRENT_WORKTREE}" == "${wt}" ]]; then
    CURRENT_WORKTREE=""
  fi
}

reservation_owns_process() {
  local process_pid="$1"
  local current_pid="${process_pid}"
  local parent_pid reservation_pid

  # A controller retains its reservation while its own run-online child
  # executes. Count that controller-child pair as one physical lane.
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

wait_for_lane() {
  while true; do
    local unreserved
    local reserved
    exec 9>"${LOCK_FILE}"
    flock -x 9
    reclaim_dead_lane_reservations
    unreserved="$(active_unreserved_run_online_lanes)"
    reserved="$(active_lane_reservations)"
    if (( unreserved + reserved < RUN_ONLINE_MAX_LANES )); then
      RESERVATION_PATH="${RESERVATION_DIR}/${LANE}-$$-$(date +%s).slot"
      printf 'lane=%s\npid=%s\ncreated=%s\n' "${LANE}" "$$" "$(date -Iseconds)" > "${RESERVATION_PATH}"
      flock -u 9
      exec 9>&-
      break
    fi
    flock -u 9
    exec 9>&-
    echo "[${LANE}] $(date -Iseconds) waiting: ${unreserved} unreserved run-online lanes active, ${reserved} controller reservations" | tee -a "${LOG}"
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

profile_runner() {
  local issue="$1"
  "${PY}" - "$ROOT" "$issue" <<'PY'
import json
import sys

root, issue = sys.argv[1], sys.argv[2]
profiles = json.load(open(f"{root}/tools/lm_bisect_profiles.json"))
print(profiles[issue]["runner"])
PY
}

preflight_issue_runner() {
  local issue="$1"
  local runner
  runner="$(profile_runner "${issue}")"
  if [[ ! -x "${PY}" ]]; then
    echo "error: runner bundle missing Python interpreter: ${PY}" >&2
    return 125
  fi
  if [[ ! -f "${ROOT}/results/issues/server-jobs/server-validation-queue-20260613.sh" ]]; then
    echo "error: runner bundle missing shared queue library" >&2
    return 125
  fi
  if [[ ! -x "${ROOT}/${runner}" ]]; then
    echo "error: runner bundle missing issue runner for ${issue}: ${ROOT}/${runner}" >&2
    return 125
  fi
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
  sleep "${STARTUP_GRACE_SECONDS}"
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
  preflight_issue_runner "${issue}"
  bad="$(profile_bad_commit "${issue}")"
  local wt="${WORK_ROOT}/${issue}-${LANE}"
  local obs="results/lm_bisect_observations/${issue}-${LANE}.json"
  CURRENT_WORKTREE="${wt}"

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
    heuristic-ablation|heuristic-ablation-compat)
      if [[ "${MODE}" == "heuristic-ablation-compat" ]]; then
        # Historical LLVM revisions need this C++-only include. Do not export
        # it for ordinary lanes because CMake's C compiler probe must stay C.
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version tuned \
        --heuristic-ablation "${HEURISTIC_ABLATION_FACTOR}" \
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
    oracle-major-tuned-semantic-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad-major-tuned-semantic \
        --oracle-first-bad-sha "${oracle_bad}" \
        --search-policy calibrated-posterior \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    oracle-major-tuned-patch-heuristic)
      oracle_bad="$(oracle_first_bad_commit "${issue}")"
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer heuristic \
        --heuristic-version oracle-first-bad-major-tuned-patch \
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
    terra-causal-parent-range2-k12|terra-causal-parent-range2-k12-compat|terra-causal-parent-range4-k12|terra-causal-parent-range4-k12-compat|terra-causal-parent-range-k12|terra-causal-parent-range-k12-compat)
      case "${MODE}" in
        terra-causal-parent-range2-k12|terra-causal-parent-range2-k12-compat)
          CAUSAL_PARENT_COUNT=2
          ;;
        terra-causal-parent-range4-k12|terra-causal-parent-range4-k12-compat)
          CAUSAL_PARENT_COUNT=4
          ;;
        *)
          CAUSAL_PARENT_COUNT=5
          ;;
      esac
      if [[ "${MODE}" == *-compat ]]; then
        # Old LLVM revisions need <cstdint> for Signals.h. Keep this C++-only
        # flag isolated so it does not break CMake's C compiler probe.
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm \
        --causal-context-parent-count "${CAUSAL_PARENT_COUNT}" \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    terra-causal-parent-expanded-window3-k12|terra-causal-parent-expanded-window3-k12-compat)
      if [[ "${MODE}" == *-compat ]]; then
        # Old LLVM revisions need <cstdint> for Signals.h. Keep this C++-only
        # flag isolated so it does not break CMake's C compiler probe.
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-expanded-evidence \
        --causal-context-parent-count 3 \
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
    causal-parent-human-k12)
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-bcr-human-crash-v1-k12}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-human \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-human-pool-k3|causal-parent-human-pool-k3-compat)
      # Retrospective proof pilot: only six crash-signal cases selected from
      # the human study. The runner preserves full-interval BCR fallback.
      if [[ "${MODE}" == "causal-parent-human-pool-k3-compat" ]]; then
        # Old LLVM revisions omit <cstdint> from Signals.h. Scope the repair
        # to C++ so CMake's C compiler feature checks remain valid.
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
        export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-human-signal-pool-v1-k3-compat}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 3 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-human-signal-pool-v1-k3}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-human-pool \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-human-prior-k3|causal-parent-human-prior-k3-compat)
      # The human study's actual policy: retain the entire interval, use
      # crash signals as a soft prior, then probe a bounded weighted midpoint.
      if [[ "${MODE}" == "causal-parent-human-prior-k3-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
        export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-human-soft-prior-v1-k3-compat}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 3 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-human-soft-prior-v1-k3}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-human-prior \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-human-frontier|causal-parent-human-frontier-compat)
      # Retrospective retrieval-recall pilot: repository-derived staged human
      # frontier, causal LLM selection inside it, then a full-BCR fallback.
      if [[ "${MODE}" == "causal-parent-human-frontier-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
        export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-human-staged-frontier-v1-compat}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-human-staged-frontier-v1}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-human-frontier \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-human-dynamic-k12|causal-parent-human-dynamic-k12-compat)
      # Online policy: derive signals and dependency usage from the crash
      # artifact and bad endpoint, then refresh evidence on the current
      # unresolved interval before every model frontier.
      if [[ "${MODE}" == "causal-parent-human-dynamic-k12-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
        export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-human-dynamic-evidence-v4-k12-compat}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-human-dynamic-evidence-v4-k12}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-human-dynamic \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-crash-aware-k12|causal-parent-crash-aware-k12-compat)
      # Crash-aware BCR keeps calibrated-posterior selection unchanged. It
      # only adds parser-derived retrieval evidence and uses a fresh cache
      # namespace. Compatibility mode is for old LLVM revisions only.
      if [[ "${MODE}" == "causal-parent-crash-aware-k12-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
        export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-bcr-crash-aware-v12-k12-compat}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-bcr-crash-aware-v12-k12}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-crash-aware \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-deterministic-facts-k12)
      # V15 replaces v14's extract-then-absolute-score pair with one ordinal
      # causal judgment over deterministic crash facts and selected hunks.
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE:-terra-bcr-deterministic-facts-v15-k12}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-deterministic-facts \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-deterministic-facts-artifact-k12|causal-parent-deterministic-facts-artifact-k12-compat)
      # V16 preserves V15's single ordinal call but discovers parser-ready
      # crash artifacts from the master-50 package. Compatibility is isolated
      # to historical revisions that require an explicit cstdint include.
      if [[ "${MODE}" == "causal-parent-deterministic-facts-artifact-k12-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:---include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-deterministic-facts-artifact \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-parent-deterministic-facts-artifact-range-k12|causal-parent-deterministic-facts-artifact-range-k12-compat)
      # Isolate the parent-window pilot from single-parent V16. The window
      # adds provenance only; retrieval, ordinal ranking, and selection stay unchanged.
      if [[ "${MODE}" == "causal-parent-deterministic-facts-artifact-range-k12-compat" ]]; then
        # Use Bash's "unset/default" expansion, which already supplies the
        # leading hyphen required by Clang's -include option.
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy calibrated-posterior \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-deterministic-facts-artifact \
        --causal-context-parent-count 5 \
        --observation-prompt-mode trace-only \
        --observations "${obs}" \
        --run-label "${LANE}" \
        --max-steps 30
      ;;
    causal-evidence-guided-k12|causal-evidence-guided-k12-compat)
      # CEG-Bisect v6 uses a manifest-verified bad-endpoint artifact, removes
      # authored issue prose, and reuses the shared calibrated BCR selector.
      CEG_INPUT_ROOT="${CEG_INPUT_ROOT:-${ROOT}/human_analysis/raw/ceg-bad-endpoint-evidence-20260906-r1}"
      if [[ ! -f "${CEG_INPUT_ROOT}/manifest.json" ]]; then
        echo "error: CEG bad-endpoint input manifest is missing: ${CEG_INPUT_ROOT}/manifest.json" >&2
        exit 2
      fi
      if [[ "${MODE}" == "causal-evidence-guided-k12-compat" ]]; then
        export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"
      fi
      run_bisect_cmd "${PY}" tools/lm_bisect.py run-online \
        --issue "${issue}" \
        --llvm-dir "${wt}" \
        --scorer model \
        --model-name gpt-5.6-terra \
        --model-reasoning-effort high \
        --search-policy causal-evidence-guided \
        --model-top-k 12 \
        --model-frontier topk \
        --model-cache-namespace "${MODEL_CACHE_NAMESPACE}" \
        --model-diff-mode parent \
        --model-diff-extraction causal-llm-ceg-bisect \
        --ceg-input-root "${CEG_INPUT_ROOT}" \
        --causal-context-parent-count 5 \
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
  cleanup_issue_worktree "${wt}"
  if (( RUN_ISSUE_EXIT_CODE == 0 )); then
    echo "[${LANE}] $(date -Iseconds) done ${issue}" | tee -a "${LOG}"
  else
    echo "[${LANE}] $(date -Iseconds) failed ${issue} exit=${RUN_ISSUE_EXIT_CODE}" | tee -a "${LOG}"
  fi
}

trap 'release_lane_reservation; cleanup_issue_worktree "${CURRENT_WORKTREE:-}"' EXIT

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
