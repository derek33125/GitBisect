#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: find-good-ref-and-bisect.sh [llvm-project-path]

Automatically validates fallback release references for issue #191581,
starting from 20.x and then 19.x, and runs git bisect once a good endpoint
is found.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
RESULTS_DIR="${ROOT_DIR}/results"
SUMMARY_LOG="${RESULTS_DIR}/pr191581-release-search.log"

mkdir -p "${RESULTS_DIR}"

if [[ ! -d "${LLVM_DIR}/.git" ]]; then
  echo "error: llvm-project checkout not found at ${LLVM_DIR}" >&2
  exit 1
fi

git -C "${LLVM_DIR}" fetch --tags origin >/dev/null 2>&1

candidate_refs=(
  llvmorg-20.1.8
  llvmorg-20.1.7
  llvmorg-20.1.6
  llvmorg-20.1.5
  llvmorg-20.1.4
  llvmorg-20.1.3
  llvmorg-20.1.2
  llvmorg-20.1.1
  llvmorg-20.1.0
  llvmorg-19.1.7
  llvmorg-19.1.6
  llvmorg-19.1.5
  llvmorg-19.1.4
  llvmorg-19.1.3
  llvmorg-19.1.2
  llvmorg-19.1.1
  llvmorg-19.1.0
)

if [[ ! -f "${SUMMARY_LOG}" ]]; then
  echo "# PR191581 Release Search" > "${SUMMARY_LOG}"
  echo "" >> "${SUMMARY_LOG}"
  echo "- issue: https://github.com/llvm/llvm-project/issues/191581" >> "${SUMMARY_LOG}"
  echo "- bad commit: \`45494d9c165965a8f5aaccd00c7301c166bcd575\`" >> "${SUMMARY_LOG}"
  echo "- search order: \`${candidate_refs[*]}\`" >> "${SUMMARY_LOG}"
  echo "" >> "${SUMMARY_LOG}"
fi

good_ref=""

for ref in "${candidate_refs[@]}"; do
  if ! git -C "${LLVM_DIR}" rev-parse -q --verify "${ref}^{commit}" >/dev/null 2>&1; then
    echo "Skipping unresolved ref ${ref}" | tee -a "${SUMMARY_LOG}"
    continue
  fi

  if rg -q --fixed-strings -- "- ${ref}: good" "${SUMMARY_LOG}"; then
    good_ref="${ref}"
    echo "Reusing previously validated good ref ${ref}" | tee -a "${SUMMARY_LOG}"
    break
  fi

  if rg -q --fixed-strings -- "- ${ref}: not usable as good endpoint" "${SUMMARY_LOG}"; then
    echo "Skipping previously rejected ref ${ref}" | tee -a "${SUMMARY_LOG}"
    continue
  fi

  log_path="${RESULTS_DIR}/pr191581-validation-${ref}.log"
  echo "Trying ${ref}" | tee -a "${SUMMARY_LOG}"
  set +e
  GOOD_REF="${ref}" bash "${SCRIPT_DIR}/validate-endpoints.sh" "${LLVM_DIR}" \
    2>&1 | tee "${log_path}"
  rc=${PIPESTATUS[0]}
  set -e

  if [[ ${rc} -eq 0 ]]; then
    good_ref="${ref}"
    echo "- ${ref}: good" | tee -a "${SUMMARY_LOG}"
    break
  fi

  echo "- ${ref}: not usable as good endpoint (rc=${rc})" | tee -a "${SUMMARY_LOG}"
done

if [[ -z "${good_ref}" ]]; then
  echo "" | tee -a "${SUMMARY_LOG}"
  echo "No good endpoint found in tested 20.x/19.x releases." | tee -a "${SUMMARY_LOG}"
  exit 1
fi

echo "" | tee -a "${SUMMARY_LOG}"
echo "Selected good ref: ${good_ref}" | tee -a "${SUMMARY_LOG}"

GOOD_REF="${good_ref}" bash "${SCRIPT_DIR}/run-bisect.sh" "${LLVM_DIR}" \
  2>&1 | tee "${RESULTS_DIR}/pr191581-bisect-run.log"
