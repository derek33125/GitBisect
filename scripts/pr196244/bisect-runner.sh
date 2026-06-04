#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds clangd from the current checkout and classifies the
current commit for llvm/llvm-project#196244.

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
TOOLS_BOOTSTRAP="${SCRIPT_DIR}/bootstrap-tools.sh"
TMP_DIR="${ROOT_DIR}/scratch/pr196244"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr196244.lock"

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

BUILD_DIR="${LLVM_DIR}/build-bisect-pr196244"
CACHE_DIR="${ROOT_DIR}/.ccache/pr196244"
BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
DEFAULT_EXTRA_CXX_FLAGS="${LM_BISECT_DEFAULT_EXTRA_CXX_FLAGS:--include cstdint -include string -include cstdlib}"
EXTRA_CXX_FLAGS="${LM_BISECT_EXTRA_CXX_FLAGS:-${DEFAULT_EXTRA_CXX_FLAGS}}"

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
GENERATOR="Ninja"
CLANGD_BIN="${BUILD_DIR}/bin/clangd"

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
echo "extra cxx flags: ${EXTRA_CXX_FLAGS:-<none>}"
echo "build dir: ${BUILD_DIR}"
echo "ccache dir: ${CCACHE_DIR}"

if [[ -d "${TMP_DIR}" ]]; then
  chmod -R u+w "${TMP_DIR}" 2>/dev/null || true
fi
rm -rf "${BUILD_DIR}" "${TMP_DIR}"
mkdir -p "${BUILD_DIR}" "${TMP_DIR}"

configure_args=(
  -G "${GENERATOR}"
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

if [[ -n "${EXTRA_CXX_FLAGS}" ]]; then
  configure_args+=(
    -DCMAKE_CXX_FLAGS="${EXTRA_CXX_FLAGS}"
  )
fi

if ! run_background_friendly cmake "${configure_args[@]}"; then
  echo "configure failed; skipping commit" >&2
  exit 125
fi

if ! run_background_friendly cmake --build "${BUILD_DIR}" --target clangd -- -j"${JOBS}"; then
  echo "build failed; skipping commit" >&2
  exit 125
fi

if [[ ! -x "${CLANGD_BIN}" ]]; then
  echo "missing built clangd; skipping commit" >&2
  exit 125
fi

WORK="$(mktemp -d -p "${TMP_DIR}" test_196244.XXXXXX)"
cleanup_work() {
  chmod -R u+w "${WORK}" 2>/dev/null || true
  rm -rf "${WORK}" 2>/dev/null || true
}
trap cleanup_work EXIT

cat > "${WORK}/reproducer.py" <<'PYEOF'
#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path

SOURCE = r"""int f(int,...) {
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f([](int=f(
[](int=f(1)){}
}
"""

def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} /path/to/clangd", file=sys.stderr)
        return 2
    clangd = sys.argv[1]
    workdir = tempfile.mkdtemp(prefix="repro-id208-")
    source_path = Path(workdir) / "main.cxx"
    source_path.write_text(SOURCE, encoding="utf-8")
    uri = source_path.resolve().as_uri()
    messages = [
        {"jsonrpc":"2.0","id":0,"method":"initialize",
         "params":{"processId":None,"rootUri":None,"capabilities":{},
                   "clientInfo":{"name":"repro-id208"}}},
        {"jsonrpc":"2.0","method":"initialized","params":{}},
        {"jsonrpc":"2.0","method":"textDocument/didOpen",
         "params":{"textDocument":{"uri":uri,"languageId":"cpp","version":1,"text":SOURCE}}},
        {"jsonrpc":"2.0","id":1,"method":"textDocument/documentSymbol",
         "params":{"textDocument":{"uri":uri}}},
        {"jsonrpc":"2.0","id":2,"method":"shutdown","params":None},
        {"jsonrpc":"2.0","method":"exit","params":{}},
    ]
    wire = "".join(
        f"Content-Length: {len((payload := json.dumps(msg)).encode())}\r\n\r\n{payload}"
        for msg in messages)
    help_proc = subprocess.run(
        [clangd, "--help"], text=True, capture_output=True, timeout=10)
    help_text = help_proc.stdout + help_proc.stderr
    args = [clangd, "-j=1"]
    if "--background-index" in help_text:
        args.append("--background-index=0")
    proc = subprocess.run(
        args, input=wire, text=True, capture_output=True, timeout=15)
    reproduced = "ExpandedTokens.back().kind() == tok::eof" in proc.stderr
    print(json.dumps({"returncode": proc.returncode, "reproduced": reproduced}))
    sys.stderr.write(proc.stderr)
    if reproduced:
        return 7
    if proc.returncode not in (0, None):
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PYEOF

set +e
python3 "${WORK}/reproducer.py" "${CLANGD_BIN}" 2>&1 | tee "${WORK}/stderr.log"
RC=${PIPESTATUS[0]}
set -e

echo "repro exit code: ${RC}"
if [[ ${RC} -eq 7 ]]; then
  exit 1
fi
if [[ ${RC} -ne 0 ]]; then
  echo "non-target clangd invocation failure; skipping commit" >&2
  exit 125
fi
exit 0
