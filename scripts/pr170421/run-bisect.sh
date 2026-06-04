#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-bisect.sh [llvm-project-path]

Runs git bisect for issue #170421 using the normalized searchable range:
  good = regression^
  bad  = fix^

The expected first bad commit is the documented regression commit 97fdc23.
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
RESULTS_DIR="${ROOT_DIR}/results/issues/pr170421"
RESULT_NOTE="${RESULTS_DIR}/pr170421-bisect.md"

DOCUMENTED_FIRST_BAD="97fdc237ddda7565c7c902cc4fc764f73e70686b"
DOCUMENTED_FIX="4e859c5a95ec0de993f5f8e75f1b5a6733ac7489"
GOOD_COMMIT="8e4fb4beada4ca34a2775512964ebe478967049d"
BAD_COMMIT="8cfda791054bf9a7cdb43369e36caae2a56032c6"

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
git -C "${LLVM_DIR}" bisect log > "${RESULTS_DIR}/pr170421-bisect-log.txt"

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${RESULTS_DIR}/pr170421-bisect-log.txt" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  echo "error: failed to extract first bad commit from bisect log" >&2
  exit 1
fi

FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")

cat > "${RESULT_NOTE}" <<EOF
# PR170421 Bisect Result

- Issue: https://github.com/llvm/llvm-project/issues/170421
- Documented first bad commit: \`${DOCUMENTED_FIRST_BAD}\`
- Documented fix commit: \`${DOCUMENTED_FIX}\`
- Normalized good endpoint: \`${GOOD_COMMIT}\` (documented first bad commit parent)
- Normalized bad endpoint: \`${BAD_COMMIT}\` (documented fix commit parent)
- First bad commit from git bisect: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`results/issues/pr170421/pr170421-bisect-log.txt\`

## Command

\`\`\`bash
bash scripts/pr170421/run-bisect.sh
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
