#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)

GOOD_COMMIT="${GOOD_COMMIT:-}"
BAD_COMMIT="${BAD_COMMIT:-ca7933e47d3a3451d81e72ac174dcb5aa28b59d1}"

if [[ -z "${GOOD_COMMIT}" ]]; then
  echo "error: GOOD_COMMIT is required for full bisect; use bad-endpoint validation first" >&2
  exit 2
fi

exec "${ROOT_DIR}/scripts/benchmark/run-validated-git-bisect.sh" pr204790 "${GOOD_COMMIT}" "${BAD_COMMIT}" "$@"
