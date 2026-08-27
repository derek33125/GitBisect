#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)

GOOD_COMMIT="${GOOD_COMMIT:-e9f758a59b2f887acb07e26e2d480c7369a72009}"
BAD_COMMIT="${BAD_COMMIT:-30fa4153a556f51143b1145af8c603581c80369a}"

exec "${ROOT_DIR}/scripts/benchmark/run-validated-git-bisect.sh" pr194000 "${GOOD_COMMIT}" "${BAD_COMMIT}" "$@"
