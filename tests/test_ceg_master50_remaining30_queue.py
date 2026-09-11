from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "scripts/benchmark/capture-ceg-master50-remaining30-endpoints.sh"
QUEUE = ROOT / "scripts/benchmark/run-ceg-master50-remaining30-queue.sh"

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


class CegMaster50Remaining30QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = CAPTURE.read_text()
        self.queue = QUEUE.read_text()
        self.shared_runner = (
            ROOT / "scripts/benchmark/validated-bisect-runner.sh"
        ).read_text()
        self.master50 = {
            row["issue"]
            for row in json.loads(
                (ROOT / "benchmark-results/master50/issues.json").read_text()
            )
        }

    def test_remaining30_completes_exact_master50_partition(self) -> None:
        remaining = shell_array(self.queue, "ISSUES")

        self.assertEqual(len(remaining), 30)
        self.assertEqual(len(set(remaining)), 30)
        self.assertTrue(set(remaining).isdisjoint(SCOPED10 | NEXT10))
        self.assertEqual(set(remaining) | SCOPED10 | NEXT10, self.master50)
        self.assertEqual(shell_array(self.capture, "ISSUES"), remaining)

    def test_each_issue_is_assigned_once_to_three_balanced_lanes(self) -> None:
        declared = shell_array(self.queue, "ISSUES")
        expected_lengths = {
            "LANE_A_ISSUES": 10,
            "LANE_B_ISSUES": 10,
            "LANE_C_ISSUES": 10,
        }
        assigned = []
        lane_costs = []
        rows = {
            row["issue"]: row
            for row in json.loads(
                (ROOT / "benchmark-results/master50/issues.json").read_text()
            )
        }
        for lane, expected_length in expected_lengths.items():
            issues = shell_array(self.queue, lane)
            self.assertEqual(len(issues), expected_length)
            assigned.extend(issues)
            lane_costs.append(
                sum(rows[issue].get("git_steps") or 30 for issue in issues)
            )

        self.assertCountEqual(assigned, declared)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertLessEqual(max(lane_costs) - min(lane_costs), 20)
        for lane in ("LANE_A_ISSUES", "LANE_B_ISSUES", "LANE_C_ISSUES"):
            self.assertEqual(
                shell_array(self.capture, lane),
                shell_array(self.queue, lane),
            )

    def test_capture_copies_only_reproducer_sources_from_first_bad_bundle(self) -> None:
        self.assertIn('find "${source_case}" -maxdepth 2 -type f', self.capture)
        self.assertIn("-name '*.cpp'", self.capture)
        self.assertIn('"first-bad", "metadata", "patch", "diff"', self.capture)
        self.assertNotIn("! -name 'crash-assertion.err'", self.capture)
        self.assertIn("RUNNER_CAPTURE_ARTIFACT", self.capture)
        self.assertIn("no-leakage-safe-reproducer", self.capture)

    def test_queue_verifies_endpoint_role_hashes_and_bad_commit(self) -> None:
        self.assertIn('manifest.get("protocol") != "ceg-bad-endpoint-v1"', self.queue)
        self.assertIn("crash artifact hash mismatch", self.queue)
        self.assertIn("reproducer hash mismatch", self.queue)
        self.assertIn("bad-endpoint commit mismatch", self.queue)
        self.assertIn('CEG_QUEUE_PARTITION="${CEG_QUEUE_PARTITION:-all30}"', self.queue)
        self.assertIn(
            "CEG input manifest must match the selected remaining30 partition",
            self.queue,
        )

    def test_queue_partitions_match_capture_partitions(self) -> None:
        for name in ("edu15", "aws15"):
            capture_match = re.search(
                rf"  {name}\)\n(?P<body>.*?)\n    ;;",
                self.capture,
                re.DOTALL,
            )
            queue_match = re.search(
                rf"  {name}\)\n(?P<body>.*?)\n    ;;",
                self.queue,
                re.DOTALL,
            )
            self.assertIsNotNone(capture_match)
            self.assertIsNotNone(queue_match)
            self.assertEqual(
                set(re.findall(r"\bpr\d+\b", capture_match.group("body"))),
                set(re.findall(r"\bpr\d+\b", queue_match.group("body"))),
            )

    def test_queue_is_resumable_and_uses_exactly_three_physical_lanes(self) -> None:
        self.assertIn("case_completed", self.queue)
        self.assertIn("ceg_physical_lanes=3", self.queue)
        self.assertIn("causal-evidence-guided-k12", self.queue)
        self.assertIn('run_case "${physical_lane}" "${issue}" || failed=1', self.queue)
        self.assertIn('return "${case_exit}"', self.queue)
        self.assertEqual(self.queue.count("run_lane lane-"), 3)

    def test_capture_and_queue_have_disk_guards_and_terminal_cache_cleanup(self) -> None:
        for text in (self.capture, self.queue):
            self.assertIn('MIN_FREE_GB="${MIN_FREE_GB:-80}"', text)
            self.assertIn('wait_for_free_space "${issue}"', text)
        self.assertIn('cleanup_issue_cache "${issue}"', self.queue)
        self.assertIn('"${ROOT}/.ccache/${issue}-git-bisect"', self.queue)

    def test_capture_preflights_every_runner_and_reproducer(self) -> None:
        self.assertIn(
            '[[ ! -f "${ROOT}/scripts/${issue}/bisect-runner.sh" ]]',
            self.capture,
        )
        self.assertIn("-exec chmod +x {} +", self.capture)
        self.assertIn(
            'find "${SOURCE_REPRO_ROOT}/cases/${issue}"',
            self.capture,
        )
        self.assertIn(
            'find "${ROOT}/scripts/${issue}" -maxdepth 1 -type f',
            self.capture,
        )
        self.assertIn('find "${source_case}" -maxdepth 2 -type f', self.capture)
        self.assertIn('error: missing allowed reproducer for ${issue}', self.capture)
        self.assertIn('if [[ "${preflight_failed}" -ne 0 ]]', self.capture)
        self.assertIn(
            '[[ "${CEG_CAPTURE_PREFLIGHT_ONLY:-0}" == "1" ]]',
            self.capture,
        )

    def test_single_case_retry_reuses_lane_without_writing_partial_manifest(self) -> None:
        self.assertIn('CEG_CAPTURE_ONLY_ISSUE="${CEG_CAPTURE_ONLY_ISSUE:-}"', self.capture)
        self.assertIn('CEG_CAPTURE_RETRY_ONLY="${CEG_CAPTURE_RETRY_ONLY:-0}"', self.capture)
        self.assertIn('ISSUES=("${CEG_CAPTURE_ONLY_ISSUE}")', self.capture)
        self.assertIn('if [[ "${CEG_CAPTURE_RETRY_ONLY}" == "1" ]]; then', self.capture)
        retry_exit = self.capture.index('retry_finished_at=%s')
        manifest_generation = self.capture.index('if ! "${PY}" - "${OUTPUT_ROOT}"')
        self.assertLess(retry_exit, manifest_generation)

    def test_old_pr50655_has_explicit_host_toolchain_compatibility(self) -> None:
        self.assertIn('[[ "${ISSUE}" == "pr50655" ]]', self.shared_runner)
        self.assertIn("command -v g++-13", self.shared_runner)
        self.assertIn("-include cstdint", self.shared_runner)

    def test_distributed_capture_partitions_are_disjoint_and_complete(self) -> None:
        expected = set(shell_array(self.capture, "ISSUES"))
        partitions = {}
        for name, following in (("edu15", "aws15"), ("aws15", r"\*")):
            match = re.search(
                rf"  {name}\)\n(?P<body>.*?)\n    ;;\n  {following}",
                self.capture,
                re.DOTALL,
            )
            self.assertIsNotNone(match)
            occurrences = re.findall(r"\bpr\d+\b", match.group("body"))
            self.assertEqual(len(occurrences), 30)
            self.assertTrue(all(occurrences.count(issue) == 2 for issue in set(occurrences)))
            partitions[name] = set(occurrences)

        self.assertEqual(len(partitions["edu15"]), 15)
        self.assertEqual(len(partitions["aws15"]), 15)
        self.assertTrue(partitions["edu15"].isdisjoint(partitions["aws15"]))
        self.assertEqual(partitions["edu15"] | partitions["aws15"], expected)


if __name__ == "__main__":
    unittest.main()
