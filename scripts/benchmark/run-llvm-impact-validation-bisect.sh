#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-llvm-impact-validation-bisect.sh ISSUE GOOD_COMMIT BAD_COMMIT [WORKTREE]

Validate an impact case's exact good/bad endpoints, then run ordinary Git
bisect. Endpoint failure prevents the bisection from starting.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 3 || $# -gt 4 ]]; then
  usage
  [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0 || exit 2
fi

ISSUE="$1"
GOOD_REF="$2"
BAD_REF="$3"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-/home/derek/gitbisect-work/worktrees}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}"
WORKTREE="${4:-${WORK_ROOT}/${ISSUE}-${RUN_ID}}"
RUNNER="${SCRIPT_DIR}/llvm-impact-runner.sh"
RESULTS_DIR="${ROOT}/results/impact-study/${ISSUE}/${RUN_ID}"
STATUS_FILE="${RESULTS_DIR}/status.txt"
BISECT_LOG="${RESULTS_DIR}/git-bisect.log"
BISECT_OUTPUT="${RESULTS_DIR}/git-bisect-run.log"
RESULT_JSON="${RESULTS_DIR}/result.json"
JOBS="${JOBS:-2}"

mkdir -p "${RESULTS_DIR}" "${WORK_ROOT}"
if [[ ! -x "${RUNNER}" ]]; then
  echo "error: impact runner is not executable: ${RUNNER}" >&2
  exit 2
fi
if ! git -C "${BASE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: LLVM base repository not found: ${BASE_REPO}" >&2
  exit 2
fi

GOOD_COMMIT=$(git -C "${BASE_REPO}" rev-parse --verify "${GOOD_REF}^{commit}")
BAD_COMMIT=$(git -C "${BASE_REPO}" rev-parse --verify "${BAD_REF}^{commit}")
if ! git -C "${BASE_REPO}" merge-base --is-ancestor "${GOOD_COMMIT}" "${BAD_COMMIT}"; then
  echo "error: good commit is not an ancestor of bad commit" >&2
  exit 2
fi

cleanup() {
  set +e
  if git -C "${WORKTREE}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${WORKTREE}" bisect reset >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -e "${WORKTREE}" ]]; then
  echo "error: impact worktree already exists: ${WORKTREE}" >&2
  exit 2
fi
git -C "${BASE_REPO}" worktree add --detach "${WORKTREE}" "${BAD_COMMIT}"

printf 'status=validating\nissue=%s\nrun_id=%s\ngood_commit=%s\nbad_commit=%s\n' \
  "${ISSUE}" "${RUN_ID}" "${GOOD_COMMIT}" "${BAD_COMMIT}" >"${STATUS_FILE}"

validate_endpoint() {
  local expected="$1"
  local commit="$2"
  local log="${RESULTS_DIR}/endpoint-${expected}.log"
  local rc=0

  git -C "${WORKTREE}" checkout -q --detach "${commit}"
  set +e
  env ROOT="${ROOT}" RUN_ID="${RUN_ID}-endpoint-${expected}" JOBS="${JOBS}" \
    "${RUNNER}" "${ISSUE}" "${WORKTREE}" >"${log}" 2>&1
  rc=$?
  set -e
  case "${expected}:${rc}" in
    good:0|bad:1) return 0 ;;
    *)
      printf 'status=endpoint-failed\nendpoint=%s\nrunner_exit=%s\n' \
        "${expected}" "${rc}" >>"${STATUS_FILE}"
      echo "error: ${expected} endpoint returned ${rc}; bisection not started" >&2
      return 1
      ;;
  esac
}

validate_endpoint "good" "${GOOD_COMMIT}"
validate_endpoint "bad" "${BAD_COMMIT}"
printf 'status=bisecting\n' >>"${STATUS_FILE}"

git -C "${WORKTREE}" checkout -q --detach "${BAD_COMMIT}"
git -C "${WORKTREE}" bisect start "${BAD_COMMIT}" "${GOOD_COMMIT}"
bisect_rc=0
set +e
env ROOT="${ROOT}" RUN_ID="${RUN_ID}-bisect" JOBS="${JOBS}" \
  git -C "${WORKTREE}" bisect run "${RUNNER}" "${ISSUE}" "${WORKTREE}" \
  >"${BISECT_OUTPUT}" 2>&1
bisect_rc=$?
set -e
git -C "${WORKTREE}" bisect log >"${BISECT_LOG}" || true

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' \
  "${BISECT_LOG}" | tail -n 1)
if [[ -n "${FIRST_BAD}" ]]; then
  RESULT_STATUS=completed
  FIRST_BAD=$(git -C "${BASE_REPO}" rev-parse "${FIRST_BAD}^{commit}")
  FIRST_BAD_PARENT=$(git -C "${BASE_REPO}" rev-parse "${FIRST_BAD}^1")
  SUBJECT=$(git -C "${BASE_REPO}" show -s --format=%s "${FIRST_BAD}")
  AUTHOR_NAME=$(git -C "${BASE_REPO}" show -s --format=%an "${FIRST_BAD}")
  AUTHOR_EMAIL=$(git -C "${BASE_REPO}" show -s --format=%ae "${FIRST_BAD}")
  AUTHOR_DATE=$(git -C "${BASE_REPO}" show -s --format=%aI "${FIRST_BAD}")
  COMMIT_DATE=$(git -C "${BASE_REPO}" show -s --format=%cI "${FIRST_BAD}")
else
  RESULT_STATUS=unresolved
  FIRST_BAD=""
  FIRST_BAD_PARENT=""
  SUBJECT=""
  AUTHOR_NAME=""
  AUTHOR_EMAIL=""
  AUTHOR_DATE=""
  COMMIT_DATE=""
fi

REPRODUCER=$(find "${ROOT}/scripts/impact/${ISSUE}" -maxdepth 1 -type f \
  \( -name 'reproducer.cpp' -o -name 'reproducer.ll' \) -print -quit)
REPRODUCER_SHA256=$(sha256sum "${REPRODUCER}" | awk '{print $1}')
INTERVAL_SIZE=$(git -C "${BASE_REPO}" rev-list --count "${GOOD_COMMIT}..${BAD_COMMIT}")
BISECT_COMMANDS=$(grep -Ec '^git bisect (good|bad|skip) ' "${BISECT_LOG}" || true)
if (( BISECT_COMMANDS >= 2 )); then
  BISECT_PROBES=$((BISECT_COMMANDS - 2))
else
  BISECT_PROBES=0
fi
SKIP_COUNT=$(grep -Ec '^git bisect skip ' "${BISECT_LOG}" || true)

env \
  IMPACT_ISSUE="${ISSUE}" \
  IMPACT_STATUS="${RESULT_STATUS}" \
  IMPACT_RUN_ID="${RUN_ID}" \
  IMPACT_GOOD_COMMIT="${GOOD_COMMIT}" \
  IMPACT_BAD_COMMIT="${BAD_COMMIT}" \
  IMPACT_INTERVAL_SIZE="${INTERVAL_SIZE}" \
  IMPACT_BISECT_EXIT_CODE="${bisect_rc}" \
  IMPACT_BISECT_PROBE_COUNT="${BISECT_PROBES}" \
  IMPACT_SKIP_COUNT="${SKIP_COUNT}" \
  IMPACT_FIRST_BAD="${FIRST_BAD}" \
  IMPACT_FIRST_BAD_PARENT="${FIRST_BAD_PARENT}" \
  IMPACT_SUBJECT="${SUBJECT}" \
  IMPACT_AUTHOR_NAME="${AUTHOR_NAME}" \
  IMPACT_AUTHOR_EMAIL="${AUTHOR_EMAIL}" \
  IMPACT_AUTHOR_DATE="${AUTHOR_DATE}" \
  IMPACT_COMMIT_DATE="${COMMIT_DATE}" \
  IMPACT_REPRODUCER="${REPRODUCER}" \
  IMPACT_REPRODUCER_SHA256="${REPRODUCER_SHA256}" \
  IMPACT_RUNNER="${RUNNER}" \
  IMPACT_BISECT_LOG="${BISECT_LOG}" \
  IMPACT_BISECT_OUTPUT="${BISECT_OUTPUT}" \
  python3 - "${RESULT_JSON}" <<'PY'
import json
import os
import sys
from pathlib import Path

result = {
    "study": "llvm-impact-validation",
    "issue": os.environ["IMPACT_ISSUE"],
    "status": os.environ["IMPACT_STATUS"],
    "run_id": os.environ["IMPACT_RUN_ID"],
    "good_commit": os.environ["IMPACT_GOOD_COMMIT"],
    "bad_commit": os.environ["IMPACT_BAD_COMMIT"],
    "endpoint_good_verdict": "good",
    "endpoint_bad_verdict": "bad",
    "interval_size": int(os.environ["IMPACT_INTERVAL_SIZE"]),
    "bisect_exit_code": int(os.environ["IMPACT_BISECT_EXIT_CODE"]),
    "bisect_probe_count": int(os.environ["IMPACT_BISECT_PROBE_COUNT"]),
    "skip_count": int(os.environ["IMPACT_SKIP_COUNT"]),
    "first_bad_commit": os.environ["IMPACT_FIRST_BAD"],
    "first_bad_parent": os.environ["IMPACT_FIRST_BAD_PARENT"],
    "subject": os.environ["IMPACT_SUBJECT"],
    "author_name": os.environ["IMPACT_AUTHOR_NAME"],
    "author_email": os.environ["IMPACT_AUTHOR_EMAIL"],
    "author_date": os.environ["IMPACT_AUTHOR_DATE"],
    "commit_date": os.environ["IMPACT_COMMIT_DATE"],
    "reproducer": os.environ["IMPACT_REPRODUCER"],
    "reproducer_sha256": os.environ["IMPACT_REPRODUCER_SHA256"],
    "runner": os.environ["IMPACT_RUNNER"],
    "git_bisect_log": os.environ["IMPACT_BISECT_LOG"],
    "git_bisect_output": os.environ["IMPACT_BISECT_OUTPUT"],
}
Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n")
PY

printf 'status=%s\nfirst_bad_commit=%s\nbisect_exit_code=%s\nfinished_at=%s\n' \
  "${RESULT_STATUS}" "${FIRST_BAD}" "${bisect_rc}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  >>"${STATUS_FILE}"

if [[ "${RESULT_STATUS}" != "completed" || "${bisect_rc}" -ne 0 ]]; then
  exit 1
fi
