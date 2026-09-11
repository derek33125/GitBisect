#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-master50-heuristic-ablation-queue.sh FACTOR LANE

Run one heuristic only-signal or leave-one-out control across the exact
master-50 cohort. Each issue is isolated so a terminal failure does not strand
the rest of the queue.

Supported factors:
  only-keywords
  only-relevant-paths
  only-high-risk-paths
  only-risky-words
  buildability
  feedback
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

FACTOR="$1"
LANE="$2"
case "${FACTOR}" in
  only-keywords|only-relevant-paths|only-high-risk-paths|only-risky-words|buildability|feedback) ;;
  *)
    echo "error: unsupported master-50 heuristic ablation: ${FACTOR}" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
QUEUE="${ROOT}/scripts/benchmark/run-comparison-queue-20260630.sh"
ISSUES_FILE="${MASTER50_ISSUES_FILE:-${ROOT}/benchmark-results/master50/issues.json}"
RUNTIME_GATE_FILE="${RUNTIME_GATE_FILE:-${ROOT}/results/issues/server-jobs/master50-runtime-generation-20260903.complete.json}"
PY="${PY:-${ROOT}/.venv/bin/python}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${LANE}-controller"
SUMMARY_PATH="${STATUS_ROOT}/summary.tsv"

if [[ ! -x "${PY}" ]]; then
  echo "error: Python executable not found: ${PY}" >&2
  exit 2
fi
if [[ ! -x "${QUEUE}" ]]; then
  echo "error: comparison queue is not executable: ${QUEUE}" >&2
  exit 2
fi

"${PY}" - "${RUNTIME_GATE_FILE}" "${ISSUES_FILE}" <<'PY'
import json
import sys
from pathlib import Path

gate_path = Path(sys.argv[1])
issues_path = Path(sys.argv[2])
gate = json.loads(gate_path.read_text())
if (
    gate.get("scope") != "master50-runtime-generation"
    or gate.get("status") != "complete"
    or gate.get("expected_cases") != 29
    or gate.get("terminal_cases") != 29
):
    raise SystemExit("master-50 runtime-generation gate is not complete at 29/29")
rows = json.loads(issues_path.read_text())
issues = [str(row.get("issue", "")) for row in rows if isinstance(row, dict)]
if len(issues) != 50 or len(set(issues)) != 50 or any(not issue for issue in issues):
    raise SystemExit(
        f"master-50 cohort must contain exactly 50 unique issue IDs; got {len(issues)}"
    )
PY

mapfile -t ISSUES < <(
  "${PY}" - "${ISSUES_FILE}" <<'PY'
import json
import sys
from pathlib import Path

for row in json.loads(Path(sys.argv[1]).read_text()):
    print(row["issue"])
PY
)

# Keep the three default only-signal arms away from the same per-issue runner
# lock and build cache. Rotation changes execution order only, not the cohort.
case "${FACTOR}" in
  only-keywords) issue_offset=0 ;;
  only-relevant-paths) issue_offset=17 ;;
  only-high-risk-paths) issue_offset=34 ;;
  only-risky-words) issue_offset=8 ;;
  buildability) issue_offset=25 ;;
  feedback) issue_offset=42 ;;
esac
if (( issue_offset > 0 )); then
  ISSUES=("${ISSUES[@]:issue_offset}" "${ISSUES[@]:0:issue_offset}")
fi

declare -A COMPAT=(
  [pr120802]=1
  [pr121365]=1
  [pr173943]=1
  [pr204178]=1
  [pr50304]=1
  [pr50585]=1
  [pr48154]=1
  [pr49535]=1
  [pr52635]=1
)

mkdir -p "${STATUS_ROOT}"
printf 'issue\tstatus\texit_code\tfinished_at\n' > "${SUMMARY_PATH}"

for issue in "${ISSUES[@]}"; do
  status_path="${STATUS_ROOT}/${issue}.status"
  if [[ -f "${status_path}" ]] && grep -q '^status=completed$' "${status_path}"; then
    printf '%s\tcompleted\t0\t%s\n' "${issue}" "$(date -Iseconds)" >> "${SUMMARY_PATH}"
    continue
  fi

  mode="heuristic-ablation"
  if [[ -n "${COMPAT[${issue}]:-}" ]]; then
    mode="heuristic-ablation-compat"
  fi
  {
    printf 'issue=%s\n' "${issue}"
    printf 'factor=%s\n' "${FACTOR}"
    printf 'mode=%s\n' "${mode}"
    printf 'status=running\n'
    printf 'started_at=%s\n' "$(date -Iseconds)"
  } > "${status_path}"

  if env \
    ROOT="${ROOT}" \
    PY="${PY}" \
    RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}" \
    HEURISTIC_ABLATION_FACTOR="${FACTOR}" \
    bash "${QUEUE}" "${mode}" "${LANE}" "${issue}"; then
    rc=0
    status="completed"
  else
    rc=$?
    status="failed"
  fi

  {
    printf 'issue=%s\n' "${issue}"
    printf 'factor=%s\n' "${FACTOR}"
    printf 'mode=%s\n' "${mode}"
    printf 'status=%s\n' "${status}"
    printf 'exit_code=%s\n' "${rc}"
    printf 'finished_at=%s\n' "$(date -Iseconds)"
  } > "${status_path}"
  printf '%s\t%s\t%s\t%s\n' \
    "${issue}" "${status}" "${rc}" "$(date -Iseconds)" >> "${SUMMARY_PATH}"
done

"${PY}" - "${STATUS_ROOT}" "${FACTOR}" "${LANE}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
statuses = {}
for path in root.glob("pr*.status"):
    fields = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    statuses[path.stem] = fields.get("status", "unknown")
payload = {
    "scope": "master50-heuristic-ablation",
    "factor": sys.argv[2],
    "lane": sys.argv[3],
    "status": "complete",
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "completed": sum(value == "completed" for value in statuses.values()),
    "failed": sum(value == "failed" for value in statuses.values()),
    "issues": statuses,
}
(root / "complete.json").write_text(json.dumps(payload, indent=2) + "\n")
PY
