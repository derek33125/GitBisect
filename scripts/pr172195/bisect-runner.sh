#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds a minimal LLVM toolchain from the current checkout and classifies the
current commit for llvm/llvm-project#172195.

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
REPRO_C="${SCRIPT_DIR}/aa-10084.c"
PROF_TXT="${SCRIPT_DIR}/prof.txt"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
CLASSIFIER="${SCRIPT_DIR}/classify-compile-stderr.sh"
TMP_DIR="${ROOT_DIR}/scratch/pr172195"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr172195.lock"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

if [[ ! -f "${REPRO_C}" ]]; then
  echo "error: missing reproducer ${REPRO_C}" >&2
  exit 125
fi

if [[ ! -f "${PROF_TXT}" ]]; then
  echo "error: missing profile text ${PROF_TXT}" >&2
  exit 125
fi

if [[ ! -x "${CLASSIFIER}" ]]; then
  echo "error: missing stderr classifier ${CLASSIFIER}" >&2
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

BUILD_DIR="${LLVM_DIR}/build-bisect-pr172195"
CACHE_DIR="${ROOT_DIR}/.ccache/pr172195"
BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-RelWithDebInfo}"
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
CLANG_BIN="${BUILD_DIR}/bin/clang"
LLVM_PROFDATA_BIN="${BUILD_DIR}/bin/llvm-profdata"

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

if ! run_background_friendly cmake --build "${BUILD_DIR}" --target clang llvm-profdata -- -j"${JOBS}"; then
  echo "build failed; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${CLANG_BIN}" ]]; then
  echo "missing built clang; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${LLVM_PROFDATA_BIN}" ]]; then
  echo "missing built llvm-profdata; skipping commit" >&2
  exit 125
fi

PROFDATA_FILE="${TMP_DIR}/prof.profdata"
OBJ_FILE_O3="${TMP_DIR}/repro-o3.o"
OBJ_FILE_OS="${TMP_DIR}/repro-os.o"

if ! "${LLVM_PROFDATA_BIN}" merge "${PROF_TXT}" -o "${PROFDATA_FILE}"; then
  echo "llvm-profdata merge failed; skipping commit" >&2
  exit 125
fi

if ! "${CLANG_BIN}" -c -O3 -fprofile-instr-use="${PROFDATA_FILE}" "${REPRO_C}" -o "${OBJ_FILE_O3}"; then
  echo "O3 compile failed; skipping commit" >&2
  exit 125
fi

set +e
COMPILE_STDERR=$("${CLANG_BIN}" -c -Os -fprofile-instr-use="${PROFDATA_FILE}" "${REPRO_C}" -o "${OBJ_FILE_OS}" 2>&1)
COMPILE_RC=$?
set -e

echo "Os compile exit code: ${COMPILE_RC}"
if [[ -n "${COMPILE_STDERR}" ]]; then
  printf '%s\n' "${COMPILE_STDERR}"
fi

if [[ ${COMPILE_RC} -eq 0 ]]; then
  exit 0
fi

if printf '%s' "${COMPILE_STDERR}" | "${CLASSIFIER}"; then
  exit 1
fi

echo "unexpected Os compile failure; skipping commit" >&2
exit 125
