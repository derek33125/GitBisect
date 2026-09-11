from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "scripts/benchmark/run-ceg-master50-next10-queue.sh"
CAPTURE = ROOT / "scripts/benchmark/capture-ceg-master50-next10-endpoints.sh"


class CegMaster50Next10QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = QUEUE.read_text()

    def test_queue_defines_exactly_ten_unique_non_scoped10_issues(self) -> None:
        issue_block = re.search(r"ISSUES=\(\n(?P<body>.*?)\n\)", self.text, re.DOTALL)
        self.assertIsNotNone(issue_block)
        issues = re.findall(r"\bpr\d+\b", issue_block.group("body"))
        scoped10 = {
            "pr204559",
            "pr204589",
            "pr201444",
            "pr193164",
            "pr50304",
            "pr50585",
            "pr48154",
            "pr49535",
            "pr52635",
            "pr200987",
        }

        self.assertEqual(len(issues), 10)
        self.assertEqual(len(set(issues)), 10)
        self.assertTrue(set(issues).isdisjoint(scoped10))

    def test_queue_uses_exactly_three_physical_lanes(self) -> None:
        lane_functions = re.findall(r"^lane_([abc])\(\) \{", self.text, re.MULTILINE)

        self.assertEqual(lane_functions, ["a", "b", "c"])
        self.assertIn("ceg_physical_lanes=3", self.text)
        self.assertNotRegex(self.text, re.compile(r"^lane_[d-z]\(\)", re.MULTILINE))

    def test_all_cases_use_corrected_ceg_with_pr120802_compatibility(self) -> None:
        self.assertIn(
            'bash "${QUEUE}" "${mode}" "${lane}" "${issue}"',
            self.text,
        )
        self.assertIn('if [[ "${issue}" == "pr120802" ]]; then', self.text)
        self.assertIn("mode=causal-evidence-guided-k12-compat", self.text)

    def test_manifest_preflight_requires_exact_queue_order(self) -> None:
        self.assertIn('CEG_INPUT_ROOT="${CEG_INPUT_ROOT:?CEG_INPUT_ROOT is required}"', self.text)
        self.assertIn("if actual != expected:", self.text)
        self.assertIn("exact queue cohort in queue order", self.text)

    def test_queue_waits_for_disk_and_cleans_terminal_issue_caches(self) -> None:
        self.assertIn('MIN_FREE_GB="${MIN_FREE_GB:-80}"', self.text)
        self.assertIn('wait_for_free_space "${issue}"', self.text)
        self.assertIn('cleanup_issue_cache "${issue}"', self.text)
        self.assertIn('"${ROOT}/.ccache/${issue}-git-bisect"', self.text)

    def test_queue_resumes_completed_cases_and_reports_any_case_failure(self) -> None:
        self.assertIn("case_completed", self.text)
        self.assertEqual(self.text.count("|| failed=1"), 10)
        self.assertIn('return "${case_exit}"', self.text)

    def test_each_issue_is_assigned_once_across_lanes(self) -> None:
        assigned = re.findall(r"run_case lane-[abc] (pr\d+)", self.text)
        issue_block = re.search(r"ISSUES=\(\n(?P<body>.*?)\n\)", self.text, re.DOTALL)
        self.assertIsNotNone(issue_block)
        declared = re.findall(r"\bpr\d+\b", issue_block.group("body"))

        self.assertCountEqual(assigned, declared)
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_capture_collector_uses_runner_capture_contract_and_resumes(self) -> None:
        text = CAPTURE.read_text()

        self.assertIn('RUNNER_CAPTURE_ARTIFACT="${raw_capture}"', text)
        self.assertIn('grep -q \'^status=completed$\' "${issue_status}"', text)
        self.assertIn('completed output root already has a manifest', text)
        self.assertIn('if [[ ! -s "${raw_capture}" ]]; then', text)
        self.assertIn('wait_for_free_space "${issue}"', text)
        self.assertEqual(text.count("capture_case lane-"), 10)
        self.assertEqual(text.count("|| failed=1"), 10)

    def test_all_custom_next10_runners_honor_capture_contract(self) -> None:
        custom_issues = [
            "pr120802",
            "pr172195",
            "pr192829",
            "pr195788",
            "pr196244",
            "pr202343",
        ]
        for issue in custom_issues:
            runner = ROOT / "scripts" / issue / "bisect-runner.sh"
            self.assertIn(
                "RUNNER_CAPTURE_ARTIFACT",
                runner.read_text(),
                f"{issue} does not persist its raw crash output",
            )

        shared = (ROOT / "scripts/benchmark/validated-bisect-runner.sh").read_text()
        self.assertIn('if [[ -n "${RUNNER_CAPTURE_ARTIFACT:-}" ]]; then', shared)


if __name__ == "__main__":
    unittest.main()
