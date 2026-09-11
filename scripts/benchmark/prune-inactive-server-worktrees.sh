#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: prune-inactive-server-worktrees.sh [--apply] BASE_REPO WORK_ROOT

Remove generated, inactive GitBisect worktrees while preserving result files,
active builds, recent worktrees, and worktrees with tracked modifications.
Without --apply, print the eligible worktrees without removing them.

Environment:
  MIN_AGE_HOURS  Minimum worktree age (default: 24).
EOF
}

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
  shift
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

BASE_REPO="$(realpath "$1")"
WORK_ROOT="$(realpath "$2")"
MIN_AGE_HOURS="${MIN_AGE_HOURS:-24}"
if [[ ! "${MIN_AGE_HOURS}" =~ ^[0-9]+$ ]]; then
  echo "error: MIN_AGE_HOURS must be a non-negative integer" >&2
  exit 2
fi
if ! git -C "${BASE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: invalid base repository: ${BASE_REPO}" >&2
  exit 2
fi
if [[ ! -d "${WORK_ROOT}" || "${WORK_ROOT}" == "/" ]]; then
  echo "error: unsafe worktree root: ${WORK_ROOT}" >&2
  exit 2
fi

exec 9>"${WORK_ROOT}/.gitbisect-prune.lock"
if ! flock -n 9; then
  echo "error: another worktree cleanup is active" >&2
  exit 1
fi

active_commands="$(ps -eo args=)"
now="$(date +%s)"
minimum_age_seconds=$((MIN_AGE_HOURS * 3600))
eligible=0
removed=0
skipped_active=0
skipped_modified=0
skipped_recent=0
before_bytes="$(df --output=avail -B1 "${WORK_ROOT}" | awk 'NR == 2 {print $1}')"

while IFS= read -r worktree; do
  [[ "${worktree}" == "${BASE_REPO}" ]] && continue
  [[ "${worktree}" == "${WORK_ROOT}/"* ]] || continue
  relative="${worktree#"${WORK_ROOT}/"}"
  [[ "${relative}" != */* ]] || continue
  [[ "${relative}" =~ ^(pr[0-9]+|skip-pr[0-9]+)- ]] || continue
  [[ -d "${worktree}" ]] || continue
  [[ ! -e "${worktree}/.preserve-worktree" ]] || continue

  if grep -Fq -- "${worktree}" <<<"${active_commands}"; then
    skipped_active=$((skipped_active + 1))
    continue
  fi
  age_seconds=$((now - $(stat -c %Y "${worktree}")))
  if (( age_seconds < minimum_age_seconds )); then
    skipped_recent=$((skipped_recent + 1))
    continue
  fi
  set +e
  timeout 1 git -C "${worktree}" diff-index --quiet HEAD -- >/dev/null 2>&1
  status_code=$?
  set -e
  if (( status_code != 0 )); then
    skipped_modified=$((skipped_modified + 1))
    continue
  fi

  eligible=$((eligible + 1))
  printf 'eligible\t%s\n' "${worktree}"
  if (( APPLY )); then
    git -C "${BASE_REPO}" worktree remove --force "${worktree}"
    removed=$((removed + 1))
  fi
done < <(
  git -C "${BASE_REPO}" worktree list --porcelain |
    awk '/^worktree / {sub(/^worktree /, ""); print}'
)

if (( APPLY )); then
  git -C "${BASE_REPO}" worktree prune
fi
after_bytes="$(df --output=avail -B1 "${WORK_ROOT}" | awk 'NR == 2 {print $1}')"
reclaimed_bytes=$((after_bytes - before_bytes))
printf 'summary\tmode=%s\teligible=%s\tremoved=%s\tskipped_active=%s\tskipped_recent=%s\tskipped_modified=%s\treclaimed_bytes=%s\n' \
  "$([[ "${APPLY}" -eq 1 ]] && printf apply || printf dry-run)" \
  "${eligible}" "${removed}" "${skipped_active}" "${skipped_recent}" \
  "${skipped_modified}" "${reclaimed_bytes}"
