#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: run-ceg-scoped10-queue.sh LANE_PREFIX

Run the scoped-10 web benchmark with CEG-Bisect v6 on exactly three physical
queues. Each queue runs its assigned issues sequentially; the shared
run-online reservation gate prevents more than three model lanes from running.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

LANE_PREFIX="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${LANE_PREFIX}-controller"

if [[ ! "${RUN_ONLINE_MAX_LANES}" =~ ^[1-3]$ ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be between 1 and 3" >&2
  exit 2
fi
if [[ ! -x "${QUEUE}" ]]; then
  echo "error: comparison queue is not executable: ${QUEUE}" >&2
  exit 2
fi

mkdir -p "${STATUS_ROOT}"
printf 'started_at=%s\nmax_lanes=%s\n' \
  "$(date -Iseconds)" "${RUN_ONLINE_MAX_LANES}" > "${STATUS_ROOT}/run.meta"

run_case() {
  local physical_lane="$1"
  local mode="$2"
  local issue="$3"
  local lane="${LANE_PREFIX}-${physical_lane}"
  local status_file="${STATUS_ROOT}/${physical_lane}.status"

  printf '%s\t%s\t%s\tstarted\t%s\n' \
    "$(date -Iseconds)" "${mode}" "${issue}" "${lane}" >> "${status_file}"
  if env \
    ROOT="${ROOT}" \
    RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES}" \
    bash "${QUEUE}" "${mode}" "${lane}" "${issue}"; then
    printf '%s\t%s\t%s\tterminal\t0\n' \
      "$(date -Iseconds)" "${mode}" "${issue}" >> "${status_file}"
  else
    local exit_code=$?
    printf '%s\t%s\t%s\tterminal\t%s\n' \
      "$(date -Iseconds)" "${mode}" "${issue}" "${exit_code}" >> "${status_file}"
  fi
}

lane_a() {
  run_case lane-a causal-evidence-guided-k12 pr204559
  run_case lane-a causal-evidence-guided-k12 pr193164
  run_case lane-a causal-evidence-guided-k12-compat pr50304
  run_case lane-a causal-evidence-guided-k12-compat pr48154
}

lane_b() {
  run_case lane-b causal-evidence-guided-k12 pr204589
  run_case lane-b causal-evidence-guided-k12 pr201444
  run_case lane-b causal-evidence-guided-k12-compat pr50585
}

lane_c() {
  run_case lane-c causal-evidence-guided-k12 pr200987
  run_case lane-c causal-evidence-guided-k12-compat pr49535
  run_case lane-c causal-evidence-guided-k12-compat pr52635
}

lane_a > "${STATUS_ROOT}/lane-a.controller.log" 2>&1 &
pid_a=$!
lane_b > "${STATUS_ROOT}/lane-b.controller.log" 2>&1 &
pid_b=$!
lane_c > "${STATUS_ROOT}/lane-c.controller.log" 2>&1 &
pid_c=$!

printf 'lane_a_pid=%s\nlane_b_pid=%s\nlane_c_pid=%s\n' \
  "${pid_a}" "${pid_b}" "${pid_c}" >> "${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
wait "${pid_b}" || exit_code=1
wait "${pid_c}" || exit_code=1

printf 'finished_at=%s\nexit_code=%s\n' \
  "$(date -Iseconds)" "${exit_code}" >> "${STATUS_ROOT}/run.meta"
exit "${exit_code}"
