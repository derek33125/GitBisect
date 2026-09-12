#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: llvm-impact-runner.sh ISSUE LLVM_PROJECT_PATH

Build and classify one commit for an approved LLVM impact case.

Exit codes:
  0 => good: the exact reproducer completes successfully
  1 => bad: the exact case-specific crash signature is present
  125 => skip: build failure or any nonmatching compiler failure
EOF
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"

case_def() {
  ISSUE="$1"
  LLVM_TARGETS_TO_BUILD=X86
  REPRODUCER="${ROOT}/scripts/impact/${ISSUE}/reproducer.cpp"
  REPRODUCER_SHA256="fce4d06b687abfc3d35c03bc982a06f65a8a292d7b7eae549a64bccdeb0d667f"
  case "${ISSUE}" in
    llvm178777)
      ;;
    llvm212819)
      LLVM_TARGETS_TO_BUILD=AArch64
      REPRODUCER="${ROOT}/scripts/impact/${ISSUE}/reproducer.ll"
      REPRODUCER_SHA256="1f461f69d14441ade2595f63834d70bb7bc3234a8cbbe8d9e53c22772cc8ded4"
      ;;
    *)
      echo "error: unsupported impact issue: ${ISSUE}" >&2
      return 2
      ;;
  esac
}

classify_repro() {
  local issue="$1"
  local repro_rc="$2"
  local stderr_file="$3"

  case "${issue}" in
    llvm178777)
      if grep -Fq "fatal error: error in backend: Cannot select" "${stderr_file}" \
          && grep -Fq "any_extend" "${stderr_file}" \
          && grep -Fq "X86ISD::PINSRB" "${stderr_file}" \
          && grep -Fq "udivrem" "${stderr_file}"; then
        printf 'bad\n'
      elif [[ "${repro_rc}" -eq 0 ]]; then
        printf 'good\n'
      else
        printf 'skip\n'
      fi
      ;;
    llvm212819)
      if grep -Fq "Failed to evaluate function length in SEH unwind info" "${stderr_file}" \
          && grep -Fq "AArch64 Assembly Printer" "${stderr_file}"; then
        printf 'bad\n'
      elif [[ "${repro_rc}" -eq 0 ]]; then
        printf 'good\n'
      else
        printf 'skip\n'
      fi
      ;;
    *)
      printf 'skip\n'
      ;;
  esac
}

if [[ "${LLVM_IMPACT_RUNNER_LIBRARY_ONLY:-0}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

case_def "$1"
LLVM_DIR="$2"
RUN_ID="${RUN_ID:-manual}"
JOBS="${JOBS:-2}"
LLVM_BUILD_TYPE="${LLVM_BUILD_TYPE:-Release}"
LLVM_ENABLE_ASSERTIONS="${LLVM_ENABLE_ASSERTIONS:-OFF}"
RESULTS_DIR="${ROOT}/results/impact-study/${ISSUE}"
BUILD_DIR="${LLVM_DIR}/build-impact-${ISSUE}"
TMP_DIR="${RESULTS_DIR}/tmp-${RUN_ID}"
CCACHE_DIR="${ROOT}/.ccache/impact-${ISSUE}"

if ! git -C "${LLVM_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: expected llvm-project checkout at ${LLVM_DIR}" >&2
  exit 125
fi
if [[ ! "${JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: JOBS must be a positive integer" >&2
  exit 125
fi
if [[ ! -f "${REPRODUCER}" ]]; then
  echo "error: missing frozen reproducer: ${REPRODUCER}" >&2
  exit 125
fi

actual_sha256=$(sha256sum "${REPRODUCER}" | awk '{print $1}')
if [[ "${actual_sha256}" != "${REPRODUCER_SHA256}" ]]; then
  echo "error: reproducer hash mismatch for ${ISSUE}" >&2
  echo "expected: ${REPRODUCER_SHA256}" >&2
  echo "actual:   ${actual_sha256}" >&2
  exit 125
fi

mkdir -p "${RESULTS_DIR}" "${TMP_DIR}" "${CCACHE_DIR}"
export CCACHE_DIR
export CCACHE_BASEDIR="${LLVM_DIR}"
export CCACHE_NOHASHDIR=1
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
ccache --set-config="max_size=${CCACHE_MAXSIZE}" >/dev/null 2>&1 || true

commit=$(git -C "${LLVM_DIR}" rev-parse HEAD)
log="${RESULTS_DIR}/${RUN_ID}-${commit}.runner.log"

run_friendly() {
  local command=("$@")
  if command -v ionice >/dev/null 2>&1; then
    command=(ionice -c "${BUILD_IONICE_CLASS:-3}" "${command[@]}")
  fi
  if command -v nice >/dev/null 2>&1; then
    command=(nice -n "${BUILD_NICE_LEVEL:-10}" "${command[@]}")
  fi
  "${command[@]}"
}

{
  echo "=== llvm-impact-runner ==="
  echo "issue: ${ISSUE}"
  echo "commit: ${commit}"
  echo "reproducer: ${REPRODUCER}"
  echo "reproducer sha256: ${actual_sha256}"
  echo "targets: ${LLVM_TARGETS_TO_BUILD}"
  echo "jobs: ${JOBS}"
  echo "build type: ${LLVM_BUILD_TYPE}"
  echo "assertions: ${LLVM_ENABLE_ASSERTIONS}"
} | tee "${log}"

rm -rf "${BUILD_DIR}"
configure=(
  cmake -G Ninja
  -S "${LLVM_DIR}/llvm"
  -B "${BUILD_DIR}"
  -DLLVM_ENABLE_PROJECTS=clang
  -DLLVM_TARGETS_TO_BUILD="${LLVM_TARGETS_TO_BUILD}"
  -DCMAKE_BUILD_TYPE="${LLVM_BUILD_TYPE}"
  -DLLVM_ENABLE_ASSERTIONS="${LLVM_ENABLE_ASSERTIONS}"
  -DLLVM_INCLUDE_TESTS=OFF
  -DLLVM_INCLUDE_EXAMPLES=OFF
  -DLLVM_INCLUDE_BENCHMARKS=OFF
  -DLLVM_INCLUDE_UTILS=ON
  -DLLVM_BUILD_TOOLS=ON
)
if command -v ccache >/dev/null 2>&1; then
  configure+=(
    -DCMAKE_C_COMPILER_LAUNCHER=ccache
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
  )
fi

if ! run_friendly "${configure[@]}" >>"${log}" 2>&1; then
  echo "configure failed; skipping commit" | tee -a "${log}" >&2
  exit 125
fi
if ! run_friendly cmake --build "${BUILD_DIR}" --target clang -- -j"${JOBS}" \
    >>"${log}" 2>&1; then
  echo "build failed; skipping commit" | tee -a "${log}" >&2
  exit 125
fi

clang_bin="${BUILD_DIR}/bin/clang"
clangxx_bin="${BUILD_DIR}/bin/clang++"
if [[ ! -x "${clang_bin}" ]]; then
  echo "missing built clang; skipping commit" | tee -a "${log}" >&2
  exit 125
fi
if [[ ! -x "${clangxx_bin}" ]]; then
  clangxx_bin="${clang_bin}"
fi

stdout_file="${TMP_DIR}/${commit}.stdout"
stderr_file="${TMP_DIR}/${commit}.stderr"
object="${TMP_DIR}/${commit}.o"
repro_rc=0
set +e
case "${ISSUE}" in
  llvm178777)
    timeout 120s "${clangxx_bin}" -O2 -c "${REPRODUCER}" -o "${object}" \
      >"${stdout_file}" 2>"${stderr_file}"
    repro_rc=$?
    ;;
  llvm212819)
    timeout 120s "${clang_bin}" -target aarch64-windows -mcpu=apple-m2 -O1 \
      -x ir -c "${REPRODUCER}" -o "${object}" \
      >"${stdout_file}" 2>"${stderr_file}"
    repro_rc=$?
    ;;
esac
set -e

cat "${stdout_file}" >>"${log}"
cat "${stderr_file}" >>"${log}"
verdict=$(classify_repro "${ISSUE}" "${repro_rc}" "${stderr_file}")
echo "repro exit code: ${repro_rc}" | tee -a "${log}"
echo "verdict: ${verdict}" | tee -a "${log}"

case "${verdict}" in
  good) exit 0 ;;
  bad) exit 1 ;;
  *)
    echo "nonmatching compiler failure; skipping commit" | tee -a "${log}" >&2
    exit 125
    ;;
esac
