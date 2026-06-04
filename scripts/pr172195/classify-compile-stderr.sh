#!/usr/bin/env bash
set -euo pipefail

payload=$(cat)

if [[ -z "${payload}" ]]; then
  exit 1
fi

if printf '%s' "${payload}" | grep -Eiq \
  'LoopVectorizationCostModel::expectedCost|LoopVectorizationPlanner::computeBestVF|LoopVectorizePass::(processLoop|runImpl|run)|Running pass "loop-vectorize'; then
  exit 0
fi

exit 1
