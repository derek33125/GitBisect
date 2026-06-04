#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds a minimal LLVM toolchain from the current checkout and classifies the
current commit for llvm/llvm-project#187875.

Exit codes:
  0 => good
  1 => bad
  125 => skip (build/configuration failure)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"${ROOT_DIR}/llvm-project"}
REPRO_LL="${SCRIPT_DIR}/repro.ll"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

if [[ ! -f "${REPRO_LL}" ]]; then
  echo "error: missing reproducer ${REPRO_LL}" >&2
  exit 125
fi

"${TOOLS_BOOTSTRAP}" >/dev/null

TOOLS_BIN="${SCRIPT_DIR}/tools/bin"
export PATH="${TOOLS_BIN}:${PATH}"

if ! command -v cmake >/dev/null 2>&1; then
  echo "error: cmake not found" >&2
  exit 125
fi

if ! command -v ninja >/dev/null 2>&1; then
  echo "error: ninja not found after bootstrap" >&2
  exit 125
fi

if ! command -v gcc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then
  echo "error: no C compiler found" >&2
  exit 125
fi

BUILD_DIR="${LLVM_DIR}/build-bisect-pr187875"
CACHE_DIR="${ROOT_DIR}/.ccache/pr187875"
mkdir -p "${CACHE_DIR}"
export CCACHE_DIR="${CACHE_DIR}"
export CCACHE_BASEDIR="${LLVM_DIR}"
export CCACHE_NOHASHDIR=1
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"

if command -v ccache >/dev/null 2>&1; then
  export CMAKE_C_COMPILER_LAUNCHER=ccache
  export CMAKE_CXX_COMPILER_LAUNCHER=ccache
fi

JOBS=${JOBS:-$(nproc)}
GENERATOR="Ninja"
OPT_BIN="${BUILD_DIR}/bin/opt"
LLI_BIN="${BUILD_DIR}/bin/lli"

echo "=== bisect-runner ==="
echo "llvm-project: ${LLVM_DIR}"
echo "commit: $(git -C "${LLVM_DIR}" rev-parse --short HEAD)"
echo "jobs: ${JOBS}"
echo "build dir: ${BUILD_DIR}"
echo "ccache dir: ${CCACHE_DIR}"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

configure_args=(
  -G "${GENERATOR}"
  -S "${LLVM_DIR}/llvm"
  -B "${BUILD_DIR}"
  -DLLVM_ENABLE_PROJECTS=
  -DLLVM_TARGETS_TO_BUILD=X86
  -DCMAKE_BUILD_TYPE=Release
  -DLLVM_ENABLE_ASSERTIONS=OFF
  -DLLVM_INCLUDE_TESTS=OFF
  -DLLVM_INCLUDE_EXAMPLES=OFF
  -DLLVM_INCLUDE_BENCHMARKS=OFF
  -DLLVM_INCLUDE_UTILS=ON
  -DLLVM_BUILD_TOOLS=ON
)

if [[ -n "${CMAKE_C_COMPILER_LAUNCHER:-}" ]]; then
  configure_args+=(
    -DCMAKE_C_COMPILER_LAUNCHER="${CMAKE_C_COMPILER_LAUNCHER}"
    -DCMAKE_CXX_COMPILER_LAUNCHER="${CMAKE_CXX_COMPILER_LAUNCHER}"
  )
fi

if ! cmake "${configure_args[@]}"; then
  echo "configure failed; skipping commit" >&2
  exit 125
fi

if ! cmake --build "${BUILD_DIR}" --target opt lli -- -j"${JOBS}"; then
  echo "build failed; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${OPT_BIN}" || ! -x "${LLI_BIN}" ]]; then
  echo "missing built tools; skipping commit" >&2
  exit 125
fi

BASE_RC=0
VEC_RC=0

set +e
"${OPT_BIN}" -passes=verify "${REPRO_LL}" | "${LLI_BIN}"
BASE_RC=$?
"${OPT_BIN}" -passes=loop-vectorize "${REPRO_LL}" | "${LLI_BIN}"
VEC_RC=$?
set -e

echo "baseline rc: ${BASE_RC}"
echo "vectorized rc: ${VEC_RC}"

if [[ ${BASE_RC} -ne 55 ]]; then
  echo "unexpected baseline result; skipping commit" >&2
  exit 125
fi

if [[ ${VEC_RC} -eq 55 ]]; then
  exit 0
fi

if [[ ${VEC_RC} -eq 53 ]]; then
  exit 1
fi

echo "unexpected vectorized result ${VEC_RC}; skipping commit" >&2
exit 125
