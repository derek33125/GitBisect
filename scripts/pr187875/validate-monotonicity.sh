#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: validate-monotonicity.sh [llvm-project-path]

Runs the pr187875 bisect runner on a targeted ancestry set around the conflicting
culprit candidates and records the raw verdicts.
EOF
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
LLVM_DIR=${1:-"/home/derek331/research/gitbisect-work/llvm-project"}
RUNNER="${SCRIPT_DIR}/bisect-runner.sh"
RESULT_LOG="${ROOT_DIR}/results/issues/pr187875/pr187875-monotonicity-validation.log"

COMMITS=(
  "d4638ad3e9be6dfcdd21b495aedb4285c85b90ca"
  "4f90eb64277c6e618c287e2bf5113003202e119b"
  "b4032db3aa68c1e67ba36a59d6667e9e3c283148"
  "f22a178b132d42a22c5d1a9402641723a655cff3"
)

cd "${LLVM_DIR}"
ORIGINAL_HEAD=$(git rev-parse HEAD)
trap 'git checkout --quiet "${ORIGINAL_HEAD}"' EXIT

rm -f "${RESULT_LOG}"
touch "${RESULT_LOG}"

echo "=== pr187875 monotonicity validation ===" | tee -a "${RESULT_LOG}"
echo "llvm-project: ${LLVM_DIR}" | tee -a "${RESULT_LOG}"
echo "original head: ${ORIGINAL_HEAD}" | tee -a "${RESULT_LOG}"

for sha in "${COMMITS[@]}"; do
  subject=$(git show -s --format='%s' "${sha}")
  echo "" | tee -a "${RESULT_LOG}"
  echo "--- ${sha} ${subject}" | tee -a "${RESULT_LOG}"
  git checkout --quiet "${sha}"

  set +e
  bash "${RUNNER}" "${LLVM_DIR}" >> "${RESULT_LOG}" 2>&1
  rc=$?
  set -e

  case "${rc}" in
    0) verdict="good" ;;
    1) verdict="bad" ;;
    125) verdict="skip" ;;
    *) verdict="unexpected-${rc}" ;;
  esac

  echo "verdict: ${verdict} (rc=${rc})" | tee -a "${RESULT_LOG}"
done

echo "" | tee -a "${RESULT_LOG}"
echo "validation log: ${RESULT_LOG}" | tee -a "${RESULT_LOG}"
