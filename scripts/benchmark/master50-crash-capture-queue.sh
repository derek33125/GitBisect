#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/derek/GitBisect}"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-$(dirname "$BASE_REPO")/worktrees}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-$ROOT/human_analysis/raw/master50-evidence-20260821}"
QUEUE_FILE="${QUEUE_FILE:-$ROOT/results/issues/server-jobs/master50-crash-capture-20260831.todo}"
DONE_FILE="${DONE_FILE:-$ROOT/results/issues/server-jobs/master50-crash-capture-20260831.done}"
LOCK_FILE="${LOCK_FILE:-$ROOT/results/issues/server-jobs/master50-crash-capture-20260831.lock}"
RUN_TAG="${RUN_TAG:-20260831-master50-crash-capture}"
LANE_ID="${LANE_ID:-lane}"

export ROOT BASE_REPO WORK_ROOT EVIDENCE_ROOT
export JOBS="${JOBS:-2}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-$JOBS}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="$JOBS"
export BUILD_NICE_LEVEL="${BUILD_NICE_LEVEL:-10}"
export BUILD_IONICE_CLASS="${BUILD_IONICE_CLASS:-3}"

mkdir -p "$(dirname "$QUEUE_FILE")" "$WORK_ROOT"
touch "$QUEUE_FILE" "$DONE_FILE"
cd "$ROOT"

log() {
  printf '[%s] [%s] %s\n' "$(date -Is)" "$LANE_ID" "$*"
}

claim_next_issue() {
  local tmp issue
  tmp="$(mktemp)"
  exec 9>"$LOCK_FILE"
  flock 9
  issue="$(awk 'NF && $1 !~ /^#/ {print $1; exit}' "$QUEUE_FILE")"
  if [[ -z "$issue" ]]; then
    rm -f "$tmp"
    flock -u 9
    return 1
  fi
  awk -v first="$issue" '
    BEGIN {removed = 0}
    NF && $1 !~ /^#/ && $1 == first && removed == 0 {removed = 1; next}
    {print}
  ' "$QUEUE_FILE" >"$tmp"
  mv "$tmp" "$QUEUE_FILE"
  printf '%s\t%s\tclaimed\t%s\n' "$(date -Is)" "$LANE_ID" "$issue" >>"$DONE_FILE"
  flock -u 9
  printf '%s\n' "$issue"
}

metadata_value() {
  local issue="$1" key="$2"
  python3 "$ROOT/tools/master50_crash_capture.py" print "$issue" \
    --root "$ROOT" --evidence-root "$EVIDENCE_ROOT" \
    | awk -F= -v k="$key" '$1 == k {print substr($0, length(k) + 2)}'
}

run_issue() {
  local issue="$1"
  local bad_sha runner run_id result_dir wt log_file runner_log started finished rc
  cleanup_worktree() {
    if [[ -n "${wt:-}" && -e "$wt" ]]; then
      git -C "$BASE_REPO" worktree remove --force "$wt" 2>/dev/null || true
    fi
  }
  trap cleanup_worktree EXIT
  bad_sha="$(metadata_value "$issue" bad_commit)"
  runner="$ROOT/$(metadata_value "$issue" runner)"
  git -C "$BASE_REPO" cat-file -e "${bad_sha}^{commit}"
  run_id="${RUN_TAG}-${LANE_ID}-${issue}"
  result_dir="$ROOT/results/issues/$issue"
  wt="$WORK_ROOT/${issue}-master50-capture-${run_id}"
  log_file="$result_dir/${issue}-master50-crash-capture-${run_id}.log"
  runner_log="$result_dir/${issue}-master50-crash-capture-${run_id}.runner.log"
  mkdir -p "$result_dir"
  started="$(date -Is)"

  if [[ -e "$wt" ]]; then
    git -C "$BASE_REPO" worktree remove --force "$wt" 2>/dev/null || true
  fi
  git -C "$BASE_REPO" worktree add --detach "$wt" "$bad_sha" >"$log_file" 2>&1
  {
    printf 'issue: %s\n' "$issue"
    printf 'tested_sha: %s\n' "$bad_sha"
    printf 'runner: %s\n' "${runner#$ROOT/}"
    printf 'start: %s\n' "$started"
    printf '%s\n' '--- runner output ---'
  } >>"$log_file"

  set +e
  "$runner" "$wt" >"$runner_log" 2>&1
  rc=$?
  set -e
  finished="$(date -Is)"
  cat "$runner_log" >>"$log_file"
  printf '%s\n' "runner_exit_code: $rc" "finish: $finished" >>"$log_file"

  python3 - "$ROOT" "$EVIDENCE_ROOT" "$issue" "$bad_sha" "$rc" "$started" "$finished" "$runner_log" <<'PY'
import sys
from pathlib import Path
from tools.master50_crash_capture import record_capture

root, evidence_root, issue, sha, rc, started, finished, log_file = sys.argv[1:]
result = record_capture(
    root=Path(root),
    evidence_root=Path(evidence_root),
    issue=issue,
    tested_sha=sha,
    exit_code=int(rc),
    started_at=started,
    finished_at=finished,
    source_log=Path(log_file),
)
print(f"marker_detected={result.marker_detected}")
print(f"source_log={result.source_log}")
PY

  cleanup_worktree
  trap - EXIT
  printf '%s\t%s\tfinished\t%s\trc=%s\tlog=%s\n' \
    "$(date -Is)" "$LANE_ID" "$issue" "$rc" "$log_file" >>"$DONE_FILE"
  log "finished issue=$issue rc=$rc"
}

main() {
  log "master-50 crash capture queue active; queue_file=$QUEUE_FILE"
  local issue
  while issue="$(claim_next_issue)"; do
    run_issue "$issue"
  done
  log "queue empty; supervisor exiting"
}

main "$@"
