#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: trace-rerun.sh [llvm-project-path] [commit-sha]

Rebuilds LLVM with trace-friendly settings and reruns the pr172195 crash case
so the failure backtrace is captured directly from the binary output.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
TARGET_SHA=${2:-e8219e5ce84db26fd521ce5091d18e75c7afbc6a}
REPRO_C="${SCRIPT_DIR}/aa-10084.c"
PROF_TXT="${SCRIPT_DIR}/prof.txt"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
TRACE_DIR="${ROOT_DIR}/results/issues/pr172195/debug-traces"
BUILD_DIR="${LLVM_DIR}/build-pr172195-trace"
CACHE_DIR="${ROOT_DIR}/.ccache/pr172195-trace"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi

if ! git -C "${LLVM_DIR}" cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null; then
  echo "error: unknown target commit ${TARGET_SHA}" >&2
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

"${TOOLS_BOOTSTRAP}" >/dev/null

TOOLS_BIN="${SCRIPT_DIR}/tools/bin"
export PATH="${TOOLS_BIN}:${PATH}"

mkdir -p "${TRACE_DIR}" "${CACHE_DIR}"
export CCACHE_DIR="${CACHE_DIR}"
export CCACHE_BASEDIR="${LLVM_DIR}"
export CCACHE_NOHASHDIR=1
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"

ORIGINAL_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)

restore_checkout() {
  git -C "${LLVM_DIR}" checkout -q "${ORIGINAL_HEAD}" || true
}

trap restore_checkout EXIT

if command -v ccache >/dev/null 2>&1; then
  export CMAKE_C_COMPILER_LAUNCHER=ccache
  export CMAKE_CXX_COMPILER_LAUNCHER=ccache
fi

JOBS=${JOBS:-1}
if [[ "${JOBS}" -lt 1 ]]; then
  JOBS=1
fi

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

git -C "${LLVM_DIR}" checkout -q "${TARGET_SHA}"

configure_args=(
  -G Ninja
  -S "${LLVM_DIR}/llvm"
  -B "${BUILD_DIR}"
  -DLLVM_ENABLE_PROJECTS=clang
  -DLLVM_TARGETS_TO_BUILD=X86
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
  -DLLVM_ENABLE_ASSERTIONS=ON
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

cmake "${configure_args[@]}"
cmake --build "${BUILD_DIR}" --target clang llvm-profdata -- -j"${JOBS}"

PROFDATA_FILE="${TRACE_DIR}/trace.profdata"
OBJ_FILE="${TRACE_DIR}/trace.o"

"${BUILD_DIR}/bin/llvm-profdata" merge "${PROF_TXT}" -o "${PROFDATA_FILE}"

set +e
LLVM_SYMBOLIZER_PATH=$(command -v llvm-symbolizer || true) \
  "${BUILD_DIR}/bin/clang" -O3 -msse4.2 -fprofile-instr-use="${PROFDATA_FILE}" "${REPRO_C}" -o "${OBJ_FILE}" \
  >"${TRACE_DIR}/trace.stdout" 2>"${TRACE_DIR}/trace.stderr"
RC=$?
set -e

{
  echo "exit_code: ${RC}"
  echo "stdout: ${TRACE_DIR}/trace.stdout"
  echo "stderr: ${TRACE_DIR}/trace.stderr"
} > "${TRACE_DIR}/trace-summary.txt"

cat "${TRACE_DIR}/trace.stdout"
cat "${TRACE_DIR}/trace.stderr"

exit "${RC}"
