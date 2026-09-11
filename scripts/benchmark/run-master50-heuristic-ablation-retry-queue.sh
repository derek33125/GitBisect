#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-master50-heuristic-ablation-retry-queue.sh FACTOR LANE ISSUE...

Retry an explicit subset of master-50 heuristic-ablation cases with the
historical-source compatibility build settings. The lane is resumable and
continues after an individual issue failure.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -lt 3 ]]; then
  usage >&2
  exit 2
fi

FACTOR="$1"
LANE="$2"
shift 2
ISSUES=("$@")

case "${FACTOR}" in
  only-keywords|only-relevant-paths|only-high-risk-paths) ;;
  *)
    echo "error: unsupported retry factor: ${FACTOR}" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
ISSUES_FILE="${MASTER50_ISSUES_FILE:-${ROOT}/benchmark-results/master50/issues.json}"
PY="${PY:-${ROOT}/.venv/bin/python}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${LANE}-controller"
SUMMARY_PATH="${STATUS_ROOT}/summary.tsv"

"${PY}" - "${ISSUES_FILE}" "${ISSUES[@]}" <<'PY'
import json
import sys
from pathlib import Path

master50 = {
    str(row["issue"])
    for row in json.loads(Path(sys.argv[1]).read_text())
    if isinstance(row, dict) and row.get("issue")
}
requested = sys.argv[2:]
if len(requested) != len(set(requested)):
    raise SystemExit("retry issue list contains duplicates")
unknown = sorted(set(requested) - master50)
if unknown:
    raise SystemExit(f"retry issues are outside master-50: {unknown}")
PY

mkdir -p "${STATUS_ROOT}"
printf 'issue\tstatus\texit_code\tfinished_at\n' > "${SUMMARY_PATH}"

for issue in "${ISSUES[@]}"; do
  status_path="${STATUS_ROOT}/${issue}.status"
  if [[ -f "${status_path}" ]] && grep -q '^status=completed$' "${status_path}"; then
    printf '%s\tcompleted\t0\t%s\n' \
      "${issue}" "$(date -Iseconds)" >> "${SUMMARY_PATH}"
    continue
  fi

  {
    printf 'issue=%s\nfactor=%s\nmode=heuristic-ablation-compat\n' \
      "${issue}" "${FACTOR}"
    printf 'status=running\nstarted_at=%s\n' "$(date -Iseconds)"
  } > "${status_path}"

  if env \
    ROOT="${ROOT}" \
    PY="${PY}" \
    RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}" \
    HEURISTIC_ABLATION_FACTOR="${FACTOR}" \
    bash "${QUEUE}" heuristic-ablation-compat "${LANE}" "${issue}"; then
    rc=0
    status=completed
  else
    rc=$?
    status=failed
  fi

  {
    printf 'issue=%s\nfactor=%s\nmode=heuristic-ablation-compat\n' \
      "${issue}" "${FACTOR}"
    printf 'status=%s\nexit_code=%s\nfinished_at=%s\n' \
      "${status}" "${rc}" "$(date -Iseconds)"
  } > "${status_path}"
  printf '%s\t%s\t%s\t%s\n' \
    "${issue}" "${status}" "${rc}" "$(date -Iseconds)" >> "${SUMMARY_PATH}"
done
