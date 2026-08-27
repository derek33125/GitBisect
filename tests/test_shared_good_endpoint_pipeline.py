from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import shared_good_endpoint


class ReleaseOrderingTests(unittest.TestCase):
    def test_release_key_orders_llvm_release_tags(self) -> None:
        tags = ["llvmorg-9.0.1", "llvmorg-17.0.6", "llvmorg-17.0.0", "llvmorg-16.0.6"]

        ordered = sorted(tags, key=shared_good_endpoint.release_key, reverse=True)

        self.assertEqual(ordered, ["llvmorg-17.0.6", "llvmorg-17.0.0", "llvmorg-16.0.6", "llvmorg-9.0.1"])

    def test_is_release_older_than_bad_rejects_equal_or_newer_tags(self) -> None:
        self.assertTrue(shared_good_endpoint.is_release_older_than_bad("llvmorg-17.0.6", "llvmorg-18.1.8"))
        self.assertFalse(shared_good_endpoint.is_release_older_than_bad("llvmorg-17.0.6", "llvmorg-17.0.6"))
        self.assertFalse(shared_good_endpoint.is_release_older_than_bad("llvmorg-18.1.0", "llvmorg-17.0.6"))


class SweepPlanningTests(unittest.TestCase):
    def test_select_common_release_prefers_newest_release_covering_most_issues(self) -> None:
        issues = [
            shared_good_endpoint.IssueEndpoint("prA", "llvmorg-22.1.0", "bad-a"),
            shared_good_endpoint.IssueEndpoint("prB", "llvmorg-20.1.0", "bad-b"),
            shared_good_endpoint.IssueEndpoint("prC", "llvmorg-17.0.6", "bad-c"),
        ]
        releases = ["llvmorg-22.1.0", "llvmorg-21.1.8", "llvmorg-20.1.8", "llvmorg-19.1.7", "llvmorg-16.0.6"]

        plan = shared_good_endpoint.select_common_release(issues, releases, min_eligible=2)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.release, "llvmorg-19.1.7")
        self.assertEqual([issue.issue for issue in plan.eligible_issues], ["prA", "prB"])

    def test_select_common_release_can_require_three_eligible_issues(self) -> None:
        issues = [
            shared_good_endpoint.IssueEndpoint("prA", "llvmorg-22.1.0", "bad-a"),
            shared_good_endpoint.IssueEndpoint("prB", "llvmorg-20.1.0", "bad-b"),
            shared_good_endpoint.IssueEndpoint("prC", "llvmorg-17.0.6", "bad-c"),
        ]
        releases = ["llvmorg-21.1.8", "llvmorg-19.1.7", "llvmorg-16.0.6"]

        plan = shared_good_endpoint.select_common_release(issues, releases, min_eligible=3)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.release, "llvmorg-16.0.6")
        self.assertEqual([issue.issue for issue in plan.eligible_issues], ["prA", "prB", "prC"])

    def test_coverage_strategy_prefers_release_covering_most_issues(self) -> None:
        issues = [
            shared_good_endpoint.IssueEndpoint("prA", "llvmorg-22.1.0", "bad-a"),
            shared_good_endpoint.IssueEndpoint("prB", "llvmorg-20.1.0", "bad-b"),
            shared_good_endpoint.IssueEndpoint("prC", "llvmorg-17.0.6", "bad-c"),
        ]
        releases = ["llvmorg-21.1.8", "llvmorg-19.1.7", "llvmorg-16.0.6"]

        plan = shared_good_endpoint.select_common_release(issues, releases, strategy="coverage")

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.release, "llvmorg-16.0.6")
        self.assertEqual([issue.issue for issue in plan.eligible_issues], ["prA", "prB", "prC"])

    def test_unknown_strategy_fails_fast(self) -> None:
        with self.assertRaises(ValueError):
            shared_good_endpoint.select_common_release([], [], strategy="mystery")


class PoolUpdateTests(unittest.TestCase):
    def test_good_results_leave_pool_and_bad_or_skip_results_stay(self) -> None:
        pool = ["prA", "prB", "prC"]
        rows = [
            shared_good_endpoint.ValidationRow("prA", "llvmorg-17.0.6", "good", "usable-good"),
            shared_good_endpoint.ValidationRow("prB", "llvmorg-17.0.6", "bad", "still-reproduces"),
            shared_good_endpoint.ValidationRow("prC", "llvmorg-17.0.6", "skip", "runner-incompatible"),
        ]

        update = shared_good_endpoint.update_pool(pool, rows)

        self.assertEqual(update.remaining, ["prB", "prC"])
        self.assertEqual(update.validated_good, ["prA"])
        self.assertEqual(update.still_bad, ["prB"])
        self.assertEqual(update.skipped, ["prC"])
        self.assertFalse(update.all_skipped)

    def test_all_skip_batch_requests_stop(self) -> None:
        pool = ["prA", "prB"]
        rows = [
            shared_good_endpoint.ValidationRow("prA", "llvmorg-16.0.6", "skip", "missing-tool"),
            shared_good_endpoint.ValidationRow("prB", "llvmorg-16.0.6", "skip", "missing-tool"),
        ]

        update = shared_good_endpoint.update_pool(pool, rows)

        self.assertEqual(update.remaining, ["prA", "prB"])
        self.assertTrue(update.all_skipped)

    def test_parse_tsv_rows_reads_validation_output(self) -> None:
        text = """issue\trelease\tbad_ref\tbad_version\tverdict\tnote
prA\tllvmorg-17.0.6\tbad-a\tllvmorg-22.1.0\tgood\tusable-good
prB\tllvmorg-17.0.6\tbad-b\tllvmorg-20.1.0\tbad\tstill-reproduces
"""

        rows = shared_good_endpoint.parse_validation_tsv(text)

        self.assertEqual(rows[0], shared_good_endpoint.ValidationRow("prA", "llvmorg-17.0.6", "good", "usable-good"))
        self.assertEqual(rows[1], shared_good_endpoint.ValidationRow("prB", "llvmorg-17.0.6", "bad", "still-reproduces"))

    def test_load_issue_endpoints_and_write_pool_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            metadata = root / "endpoints.json"
            out = root / "pool.json"
            metadata.write_text(
                json.dumps(
                    [
                        {"issue": "prA", "bad_version": "llvmorg-22.1.0", "bad_ref": "bad-a"},
                        {
                            "issue": "prB",
                            "bad_version": "llvmorg-20.1.0",
                            "bad_ref": "bad-b",
                            "status": "dropped",
                        },
                    ]
                )
            )

            endpoints = shared_good_endpoint.load_issue_endpoints(metadata)
            update = shared_good_endpoint.PoolUpdate(
                remaining=["prA"],
                validated_good=[],
                still_bad=["prA"],
                skipped=[],
                all_skipped=False,
            )
            shared_good_endpoint.write_pool_update(out, update)

            self.assertEqual(endpoints[0].issue, "prA")
            self.assertEqual(endpoints[1].status, "dropped")
            self.assertEqual(json.loads(out.read_text())["still_bad"], ["prA"])


if __name__ == "__main__":
    unittest.main()
