#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: capture-ceg-master50-next10-endpoints.sh OUTPUT_ROOT CAPTURE_LABEL

Capture crash evidence at each configured bad endpoint for the exact ten-case
master-50 CEG expansion. Three physical lanes run in parallel. A
ceg-bad-endpoint-v1 manifest is written only after every runner classifies its
configured endpoint as bad.

Required environment:
  SOURCE_REPRO_ROOT  Existing ten-case bundle used only for reproducer files.
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

OUTPUT_ROOT="$1"
CAPTURE_LABEL="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PY="${PY:-${ROOT}/.venv/bin/python}"
PROFILES="${ROOT}/tools/lm_bisect_profiles.json"
BASE_REPO="${BASE_REPO:-/home/derek/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-$(dirname "${BASE_REPO}")/worktrees}"
SOURCE_REPRO_ROOT="${SOURCE_REPRO_ROOT:?SOURCE_REPRO_ROOT is required}"
STATUS_ROOT="${ROOT}/results/issues/server-jobs/${CAPTURE_LABEL}-controller"
MIN_FREE_GB="${MIN_FREE_GB:-80}"

ISSUES=(
  pr165039 pr197797 pr120802
  pr195788 pr196244 pr172195
  pr198257 pr200742 pr192829 pr202343
)

if [[ ! -x "${PY}" ]]; then
  echo "error: Python executable not found: ${PY}" >&2
  exit 2
fi
if [[ ! "${MIN_FREE_GB}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: MIN_FREE_GB must be a positive integer" >&2
  exit 2
fi
if ! git -C "${BASE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: LLVM base repository not found: ${BASE_REPO}" >&2
  exit 2
fi
if [[ ! -f "${PROFILES}" ]]; then
  echo "error: issue profiles not found: ${PROFILES}" >&2
  exit 2
fi
if [[ ! -f "${SOURCE_REPRO_ROOT}/manifest.json" ]]; then
  echo "error: source reproducer manifest not found: ${SOURCE_REPRO_ROOT}/manifest.json" >&2
  exit 2
fi
if [[ -f "${OUTPUT_ROOT}/manifest.json" ]]; then
  echo "error: completed output root already has a manifest: ${OUTPUT_ROOT}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_ROOT}/cases" "${STATUS_ROOT}" "${WORK_ROOT}"
printf 'started_at=%s\ncapture_lanes=3\noutput_root=%s\nresume_incomplete=1\nmin_free_gb=%s\n' \
  "$(date -Iseconds)" "${OUTPUT_ROOT}" "${MIN_FREE_GB}" > "${STATUS_ROOT}/run.meta"

profile_bad_commit() {
  "${PY}" - "${PROFILES}" "$1" <<'PY'
import json
import sys
from pathlib import Path

profiles = json.loads(Path(sys.argv[1]).read_text())
print(profiles[sys.argv[2]]["bad_commit"])
PY
}

cleanup_worktree() {
  local worktree="$1"
  git -C "${BASE_REPO}" worktree remove --force "${worktree}" >/dev/null 2>&1 || true
  rm -rf "${worktree}"
  git -C "${BASE_REPO}" worktree prune >/dev/null 2>&1 || true
}

wait_for_free_space() {
  local issue="$1"
  local available_kb
  local required_kb=$((MIN_FREE_GB * 1024 * 1024))
  while true; do
    available_kb="$(df -Pk "${WORK_ROOT}" | awk 'NR == 2 {print $4}')"
    if [[ "${available_kb}" =~ ^[0-9]+$ ]] && (( available_kb >= required_kb )); then
      return 0
    fi
    printf '%s\t%s\twaiting-for-disk\tavailable_kb=%s\trequired_kb=%s\n' \
      "$(date -Iseconds)" "${issue}" "${available_kb:-unknown}" "${required_kb}" \
      >> "${STATUS_ROOT}/disk.status"
    sleep 300
  done
}

capture_case() {
  local lane="$1"
  local issue="$2"
  local bad_commit
  local worktree="${WORK_ROOT}/${issue}-${CAPTURE_LABEL}-${lane}"
  local run_id="${CAPTURE_LABEL}-${lane}-${issue}"
  local issue_status="${STATUS_ROOT}/${issue}.status"
  local result_dir="${ROOT}/results/issues/${issue}"
  local tmp_dir="${result_dir}/tmp-git-bisect-${run_id}"
  local raw_capture="${result_dir}/${run_id}-bad-endpoint.raw"
  local case_dir="${OUTPUT_ROOT}/cases/${issue}"

  bad_commit="$(profile_bad_commit "${issue}")"
  if grep -q '^status=completed$' "${issue_status}" 2>/dev/null \
      && [[ -s "${raw_capture}" ]] \
      && [[ -s "${case_dir}/crash-assertion.err" ]]; then
    printf '%s\t%s\talready captured; retaining verified endpoint artifact\n' \
      "$(date -Iseconds)" "${issue}" >> "${STATUS_ROOT}/${lane}.resume.log"
    return 0
  fi
  wait_for_free_space "${issue}"
  {
    printf 'issue=%s\n' "${issue}"
    printf 'lane=%s\n' "${lane}"
    printf 'capture_commit=%s\n' "${bad_commit}"
    printf 'status=running\n'
    printf 'started_at=%s\n' "$(date -Iseconds)"
  } > "${issue_status}"

  cleanup_worktree "${worktree}"
  git -C "${BASE_REPO}" worktree add --detach "${worktree}" "${bad_commit}"
  mkdir -p "${result_dir}"
  rm -f "${raw_capture}"

  set +e
  env \
    ROOT="${ROOT}" \
    BASE_REPO="${BASE_REPO}" \
    WORK_ROOT="${WORK_ROOT}" \
    RUN_ID="${run_id}" \
    CAPTURE_RUNNING_PASS=1 \
    RUNNER_CAPTURE_ARTIFACT="${raw_capture}" \
    JOBS="${JOBS:-2}" \
    LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS:-2}}" \
    bash "${ROOT}/scripts/${issue}/bisect-runner.sh" "${worktree}"
  local rc=$?
  set -e

  if [[ "${rc}" -ne 1 ]]; then
    {
      printf 'status=failed\n'
      printf 'runner_exit_code=%s\n' "${rc}"
      printf 'finished_at=%s\n' "$(date -Iseconds)"
    } >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi

  if [[ ! -s "${raw_capture}" ]]; then
    mapfile -t capture_files < <(
      for suffix in out err passdiag.out passdiag.err; do
        for path in "${tmp_dir}"/*."${suffix}"; do
          [[ -s "${path}" ]] && printf '%s\n' "${path}"
        done
      done
    )
    : > "${raw_capture}"
    for path in "${capture_files[@]}"; do
      cat "${path}" >> "${raw_capture}"
      printf '\n' >> "${raw_capture}"
    done
  fi

  if [[ ! -s "${raw_capture}" ]]; then
    {
      printf 'status=failed\n'
      printf 'reason=no-nonempty-runner-output\n'
      printf 'finished_at=%s\n' "$(date -Iseconds)"
    } >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi

  mkdir -p "${case_dir}"
  cp "${raw_capture}" "${case_dir}/crash-assertion.err"

  mapfile -t reproducer_files < <(
    find "${SOURCE_REPRO_ROOT}/cases/${issue}" -maxdepth 1 -type f \
      ! -name 'crash-assertion.err' -print | sort
  )
  if [[ "${#reproducer_files[@]}" -eq 0 ]]; then
    {
      printf 'status=failed\n'
      printf 'reason=no-reproducer-files\n'
      printf 'finished_at=%s\n' "$(date -Iseconds)"
    } >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi
  for path in "${reproducer_files[@]}"; do
    cp "${path}" "${case_dir}/$(basename "${path}")"
  done

  {
    printf 'status=completed\n'
    printf 'runner_exit_code=1\n'
    printf 'source_artifact=%s\n' "${raw_capture}"
    printf 'finished_at=%s\n' "$(date -Iseconds)"
  } >> "${issue_status}"
  cleanup_worktree "${worktree}"
}

lane_a() {
  local failed=0
  capture_case lane-a pr165039 || failed=1
  capture_case lane-a pr195788 || failed=1
  capture_case lane-a pr198257 || failed=1
  return "${failed}"
}

lane_b() {
  local failed=0
  capture_case lane-b pr197797 || failed=1
  capture_case lane-b pr196244 || failed=1
  capture_case lane-b pr200742 || failed=1
  return "${failed}"
}

lane_c() {
  local failed=0
  capture_case lane-c pr120802 || failed=1
  capture_case lane-c pr172195 || failed=1
  capture_case lane-c pr192829 || failed=1
  capture_case lane-c pr202343 || failed=1
  return "${failed}"
}

lane_a > "${STATUS_ROOT}/lane-a.capture.log" 2>&1 &
pid_a=$!
lane_b > "${STATUS_ROOT}/lane-b.capture.log" 2>&1 &
pid_b=$!
lane_c > "${STATUS_ROOT}/lane-c.capture.log" 2>&1 &
pid_c=$!
printf 'lane_a_pid=%s\nlane_b_pid=%s\nlane_c_pid=%s\n' \
  "${pid_a}" "${pid_b}" "${pid_c}" >> "${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
wait "${pid_b}" || exit_code=1
wait "${pid_c}" || exit_code=1
if [[ "${exit_code}" -ne 0 ]]; then
  printf 'finished_at=%s\nexit_code=1\n' "$(date -Iseconds)" >> "${STATUS_ROOT}/run.meta"
  exit 1
fi

if ! "${PY}" - "${OUTPUT_ROOT}" "${PROFILES}" "${ROOT}" "${CAPTURE_LABEL}" "${ISSUES[@]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

output_root = Path(sys.argv[1]).resolve()
profiles = json.loads(Path(sys.argv[2]).read_text())
repo_root = Path(sys.argv[3]).resolve()
capture_label = sys.argv[4]
issues = sys.argv[5:]
cases = []
for issue in issues:
    case_dir = output_root / "cases" / issue
    artifact = case_dir / "crash-assertion.err"
    reproducers = sorted(
        path for path in case_dir.iterdir()
        if path.is_file() and path.name != artifact.name
    )
    if not artifact.is_file() or not reproducers:
        raise SystemExit(f"incomplete CEG capture for {issue}")
    relative_artifact = artifact.relative_to(output_root).as_posix()
    source = (
        repo_root / "results" / "issues" / issue
        / f"{capture_label}-lane-{'a' if issue in {'pr165039', 'pr195788', 'pr198257'} else 'b' if issue in {'pr197797', 'pr196244', 'pr200742'} else 'c'}-{issue}-bad-endpoint.raw"
    )
    cases.append(
        {
            "issue": issue,
            "capture_role": "bad-endpoint",
            "capture_commit": profiles[issue]["bad_commit"],
            "crash_artifact": relative_artifact,
            "crash_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "source_artifact": source.relative_to(repo_root).as_posix(),
            "reproducer_paths": [
                path.relative_to(output_root).as_posix() for path in reproducers
            ],
            "reproducer_sha256": {
                path.relative_to(output_root).as_posix():
                    hashlib.sha256(path.read_bytes()).hexdigest()
                for path in reproducers
            },
        }
    )

manifest = {
    "protocol": "ceg-bad-endpoint-v1",
    "label": capture_label,
    "purpose": (
        "Leakage-controlled CEG inputs captured by the issue runner at each "
        "configured bad endpoint."
    ),
    "forbidden_inputs": [
        "validated first-bad SHA",
        "first-bad patch",
        "first-bad compact diff",
        "researcher-authored issue summary",
        "researcher-authored relevant/high-risk paths",
    ],
    "cases": cases,
}
(output_root / "manifest.json").write_text(
    json.dumps(manifest, indent=2) + "\n",
    encoding="utf-8",
)
PY
then
  printf 'finished_at=%s\nexit_code=1\nreason=manifest-generation-failed\n' \
    "$(date -Iseconds)" >> "${STATUS_ROOT}/run.meta"
  exit 1
fi

printf 'finished_at=%s\nexit_code=0\n' "$(date -Iseconds)" >> "${STATUS_ROOT}/run.meta"
