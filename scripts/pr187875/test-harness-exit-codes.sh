#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUN_BISECT="${SCRIPT_DIR}/run-bisect.sh"
VALIDATE_ENDPOINTS="${SCRIPT_DIR}/validate-endpoints.sh"

if grep -q 'if "${RUNNER}" "${LLVM_DIR}"; then' "${RUN_BISECT}"; then
  echo "run-bisect.sh still loses the known-bad runner exit status" >&2
  exit 1
fi

if grep -q 'if ! "${RUNNER}" "${LLVM_DIR}"; then' "${RUN_BISECT}" "${VALIDATE_ENDPOINTS}"; then
  echo "a harness script still captures an inverted runner exit status" >&2
  exit 1
fi

echo "harness exit-code checks passed"
