from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT = Path("scripts/benchmark/run-validated-heuristic-controls-20260725.sh")


class ValidatedHeuristicControlQueueTests(unittest.TestCase):
    def test_accepts_only_requested_control_modes(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("heuristic|general-heuristic)", text)
        self.assertIn('error: unsupported control mode: ${MODE}', text)

    def test_separates_standard_and_old_source_compatibility_cohorts(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("STANDARD_ISSUES=(", text)
        self.assertIn("COMPATIBILITY_ISSUES=(pr48154 pr49535 pr50304 pr50585 pr52635)", text)
        self.assertIn('run_queue standard "${STANDARD_ISSUES[@]}"', text)
        self.assertIn('bash "${QUEUE}" "${MODE}"', text)
        self.assertIn('EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"', text)
        self.assertIn('run_queue compatibility "${COMPATIBILITY_ISSUES[@]}"', text)

    def test_falls_back_to_system_python_when_runner_root_has_no_venv(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('if [[ -x "${ROOT}/.venv/bin/python" ]]', text)
        self.assertIn('PY="$(command -v python3)"', text)


if __name__ == "__main__":
    unittest.main()
