#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: run-llvm-impact-validation-queue.sh LANE_PREFIX

Run exactly two independent EDU impact-study lanes. These results are kept
outside all Master-50 and CEG aggregate namespaces.
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
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
LAUNCHER="${SCRIPT_DIR}/run-llvm-impact-validation-bisect.sh"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-/home/derek/gitbisect-work/worktrees}"
STATUS_ROOT="${ROOT}/results/impact-study/${LANE_PREFIX}-controller"
GOOD_COMMIT=bd6bfba3e50343c112a04b639394ab85be17c29b

mkdir -p "${STATUS_ROOT}"
printf 'started_at=%s\nimpact_physical_lanes=2\ngood_commit=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${GOOD_COMMIT}" >"${STATUS_ROOT}/run.meta"

run_case() {
  local lane="$1"
  local issue="$2"
  local bad_commit run_id worktree
  case "${issue}" in
    llvm178777) bad_commit=3c45148e9266bbd9830c2b977c497795d9307c72 ;;
    llvm212819) bad_commit=561093d94eb7156dea780c1c71a779824ef90e5b ;;
    *) echo "error: unsupported impact case ${issue}" >&2; return 2 ;;
  esac
  run_id="${LANE_PREFIX}-${lane}-${issue}"
  worktree="${WORK_ROOT}/${issue}-${run_id}"
  env ROOT="${ROOT}" BASE_REPO="${BASE_REPO}" WORK_ROOT="${WORK_ROOT}" \
    RUN_ID="${run_id}" JOBS="${JOBS:-2}" \
    bash "${LAUNCHER}" "${issue}" "${GOOD_COMMIT}" "${bad_commit}" "${worktree}"
}

run_case lane-a llvm178777 >"${STATUS_ROOT}/lane-a.log" 2>&1 &
pid_a=$!
run_case lane-b llvm212819 >"${STATUS_ROOT}/lane-b.log" 2>&1 &
pid_b=$!
printf 'lane_a_pid=%s\nlane_b_pid=%s\n' "${pid_a}" "${pid_b}" >>"${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
wait "${pid_b}" || exit_code=1
printf 'finished_at=%s\nexit_code=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${exit_code}" >>"${STATUS_ROOT}/run.meta"
exit "${exit_code}"
