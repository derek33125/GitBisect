#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bisect-runner.sh [llvm-project-path]

Builds clangd from the current checkout and classifies the
current commit for llvm/llvm-project#195788.

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
TMP_DIR="${ROOT_DIR}/scratch/pr195788"
LOCK_DIR="${ROOT_DIR}/scratch/locks"
LOCK_FILE="${LOCK_DIR}/pr195788.lock"

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

BUILD_DIR="${LLVM_DIR}/build-bisect-pr195788"
CACHE_DIR="${ROOT_DIR}/.ccache/pr195788"
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
echo "build dir: ${BUILD_DIR}"
echo "ccache dir: ${CCACHE_DIR}"

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

WORK="$(mktemp -d -p "${TMP_DIR}" test_195788.XXXXXX)"
trap 'rm -rf "${WORK}"' EXIT

cat > "${WORK}/repro.py" <<'PYEOF'
#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile
from pathlib import Path

SOURCE = """struct A { ~A(); };
void b(const A *y) {
  y->~decltype(A())();
}
"""

FINAL_WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 30

def make_msg(obj):
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body

def main():
    if len(sys.argv) < 2:
        print("usage: repro.py /path/to/clangd", file=sys.stderr); return 2
    target = sys.argv[1]
    workdir = Path(tempfile.mkdtemp(prefix="id59_embedded_"))
    source_path = workdir / "main.hpp"
    source_path.write_text(SOURCE)
    uri = source_path.as_uri()

    line_count = len(SOURCE.splitlines())
    end_line = max(line_count, 0)

    initialize = {
        "jsonrpc":"2.0","id":0,"method":"initialize",
        "params":{"processId":None,"rootUri":workdir.as_uri()+"/",
                  "capabilities":{"workspace":{"workspaceFolders":True},
                                  "textDocument":{"codeAction":{"dataSupport":True,
                                    "codeActionLiteralSupport":{"codeActionKind":{"valueSet":["quickfix","refactor","source"]}}}}},
                  "workspaceFolders":[{"uri":workdir.as_uri()+"/","name":"default_workspace"}],
                  "clientInfo":{"name":"id59-embedded-repro","version":"0.1.0"},"trace":"off"}}
    initialized = {"jsonrpc":"2.0","method":"initialized","params":{}}
    did_open = {"jsonrpc":"2.0","method":"textDocument/didOpen",
                "params":{"textDocument":{"uri":uri,"languageId":"cpp","version":1,"text":SOURCE}}}
    code_action = {"jsonrpc":"2.0","id":1,"method":"textDocument/codeAction",
        "params":{"textDocument":{"uri":uri},
                  "range":{"start":{"line":0,"character":0},"end":{"line":end_line,"character":0}},
                  "context":{"diagnostics":[],"only":["source.organizeImports","source"],"triggerKind":2}}}
    shutdown = {"jsonrpc":"2.0","id":2,"method":"shutdown","params":None}
    exit_msg = {"jsonrpc":"2.0","method":"exit","params":None}

    payload = b"".join(make_msg(m) for m in
        [initialize, initialized, did_open, code_action, shutdown, exit_msg])

    env = os.environ.copy()
    env["ASAN_OPTIONS"] = (f"log_path={workdir/'asan.log'}:abort_on_error=1:"
                            "handle_abort=2:detect_leaks=0:symbolize=1")
    env["LSAN_OPTIONS"] = "detect_leaks=0"

    stderr_log = workdir / "stderr.log"
    with open(stderr_log, "wb") as fp:
        p = subprocess.Popen([target], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=fp,
                             env=env, cwd=str(workdir))
        timed_out = False
        try:
            p.communicate(input=payload, timeout=FINAL_WAIT)
        except subprocess.TimeoutExpired:
            timed_out = True; p.kill(); p.wait()

    asan_logs = sorted(workdir.glob("asan.log.*"))
    crashed = (p.returncode not in (0, None)) or bool(asan_logs) or timed_out
    sys.stderr.write(stderr_log.read_text(errors="ignore"))
    return 7 if crashed else 0

if __name__ == "__main__":
    raise SystemExit(main())
PYEOF

set +e
python3 "${WORK}/repro.py" "${CLANGD_BIN}" 2>&1 | tee "${WORK}/stderr.log"
RC=${PIPESTATUS[0]}
set -e

echo "repro exit code: ${RC}"
if [[ ${RC} -eq 7 ]]; then
  exit 1
fi
if [[ ${RC} -ne 0 ]]; then
  exit 1
fi
exit 0
