#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-bisect.sh [llvm-project-path]

Validates a good/bad range for issue #176682 and runs git bisect with the
local bisect runner.
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
RESULTS_DIR="${ROOT_DIR}/results/issues/pr176682"
RESULT_NOTE="${RESULTS_DIR}/pr176682-bisect.md"

BAD_COMMIT="af98aadc63637ac674d403c77ccba42b05a3d11c"
GOOD_REF_DEFAULT="llvmorg-21.1.0"
GOOD_REF="${GOOD_REF:-${GOOD_REF_DEFAULT}}"

mkdir -p "${RESULTS_DIR}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" fetch --tags origin

GOOD_COMMIT=$(git -C "${LLVM_DIR}" rev-list -n 1 "${GOOD_REF}") || {
  echo "error: could not resolve GOOD_REF=${GOOD_REF}" >&2
  exit 1
}

echo "Using good ref ${GOOD_REF} => ${GOOD_COMMIT}"
echo "Using bad commit ${BAD_COMMIT}"

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)

cleanup() {
  set +e
  git -C "${LLVM_DIR}" bisect reset >/dev/null 2>&1 || true
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "${LLVM_DIR}" checkout -q "${BAD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
bad_rc=$?
set -e
if [[ ${bad_rc} -eq 0 ]]; then
  echo "error: known bad revision classified as good" >&2
  exit 1
fi
if [[ ${bad_rc} -ne 1 ]]; then
  echo "error: known bad revision produced skip rc=${bad_rc}" >&2
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
git -C "${LLVM_DIR}" bisect log > "${RESULTS_DIR}/pr176682-bisect-log.txt"

FIRST_BAD=$(sed -n 's/^# first bad commit: \[\([0-9a-f]\{7,\}\)\].*/\1/p' "${RESULTS_DIR}/pr176682-bisect-log.txt" | tail -n 1)
if [[ -z "${FIRST_BAD}" ]]; then
  echo "error: failed to extract first bad commit from bisect log" >&2
  exit 1
fi
FIRST_BAD_SUBJECT=$(git -C "${LLVM_DIR}" show -s --format=%s "${FIRST_BAD}")
FIRST_BAD_DATE=$(git -C "${LLVM_DIR}" show -s --format=%cs "${FIRST_BAD}")

cat > "${RESULT_NOTE}" <<EOF
# PR176682 Bisect Result

- Issue: https://github.com/llvm/llvm-project/issues/176682
- Reproducer: \`scripts/pr176682/aa-831991.c\` + \`scripts/pr176682/prof.txt\`
- Good reference: \`${GOOD_REF}\`
- Good commit: \`${GOOD_COMMIT}\`
- Known bad commit: \`${BAD_COMMIT}\`
- First bad commit: \`${FIRST_BAD}\`
- Subject: ${FIRST_BAD_SUBJECT}
- Commit date: ${FIRST_BAD_DATE}
- Bisect log: \`results/issues/pr176682/pr176682-bisect-log.txt\`

## Command

\`\`\`bash
bash scripts/pr176682/run-bisect.sh
\`\`\`
EOF

echo "First bad commit: ${FIRST_BAD} ${FIRST_BAD_SUBJECT}"
echo "Result note written to ${RESULT_NOTE}"
