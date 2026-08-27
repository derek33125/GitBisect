#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds clang from the current checkout and classifies the current commit for
llvm/llvm-project#200987 using the shared validated benchmark runner.

Exit codes:
  0 => good
  1 => bad
  125 => skip
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR="${1:-${BASE_REPO:-/home/derek331/research/gitbisect-work/llvm-project}}"

exec "${ROOT_DIR}/scripts/benchmark/validated-bisect-runner.sh" pr200987 "${LLVM_DIR}"
