#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: validate-good-endpoint.sh [llvm-project-path]

Validates the documented fix commit for PR196450 as a candidate good endpoint.
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
RESULTS_DIR="${ROOT_DIR}/results/issues/pr196450"
RESULT_NOTE="${RESULTS_DIR}/pr196450-validation-note.md"

GOOD_COMMIT="545f16217dff218c85c94990566439d3aedfb8e1"
PARENT_COMMIT="cc017256e743c325d05428cb15a46ed54ff5ed5d"

mkdir -p "${RESULTS_DIR}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)
cleanup() {
  set +e
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Validating good commit ${GOOD_COMMIT}"
git -C "${LLVM_DIR}" checkout -q "${GOOD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}" 2>&1 | tee "${RESULTS_DIR}/pr196450-validate-good.log"
rc=$?
set -e

cat > "${RESULT_NOTE}" <<EOF
# PR196450 Good-Endpoint Validation Note

- Issue: https://github.com/llvm/llvm-project/issues/196450
- Candidate good commit: \`${GOOD_COMMIT}\`
- Parent commit for later regression discovery: \`${PARENT_COMMIT}\`
- Validation settings:
  - \`Release\`
  - \`LLVM_ENABLE_ASSERTIONS=ON\`
  - \`jobs=4\`
- Runner log: \`results/issues/pr196450/pr196450-validate-good.log\`
- Runner exit code: \`${rc}\`
EOF

if [[ ${rc} -ne 0 ]]; then
  echo "good endpoint failed validation with rc=${rc}" >&2
  exit 1
fi

echo "Good endpoint validation succeeded." | tee -a "${RESULT_NOTE}"
