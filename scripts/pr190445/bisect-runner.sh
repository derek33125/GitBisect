#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
SOURCE_CPP="${SCRIPT_DIR}/repro.cpp"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
TMP_DIR="${ROOT_DIR}/scratch/pr190445"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr190445.lock"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

if [[ ! -f "${SOURCE_CPP}" ]]; then
  echo "error: missing reproducer ${SOURCE_CPP}" >&2
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

if ! command -v cmake >/dev/null 2>&1 || ! command -v ninja >/dev/null 2>&1; then
  echo "error: cmake or ninja missing" >&2
  exit 125
fi

BUILD_DIR="${LLVM_DIR}/build-bisect-pr190445"
CACHE_DIR="${ROOT_DIR}/.ccache/pr190445"
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
CLANGXX_BIN="${BUILD_DIR}/bin/clang++"

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
  -G Ninja
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

if [[ ! -x "${CLANGXX_BIN}" ]]; then
  echo "missing built clang++; skipping commit" >&2
  exit 125
fi

cp "${SOURCE_CPP}" "${TMP_DIR}/repro.cpp"
OUT_S="${TMP_DIR}/repro.s"

set +e
COMPILE_STDERR=$("${CLANGXX_BIN}" -g -S -mllvm --x86-asm-syntax=intel -fno-verbose-asm -fno-crash-diagnostics -fcolor-diagnostics "${TMP_DIR}/repro.cpp" -o "${OUT_S}" 2>&1)
COMPILE_RC=$?
set -e

echo "compile exit code: ${COMPILE_RC}"
if [[ -n "${COMPILE_STDERR}" ]]; then
  printf '%s\n' "${COMPILE_STDERR}"
fi

if [[ ${COMPILE_RC} -eq 0 ]]; then
  exit 0
fi

if printf '%s\n' "${COMPILE_STDERR}" | grep -Eq 'Invalid APInt ZeroExtend request|APInt::zext|Stack dump:|PLEASE submit a bug report'; then
  exit 1
fi

echo "unexpected compile failure; skipping commit" >&2
exit 125
