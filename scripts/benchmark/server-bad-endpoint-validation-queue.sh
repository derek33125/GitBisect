#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/derek/GitBisect}"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-/home/derek/gitbisect-work/worktrees}"
QUEUE_FILE="${QUEUE_FILE:-$ROOT/results/issues/server-jobs/bad-endpoint-validation-queue-20260615.todo}"
DONE_FILE="${DONE_FILE:-$ROOT/results/issues/server-jobs/bad-endpoint-validation-queue-20260615.done}"
LOCK_FILE="${LOCK_FILE:-$ROOT/results/issues/server-jobs/bad-endpoint-validation-queue-20260615.lock}"
RUN_TAG="${RUN_TAG:-20260615-bad-endpoint}"
LANE_ID="${LANE_ID:-lane}"

export ROOT BASE_REPO WORK_ROOT
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-$JOBS}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="$JOBS"
export BUILD_NICE_LEVEL="${BUILD_NICE_LEVEL:-10}"
export BUILD_IONICE_CLASS="${BUILD_IONICE_CLASS:-3}"

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

metadata_value() {
  local issue="$1" key="$2"
  python3 "$ROOT/tools/bad_endpoint_queue.py" print "$issue" --root "$ROOT" \
    | awk -F= -v k="$key" '$1 == k {print substr($0, length(k) + 2)}'
}

run_issue() {
  local issue="$1"
  local bad_ref bad_sha runner run_id result_dir wt log_file summary rc verdict interval_size
  bad_ref="$(metadata_value "$issue" bad_ref)"
  runner="$ROOT/$(metadata_value "$issue" runner)"
  bad_sha="$(git -C "$BASE_REPO" rev-parse --verify "${bad_ref}^{commit}")"
  run_id="${RUN_TAG}-${LANE_ID}-${issue}"
  result_dir="$ROOT/results/issues/$issue"
  wt="$WORK_ROOT/${issue}-bad-endpoint-${run_id}"
  log_file="$result_dir/${issue}-bad-endpoint-validation-${run_id}.log"
  summary="$result_dir/${issue}-bad-endpoint-validation-${run_id}.md"

  mkdir -p "$result_dir"
  log "starting issue=$issue bad_ref=$bad_ref bad_sha=$bad_sha worktree=$wt"

  {
    echo "issue: $issue"
    echo "bad_ref: $bad_ref"
    echo "bad_sha: $bad_sha"
    echo "runner: ${runner#$ROOT/}"
    echo "worktree: $wt"
    echo "jobs: $JOBS"
    echo "build_type: $LM_BISECT_BUILD_TYPE"
    echo "assertions: $LM_BISECT_ENABLE_ASSERTIONS"
    echo "start: $(date -Is)"
  } >"$log_file"

  if [[ ! -d "$wt/.git" && ! -f "$wt/.git" ]]; then
    git -C "$BASE_REPO" worktree add --detach "$wt" "$bad_sha" >>"$log_file" 2>&1
  else
    git -C "$wt" checkout -q "$bad_sha" >>"$log_file" 2>&1
  fi

  set +e
  "$runner" "$wt" >>"$log_file" 2>&1
  rc=$?
  set -e

  case "$rc" in
    0) verdict=good ;;
    1) verdict=bad ;;
    125) verdict=skip ;;
    *) verdict=error ;;
  esac
  interval_size="NA"
  if [[ -n "${GOOD_REF:-}" ]]; then
    interval_size="$(git -C "$BASE_REPO" rev-list --count "${GOOD_REF}..${bad_sha}" 2>/dev/null || printf 'NA')"
  fi

  cat >"$summary" <<EOF
# ${issue} Bad Endpoint Validation

- Bad reference: \`${bad_ref}\`
- Bad commit: \`${bad_sha}\`
- Runner: \`${runner#$ROOT/}\`
- Verdict: ${verdict}
- Runner exit code: ${rc}
- Log: \`${log_file#$ROOT/}\`
- Build settings: \`JOBS=${JOBS}\`, \`LM_BISECT_BUILD_TYPE=${LM_BISECT_BUILD_TYPE}\`, \`LM_BISECT_ENABLE_ASSERTIONS=${LM_BISECT_ENABLE_ASSERTIONS}\`, \`CCACHE_MAXSIZE=${CCACHE_MAXSIZE}\`
EOF

  printf '%s\t%s\tfinished\t%s\tverdict=%s\trc=%s\tlog=%s\n' \
    "$(date -Is)" "$LANE_ID" "$issue" "$verdict" "$rc" "$log_file" >>"$DONE_FILE"
  log "finished issue=$issue verdict=$verdict rc=$rc"
}

main() {
  log "bad endpoint queue active; queue_file=$QUEUE_FILE"
  local issue
  while issue="$(claim_next_issue)"; do
    run_issue "$issue"
  done
  log "queue empty; supervisor exiting"
}

main "$@"
