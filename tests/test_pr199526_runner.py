from __future__ import annotations

import unittest
from pathlib import Path


RUNNER = Path("scripts/pr199526/bisect-runner.sh")


class Pr199526RunnerTests(unittest.TestCase):
    def test_missing_bootstrap_is_reported_as_a_skip(self) -> None:
        text = RUNNER.read_text()

        self.assertIn('if [[ ! -x "${TOOLS_BOOTSTRAP}" ]]; then', text)
        self.assertIn('error: missing tool bootstrap: ${TOOLS_BOOTSTRAP}', text)
        self.assertIn('error: tool bootstrap failed: ${TOOLS_BOOTSTRAP}', text)
        self.assertIn('exit 125', text)


if __name__ == "__main__":
    unittest.main()
