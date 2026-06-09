#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds clang-tidy from the current checkout and classifies the current commit
for llvm/llvm-project#192829.

Exit codes:
  0 => good
  1 => bad
  125 => skip (build/configuration failure or non-target failure)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
TOOLS_BOOTSTRAP="${ROOT_DIR}/scripts/pr172195/bootstrap-tools.sh"
TMP_DIR="${ROOT_DIR}/scratch/pr192829"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr192829.lock"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

"${TOOLS_BOOTSTRAP}" >/dev/null

mkdir -p "${LOCK_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "error: runner lock is busy at ${LOCK_FILE}" >&2
  exit 125
fi

TOOLS_BIN="${ROOT_DIR}/scripts/pr172195/tools/bin"
export PATH="${TOOLS_BIN}:${PATH}"

if ! command -v cmake >/dev/null 2>&1; then
  echo "error: cmake not found" >&2
  exit 125
fi

if ! command -v ninja >/dev/null 2>&1; then
  echo "error: ninja not found after bootstrap" >&2
  exit 125
fi

BUILD_DIR="${LLVM_DIR}/build-bisect-pr192829"
CACHE_DIR="${ROOT_DIR}/.ccache/pr192829"
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
BUILD_NICE_LEVEL=${BUILD_NICE_LEVEL:-10}
BUILD_IONICE_CLASS=${BUILD_IONICE_CLASS:-3}
TIDY_BIN="${BUILD_DIR}/bin/clang-tidy"

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
  -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra"
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

if ! run_background_friendly cmake --build "${BUILD_DIR}" --target clang-tidy -- -j"${JOBS}"; then
  echo "build failed; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${TIDY_BIN}" ]]; then
  echo "missing built clang-tidy; skipping commit" >&2
  exit 125
fi

SRC="${TMP_DIR}/empty.cpp"
cat > "${SRC}" <<'CPP'
#include <string>

void f(std::string &s) {
  s.~basic_string();
}
CPP

set +e
TIDY_STDERR=$("${TIDY_BIN}" --checks=readability-container-size-empty "${SRC}" -- 2>&1)
TIDY_RC=$?
set -e

echo "clang-tidy exit code: ${TIDY_RC}"
if [[ -n "${TIDY_STDERR}" ]]; then
  printf '%s\n' "${TIDY_STDERR}"
fi

if printf '%s\n' "${TIDY_STDERR}" | grep -Eq 'Name is not a simple identifier|NamedDecl::getName\(\)|PLEASE submit a bug report|Stack dump:'; then
  exit 1
fi
if [[ ${TIDY_RC} -eq 0 ]]; then
  exit 0
fi

exit 125
