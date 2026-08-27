#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: validated-bisect-runner.sh ISSUE [llvm-project-path]

Builds clang for the current checkout and classifies the commit using the same
issue definition as the endpoint validation queue.

Exit codes:
  0 => good
  1 => bad
  125 => skip
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 1 ]]; then
  usage
  exit 0
fi

ISSUE="$1"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
LLVM_DIR="${2:-${LLVM_DIR:-${BASE_REPO:-/home/derek331/research/gitbisect-work/llvm-project}}}"
QUEUE_LIB="${ROOT}/results/issues/server-jobs/server-validation-queue-20260613.sh"

if [[ ! -f "${QUEUE_LIB}" ]]; then
  echo "error: queue library not found: ${QUEUE_LIB}" >&2
  exit 125
fi

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 125
fi

export ROOT
export BASE_REPO="${BASE_REPO:-${LLVM_DIR}}"
export WORK_ROOT="${WORK_ROOT:-$(dirname "${LLVM_DIR}")}"
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS}}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"
export LM_BISECT_QUEUE_LIBRARY_ONLY=1

# shellcheck source=/dev/null
source "${QUEUE_LIB}" crash

issue_def "${ISSUE}"

RESULTS_DIR="${ROOT}/results/issues/${ISSUE}"
TMP_DIR="${RESULTS_DIR}/tmp-git-bisect-${RUN_ID:-manual}"
WT="${LLVM_DIR}"
BUILD_DIR="${WT}/build-bisect-${ISSUE}"
LOG="${RESULTS_DIR}/${ISSUE}-git-bisect-runner-${RUN_ID:-manual}.log"

mkdir -p "${RESULTS_DIR}" "${TMP_DIR}"
export CCACHE_DIR="${ROOT}/.ccache/${ISSUE}-git-bisect"
export CCACHE_BASEDIR="${WT}"
export CCACHE_NOHASHDIR=1
mkdir -p "${CCACHE_DIR}"
ccache --set-config="max_size=${CCACHE_MAXSIZE}" >/dev/null 2>&1 || true

{
  echo "=== validated-bisect-runner ==="
  echo "issue: ${ISSUE}"
  echo "llvm-project: ${WT}"
  echo "commit: $(git -C "${WT}" rev-parse --short HEAD)"
  echo "jobs: ${JOBS}"
  echo "build type: ${LM_BISECT_BUILD_TYPE}"
  echo "assertions: ${LM_BISECT_ENABLE_ASSERTIONS}"
  echo "ccache dir: ${CCACHE_DIR}"
} | tee -a "${LOG}"

build_rc=0
set +e
configure_and_build
build_rc=$?
set -e
if [[ ${build_rc} -ne 0 ]]; then
  echo "build failed; skipping commit" | tee -a "${LOG}" >&2
  exit 125
fi

repro_rc=0
set +e
run_repro "$(git -C "${WT}" rev-parse --short HEAD)"
repro_rc=$?
set -e

safe="$(safe_name "$(git -C "${WT}" rev-parse --short HEAD)")"
err="${TMP_DIR}/${safe}.err"
out="${TMP_DIR}/${safe}.out"
[[ -f "${out}" ]] && cat "${out}" >>"${LOG}" || true
[[ -f "${err}" ]] && cat "${err}" >>"${LOG}" || true

verdict="$(classify_repro "${repro_rc}" "${err}")"
echo "repro exit code: ${repro_rc}" | tee -a "${LOG}"
echo "verdict: ${verdict}" | tee -a "${LOG}"

case "${verdict}" in
  good) exit 0 ;;
  bad) exit 1 ;;
  *) exit 125 ;;
esac
