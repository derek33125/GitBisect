#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR="${1:-${BASE_REPO:-/home/derek331/research/gitbisect-work/llvm-project}}"

exec "${ROOT_DIR}/scripts/benchmark/validated-bisect-runner.sh" pr203278 "${LLVM_DIR}"
