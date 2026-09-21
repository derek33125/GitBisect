from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.package_master50_ceg_evidence import (
    public_method_row,
    resolve_interval,
    select_history,
    summarize_history,
)


class Master50ResultPackageTests(unittest.TestCase):
    def test_selects_newest_valid_history_over_newer_invalid_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_path = root / "valid.json"
            invalid_path = root / "invalid.json"
            valid_payload = {
                "issue": "pr1",
                "search_policy": "causal-evidence-guided",
                "status": "completed",
                "remaining_unresolved": 1,
                "first_bad_commit": "a" * 40,
                "initial_unresolved": 100,
                "run_label": "valid",
                "steps": [{"verdict": "good"}, {"verdict": "bad"}],
            }
            invalid_payload = {
                **valid_payload,
                "status": "max_steps_exhausted",
                "remaining_unresolved": 5,
                "first_bad_commit": None,
                "run_label": "invalid-newer",
            }
            valid_path.write_text(json.dumps(valid_payload), encoding="utf-8")
            invalid_path.write_text(json.dumps(invalid_payload), encoding="utf-8")
            valid = summarize_history(valid_path, valid_payload)
            invalid = summarize_history(invalid_path, invalid_payload)
            valid["mtime"] = 1.0
            invalid["mtime"] = 2.0

            selected, latest = select_history(
                [valid, invalid],
                "pr1",
                "ceg",
            )

            self.assertEqual(selected["run_label"], "valid")
            self.assertEqual(latest["run_label"], "invalid-newer")
            public = public_method_row(selected, latest, "ceg/pr1.json")
            self.assertEqual(public["availability"], "available")
            self.assertEqual(public["runner_builds"], 2)
            self.assertEqual(public["first_bad_commit"], "a" * 40)

    def test_missing_valid_baseline_stays_explicit(self) -> None:
        latest = {
            "status": "in_progress",
            "runner_builds": 3,
            "skips": 0,
            "first_bad_commit": None,
            "run_label": "partial",
            "filename": "partial.json",
        }

        public = public_method_row(None, latest, None)

        self.assertEqual(public["availability"], "no_valid_result")
        self.assertEqual(public["status"], "in_progress")
        self.assertEqual(public["latest_invalid_history"], "partial.json")

    def test_interval_falls_back_to_run_history_when_ledger_is_null(self) -> None:
        interval, source = resolve_interval(
            {"issue": "pr165246", "interval_commits": None},
            {"interval_commits": 35139},
        )
        self.assertEqual(interval, 35139)
        self.assertEqual(source, "run-history")


if __name__ == "__main__":
    unittest.main()
