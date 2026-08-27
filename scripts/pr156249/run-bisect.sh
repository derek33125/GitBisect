#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)

GOOD_COMMIT="${GOOD_COMMIT:-8c2574832ed2064996389e4259eaf0bea0fa7951}"
BAD_COMMIT="${BAD_COMMIT:-66955aa8003468bdfec098a512fb0df30ce66dd8}"

exec "${ROOT_DIR}/scripts/benchmark/run-validated-git-bisect.sh" pr156249 "${GOOD_COMMIT}" "${BAD_COMMIT}" "$@"
