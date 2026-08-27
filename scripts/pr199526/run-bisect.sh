#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-bisect.sh [llvm-project-path]

Runs git bisect for issue #199526 using the validated normalized interval.
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
RESULTS_DIR="${ROOT_DIR}/results/issues/pr199526"
RESULT_NOTE="${RESULTS_DIR}/pr199526-bisect.md"
RUN_LOG="${RESULTS_DIR}/pr199526-bisect-run.log"

GOOD_COMMIT="e9f758a59b2f887acb07e26e2d480c7369a72009"
GOOD_REF="merge-base(llvmorg-22.1.0, bad)"
BAD_COMMIT="942292bf75168377cc06dfa2ba90a552d6d24a69"
BAD_REF="fix-parent"

mkdir -p "${RESULTS_DIR}"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

clear_stale_git_lock() {
  local git_dir
  git_dir=$(git -C "${LLVM_DIR}" rev-parse --git-dir)
  if [[ "${git_dir}" != /* ]]; then
    git_dir="${LLVM_DIR}/${git_dir}"
  fi
  local lock_file="${git_dir}/index.lock"
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
git -C "${LLVM_DIR}" bisect log > "${RESULTS_DIR}/pr199526-bisect-log.txt"

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${RESULTS_DIR}/pr199526-bisect-log.txt" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  echo "error: failed to extract first bad commit from bisect log" >&2
  exit 1
fi
FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")
INTERVAL_SIZE=$(git -C "${LLVM_DIR}" rev-list --count "${GOOD_COMMIT}..${BAD_COMMIT}")

cat > "${RESULT_NOTE}" <<EOF
# PR199526 Bisect Result

- Issue: https://github.com/llvm/llvm-project/issues/199526
- Good reference: \`${GOOD_REF}\`
- Good commit: \`${GOOD_COMMIT}\`
- Bad reference: \`${BAD_REF}\`
- Bad commit: \`${BAD_COMMIT}\`
- Interval size: ${INTERVAL_SIZE} commits
- First bad commit: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`results/issues/pr199526/pr199526-bisect-log.txt\`

## Command

\`\`\`bash
bash scripts/pr199526/run-bisect.sh
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
echo "[run-bisect] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
