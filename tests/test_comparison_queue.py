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

    def test_supports_oracle_first_bad_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-first-bad-heuristic)', text)
        self.assertIn('oracle_first_bad_commit()', text)
        self.assertIn('--oracle-first-bad-sha "${oracle_bad}"', text)

    def test_supports_oracle_major_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major', text)

    def test_supports_combined_oracle_major_tuned_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-tuned-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned', text)

    def test_supports_direct_combined_oracle_anchor_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-anchor-major-tuned-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned-anchor', text)

    def test_direct_anchor_failure_does_not_drop_remaining_diagnostics(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('if wait "${child_pid}"; then', text)
        self.assertIn('RUN_ISSUE_EXIT_CODE=$?', text)
        self.assertIn('if (( RUN_ISSUE_EXIT_CODE != 0 )); then', text)
        self.assertIn('retained failed direct-anchor validation for ${issue} exit=${RUN_ISSUE_EXIT_CODE}', text)
        self.assertIn('[[ "${MODE}" == "oracle-anchor-major-tuned-keyword-heuristic" ]]', text)

    def test_model_modes_accept_environment_model_configuration(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('MODEL_NAME="${MODEL_NAME:-}"', text)
        self.assertIn('MODEL_REASONING_EFFORT="${MODEL_REASONING_EFFORT:-}"', text)
        self.assertIn('"${MODEL_ARGS[@]}"', text)


if __name__ == "__main__":
    unittest.main()
