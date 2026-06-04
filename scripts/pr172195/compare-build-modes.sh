#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: compare-build-modes.sh [llvm-project-path] [commit-sha]

Builds the given PR172195 commit in Release, RelWithDebInfo, and Debug
sequentially, reusing the same ccache directory but deleting the build dir
between runs. After each successful build, reruns the single-commit PR172195
reproducer and saves stdout/stderr/exit status for that mode.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/worktrees/pr172195-build-modes"}
TARGET_SHA=${2:-e8219e5ce84db26fd521ce5091d18e75c7afbc6a}
RESULTS_DIR="${ROOT_DIR}/results/issues/pr172195/build-mode-comparison-v3"
CACHE_DIR="${ROOT_DIR}/.ccache/pr172195-build-mode-comparison"
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
BUILD_DIR="${LLVM_DIR}/build-pr172195-build-modes-v3"
REPRO_C="${SCRIPT_DIR}/aa-10084.c"
PROF_TXT="${SCRIPT_DIR}/prof.txt"
TRACE_SYMBOLIZER=$(command -v llvm-symbolizer || true)

mkdir -p "${RESULTS_DIR}" "${CACHE_DIR}"

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
export CCACHE_DIR="${CACHE_DIR}"
export CCACHE_BASEDIR="${LLVM_DIR}"
export CCACHE_NOHASHDIR=1
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"

if command -v ccache >/dev/null 2>&1; then
  export CMAKE_C_COMPILER_LAUNCHER=ccache
  export CMAKE_CXX_COMPILER_LAUNCHER=ccache
fi

JOBS=${JOBS:-${LM_BISECT_JOBS:-4}}
if [[ "${JOBS}" -lt 1 ]]; then
  JOBS=1
fi

ORIGINAL_HEAD=$(git -C "${LLVM_DIR}" rev-parse --verify HEAD)
cleanup() {
  set +e
  git -C "${LLVM_DIR}" checkout -q "${ORIGINAL_HEAD}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_mode() {
  local build_type="$1"
  local out_prefix="${RESULTS_DIR}/${build_type,,}"
  local mode_dir="${RESULTS_DIR}/${build_type,,}"
  local build_log="${out_prefix}.log"
  local build_summary="${out_prefix}.summary"
  local sanity_stdout="${mode_dir}/sanity-o3.stdout"
  local sanity_stderr="${mode_dir}/sanity-o3.stderr"
  local repro_stdout="${mode_dir}/crash-os.stdout"
  local repro_stderr="${mode_dir}/crash-os.stderr"
  local crash_report="${mode_dir}/crash-report.txt"
  local repro_summary="${mode_dir}/repro.summary"
  local profdata_file="${mode_dir}/prof.profdata"
  local sanity_obj="${mode_dir}/sanity-o3.o"
  local repro_obj="${mode_dir}/crash-os.o"
  local build_rc=0
  local sanity_rc=0
  local repro_rc=0

  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"
  mkdir -p "${mode_dir}"

  git -C "${LLVM_DIR}" checkout -q "${TARGET_SHA}"

  local cmake_args=(
    -G Ninja
    -S "${LLVM_DIR}/llvm"
    -B "${BUILD_DIR}"
    -DLLVM_ENABLE_PROJECTS=clang
    -DLLVM_TARGETS_TO_BUILD=X86
    -DCMAKE_BUILD_TYPE="${build_type}"
    -DLLVM_ENABLE_ASSERTIONS=ON
    -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF
    -DLLVM_INCLUDE_UTILS=ON
    -DLLVM_BUILD_TOOLS=ON
  )

  if [[ -n "${CMAKE_C_COMPILER_LAUNCHER:-}" ]]; then
    cmake_args+=(
      -DCMAKE_C_COMPILER_LAUNCHER="${CMAKE_C_COMPILER_LAUNCHER}"
      -DCMAKE_CXX_COMPILER_LAUNCHER="${CMAKE_CXX_COMPILER_LAUNCHER}"
    )
  fi

  {
    echo "=== build mode: ${build_type} ==="
    echo "commit: ${TARGET_SHA}"
    echo "jobs: ${JOBS}"
    echo "build dir: ${BUILD_DIR}"
    echo "cache dir: ${CCACHE_DIR}"
    echo "target: clang llvm-profdata"
  } > "${build_log}"

  set +e
  /usr/bin/time -f "elapsed_sec=%e\nmax_rss_kb=%M\nexit_code=%x" \
    cmake "${cmake_args[@]}" >> "${build_log}" 2>&1
  build_rc=$?
  set -e

  if [[ ${build_rc} -eq 0 ]]; then
    set +e
    /usr/bin/time -f "elapsed_sec=%e\nmax_rss_kb=%M\nexit_code=%x" \
      cmake --build "${BUILD_DIR}" --target clang llvm-profdata -- -j"${JOBS}" >> "${build_log}" 2>&1
    build_rc=$?
    set -e
  fi

  if [[ ${build_rc} -ne 0 || ! -x "${BUILD_DIR}/bin/clang" || ! -x "${BUILD_DIR}/bin/llvm-profdata" ]]; then
    {
      echo "build_mode=${build_type}"
      echo "commit=${TARGET_SHA}"
      echo "build_exit_code=${build_rc}"
      echo "clang_exists=$([[ -x "${BUILD_DIR}/bin/clang" ]] && echo yes || echo no)"
      echo "llvm_profdata_exists=$([[ -x "${BUILD_DIR}/bin/llvm-profdata" ]] && echo yes || echo no)"
      echo "repro_exit_code=125"
      echo "repro_stdout=${repro_stdout}"
      echo "repro_stderr=${repro_stderr}"
      echo "crash_report=${crash_report}"
    } > "${build_summary}"
    printf 'build failed for %s; no crash report\n' "${build_type}" > "${crash_report}"
    echo "build failed for ${build_type}; skipping repro" >> "${build_log}"
    return 125
  fi

  set +e
  "${BUILD_DIR}/bin/llvm-profdata" merge "${PROF_TXT}" -o "${profdata_file}" >> "${build_log}" 2>&1
  repro_rc=$?
  set -e

  if [[ ${repro_rc} -eq 0 ]]; then
    set +e
    LLVM_SYMBOLIZER_PATH="${TRACE_SYMBOLIZER}" \
      /usr/bin/time -f "elapsed_sec=%e\nmax_rss_kb=%M\nexit_code=%x" \
      "${BUILD_DIR}/bin/clang" -O3 -msse4.2 -fprofile-instr-use="${profdata_file}" "${REPRO_C}" -o "${sanity_obj}" \
      >"${sanity_stdout}" 2>"${sanity_stderr}"
    sanity_rc=$?
    set -e
  else
    : > "${sanity_stdout}"
    printf 'llvm-profdata merge failed for %s\n' "${build_type}" > "${sanity_stderr}"
    sanity_rc=125
  fi

  if [[ ${sanity_rc} -eq 0 ]]; then
    set +e
    LLVM_SYMBOLIZER_PATH="${TRACE_SYMBOLIZER}" \
      /usr/bin/time -f "elapsed_sec=%e\nmax_rss_kb=%M\nexit_code=%x" \
      "${BUILD_DIR}/bin/clang" -c -Os -fprofile-instr-use="${profdata_file}" "${REPRO_C}" -o "${repro_obj}" \
      >"${repro_stdout}" 2>"${repro_stderr}"
    repro_rc=$?
    set -e
  else
    : > "${repro_stdout}"
    printf 'O3 sanity compile failed for %s; skipping Os crash repro\n' "${build_type}" > "${repro_stderr}"
    repro_rc=125
  fi

  {
    echo "build_mode=${build_type}"
    echo "commit=${TARGET_SHA}"
    echo "build_exit_code=${build_rc}"
    echo "clang_exists=$([[ -x "${BUILD_DIR}/bin/clang" ]] && echo yes || echo no)"
    echo "llvm_profdata_exists=$([[ -x "${BUILD_DIR}/bin/llvm-profdata" ]] && echo yes || echo no)"
    echo "sanity_o3_exit_code=${sanity_rc}"
    echo "sanity_o3_stdout=${sanity_stdout}"
    echo "sanity_o3_stderr=${sanity_stderr}"
    echo "repro_exit_code=${repro_rc}"
    echo "repro_stdout=${repro_stdout}"
    echo "repro_stderr=${repro_stderr}"
    echo "crash_report=${crash_report}"
  } > "${build_summary}"

  {
    echo "build_mode=${build_type}"
    echo "commit=${TARGET_SHA}"
    echo "sanity_o3_exit_code=${sanity_rc}"
    echo "repro_exit_code=${repro_rc}"
    echo "sanity_o3_stdout=${sanity_stdout}"
    echo "sanity_o3_stderr=${sanity_stderr}"
    echo "repro_stdout=${repro_stdout}"
    echo "repro_stderr=${repro_stderr}"
    echo "crash_report=${crash_report}"
  } > "${repro_summary}"

  {
    echo "=== PR172195 ${build_type} crash report ==="
    echo "commit: ${TARGET_SHA}"
    echo "build_type: ${build_type}"
    echo "assertions: ON"
    echo "profile: ${profdata_file}"
    echo "sanity_command: ${BUILD_DIR}/bin/clang -O3 -msse4.2 -fprofile-instr-use=${profdata_file} ${REPRO_C} -o ${sanity_obj}"
    echo "sanity_exit_code: ${sanity_rc}"
    echo "crash_command: ${BUILD_DIR}/bin/clang -c -Os -fprofile-instr-use=${profdata_file} ${REPRO_C} -o ${repro_obj}"
    echo "crash_exit_code: ${repro_rc}"
    echo
    echo "--- crash stdout ---"
    cat "${repro_stdout}"
    echo
    echo "--- crash stderr ---"
    cat "${repro_stderr}"
  } > "${crash_report}"

  {
    echo "=== repro mode: ${build_type} ==="
    echo "commit: ${TARGET_SHA}"
    echo "llvm-profdata: ${BUILD_DIR}/bin/llvm-profdata"
    echo "clang: ${BUILD_DIR}/bin/clang"
    echo "sanity O3 stdout: ${sanity_stdout}"
    echo "sanity O3 stderr: ${sanity_stderr}"
    echo "sanity O3 exit code: ${sanity_rc}"
    echo "repro stdout: ${repro_stdout}"
    echo "repro stderr: ${repro_stderr}"
    echo "crash report: ${crash_report}"
    echo "repro exit code: ${repro_rc}"
    echo
    echo "--- sanity O3 stdout ---"
    cat "${sanity_stdout}"
    echo
    echo "--- sanity O3 stderr ---"
    cat "${sanity_stderr}"
    echo
    echo "--- repro stdout ---"
    cat "${repro_stdout}"
    echo
    echo "--- repro stderr ---"
    cat "${repro_stderr}"
  } >> "${build_log}"

  return 0
}

overall_rc=0
run_mode Release || overall_rc=$?
run_mode RelWithDebInfo || overall_rc=$?
run_mode Debug || overall_rc=$?

cat > "${RESULTS_DIR}/pr172195-build-mode-comparison.md" <<EOF
# PR172195 Build-Mode Comparison v3

- Commit: \`${TARGET_SHA}\`
- Build dir: \`${BUILD_DIR}\`
- Cache dir: \`${CCACHE_DIR}\`
- Repro: \`${REPRO_C}\`
- Profile: \`${PROF_TXT}\`

Bug-report signal:
- Crash is described as a frontend failure in the vectorizer path, centered on \`LoopVectorizationCostModel::expectedCost\`.

Concrete repro signal:
- Each mode builds \`clang\` and \`llvm-profdata\`, runs an \`-O3\` sanity compile, then runs the real crashing
  \`-Os -fprofile-instr-use\` repro.
- The assertion is the primary evidence for the LLM:
  \`!Name.empty() && "Must have a name!"\`.
- The stack/pass trace is supporting context only. In this case it says the abort happens while running
  \`loop-vectorize\` on \`func_21\`.
- The bug report summary alone is weaker than the concrete assertion because it describes a broad vectorizer crash,
  while the assertion gives the exact failed invariant.

Per-mode artifacts:
- \`release/\`
- \`relwithdebinfo/\`
- \`debug/\`
EOF

echo "done" > "${RESULTS_DIR}/done.txt"
exit "${overall_rc}"
