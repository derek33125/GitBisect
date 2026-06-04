#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: validate-endpoints.sh [llvm-project-path]

Validates the chosen good and bad revisions for PR176682 before running
git bisect. This does not run bisect itself.
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

BAD_COMMIT="af98aadc63637ac674d403c77ccba42b05a3d11c"
GOOD_REF="${GOOD_REF:-llvmorg-21.1.0}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

GOOD_COMMIT=$(git -C "${LLVM_DIR}" rev-list -n 1 "${GOOD_REF}") || {
  echo "error: could not resolve GOOD_REF=${GOOD_REF}" >&2
  exit 1
}

ORIG_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)
cleanup() {
  set +e
  git -C "${LLVM_DIR}" checkout -q "${ORIG_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Validating good ref ${GOOD_REF} => ${GOOD_COMMIT}"
git -C "${LLVM_DIR}" checkout -q "${GOOD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
rc=$?
set -e
if [[ ${rc} -ne 0 ]]; then
  echo "good endpoint failed validation with rc=${rc}" >&2
  exit 1
fi

echo "Validating bad commit ${BAD_COMMIT}"
git -C "${LLVM_DIR}" checkout -q "${BAD_COMMIT}"
set +e
"${RUNNER}" "${LLVM_DIR}"
rc=$?
set -e
if [[ ${rc} -ne 1 ]]; then
  echo "bad endpoint did not validate as bad; rc=${rc}" >&2
  exit 1
fi

echo "Endpoint validation succeeded."
