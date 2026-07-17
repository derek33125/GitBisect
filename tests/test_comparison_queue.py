from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT = Path("scripts/benchmark/run-comparison-queue-20260630.sh")


class ComparisonQueueTests(unittest.TestCase):
    def test_runs_project_relative_commands_from_resolved_root(self) -> None:
        text = SCRIPT.read_text()

        root_assignment = text.index('ROOT="${ROOT:-${DEFAULT_ROOT}}"')
        root_change = text.index('cd "${ROOT}"')
        command = text.index('"${PY}" tools/lm_bisect.py run-online')

        self.assertLess(root_assignment, root_change)
        self.assertLess(root_change, command)

    def test_supports_adaptive_parent_extraction_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('adaptive-parent-extract)', text)
        self.assertIn('--adaptive-top-k-threshold "${ADAPTIVE_TOP_K_THRESHOLD}"', text)
        self.assertIn('--adaptive-top-k-large "${ADAPTIVE_TOP_K_LARGE}"', text)
        self.assertIn('--adaptive-top-k-small "${ADAPTIVE_TOP_K_SMALL}"', text)
        self.assertIn('--model-cache-namespace "${MODEL_CACHE_NAMESPACE}"', text)


if __name__ == "__main__":
    unittest.main()
