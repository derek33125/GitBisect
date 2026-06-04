#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: godbolt-coarse-probe.sh [major-version]

Runs a public Compiler Explorer compile-only screen for PR196244.
This is a cheap version-bucket prefilter and does not replace the local
clangd LSP runner for exact validation.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
MAJOR="${1:-13}"
RESULTS_DIR="${ROOT_DIR}/results/issues/pr196244"
SOURCE_FILE="${RESULTS_DIR}/pr196244-godbolt-source.cxx"

mkdir -p "${RESULTS_DIR}"

python3 - <<'PY' "${SCRIPT_DIR}/bisect-runner.sh" "${SOURCE_FILE}"
import re
import sys
from pathlib import Path

runner = Path(sys.argv[1])
source_file = Path(sys.argv[2])
text = runner.read_text(encoding="utf-8")
match = re.search(r'SOURCE = r"""(.*?)"""', text, re.S)
if not match:
    raise SystemExit("failed to extract SOURCE from pr196244 runner")
source_file.write_text(match.group(1), encoding="utf-8")
PY

python3 "${ROOT_DIR}/tools/godbolt_coarse_probe.py" \
  --issue pr196244 \
  --major "${MAJOR}" \
  --source-file "${SOURCE_FILE}" \
  --output-dir "${RESULTS_DIR}" \
  --arguments "-std=c++20 -fbracket-depth=1024" \
  --bad-pattern "ExpandedTokens.back().kind() == tok::eof" \
  --inconclusive-pattern "bracket nesting level exceeded" \
  --inconclusive-pattern "function scope depth exceeded"
