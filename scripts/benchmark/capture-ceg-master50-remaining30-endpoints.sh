#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: capture-ceg-master50-remaining30-endpoints.sh OUTPUT_ROOT CAPTURE_LABEL

Capture leakage-safe crash evidence at each configured bad endpoint for the
30 master-50 issues not covered by the completed scoped-10 CEG study or the
master50-next10 expansion. Three physical lanes run in parallel.

Required environment:
  SOURCE_REPRO_ROOT  Master-50 evidence bundle; only reproducer.* source files
                     may be copied from it.
  CEG_CAPTURE_PARTITION
                     all30 (default), edu15, or aws15. The two 15-case
                     partitions are disjoint and union to the exact remaining30.
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
CEG_CAPTURE_PARTITION="${CEG_CAPTURE_PARTITION:-all30}"
CEG_CAPTURE_ONLY_ISSUE="${CEG_CAPTURE_ONLY_ISSUE:-}"
CEG_CAPTURE_RETRY_ONLY="${CEG_CAPTURE_RETRY_ONLY:-0}"

ISSUES=(
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
LANE_A_ISSUES=(
  pr121365 pr165445 pr170421 pr194000 pr197067
  pr199526 pr201855 pr203261 pr204178 pr206007
)
LANE_B_ISSUES=(
  pr156249 pr167514 pr173943 pr194590 pr198339
  pr200330 pr202003 pr203278 pr204561 pr50655
)
LANE_C_ISSUES=(
  pr165246 pr168912 pr190445 pr196450 pr199162
  pr200648 pr202043 pr203519 pr205971 pr65982
)

case "${CEG_CAPTURE_PARTITION}" in
  all30)
    ;;
  edu15)
    ISSUES=(
      pr121365 pr170421 pr197067 pr201855 pr204178
      pr156249 pr173943 pr198339 pr202003 pr204561
      pr165246 pr190445 pr199162 pr202043 pr205971
    )
    LANE_A_ISSUES=(pr121365 pr170421 pr197067 pr201855 pr204178)
    LANE_B_ISSUES=(pr156249 pr173943 pr198339 pr202003 pr204561)
    LANE_C_ISSUES=(pr165246 pr190445 pr199162 pr202043 pr205971)
    ;;
  aws15)
    ISSUES=(
      pr165445 pr194000 pr199526 pr203261 pr206007
      pr167514 pr194590 pr200330 pr203278 pr50655
      pr168912 pr196450 pr200648 pr203519 pr65982
    )
    LANE_A_ISSUES=(pr165445 pr194000 pr199526 pr203261 pr206007)
    LANE_B_ISSUES=(pr167514 pr194590 pr200330 pr203278 pr50655)
    LANE_C_ISSUES=(pr168912 pr196450 pr200648 pr203519 pr65982)
    ;;
  *)
    echo "error: unsupported CEG_CAPTURE_PARTITION=${CEG_CAPTURE_PARTITION}" >&2
    exit 2
    ;;
esac

issue_in_array() {
  local needle="$1"
  shift
  local candidate
  for candidate in "$@"; do
    [[ "${candidate}" == "${needle}" ]] && return 0
  done
  return 1
}

if [[ -n "${CEG_CAPTURE_ONLY_ISSUE}" ]]; then
  if [[ "${CEG_CAPTURE_RETRY_ONLY}" != "1" ]]; then
    echo "error: CEG_CAPTURE_ONLY_ISSUE requires CEG_CAPTURE_RETRY_ONLY=1" >&2
    exit 2
  fi
  if ! issue_in_array "${CEG_CAPTURE_ONLY_ISSUE}" "${ISSUES[@]}"; then
    echo "error: retry issue is outside ${CEG_CAPTURE_PARTITION}" >&2
    exit 2
  fi
  retry_lane=""
  issue_in_array "${CEG_CAPTURE_ONLY_ISSUE}" "${LANE_A_ISSUES[@]}" \
    && retry_lane=lane-a
  issue_in_array "${CEG_CAPTURE_ONLY_ISSUE}" "${LANE_B_ISSUES[@]}" \
    && retry_lane=lane-b
  issue_in_array "${CEG_CAPTURE_ONLY_ISSUE}" "${LANE_C_ISSUES[@]}" \
    && retry_lane=lane-c
  if [[ -z "${retry_lane}" ]]; then
    echo "error: retry issue has no physical lane" >&2
    exit 2
  fi
  ISSUES=("${CEG_CAPTURE_ONLY_ISSUE}")
  LANE_A_ISSUES=()
  LANE_B_ISSUES=()
  LANE_C_ISSUES=()
  case "${retry_lane}" in
    lane-a) LANE_A_ISSUES=("${CEG_CAPTURE_ONLY_ISSUE}") ;;
    lane-b) LANE_B_ISSUES=("${CEG_CAPTURE_ONLY_ISSUE}") ;;
    lane-c) LANE_C_ISSUES=("${CEG_CAPTURE_ONLY_ISSUE}") ;;
  esac
elif [[ "${CEG_CAPTURE_RETRY_ONLY}" == "1" ]]; then
  echo "error: CEG_CAPTURE_RETRY_ONLY=1 requires CEG_CAPTURE_ONLY_ISSUE" >&2
  exit 2
fi

if [[ ! -x "${PY}" || ! -f "${PROFILES}" ]]; then
  echo "error: capture runtime or profiles are missing" >&2
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
if [[ ! -f "${SOURCE_REPRO_ROOT}/manifest.json" ]]; then
  echo "error: source reproducer manifest not found" >&2
  exit 2
fi
if [[ -f "${OUTPUT_ROOT}/manifest.json" ]]; then
  echo "error: completed output root already has a manifest" >&2
  exit 2
fi

"${PY}" - "${PROFILES}" "${#ISSUES[@]}" "${ISSUES[@]}" <<'PY'
import json
import sys
from pathlib import Path

profiles = json.loads(Path(sys.argv[1]).read_text())
expected_count = int(sys.argv[2])
issues = sys.argv[3:]
if len(issues) != expected_count or len(set(issues)) != expected_count:
    raise SystemExit("remaining master-50 CEG capture cohort has duplicate or missing issues")
missing = [issue for issue in issues if issue not in profiles]
if missing:
    raise SystemExit(f"missing issue profiles: {missing}")
PY

preflight_failed=0
for issue in "${ISSUES[@]}"; do
  # A full resume is also the manifest-finalization path. Completed cases are
  # self-contained, so do not require their original reproducer source to
  # remain available after a successful capture.
  existing_case="${OUTPUT_ROOT}/cases/${issue}"
  existing_status="${STATUS_ROOT}/${issue}.status"
  if grep -q '^status=completed$' "${existing_status}" 2>/dev/null \
      && [[ -s "${existing_case}/crash-assertion.err" ]] \
      && find "${existing_case}" -maxdepth 1 -type f \
        \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
           -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
           -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
        -print -quit 2>/dev/null | grep -q . \
      && find "${ROOT}/results/issues/${issue}" -maxdepth 1 -type f \
        -name "${CAPTURE_LABEL}-lane-*-${issue}-bad-endpoint.raw" -size +0c \
        -print -quit 2>/dev/null | grep -q .; then
    continue
  fi
  reproducer_source="$(
    find "${SOURCE_REPRO_ROOT}/cases/${issue}" -maxdepth 2 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print -quit 2>/dev/null
    find "${ROOT}/scripts/${issue}" -maxdepth 1 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print -quit 2>/dev/null
  )"
  find "${ROOT}/scripts/${issue}" -maxdepth 1 -type f -name '*.sh' \
    -exec chmod +x {} +
  if [[ ! -f "${ROOT}/scripts/${issue}/bisect-runner.sh" ]]; then
    echo "error: missing runner for ${issue}" >&2
    preflight_failed=1
  fi
  if [[ -z "${reproducer_source}" ]]; then
    echo "error: missing allowed reproducer for ${issue}" >&2
    preflight_failed=1
  fi
done
if [[ "${preflight_failed}" -ne 0 ]]; then
  exit 2
fi
if [[ "${CEG_CAPTURE_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  printf 'preflight passed for %s (%s cases)\n' \
    "${CEG_CAPTURE_PARTITION}" "${#ISSUES[@]}"
  exit 0
fi

mkdir -p "${OUTPUT_ROOT}/cases" "${STATUS_ROOT}" "${WORK_ROOT}"
if [[ "${CEG_CAPTURE_RETRY_ONLY}" == "1" ]]; then
  printf 'retry_started_at=%s\nretry_issue=%s\nretry_lane=%s\n' \
    "$(date -Iseconds)" "${CEG_CAPTURE_ONLY_ISSUE}" "${retry_lane}" \
    >> "${STATUS_ROOT}/run.meta"
else
  printf 'started_at=%s\ncapture_lanes=3\nexpected_cases=%s\npartition=%s\noutput_root=%s\nmin_free_gb=%s\n' \
    "$(date -Iseconds)" "${#ISSUES[@]}" "${CEG_CAPTURE_PARTITION}" \
    "${OUTPUT_ROOT}" "${MIN_FREE_GB}" > "${STATUS_ROOT}/run.meta"
fi

profile_bad_commit() {
  "${PY}" - "${PROFILES}" "$1" <<'PY'
import json
import sys
from pathlib import Path

print(json.loads(Path(sys.argv[1]).read_text())[sys.argv[2]]["bad_commit"])
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

copy_reproducers() {
  local issue="$1"
  local tmp_dir="$2"
  local case_dir="$3"
  local source_case="${SOURCE_REPRO_ROOT}/cases/${issue}"
  local scratch_case="${ROOT}/scratch/${issue}"
  local copied=0
  local path
  local target

  if find "${case_dir}" -maxdepth 1 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print -quit 2>/dev/null | grep -q .; then
    copied=1
  fi

  while IFS= read -r path; do
    [[ -f "${path}" ]] || continue
    cp "${path}" "${case_dir}/$(basename "${path}")"
    copied=1
  done < <(
    find "${source_case}" -maxdepth 2 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print 2>/dev/null | sort
  )

  if [[ "${copied}" -eq 1 ]]; then
    return 0
  fi

  while IFS= read -r path; do
    [[ -f "${path}" ]] || continue
    cp "${path}" "${case_dir}/$(basename "${path}")"
    copied=1
  done < <(
    find "${ROOT}/scripts/${issue}" -maxdepth 1 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print 2>/dev/null | sort
  )

  if [[ "${copied}" -eq 1 ]]; then
    return 0
  fi

  while IFS= read -r path; do
    [[ -f "${path}" ]] || continue
    target="${case_dir}/$(basename "${path}")"
    if [[ -e "${target}" ]]; then
      target="${case_dir}/runner-$(basename "${path}")"
    fi
    cp "${path}" "${target}"
    copied=1
  done < <(
    find "${tmp_dir}" "${scratch_case}" -maxdepth 3 -type f \
      \( -name '*.c' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
         -o -name '*.h' -o -name '*.hpp' -o -name '*.cppm' -o -name '*.ll' \
         -o -name '*.m' -o -name '*.mm' -o -name '*.py' \) \
      -print 2>/dev/null | sort
  )

  [[ "${copied}" -eq 1 ]]
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
      && [[ -s "${raw_capture}" && -s "${case_dir}/crash-assertion.err" ]]; then
    return 0
  fi

  wait_for_free_space "${issue}"
  {
    printf 'issue=%s\nlane=%s\ncapture_commit=%s\n' \
      "${issue}" "${lane}" "${bad_commit}"
    printf 'status=running\nstarted_at=%s\n' "$(date -Iseconds)"
  } > "${issue_status}"

  cleanup_worktree "${worktree}"
  git -C "${BASE_REPO}" worktree add --detach "${worktree}" "${bad_commit}"
  mkdir -p "${result_dir}" "${case_dir}"
  rm -f "${raw_capture}"

  env \
    ROOT="${ROOT}" \
    BASE_REPO="${BASE_REPO}" \
    WORK_ROOT="${WORK_ROOT}" \
    RUN_ID="${run_id}" \
    CAPTURE_RUNNING_PASS=1 \
    RUNNER_CAPTURE_ARTIFACT="${raw_capture}" \
    CAPTURE_REPRODUCER_DIR="${case_dir}" \
    JOBS="${JOBS:-2}" \
    LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS:-2}}" \
    bash "${ROOT}/scripts/${issue}/bisect-runner.sh" "${worktree}"
  local rc=$?

  if [[ "${rc}" -ne 1 ]]; then
    printf 'status=failed\nrunner_exit_code=%s\nfinished_at=%s\n' \
      "${rc}" "$(date -Iseconds)" >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi

  if [[ ! -s "${raw_capture}" ]]; then
    : > "${raw_capture}"
    for suffix in out err passdiag.out passdiag.err; do
      for path in "${tmp_dir}"/*."${suffix}"; do
        if [[ -s "${path}" ]]; then
          cat "${path}" >> "${raw_capture}"
          printf '\n' >> "${raw_capture}"
        fi
      done
    done
  fi
  if [[ ! -s "${raw_capture}" ]]; then
    printf 'status=failed\nreason=no-nonempty-runner-output\nfinished_at=%s\n' \
      "$(date -Iseconds)" >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi

  cp "${raw_capture}" "${case_dir}/crash-assertion.err"
  if ! copy_reproducers "${issue}" "${tmp_dir}" "${case_dir}"; then
    printf 'status=failed\nreason=no-leakage-safe-reproducer\nfinished_at=%s\n' \
      "$(date -Iseconds)" >> "${issue_status}"
    cleanup_worktree "${worktree}"
    return 1
  fi

  printf 'status=completed\nrunner_exit_code=1\nsource_artifact=%s\nfinished_at=%s\n' \
    "${raw_capture}" "$(date -Iseconds)" >> "${issue_status}"
  cleanup_worktree "${worktree}"
}

run_lane() {
  local lane="$1"
  shift
  local failed=0
  local issue
  for issue in "$@"; do
    capture_case "${lane}" "${issue}" || failed=1
  done
  return "${failed}"
}

lane_log_tag=""
if [[ "${CEG_CAPTURE_RETRY_ONLY}" == "1" ]]; then
  lane_log_tag=".retry-${CEG_CAPTURE_ONLY_ISSUE}"
fi
run_lane lane-a "${LANE_A_ISSUES[@]}" \
  > "${STATUS_ROOT}/lane-a${lane_log_tag}.capture.log" 2>&1 &
pid_a=$!
run_lane lane-b "${LANE_B_ISSUES[@]}" \
  > "${STATUS_ROOT}/lane-b${lane_log_tag}.capture.log" 2>&1 &
pid_b=$!
run_lane lane-c "${LANE_C_ISSUES[@]}" \
  > "${STATUS_ROOT}/lane-c${lane_log_tag}.capture.log" 2>&1 &
pid_c=$!
printf 'lane_a_pid=%s\nlane_b_pid=%s\nlane_c_pid=%s\n' \
  "${pid_a}" "${pid_b}" "${pid_c}" >> "${STATUS_ROOT}/run.meta"

exit_code=0
wait "${pid_a}" || exit_code=1
wait "${pid_b}" || exit_code=1
wait "${pid_c}" || exit_code=1
if [[ "${CEG_CAPTURE_RETRY_ONLY}" == "1" ]]; then
  printf 'retry_finished_at=%s\nretry_issue=%s\nretry_exit_code=%s\n' \
    "$(date -Iseconds)" "${CEG_CAPTURE_ONLY_ISSUE}" "${exit_code}" \
    >> "${STATUS_ROOT}/run.meta"
  exit "${exit_code}"
fi
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
allowed_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cppm", ".ll", ".m", ".mm", ".py"}
cases = []
for issue in issues:
    case_dir = output_root / "cases" / issue
    artifact = case_dir / "crash-assertion.err"
    reproducers = sorted(
        path for path in case_dir.iterdir()
        if path.is_file() and path != artifact and path.suffix in allowed_suffixes
    )
    if not artifact.is_file() or not reproducers:
        raise SystemExit(f"incomplete CEG capture for {issue}")
    forbidden = [
        path.name for path in case_dir.iterdir()
        if any(token in path.name.lower() for token in ("first-bad", "metadata", "patch", "diff"))
    ]
    if forbidden:
        raise SystemExit(f"forbidden answer-leaking files for {issue}: {forbidden}")
    sources = list(
        (repo_root / "results" / "issues" / issue).glob(
            f"{capture_label}-lane-*-{issue}-bad-endpoint.raw"
        )
    )
    if len(sources) != 1:
        raise SystemExit(f"expected one endpoint source artifact for {issue}; got {sources}")
    cases.append(
        {
            "issue": issue,
            "capture_role": "bad-endpoint",
            "capture_commit": profiles[issue]["bad_commit"],
            "crash_artifact": artifact.relative_to(output_root).as_posix(),
            "crash_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "source_artifact": sources[0].relative_to(repo_root).as_posix(),
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
    "purpose": "Leakage-controlled CEG inputs captured at configured bad endpoints.",
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
