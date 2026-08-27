#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: server-candidate-endpoint-validation-queue.sh

Runs bad-endpoint validation for candidate LLVM crash issues from a shared queue.
Each lane claims one issue at a time, then delegates the actual build/repro
logic to results/issues/server-jobs/server-validation-queue-20260613.sh.

Environment:
  ROOT             Repo root. Default: /home/derek/GitBisect
  QUEUE_FILE       Queue file with one issue id per line.
  DONE_FILE        Append-only claim/finish log.
  LOCK_FILE        Flock lock file.
  RUN_TAG          Batch tag for logs/worktrees.
  LANE_ID          Lane identifier, e.g. lane-1.
  JOBS             Build parallelism. Default: 4.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

ROOT="${ROOT:-/home/derek/GitBisect}"
QUEUE_FILE="${QUEUE_FILE:-$ROOT/results/issues/server-jobs/candidate-endpoint-validation-queue-20260616.todo}"
DONE_FILE="${DONE_FILE:-$ROOT/results/issues/server-jobs/candidate-endpoint-validation-queue-20260616.done}"
LOCK_FILE="${LOCK_FILE:-$ROOT/results/issues/server-jobs/candidate-endpoint-validation-queue-20260616.lock}"
RUN_TAG="${RUN_TAG:-20260616-candidate-endpoint}"
LANE_ID="${LANE_ID:-lane}"
VALIDATION_SCRIPT="${VALIDATION_SCRIPT:-$ROOT/results/issues/server-jobs/server-validation-queue-20260613.sh}"

export ROOT
export BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
export WORK_ROOT="${WORK_ROOT:-/home/derek/gitbisect-work/worktrees}"
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-$JOBS}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="$JOBS"

mkdir -p "$(dirname "$QUEUE_FILE")" "$WORK_ROOT"
touch "$QUEUE_FILE" "$DONE_FILE"

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

run_issue() {
  local issue="$1"
  local run_id rc
  run_id="${RUN_TAG}-${LANE_ID}-${issue}"
  log "starting issue=$issue run_id=$run_id"
  set +e
  RUN_ID="$run_id" QUEUE_OVERRIDE="$issue" bash "$VALIDATION_SCRIPT" crash
  rc=$?
  set -e
  printf '%s\t%s\tfinished\t%s\trc=%s\trun_id=%s\n' \
    "$(date -Is)" "$LANE_ID" "$issue" "$rc" "$run_id" >>"$DONE_FILE"
  log "finished issue=$issue rc=$rc"
}

main() {
  if [[ ! -f "$VALIDATION_SCRIPT" ]]; then
    log "missing validation script: $VALIDATION_SCRIPT"
    exit 2
  fi
  log "candidate endpoint queue active; queue_file=$QUEUE_FILE"
  local issue
  while issue="$(claim_next_issue)"; do
    run_issue "$issue"
  done
  log "queue empty; supervisor exiting"
}

main "$@"
