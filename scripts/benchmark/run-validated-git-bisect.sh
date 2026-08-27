#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-validated-git-bisect.sh ISSUE GOOD_COMMIT BAD_COMMIT [worktree-path]

Runs a git-bisect baseline for a previously endpoint-validated issue interval.
The per-commit classification is delegated to validated-bisect-runner.sh.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 3 ]]; then
  usage
  exit 0
fi

ISSUE="$1"
GOOD_COMMIT="$2"
BAD_COMMIT="$3"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
BASE_REPO="${BASE_REPO:-/home/derek331/research/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-/home/derek331/research/gitbisect-work/worktrees}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
LLVM_DIR="${4:-${WORK_ROOT}/${ISSUE}-git-bisect-${RUN_ID}}"
RUNNER="${SCRIPT_DIR}/validated-bisect-runner.sh"
RESULTS_DIR="${ROOT}/results/issues/${ISSUE}"
RUN_LOG="${RESULTS_DIR}/${ISSUE}-git-bisect-${RUN_ID}.outer.log"
BISECT_LOG="${RESULTS_DIR}/${ISSUE}-git-bisect-${RUN_ID}.txt"
RESULT_NOTE="${RESULTS_DIR}/${ISSUE}-git-bisect-${RUN_ID}.md"

mkdir -p "${RESULTS_DIR}" "${WORK_ROOT}"

if [[ ! -x "${RUNNER}" ]]; then
  echo "error: runner is not executable: ${RUNNER}" >&2
  exit 2
fi

if [[ ! -d "${LLVM_DIR}" ]]; then
  git -C "${BASE_REPO}" worktree add --detach "${LLVM_DIR}" "${BAD_COMMIT}"
fi

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 2
fi

if ! git -C "${LLVM_DIR}" merge-base --is-ancestor "${GOOD_COMMIT}" "${BAD_COMMIT}"; then
  echo "error: good commit is not an ancestor of bad commit" >&2
  exit 2
fi

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)

cleanup() {
  set +e
  git -C "${LLVM_DIR}" bisect reset >/dev/null 2>&1 || true
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

exec > >(tee -a "${RUN_LOG}") 2>&1

echo "[run-validated-git-bisect] start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "issue: ${ISSUE}"
echo "good: ${GOOD_COMMIT}"
echo "bad: ${BAD_COMMIT}"
echo "worktree: ${LLVM_DIR}"
echo "jobs: ${JOBS:-4}"
echo "build type: ${LM_BISECT_BUILD_TYPE:-Release}"
echo "assertions: ${LM_BISECT_ENABLE_ASSERTIONS:-ON}"

git -C "${LLVM_DIR}" checkout -q "${BAD_COMMIT}"
git -C "${LLVM_DIR}" bisect start
git -C "${LLVM_DIR}" bisect bad "${BAD_COMMIT}"
git -C "${LLVM_DIR}" bisect good "${GOOD_COMMIT}"

set +e
git -C "${LLVM_DIR}" bisect run "${RUNNER}" "${ISSUE}" "${LLVM_DIR}"
bisect_rc=$?
set -e

git -C "${LLVM_DIR}" bisect log > "${BISECT_LOG}" || true

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${BISECT_LOG}" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  FIRST_BAD="UNRESOLVED"
  FIRST_BAD_SUBJECT="UNRESOLVED"
  FIRST_BAD_DATE="UNRESOLVED"
else
  FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
  FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")
fi

INTERVAL_SIZE=$(git -C "${LLVM_DIR}" rev-list --count "${GOOD_COMMIT}..${BAD_COMMIT}")
TESTED_STEPS=$(grep -c '^git bisect ' "${BISECT_LOG}" || true)
SKIP_STEPS=$(grep -c '^git bisect skip' "${BISECT_LOG}" || true)

cat > "${RESULT_NOTE}" <<EOF
# ${ISSUE} Git Bisect Result

- Good commit: \`${GOOD_COMMIT}\`
- Bad commit: \`${BAD_COMMIT}\`
- Interval size: ${INTERVAL_SIZE} commits
- Bisect exit code: ${bisect_rc}
- First bad commit: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`${BISECT_LOG#${ROOT}/}\`
- Runner log: \`${RUN_LOG#${ROOT}/}\`
- Git-bisect command count in log: ${TESTED_STEPS}
- Skip commands in log: ${SKIP_STEPS}

## Command

\`\`\`bash
RUN_ID=${RUN_ID} JOBS=${JOBS:-4} LM_BISECT_JOBS=${LM_BISECT_JOBS:-${JOBS:-4}} LM_BISECT_BUILD_TYPE=${LM_BISECT_BUILD_TYPE:-Release} LM_BISECT_ENABLE_ASSERTIONS=${LM_BISECT_ENABLE_ASSERTIONS:-ON} bash scripts/benchmark/run-validated-git-bisect.sh ${ISSUE} ${GOOD_COMMIT} ${BAD_COMMIT} ${LLVM_DIR}
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
echo "[run-validated-git-bisect] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit "${bisect_rc}"
