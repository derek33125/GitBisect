#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: run-ceg-mixture-master50-queue.sh LANE_PREFIX

Run CEG-Bisect with coverage-mixture-v1 on the Master-50 cases whose prior
CEG histories used 16-30 probes. Exactly one physical lane is used.
Runner-skip issues (pr199162, pr199526) are excluded.

Required environment:
  CEG_SCOPED10_ROOT      scoped-10 bad-endpoint bundle
  CEG_NEXT10_ROOT        next-10 r3 bad-endpoint bundle
  CEG_REMAINING30_ROOT   remaining-30 bad-endpoint bundle

Optional environment:
  CEG_FUSION_POLICY      coverage-mixture-v1 (default)
  RUN_ONLINE_MAX_LANES   1 (default)
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
PY="${PY:-${ROOT}/.venv/bin/python}"
if [[ -z "${BASE_REPO:-}" ]]; then
  if [[ -d /home/ubuntu/gitbisect-work/llvm-project ]]; then
    BASE_REPO=/home/ubuntu/gitbisect-work/llvm-project
  elif [[ -d /home/derek/gitbisect-work/llvm-project ]]; then
    BASE_REPO=/home/derek/gitbisect-work/llvm-project
  else
    BASE_REPO="${ROOT}/../gitbisect-work/llvm-project"
  fi
fi
WORK_ROOT="${WORK_ROOT:-$(dirname "${BASE_REPO}")/worktrees-ceg-mixture-master50}"
RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-1}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${LANE_PREFIX}-controller"
MIN_FREE_GB="${MIN_FREE_GB:-80}"
CEG_KEEP_ISSUE_CACHE="${CEG_KEEP_ISSUE_CACHE:-0}"
CEG_FUSION_POLICY="${CEG_FUSION_POLICY:-coverage-mixture-v1}"
MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-ceg-mixture-v1-master50-20260915}"
CEG_SCOPED10_ROOT="${CEG_SCOPED10_ROOT:?CEG_SCOPED10_ROOT is required}"
CEG_NEXT10_ROOT="${CEG_NEXT10_ROOT:?CEG_NEXT10_ROOT is required}"
CEG_REMAINING30_ROOT="${CEG_REMAINING30_ROOT:?CEG_REMAINING30_ROOT is required}"

# Remaining high-step cases after AWS completed pr204178.
ISSUES=(
  pr195788 pr156249
)
LANE_A_ISSUES=(
  pr195788 pr156249
)
SCOPED10_ISSUES=(
  pr204559 pr201444 pr204589 pr193164 pr200987
  pr50304 pr50585 pr48154 pr49535 pr52635
)
NEXT10_ISSUES=(
  pr165039 pr197797 pr120802 pr195788 pr196244
  pr172195 pr198257 pr200742 pr192829 pr202343
)
REMAINING30_ISSUES=(
  pr121365 pr156249 pr165246
  pr165445 pr167514 pr168912
  pr170421 pr173943 pr190445
  pr194000 pr194590 pr196450
  pr197067 pr198339 pr199162
  pr199526 pr200330 pr200648
  pr201855 pr202003 pr202043
  pr203261 pr203278 pr203519
  pr204178 pr204561 pr205971
  pr206007 pr50655 pr65982
)
if [[ "${RUN_ONLINE_MAX_LANES}" != "1" ]]; then
  echo "error: RUN_ONLINE_MAX_LANES must be 1 for the high-step mixture queue" >&2
  exit 2
fi
if [[ ! "${MIN_FREE_GB}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: MIN_FREE_GB must be a positive integer" >&2
  exit 2
fi
if [[ "${CEG_FUSION_POLICY}" != "coverage-mixture-v1" ]]; then
  echo "error: CEG_FUSION_POLICY must be coverage-mixture-v1" >&2
  exit 2
fi
if [[ ! -x "${PY}" || ! -x "${QUEUE}" ]]; then
  echo "error: CEG mixture runtime is incomplete" >&2
  exit 2
fi
if [[ ! -f "${ROOT}/benchmark-results/master50/issues.json" ]]; then
  echo "error: Master-50 issue ledger is missing" >&2
  exit 2
fi
for bundle_root in \
  "${CEG_SCOPED10_ROOT}" \
  "${CEG_NEXT10_ROOT}" \
  "${CEG_REMAINING30_ROOT}"; do
  if [[ ! -f "${bundle_root}/manifest.json" ]]; then
    echo "error: CEG input manifest is missing: ${bundle_root}/manifest.json" >&2
    exit 2
  fi
done

"${PY}" - \
  "${ROOT}/benchmark-results/master50/issues.json" \
  "${ROOT}/tools/lm_bisect_profiles.json" \
  "${CEG_SCOPED10_ROOT}/manifest.json" \
  "${CEG_NEXT10_ROOT}/manifest.json" \
  "${CEG_REMAINING30_ROOT}/manifest.json" \
  "${ISSUES[*]}" \
  "${LANE_A_ISSUES[*]}" \
  "${SCOPED10_ISSUES[*]}" \
  "${NEXT10_ISSUES[*]}" \
  "${REMAINING30_ISSUES[*]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

ledger = {row["issue"] for row in json.loads(Path(sys.argv[1]).read_text())}
profiles = json.loads(Path(sys.argv[2]).read_text())
manifest_paths = [Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5])]
declared = sys.argv[6].split()
lane_a = sys.argv[7].split()
cohorts = [sys.argv[8].split(), sys.argv[9].split(), sys.argv[10].split()]
expected = ["pr195788", "pr156249"]
if declared != expected or lane_a != expected:
    raise SystemExit("CEG mixture high-step queue must be pr195788 pr156249")
if len(set(declared)) != 2 or not set(declared).issubset(ledger):
    raise SystemExit("CEG mixture high-step issues must be unique Master-50 cases")
if set(declared) & {"pr199162", "pr199526"}:
    raise SystemExit("runner-skip issues must stay out of the high-step mixture queue")
issue_to_root = {}
for manifest_path, cohort in zip(manifest_paths, cohorts):
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    cases = {
        str(case.get("issue", "")): case
        for case in manifest.get("cases", [])
        if isinstance(case, dict)
    }
    if manifest.get("protocol") != "ceg-bad-endpoint-v1":
        raise SystemExit("CEG endpoint protocol mismatch")
    if sorted(cases) != sorted(cohort):
        raise SystemExit(f"CEG input manifest mismatch: {manifest_path}")
    for issue in declared:
        if issue in cases:
            if issue in issue_to_root:
                raise SystemExit(f"duplicate endpoint issue {issue}")
            issue_to_root[issue] = (root, cases[issue])
if set(issue_to_root) != set(declared):
    raise SystemExit("each high-step issue must appear in exactly one endpoint bundle")
for issue, (root, case) in issue_to_root.items():
    if case.get("capture_commit") != profiles[issue]["bad_commit"]:
        raise SystemExit(f"bad-endpoint commit mismatch for {issue}")
    artifact = root / case["crash_artifact"]
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != case["crash_artifact_sha256"]:
        raise SystemExit(f"crash artifact hash mismatch for {issue}")
    for relative, digest in case["reproducer_sha256"].items():
        path = root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"reproducer hash mismatch for {issue}: {relative}")
PY

mkdir -p "${STATUS_ROOT}" "${WORK_ROOT}"
printf 'started_at=%s\nmax_server_lanes=%s\nceg_physical_lanes=1\nexpected_cases=%s\nmin_free_gb=%s\nceg_fusion_policy=%s\nmodel_cache_namespace=%s\nwork_root=%s\n' \
  "$(date -Iseconds)" "${RUN_ONLINE_MAX_LANES}" "${#ISSUES[@]}" \
  "${MIN_FREE_GB}" "${CEG_FUSION_POLICY}" "${MODEL_CACHE_NAMESPACE}" \
  "${WORK_ROOT}" > "${STATUS_ROOT}/run.meta"

ceg_input_root_for() {
  local issue="$1"
  local item
  for item in "${SCOPED10_ISSUES[@]}"; do
    if [[ "${item}" == "${issue}" ]]; then
      printf '%s\n' "${CEG_SCOPED10_ROOT}"
      return 0
    fi
  done
  for item in "${NEXT10_ISSUES[@]}"; do
    if [[ "${item}" == "${issue}" ]]; then
      printf '%s\n' "${CEG_NEXT10_ROOT}"
      return 0
    fi
  done
  for item in "${REMAINING30_ISSUES[@]}"; do
    if [[ "${item}" == "${issue}" ]]; then
      printf '%s\n' "${CEG_REMAINING30_ROOT}"
      return 0
    fi
  done
  echo "error: no CEG input root for ${issue}" >&2
  return 1
}

wait_for_free_space() {
  local issue="$1"
  local available_kb
  local required_kb=$((MIN_FREE_GB * 1024 * 1024))
  while true; do
    available_kb="$(df -Pk "${WORK_ROOT}" | awk 'NR == 2 {print $4}')"
    if [[ "${available_kb}" =~ ^[1-9][0-9]*$ ]] && (( available_kb >= required_kb )); then
      return 0
    fi
    printf '%s\t%s\twaiting-for-disk\tavailable_kb=%s\trequired_kb=%s\n' \
      "$(date -Iseconds)" "${issue}" "${available_kb:-unknown}" "${required_kb}" \
      >> "${STATUS_ROOT}/disk.status"
    sleep 300
  done
}

cleanup_issue_cache() {
  local issue="$1"
  if [[ "${CEG_KEEP_ISSUE_CACHE}" == "1" ]]; then
    return
  fi
  rm -rf \
    "${ROOT}/.ccache/${issue}" \
    "${ROOT}/.ccache/${issue}-git-bisect"
}

case_completed() {
  local status_file="$1"
  local issue="$2"
  awk -F '\t' -v issue="${issue}" \
    '$2 == issue && $3 == "terminal" && $4 == "0" {found=1} END {exit !found}' \
    "${status_file}" 2>/dev/null
}

run_case() {
  local physical_lane="$1"
  local issue="$2"
  local lane="${LANE_PREFIX}-${physical_lane}"
  local status_file="${STATUS_ROOT}/${physical_lane}.status"
  local mode=causal-evidence-guided-k12
  local case_exit=0
  local input_root

  if case_completed "${status_file}" "${issue}"; then
    return 0
  fi
  input_root="$(ceg_input_root_for "${issue}")"

  wait_for_free_space "${issue}"
  printf '%s\t%s\tstarted\t%s\t%s\n' \
    "$(date -Iseconds)" "${issue}" "${lane}" "${mode}" >> "${status_file}"
  if env \
    ROOT="${ROOT}" \
    PY="${PY}" \
    BASE_REPO="${BASE_REPO}" \
    WORK_ROOT="${WORK_ROOT}" \
    RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES}" \
    CEG_INPUT_ROOT="${input_root}" \
    CEG_FUSION_POLICY="${CEG_FUSION_POLICY}" \
    MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE}" \
    bash "${QUEUE}" "${mode}" "${lane}" "${issue}"; then
    printf '%s\t%s\tterminal\t0\n' \
      "$(date -Iseconds)" "${issue}" >> "${status_file}"
  else
    local exit_code=$?
    case_exit="${exit_code}"
    printf '%s\t%s\tterminal\t%s\n' \
      "$(date -Iseconds)" "${issue}" "${exit_code}" >> "${status_file}"
  fi
  cleanup_issue_cache "${issue}"
  return "${case_exit}"
}

run_lane() {
  local physical_lane="$1"
  shift
  local failed=0
  local issue
  for issue in "$@"; do
    run_case "${physical_lane}" "${issue}" || failed=1
  done
  return "${failed}"
}

run_lane lane-a "${LANE_A_ISSUES[@]}" > "${STATUS_ROOT}/lane-a.controller.log" 2>&1 &
pid_a=$!
printf 'lane_a_pid=%s\n' "${pid_a}" >> "${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
printf 'finished_at=%s\nexit_code=%s\n' \
  "$(date -Iseconds)" "${exit_code}" >> "${STATUS_ROOT}/run.meta"
exit "${exit_code}"
