#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-bisect.sh [llvm-project-path]

Runs git bisect for issue #193932 using the normalized searchable range:
  good = regression^
  bad  = fix^

The expected first bad commit is the documented regression commit 366f48890d64.
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
RESULTS_DIR="${ROOT_DIR}/results/issues/pr193932"
RESULT_NOTE="${RESULTS_DIR}/pr193932-bisect.md"

DOCUMENTED_FIRST_BAD="366f48890d643e15e1317ada300f2cc1be437721"
DOCUMENTED_FIX="6c79d6d6e40283fafb9fa917e58ce97a33252018"
GOOD_COMMIT="b1b84a629d5a6d7ed3d4f05077c1db8b6898115b"
BAD_COMMIT="3b832a0c54877e083ce982f36762734f091f09f6"

mkdir -p "${RESULTS_DIR}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)

cleanup() {
  set +e
  git -C "${LLVM_DIR}" bisect reset >/dev/null 2>&1 || true
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "${LLVM_DIR}" checkout -q "${GOOD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
good_rc=$?
set -e
if [[ ${good_rc} -ne 0 ]]; then
  echo "error: normalized good endpoint failed classification rc=${good_rc}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" checkout -q "${DOCUMENTED_FIRST_BAD}"
set +e
"${RUNNER}" "${LLVM_DIR}"
first_bad_rc=$?
set -e
if [[ ${first_bad_rc} -ne 1 ]]; then
  echo "error: documented first bad commit did not classify as bad rc=${first_bad_rc}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" checkout -q "${BAD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
bad_rc=$?
set -e
if [[ ${bad_rc} -ne 1 ]]; then
  echo "error: normalized bad endpoint did not classify as bad rc=${bad_rc}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" bisect start
git -C "${LLVM_DIR}" bisect bad "${BAD_COMMIT}"
git -C "${LLVM_DIR}" bisect good "${GOOD_COMMIT}"
git -C "${LLVM_DIR}" bisect run "${RUNNER}" "${LLVM_DIR}"
git -C "${LLVM_DIR}" bisect log > "${RESULTS_DIR}/pr193932-bisect-log.txt"

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${RESULTS_DIR}/pr193932-bisect-log.txt" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  echo "error: failed to extract first bad commit from bisect log" >&2
  exit 1
fi

FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")

cat > "${RESULT_NOTE}" <<EOF
# PR193932 Bisect Result

- Issue: https://github.com/llvm/llvm-project/issues/193932
- Documented first bad commit: \`${DOCUMENTED_FIRST_BAD}\`
- Documented fix commit: \`${DOCUMENTED_FIX}\`
- Normalized good endpoint: \`${GOOD_COMMIT}\` (documented first bad commit parent)
- Normalized bad endpoint: \`${BAD_COMMIT}\` (documented fix commit parent)
- First bad commit from git bisect: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`results/issues/pr193932/pr193932-bisect-log.txt\`

## Command

\`\`\`bash
bash scripts/pr193932/run-bisect.sh
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
