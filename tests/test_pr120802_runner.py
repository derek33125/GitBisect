from __future__ import annotations

import unittest
from pathlib import Path


RUNNER = Path("scripts/pr120802/bisect-runner.sh")


class Pr120802RunnerTests(unittest.TestCase):
    def test_accepts_an_opt_in_cxx_only_compatibility_flag(self) -> None:
        text = RUNNER.read_text()

        self.assertIn('if [[ -n "${EXTRA_CMAKE_CXX_FLAGS:-}" ]]; then', text)
        self.assertIn(
            'configure_args+=("-DCMAKE_CXX_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}")',
            text,
        )
        self.assertNotIn("-DCMAKE_C_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}", text)

