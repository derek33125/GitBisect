#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: shared-good-endpoint-sweep.sh [options]

Build one shared LLVM release/tag and run multiple issue reproducers against
that prebuilt toolchain to discover reusable good endpoints.

Options:
  --release TAG          Use an explicit LLVM release tag, e.g. llvmorg-17.0.6.
  --iterations N         Run at most N release sweeps. Default: 1.
  --min-eligible N       Minimum eligible issues for an auto-selected release. Default: 1.
  --selection MODE       newest or coverage. Default: newest.
  --issues LIST          Space/comma separated issue ids to keep from metadata.
  --metadata PATH        Endpoint metadata JSON. Default: scripts/benchmark/shared-good-endpoints.json.
  --releases LIST        Candidate release tags, newest to oldest by default.
  --dry-run              Print selected releases/issues without building.
  -h, --help             Show this help.

Environment:
  ROOT                   Repo root. Default: detected from this script.
  BASE_REPO              llvm-project checkout. Default: /home/derek331/research/gitbisect-work/llvm-project.
  WORK_ROOT              Work/build root. Default: /home/derek331/research/gitbisect-work/worktrees.
  JOBS                   Build parallelism. Default: 4.
  LM_BISECT_BUILD_TYPE   Release by default.
  LM_BISECT_ENABLE_ASSERTIONS ON by default.
  SHARED_GOOD_BUILD_TARGETS Space-separated CMake targets. Default: clang.
  SHARED_GOOD_LLVM_ENABLE_PROJECTS Semicolon-separated LLVM projects. Default: clang.
  SHARED_GOOD_LLVM_TARGETS Semicolon-separated LLVM targets. Default: X86.
EOF
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT="${ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
BASE_REPO="${BASE_REPO:-/home/derek331/research/gitbisect-work/llvm-project}"
WORK_ROOT="${WORK_ROOT:-/home/derek331/research/gitbisect-work/worktrees}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
QUEUE_LIB="${ROOT}/results/issues/server-jobs/server-validation-queue-20260613.sh"
METADATA="${SCRIPT_DIR}/shared-good-endpoints.json"
ITERATIONS=1
MIN_ELIGIBLE=1
SELECTION="newest"
DRY_RUN=0
EXPLICIT_RELEASE=""
ISSUES_FILTER=""
RELEASES="llvmorg-22.1.0 llvmorg-22.0.0 llvmorg-21.1.8 llvmorg-21.1.0 llvmorg-20.1.8 llvmorg-20.1.0 llvmorg-19.1.7 llvmorg-19.1.0 llvmorg-18.1.8 llvmorg-18.1.0 llvmorg-17.0.6 llvmorg-17.0.0 llvmorg-16.0.6 llvmorg-16.0.0 llvmorg-15.0.7 llvmorg-14.0.6 llvmorg-13.0.1 llvmorg-12.0.1 llvmorg-11.1.0 llvmorg-10.0.1 llvmorg-9.0.1 llvmorg-8.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)
      EXPLICIT_RELEASE="$2"
      shift 2
      ;;
    --iterations)
      ITERATIONS="$2"
      shift 2
      ;;
    --min-eligible)
      MIN_ELIGIBLE="$2"
      shift 2
      ;;
    --selection)
      SELECTION="$2"
      shift 2
      ;;
    --issues)
      ISSUES_FILTER="$2"
      shift 2
      ;;
    --metadata)
      METADATA="$2"
      shift 2
      ;;
    --releases)
      RELEASES="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${QUEUE_LIB}" ]]; then
  echo "error: queue library not found: ${QUEUE_LIB}" >&2
  exit 2
fi
if [[ ! -f "${METADATA}" ]]; then
  echo "error: endpoint metadata not found: ${METADATA}" >&2
  exit 2
fi
if ! git -C "${BASE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: llvm-project checkout not found at ${BASE_REPO}" >&2
  exit 2
fi

export ROOT BASE_REPO WORK_ROOT
export JOBS="${JOBS:-4}"
export LM_BISECT_JOBS="${LM_BISECT_JOBS:-${JOBS}}"
export LM_BISECT_BUILD_TYPE="${LM_BISECT_BUILD_TYPE:-Release}"
export LM_BISECT_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS:-ON}"
export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"
export LM_BISECT_QUEUE_LIBRARY_ONLY=1
export SHARED_GOOD_BUILD_TARGETS="${SHARED_GOOD_BUILD_TARGETS:-clang}"
export SHARED_GOOD_LLVM_ENABLE_PROJECTS="${SHARED_GOOD_LLVM_ENABLE_PROJECTS:-clang}"
export SHARED_GOOD_LLVM_TARGETS="${SHARED_GOOD_LLVM_TARGETS:-X86}"

# shellcheck source=/dev/null
source "${QUEUE_LIB}" crash

RESULTS_DIR="${ROOT}/results/shared-good-endpoints/${RUN_ID}"
mkdir -p "${RESULTS_DIR}" "${WORK_ROOT}"
SUMMARY="${RESULTS_DIR}/summary.tsv"
PLAN_JSON="${RESULTS_DIR}/plan.json"
POOL_JSON="${RESULTS_DIR}/pool.json"
printf 'issue\trelease\tbad_ref\tbad_version\tverdict\tnote\trepro_rc\terr_path\n' >"${SUMMARY}"

normalize_list() {
  printf '%s\n' "$1" | tr ',' ' ' | xargs
}

python_select_plan() {
  local remaining="$1"
  local used_releases="$2"
  local release_arg="$3"
  local available_releases=""
  local tag
  for tag in $RELEASES; do
    if git -C "$BASE_REPO" rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
      available_releases="${available_releases} ${tag}"
    else
      echo "warning: skipping unavailable LLVM release tag: ${tag}" >&2
    fi
  done
  if [[ -z "${available_releases// }" ]]; then
    echo "error: none of the requested LLVM release tags exist in ${BASE_REPO}" >&2
    return 2
  fi
  REMAINING="$remaining" USED_RELEASES="$used_releases" RELEASE_ARG="$release_arg" RELEASES_ARG="$available_releases" \
  MIN_ELIGIBLE="$MIN_ELIGIBLE" SELECTION="$SELECTION" METADATA="$METADATA" python3 - <<'PY'
import json
import os
from pathlib import Path
from tools import shared_good_endpoint as sge

metadata = Path(os.environ["METADATA"])
issues = sge.load_issue_endpoints(metadata)
remaining_raw = os.environ["REMAINING"].strip()
if remaining_raw:
    keep = set(remaining_raw.split())
    issues = [issue for issue in issues if issue.issue in keep]
release_arg = os.environ["RELEASE_ARG"].strip()
used = set(os.environ["USED_RELEASES"].split())
if release_arg:
    releases = [release_arg]
else:
    releases = [tag for tag in os.environ["RELEASES_ARG"].split() if tag not in used]
plan = sge.select_common_release(
    issues,
    releases,
    min_eligible=int(os.environ["MIN_ELIGIBLE"]),
    strategy=os.environ["SELECTION"],
)
if plan is None:
    print(json.dumps({"release": "", "issues": []}))
else:
    print(json.dumps({"release": plan.release, "issues": [issue.__dict__ for issue in plan.eligible_issues]}))
PY
}

write_pool_update() {
  local pool="$1"
  POOL="$pool" SUMMARY="$SUMMARY" POOL_JSON="$POOL_JSON" python3 - <<'PY'
import os
from pathlib import Path
from tools import shared_good_endpoint as sge

pool = os.environ["POOL"].split()
summary = Path(os.environ["SUMMARY"])
rows = sge.parse_validation_tsv(summary.read_text())
update = sge.update_pool(pool, rows)
sge.write_pool_update(Path(os.environ["POOL_JSON"]), update)
print(" ".join(update.remaining))
if update.all_skipped:
    raise SystemExit(77)
PY
}

run_friendly_local() {
  if command -v ionice >/dev/null 2>&1; then
    ionice -c 3 nice -n 10 "$@"
  else
    nice -n 10 "$@"
  fi
}

safe_name_local() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

build_release_once() {
  local release="$1"
  local wt="${WORK_ROOT}/shared-good-${release}"
  local build_dir="${wt}/build-shared-good"
  local log="${RESULTS_DIR}/build-${release}.log"
  if [[ ! -d "${wt}/.git" ]]; then
    git -C "${BASE_REPO}" worktree add --detach "${wt}" "${release}" >>"${log}" 2>&1
  else
    git -C "${wt}" checkout -q "${release}" >>"${log}" 2>&1
  fi
  rm -rf "${build_dir}"
  mkdir -p "${build_dir}"
  export CCACHE_DIR="${ROOT}/.ccache/shared-good-endpoints"
  export CCACHE_BASEDIR="${wt}"
  export CCACHE_NOHASHDIR=1
  mkdir -p "${CCACHE_DIR}"
  ccache --set-config="max_size=${CCACHE_MAXSIZE}" >/dev/null 2>&1 || true
  local cfg=(
    cmake -G Ninja
    -S "${wt}/llvm"
    -B "${build_dir}"
    -DLLVM_ENABLE_PROJECTS="${SHARED_GOOD_LLVM_ENABLE_PROJECTS}"
    -DLLVM_TARGETS_TO_BUILD="${SHARED_GOOD_LLVM_TARGETS}"
    -DCMAKE_BUILD_TYPE="${LM_BISECT_BUILD_TYPE}"
    -DLLVM_ENABLE_ASSERTIONS="${LM_BISECT_ENABLE_ASSERTIONS}"
    -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF
    -DLLVM_INCLUDE_UTILS=ON
    -DLLVM_BUILD_TOOLS=ON
  )
  if command -v ccache >/dev/null 2>&1; then
    cfg+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
  fi
  if [[ -n "${EXTRA_CMAKE_C_FLAGS:-}" ]]; then
    cfg+=("-DCMAKE_C_FLAGS=${EXTRA_CMAKE_C_FLAGS}")
  fi
  if [[ -n "${EXTRA_CMAKE_CXX_FLAGS:-}" ]]; then
    cfg+=("-DCMAKE_CXX_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}")
  fi
  if [[ -n "${EXTRA_CMAKE_ARGS:-}" ]]; then
    local extra_cmake_args=()
    read -r -a extra_cmake_args <<<"${EXTRA_CMAKE_ARGS}"
    cfg+=("${extra_cmake_args[@]}")
  fi
  run_friendly_local "${cfg[@]}" >>"${log}" 2>&1
  read -r -a build_targets <<<"${SHARED_GOOD_BUILD_TARGETS}"
  run_friendly_local cmake --build "${build_dir}" --target "${build_targets[@]}" -- -j"${JOBS}" >>"${log}" 2>&1
  local target
  for target in "${build_targets[@]}"; do
    if [[ ! -x "${build_dir}/bin/${target}" ]]; then
      echo "error: requested shared-good tool was not built: ${build_dir}/bin/${target}" >>"${log}"
      return 1
    fi
  done
  printf '%s\n' "${build_dir}"
}

validate_issue_with_build() {
  local issue="$1"
  local release="$2"
  local bad_ref="$3"
  local bad_version="$4"
  local build_dir="$5"
  local issue_dir="${ROOT}/results/issues/${issue}"
  mkdir -p "${issue_dir}"
  TMP_DIR="${RESULTS_DIR}/tmp-${issue}-${release}"
  mkdir -p "${TMP_DIR}"
  BUILD_DIR="${build_dir}"
  LOG="${RESULTS_DIR}/${issue}-${release}.log"
  if ! issue_def "$issue" >>"${LOG}" 2>&1; then
    local err="${TMP_DIR}/${issue}-${release}.err"
    printf 'missing issue definition for shared-good validation: %s\n' "${issue}" >"${err}"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${issue}" "${release}" "${bad_ref}" "${bad_version}" "skip" "missing-issue-definition" "125" "${err}" >>"${SUMMARY}"
    return 0
  fi
  local label
  label="$(safe_name_local "${issue}-${release}")"
  local repro_rc=0
  set +e
  run_repro "$label" >>"${LOG}" 2>&1
  repro_rc=$?
  set -e
  local err="${TMP_DIR}/${label}.err"
  local verdict
  if [[ ! -f "${err}" ]]; then
    verdict="skip"
    printf 'missing reproducer stderr at %s\n' "${err}" >>"${LOG}"
  else
    cat "${err}" >>"${LOG}" || true
    verdict="$(classify_repro "${repro_rc}" "${err}")"
  fi
  local note="shared-prebuilt-repro"
  if [[ "${verdict}" == "bad" ]]; then
    note="still-bad-at-shared-release"
  elif [[ "${verdict}" == "skip" ]]; then
    note="shared-release-skip-or-incompatible"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${issue}" "${release}" "${bad_ref}" "${bad_version}" "${verdict}" "${note}" "${repro_rc}" "${err}" >>"${SUMMARY}"
}

if [[ -n "${ISSUES_FILTER}" ]]; then
  REMAINING="$(normalize_list "${ISSUES_FILTER}")"
else
  REMAINING="$(METADATA="${METADATA}" python3 - <<'PY'
from pathlib import Path
import os
from tools import shared_good_endpoint as sge
print(" ".join(issue.issue for issue in sge.load_issue_endpoints(Path(os.environ["METADATA"])) if issue.status not in {"done", "good", "dropped"}))
PY
)"
fi

USED_RELEASES=""
for ((iteration = 1; iteration <= ITERATIONS; iteration++)); do
  if [[ -z "${REMAINING}" ]]; then
    echo "pool is empty; stopping"
    break
  fi
  PLAN="$(python_select_plan "${REMAINING}" "${USED_RELEASES}" "${EXPLICIT_RELEASE}")"
  printf '%s\n' "${PLAN}" >"${RESULTS_DIR}/plan-${iteration}.json"
  RELEASE="$(PLAN_JSON_TEXT="${PLAN}" python3 - <<'PY'
import json, os
print(json.loads(os.environ["PLAN_JSON_TEXT"])["release"])
PY
)"
  if [[ -z "${RELEASE}" ]]; then
    echo "no release satisfies the remaining pool; stopping"
    break
  fi
  cat "${RESULTS_DIR}/plan-${iteration}.json" >"${PLAN_JSON}"
  echo "iteration ${iteration}: release=${RELEASE}"
  PLAN_ISSUES="$(PLAN_JSON_TEXT="${PLAN}" python3 - <<'PY'
import json, os
payload = json.loads(os.environ["PLAN_JSON_TEXT"])
print(" ".join(issue["issue"] for issue in payload["issues"]))
PY
)"
  echo "eligible issues: ${PLAN_ISSUES}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    USED_RELEASES="${USED_RELEASES} ${RELEASE}"
    continue
  fi
  if ! SHARED_BUILD_DIR="$(build_release_once "${RELEASE}")"; then
    echo "shared-good build failed for ${RELEASE}; see ${RESULTS_DIR}/build-${RELEASE}.log" >&2
    exit 125
  fi
  PLAN_JSON_TEXT="${PLAN}" python3 - <<'PY' >"${RESULTS_DIR}/issues-${iteration}.tsv"
import json, os
payload = json.loads(os.environ["PLAN_JSON_TEXT"])
for issue in payload["issues"]:
    print("\t".join([issue["issue"], issue["bad_ref"], issue["bad_version"]]))
PY
  while IFS=$'\t' read -r issue bad_ref bad_version; do
    validate_issue_with_build "${issue}" "${RELEASE}" "${bad_ref}" "${bad_version}" "${SHARED_BUILD_DIR}"
  done <"${RESULTS_DIR}/issues-${iteration}.tsv"
  set +e
  NEXT_REMAINING="$(write_pool_update "${REMAINING}")"
  pool_rc=$?
  set -e
  REMAINING="${NEXT_REMAINING}"
  if [[ "${pool_rc}" -eq 77 ]]; then
    echo "all tested issues skipped for ${RELEASE}; stopping for user inspection"
    exit 77
  fi
  USED_RELEASES="${USED_RELEASES} ${RELEASE}"
done

echo "summary: ${SUMMARY}"
echo "pool: ${POOL_JSON}"
