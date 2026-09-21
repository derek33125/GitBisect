from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "scripts/benchmark/run-ceg-master50-leftover-queue.sh"


class CegMaster50LeftoverQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = QUEUE.read_text()

    def test_one_lane_legacy_leftovers_only(self) -> None:
        self.assertIn("ceg_physical_lanes=1", self.text)
        self.assertIn('CEG_FUSION_POLICY="${CEG_FUSION_POLICY:-legacy}"', self.text)
        self.assertIn("leftover CEG queue must use CEG_FUSION_POLICY=legacy", self.text)
        self.assertIn("leave_evidence_lanes=1", self.text)
        self.assertNotIn("run-evidence-diverse-master50-queue.sh", self.text)
        issues = re.findall(r"\bpr\d+\b", re.search(r"^ISSUES=\(\n(?P<body>.*?)\n\)", self.text, re.M | re.S).group("body"))
        self.assertEqual(
            issues,
            ["pr50304", "pr199526", "pr204589", "pr199162"],
        )
        self.assertIn("[pr50304]=1", self.text)
        self.assertIn("RUN_ONLINE_MAX_LANES must be 1", self.text)


if __name__ == "__main__":
    unittest.main()
