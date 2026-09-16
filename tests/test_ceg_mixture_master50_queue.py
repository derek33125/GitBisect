from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "scripts/benchmark/run-ceg-mixture-master50-queue.sh"
COMPARISON = ROOT / "scripts/benchmark/run-comparison-queue-20260630.sh"
REMAINING30 = ROOT / "scripts/benchmark/run-ceg-master50-remaining30-queue.sh"

HIGH_STEP = ["pr195788", "pr156249"]
RUNNER_SKIP = {"pr199162", "pr199526"}
SCOPED10 = {
    "pr204559",
    "pr201444",
    "pr204589",
    "pr193164",
    "pr200987",
    "pr50304",
    "pr50585",
    "pr48154",
    "pr49535",
    "pr52635",
}
NEXT10 = {
    "pr165039",
    "pr197797",
    "pr120802",
    "pr195788",
    "pr196244",
    "pr172195",
    "pr198257",
    "pr200742",
    "pr192829",
    "pr202343",
}


def shell_array(text: str, name: str) -> list[str]:
    match = re.search(rf"^{name}=\(\n(?P<body>.*?)\n\)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f"missing shell array {name}")
    return re.findall(r"\bpr\d+\b", match.group("body"))


class CegMixtureMaster50QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = QUEUE.read_text(encoding="utf-8")
        self.comparison = COMPARISON.read_text(encoding="utf-8")
        self.remaining30 = REMAINING30.read_text(encoding="utf-8")
        self.master50 = {
            row["issue"]
            for row in json.loads(
                (ROOT / "benchmark-results/master50/issues.json").read_text(
                    encoding="utf-8"
                )
            )
        }

    def test_queue_is_one_lane_of_16_to_30_step_cases(self) -> None:
        declared = shell_array(self.queue, "ISSUES")
        self.assertEqual(declared, HIGH_STEP)
        self.assertEqual(shell_array(self.queue, "LANE_A_ISSUES"), HIGH_STEP)
        self.assertTrue(set(declared).issubset(self.master50))
        self.assertTrue(set(declared).isdisjoint(RUNNER_SKIP))
        self.assertEqual(self.queue.count("run_lane lane-"), 1)
        self.assertNotIn("run_lane lane-b", self.queue)
        self.assertNotIn("run_lane lane-c", self.queue)
        self.assertIn('RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-1}"', self.queue)
        self.assertIn("must be 1 for the high-step mixture queue", self.queue)
        self.assertIn("ceg_physical_lanes=1", self.queue)

    def test_endpoint_cohorts_still_map_the_selected_issues(self) -> None:
        scoped = set(shell_array(self.queue, "SCOPED10_ISSUES"))
        next10 = set(shell_array(self.queue, "NEXT10_ISSUES"))
        remaining = set(shell_array(self.queue, "REMAINING30_ISSUES"))
        self.assertEqual(scoped, SCOPED10)
        self.assertEqual(next10, NEXT10)
        self.assertEqual(remaining, set(shell_array(self.remaining30, "ISSUES")))
        self.assertIn("pr195788", next10)
        self.assertIn("pr156249", remaining)
        self.assertNotIn("[pr204178]=1", self.queue)

    def test_queue_uses_mixture_fusion_and_isolated_worktrees(self) -> None:
        self.assertIn('CEG_FUSION_POLICY="${CEG_FUSION_POLICY:-coverage-mixture-v1}"', self.queue)
        self.assertIn("must be coverage-mixture-v1", self.queue)
        self.assertIn("worktrees-ceg-mixture-master50", self.queue)
        self.assertIn("terra-ceg-mixture-v1-master50-20260915", self.queue)
        self.assertIn("ceg_input_root_for", self.queue)
        self.assertIn("crash artifact hash mismatch", self.queue)
        self.assertIn("bad-endpoint commit mismatch", self.queue)
        self.assertIn("case_completed", self.queue)
        self.assertIn("causal-evidence-guided-k12", self.queue)
        self.assertNotIn("causal-evidence-guided-k12-compat", self.queue)

    def test_comparison_queue_forwards_the_fusion_policy(self) -> None:
        self.assertIn('CEG_FUSION_POLICY="${CEG_FUSION_POLICY:-legacy}"', self.comparison)
        self.assertIn('--ceg-fusion-policy "${CEG_FUSION_POLICY}"', self.comparison)
        self.assertIn("unsupported CEG_FUSION_POLICY", self.comparison)


if __name__ == "__main__":
    unittest.main()
