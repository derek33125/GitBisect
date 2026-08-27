#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Classifies the current checkout for llvm/llvm-project#202003 using the shared
validated benchmark runner.

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
RUNNER="${ROOT_DIR}/scripts/benchmark/validated-bisect-runner.sh"

if [[ ! -x "${RUNNER}" ]]; then
  echo "error: missing shared runner: ${RUNNER}" >&2
  exit 125
fi

if [[ $# -gt 0 ]]; then
  exec "${RUNNER}" pr202003 "$1"
fi

exec "${RUNNER}" pr202003
