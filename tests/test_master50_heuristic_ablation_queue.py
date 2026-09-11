from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "scripts/benchmark/run-master50-heuristic-ablation-queue.sh"
RETRY_QUEUE = ROOT / "scripts/benchmark/run-master50-heuristic-ablation-retry-queue.sh"


class Master50HeuristicAblationQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = QUEUE.read_text()

    def test_supports_remaining_nonduplicate_controls(self) -> None:
        for factor in ("only-risky-words", "buildability", "feedback"):
            self.assertIn(factor, self.text)
        self.assertNotIn("shuffle-signals", self.text)

    def test_remaining_controls_start_at_distinct_offsets(self) -> None:
        self.assertIn("only-risky-words) issue_offset=8", self.text)
        self.assertIn("buildability) issue_offset=25", self.text)
        self.assertIn("feedback) issue_offset=42", self.text)

    def test_runtime_gate_and_exact_master50_checks_remain_enabled(self) -> None:
        self.assertIn("master50-runtime-generation-20260903.complete.json", self.text)
        self.assertIn('gate.get("status") != "complete"', self.text)
        self.assertIn('gate.get("terminal_cases") != 29', self.text)
        self.assertIn("master-50 runtime-generation gate is not complete at 29/29", self.text)
        self.assertIn("master-50 cohort must contain exactly 50 unique issue IDs", self.text)

    def test_controller_continues_after_individual_issue_failure(self) -> None:
        self.assertIn('status="failed"', self.text)
        self.assertIn('printf \'%s\\t%s\\t%s\\t%s\\n\'', self.text)
        self.assertIn('for issue in "${ISSUES[@]}"; do', self.text)

    def test_routes_known_host_toolchain_failures_through_compat_mode(self) -> None:
        for issue in ("pr120802", "pr121365", "pr173943", "pr204178"):
            self.assertIn(f"[{issue}]=1", self.text)

    def test_pr196244_supports_cmake4_and_old_clangd_configuration(self) -> None:
        runner = (ROOT / "scripts/pr196244/bisect-runner.sh").read_text()

        self.assertIn("-DCMAKE_POLICY_VERSION_MINIMUM=", runner)
        self.assertIn("-DCLANGD_ENABLE_REMOTE=OFF", runner)

    def test_retry_queue_is_subset_scoped_resumable_and_compat_only(self) -> None:
        text = RETRY_QUEUE.read_text()

        self.assertIn("retry issues are outside master-50", text)
        self.assertIn("retry issue list contains duplicates", text)
        self.assertIn("heuristic-ablation-compat", text)
        self.assertIn('grep -q \'^status=completed$\'', text)
        self.assertIn('status=failed', text)

    def test_all_master50_runners_support_clean_endpoint_capture(self) -> None:
        profiles = json.loads((ROOT / "tools/lm_bisect_profiles.json").read_text())
        issues = [
            row["issue"]
            for row in json.loads(
                (ROOT / "benchmark-results/master50/issues.json").read_text()
            )
        ]
        shared_capture = (
            ROOT / "scripts/benchmark/validated-bisect-runner.sh"
        ).read_text()
        self.assertIn("RUNNER_CAPTURE_ARTIFACT", shared_capture)

        for issue in issues:
            runner = ROOT / profiles[issue]["runner"]
            text = runner.read_text()
            if "validated-bisect-runner.sh" not in text:
                self.assertIn(
                    "RUNNER_CAPTURE_ARTIFACT",
                    text,
                    f"{issue} custom runner lacks endpoint capture support",
                )


if __name__ == "__main__":
    unittest.main()
