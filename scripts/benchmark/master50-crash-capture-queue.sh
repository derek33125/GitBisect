#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/derek/GitBisect}"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-$(dirname "$BASE_REPO")/worktrees}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-$ROOT/human_analysis/raw/master50-evidence-20260903-r4}"
QUEUE_FILE="${QUEUE_FILE:-$ROOT/results/issues/server-jobs/master50-first-bad-capture-20260903.todo}"
DONE_FILE="${DONE_FILE:-$ROOT/results/issues/server-jobs/master50-first-bad-capture-20260903.done}"
LOCK_FILE="${LOCK_FILE:-$ROOT/results/issues/server-jobs/master50-first-bad-capture-20260903.lock}"
RUN_TAG="${RUN_TAG:-20260903-master50-first-bad-capture}"
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
export REQUIRE_RUNTIME_SIGNALS="${REQUIRE_RUNTIME_SIGNALS:-0}"

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

select_primary_stderr() {
  local temp_dir="$1"
  local canonical_stderr="$2"

  if [[ -s "$canonical_stderr" ]]; then
    printf '%s\n' "$canonical_stderr"
    return
  fi
  if [[ -d "$temp_dir" ]]; then
    find "$temp_dir" -maxdepth 1 -type f \
      -name '*.err' ! -name '*.passdiag.err' -size +0c -print -quit
  fi
}

run_issue() {
  local issue="$1"
  local first_bad_sha runner run_id result_dir capture_reproducer_dir wt log_file runner_log validated_runner_log pass_log source_log err_file started finished rc capture_record_rc capture_status
  local -a reproducers
  cleanup_worktree() {
    if [[ -n "${wt:-}" && -e "$wt" ]]; then
      git -C "$BASE_REPO" worktree remove --force "$wt" 2>/dev/null || true
    fi
  }
  trap cleanup_worktree EXIT
  first_bad_sha="$(metadata_value "$issue" first_bad_commit)"
  runner="$ROOT/$(metadata_value "$issue" runner)"
  git -C "$BASE_REPO" cat-file -e "${first_bad_sha}^{commit}"
  run_id="${RUN_TAG}-${LANE_ID}-${issue}"
  result_dir="$ROOT/results/issues/$issue"
  capture_reproducer_dir="$result_dir/capture-inputs-$run_id"
  wt="$WORK_ROOT/${issue}-master50-capture-${run_id}"
  log_file="$result_dir/${issue}-master50-crash-capture-${run_id}.log"
  runner_log="$result_dir/${issue}-master50-crash-capture-${run_id}.runner.log"
  validated_runner_log="$result_dir/${issue}-git-bisect-runner-${run_id}.log"
  rm -rf "$capture_reproducer_dir"
  mkdir -p "$result_dir" "$capture_reproducer_dir"
  started="$(date -Is)"

  if [[ -e "$wt" ]]; then
    git -C "$BASE_REPO" worktree remove --force "$wt" 2>/dev/null || true
  fi
  git -C "$BASE_REPO" worktree add --detach "$wt" "$first_bad_sha" >"$log_file" 2>&1
  {
    printf 'issue: %s\n' "$issue"
    printf 'tested_sha: %s\n' "$first_bad_sha"
    printf 'runner: %s\n' "${runner#$ROOT/}"
    printf 'start: %s\n' "$started"
    printf '%s\n' '--- runner output ---'
  } >>"$log_file"

  set +e
  RUN_ID="$run_id" CAPTURE_REPRODUCER_DIR="$capture_reproducer_dir" CAPTURE_RUNNING_PASS=1 \
    "$runner" "$wt" >"$runner_log" 2>&1
  rc=$?
  set -e
  finished="$(date -Is)"
  cat "$runner_log" >>"$log_file"
  local temp_dir="$result_dir/tmp-git-bisect-$run_id"
  err_file="$temp_dir/${first_bad_sha:0:12}.err"
  # The per-issue wrapper writes the detailed build/replay output to this log;
  # the outer runner log additionally carries capture-only diagnostic output.
  pass_log="$runner_log"
  source_log="$runner_log"
  if [[ -s "$validated_runner_log" ]]; then
    source_log="$validated_runner_log"
  fi
  pass_log="$result_dir/${issue}-master50-pass-evidence-${run_id}.log"
  : >"$pass_log"
  if [[ -s "$validated_runner_log" ]]; then
    cat "$validated_runner_log" >>"$pass_log"
  fi
  cat "$runner_log" >>"$pass_log"
  # Prefer the ordinary first-bad replay stderr. The pass-diagnostic retry is
  # auxiliary evidence and must never become the canonical crash artifact.
  err_file="$(select_primary_stderr "$temp_dir" "$err_file")"
  if [[ -n "$err_file" && -s "$err_file" ]]; then
    source_log="$err_file"
  fi
  reproducers=()
  while IFS= read -r -d '' path; do
    reproducers+=("$path")
  done < <(
    find "$result_dir/tmp-git-bisect-$run_id" "$capture_reproducer_dir" \
      "$ROOT/scratch/$issue" "$ROOT/scripts/$issue" \
      -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' -o -name '*.ll' -o -name '*.m' -o -name '*.mm' -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.py' \) \
      ! -path '*/tools/*' ! -path '*/build-*/*' -print0 2>/dev/null | sort -z
  )
  printf '%s\n' "runner_exit_code: $rc" "finish: $finished" >>"$log_file"

  set +e
  REQUIRE_RUNTIME_SIGNALS="$REQUIRE_RUNTIME_SIGNALS" python3 - "$ROOT" "$EVIDENCE_ROOT" "$issue" "$first_bad_sha" "$rc" "$started" "$finished" "$source_log" "$pass_log" "${reproducers[@]}" <<'PY'
import os
import sys
from pathlib import Path
from tools.master50_crash_capture import record_capture

root, evidence_root, issue, sha, rc, started, finished, log_file, pass_log, *reproducers = sys.argv[1:]
result = record_capture(
    root=Path(root),
    evidence_root=Path(evidence_root),
    issue=issue,
    tested_sha=sha,
    exit_code=int(rc),
    started_at=started,
    finished_at=finished,
    source_log=Path(log_file),
    pass_source_log=Path(pass_log),
    reproducer=[Path(path) for path in reproducers],
    require_runtime_signals=os.environ.get("REQUIRE_RUNTIME_SIGNALS") == "1",
)
print(f"marker_detected={result.marker_detected}")
print(f"source_log={result.source_log}")
print(f"runtime_signal_gaps={','.join(result.runtime_signal_gaps)}")
raise SystemExit(3 if result.runtime_signal_gaps and os.environ.get("REQUIRE_RUNTIME_SIGNALS") == "1" else 0)
PY
  capture_record_rc=$?
  set -e

  cleanup_worktree
  trap - EXIT
  if (( capture_record_rc == 3 )); then
    capture_status="signal_incomplete"
  elif (( capture_record_rc != 0 )); then
    printf '%s\t%s\tcapture_record_failed\t%s\trc=%s\tlog=%s\n' \
      "$(date -Is)" "$LANE_ID" "$issue" "$capture_record_rc" "$log_file" >>"$DONE_FILE"
    log "capture record failed issue=$issue rc=$capture_record_rc"
    return 0
  else
    capture_status="finished"
  fi
  printf '%s\t%s\t%s\t%s\trc=%s\tlog=%s\n' \
    "$(date -Is)" "$LANE_ID" "$capture_status" "$issue" "$rc" "$log_file" >>"$DONE_FILE"
  log "$capture_status issue=$issue rc=$rc"
}

main() {
  log "master-50 crash capture queue active; queue_file=$QUEUE_FILE"
  local issue
  while true; do
    issue=""
    issue="$(claim_next_issue)" || break
    run_issue "$issue"
  done
  log "queue empty; supervisor exiting"
}

if [[ "${MASTER50_CAPTURE_QUEUE_LIBRARY_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
