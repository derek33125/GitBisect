#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-bisect.sh [llvm-project-path]

Runs git bisect for issue #196244 using the long benchmark interval.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
RUNNER="${SCRIPT_DIR}/bisect-runner.sh"
RESULTS_DIR="${ROOT_DIR}/results/issues/pr196244"
RESULT_NOTE="${RESULTS_DIR}/pr196244-bisect.md"
RUN_LOG="${RESULTS_DIR}/pr196244-bisect-run.log"

GOOD_COMMIT="19a71f6bdf2dddb10764939e7f0ec2b98dba76c9"
GOOD_REF="llvmorg-8.0.1"
BAD_COMMIT="4434dabb69916856b824f68a64b029c67175e532"
BAD_REF="llvmorg-22.1.0"

mkdir -p "${RESULTS_DIR}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

clear_stale_git_lock() {
  local lock_file="${LLVM_DIR}/.git/index.lock"
  if [[ ! -e "${lock_file}" ]]; then
    return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    if lsof "${lock_file}" >/dev/null 2>&1; then
      echo "error: active git lock present at ${lock_file}" >&2
      return 1
    fi
  fi
  rm -f "${lock_file}"
}

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)

cleanup() {
  set +e
  git -C "${LLVM_DIR}" bisect reset >/dev/null 2>&1 || true
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

clear_stale_git_lock

exec > >(tee -a "${RUN_LOG}") 2>&1

echo "[run-bisect] start $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Good reference: ${GOOD_REF} ${GOOD_COMMIT}"
echo "Bad reference: ${BAD_REF} ${BAD_COMMIT}"

git -C "${LLVM_DIR}" checkout -q "${BAD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
bad_rc=$?
set -e
if [[ ${bad_rc} -ne 1 ]]; then
  echo "error: known bad revision produced rc=${bad_rc}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" checkout -q "${GOOD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
good_rc=$?
set -e
if [[ ${good_rc} -ne 0 ]]; then
  echo "error: candidate good revision failed classification rc=${good_rc}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" bisect start
git -C "${LLVM_DIR}" bisect bad "${BAD_COMMIT}"
git -C "${LLVM_DIR}" bisect good "${GOOD_COMMIT}"
git -C "${LLVM_DIR}" bisect run "${RUNNER}" "${LLVM_DIR}"
git -C "${LLVM_DIR}" bisect log > "${RESULTS_DIR}/pr196244-bisect-log.txt"

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${RESULTS_DIR}/pr196244-bisect-log.txt" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  echo "error: failed to extract first bad commit from bisect log" >&2
  exit 1
fi
FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")

cat > "${RESULT_NOTE}" <<EOF
# PR196244 Bisect Result

- Issue: https://github.com/llvm/llvm-project/issues/196244
- Good reference: \`${GOOD_REF}\`
- Good commit: \`${GOOD_COMMIT}\`
- Bad reference: \`${BAD_REF}\`
- Bad commit: \`${BAD_COMMIT}\`
- First bad commit: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`results/issues/pr196244/pr196244-bisect-log.txt\`

## Command

\`\`\`bash
bash scripts/pr196244/run-bisect.sh
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
echo "[run-bisect] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
