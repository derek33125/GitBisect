#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds a minimal clang from the current checkout and classifies the
current commit for llvm/llvm-project#165445.

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
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
HEADER_SRC="${SCRIPT_DIR}/badHeader.h"
MODULE_SRC="${SCRIPT_DIR}/badModule.cppm"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
TMP_DIR="${ROOT_DIR}/scratch/pr165445"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr165445.lock"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

if [[ ! -f "${HEADER_SRC}" || ! -f "${MODULE_SRC}" ]]; then
  echo "error: missing reproducer files for pr165445" >&2
  exit 125
fi

"${TOOLS_BOOTSTRAP}" >/dev/null

mkdir -p "${LOCK_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "error: runner lock is busy at ${LOCK_FILE}" >&2
  exit 125
fi

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

BUILD_DIR="${LLVM_DIR}/build-bisect-pr165445"
CACHE_DIR="${ROOT_DIR}/.ccache/pr165445"
BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
mkdir -p "${CACHE_DIR}"
export CCACHE_DIR="${CACHE_DIR}"
export CCACHE_BASEDIR="${LLVM_DIR}"
export CCACHE_NOHASHDIR=1
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"

if command -v ccache >/dev/null 2>&1; then
  export CMAKE_C_COMPILER_LAUNCHER=ccache
  export CMAKE_CXX_COMPILER_LAUNCHER=ccache
fi

JOBS=${JOBS:-${LM_BISECT_JOBS:-2}}
if [[ "${JOBS}" -lt 1 ]]; then
  JOBS=1
fi
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"
BUILD_NICE_LEVEL=${BUILD_NICE_LEVEL:-10}
BUILD_IONICE_CLASS=${BUILD_IONICE_CLASS:-3}
GENERATOR="Ninja"
CLANG_BIN="${BUILD_DIR}/bin/clang++"

run_background_friendly() {
  local cmd=("$@")
  if command -v ionice >/dev/null 2>&1; then
    cmd=(ionice -c "${BUILD_IONICE_CLASS}" "${cmd[@]}")
  fi
  if command -v nice >/dev/null 2>&1; then
    cmd=(nice -n "${BUILD_NICE_LEVEL}" "${cmd[@]}")
  fi
  "${cmd[@]}"
}

echo "=== bisect-runner ==="
echo "llvm-project: ${LLVM_DIR}"
echo "commit: $(git -C "${LLVM_DIR}" rev-parse --short HEAD)"
echo "jobs: ${JOBS}"
echo "nice level: ${BUILD_NICE_LEVEL}"
echo "ionice class: ${BUILD_IONICE_CLASS}"
echo "build type: ${BUILD_TYPE}"
echo "assertions: ${ENABLE_ASSERTIONS}"
echo "build dir: ${BUILD_DIR}"
echo "ccache dir: ${CCACHE_DIR}"

rm -rf "${BUILD_DIR}" "${TMP_DIR}"
mkdir -p "${BUILD_DIR}" "${TMP_DIR}"

configure_args=(
  -G "${GENERATOR}"
  -S "${LLVM_DIR}/llvm"
  -B "${BUILD_DIR}"
  -DLLVM_ENABLE_PROJECTS=clang
  -DLLVM_TARGETS_TO_BUILD=X86
  -DCMAKE_BUILD_TYPE="${BUILD_TYPE}"
  -DLLVM_ENABLE_ASSERTIONS="${ENABLE_ASSERTIONS}"
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

if ! run_background_friendly cmake "${configure_args[@]}"; then
  echo "configure failed; skipping commit" >&2
  exit 125
fi

if ! run_background_friendly cmake --build "${BUILD_DIR}" --target clang -- -j"${JOBS}"; then
  echo "build failed; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${CLANG_BIN}" ]]; then
  echo "missing built clang++; skipping commit" >&2
  exit 125
fi

cp "${HEADER_SRC}" "${TMP_DIR}/badHeader.h"
cp "${MODULE_SRC}" "${TMP_DIR}/badModule.cppm"

set +e
HEADER_STDERR=$("${CLANG_BIN}" -std=gnu++23 -fmodule-header=system "${TMP_DIR}/badHeader.h" -I "${TMP_DIR}" 2>&1)
HEADER_RC=$?
set -e

echo "header compile exit code: ${HEADER_RC}"
if [[ -n "${HEADER_STDERR}" ]]; then
  printf '%s\n' "${HEADER_STDERR}"
fi
if [[ ${HEADER_RC} -ne 0 ]]; then
  echo "header-unit compile failed; skipping commit" >&2
  exit 125
fi

PCM_FILE="${TMP_DIR}/badHeader.pcm"
if [[ ! -f "${TMP_DIR}/badHeader.pcm" ]]; then
  echo "missing generated badHeader.pcm; skipping commit" >&2
  exit 125
fi

set +e
MODULE_STDERR=$("${CLANG_BIN}" -std=gnu++23 -fmodule-file="${PCM_FILE}" --precompile "${TMP_DIR}/badModule.cppm" -I "${TMP_DIR}" 2>&1)
MODULE_RC=$?
set -e

echo "module compile exit code: ${MODULE_RC}"
if [[ -n "${MODULE_STDERR}" ]]; then
  printf '%s\n' "${MODULE_STDERR}"
fi

if [[ ${MODULE_RC} -eq 0 ]]; then
  exit 0
fi

if printf '%s\n' "${MODULE_STDERR}" | grep -Eq 'ASTWriter::GenerateNameLookupTable|WriteDeclContextVisibleUpdate|PLEASE submit a bug report|Stack dump:'; then
  exit 1
fi

echo "unexpected module compile failure; skipping commit" >&2
exit 125
