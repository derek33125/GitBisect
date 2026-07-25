#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?mode required: heuristic|general-heuristic}"
LANE="${2:?lane label required}"

case "${MODE}" in
  heuristic|general-heuristic) ;;
  *)
    echo "error: unsupported control mode: ${MODE}" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"

if [[ -z "${PY:-}" ]]; then
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    PY="${ROOT}/.venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
export PY

STANDARD_ISSUES=(
  pr165445 pr165039 pr50655 pr170421 pr199162 pr173943 pr200987 pr199526
  pr197067 pr202003 pr197797 pr198257 pr198339 pr200330 pr200742 pr201855
  pr201444 pr203278 pr203261 pr204178 pr204589 pr121365 pr193164 pr156249
  pr194000 pr204559
)
COMPATIBILITY_ISSUES=(pr48154 pr49535 pr50304 pr50585 pr52635)

run_queue() {
  local suffix="$1"
  shift
  local -a issues=("$@")

  bash "${QUEUE}" "${MODE}" "${LANE}-${suffix}" "${issues[@]}"
}

# The older LLVM revisions need this C++-only host-header compatibility flag.
# Applying it to C compilation breaks CMake's compiler test, so keep cohorts
# separate rather than exporting one setting for the full control population.
run_queue standard "${STANDARD_ISSUES[@]}"
EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}" \
  run_queue compatibility "${COMPATIBILITY_ISSUES[@]}"
