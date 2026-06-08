from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import generate_issue_summary


class BisectLogParsingTests(unittest.TestCase):
    def test_parse_bisect_log_keeps_order_and_first_bad(self) -> None:
        first_bad = "d" * 40
        log_text = f"""git bisect start
# bad: [{"b" * 40}] bad endpoint
git bisect bad {"b" * 40}
# good: [{"a" * 40}] good endpoint
git bisect good {"a" * 40}
# good: [{"c" * 40}] middle good
git bisect good {"c" * 40}
# bad: [{first_bad}] middle bad
git bisect bad {first_bad}
# first bad commit: [{first_bad}] middle bad
"""

        parsed = generate_issue_summary.parse_git_bisect_log(log_text)

        self.assertEqual([step.sha for step in parsed.steps], ["b" * 40, "a" * 40, "c" * 40, first_bad])
        self.assertEqual([step.verdict for step in parsed.steps], ["bad", "good", "good", "bad"])
        self.assertEqual(parsed.first_bad_sha, first_bad)


class IntervalStatsTests(unittest.TestCase):
    def test_compute_interval_stats_uses_first_bad_boundary(self) -> None:
        revs = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

        with mock.patch.object(generate_issue_summary, "git_lines", return_value=revs):
            stats = generate_issue_summary.compute_interval_stats(
                Path("/repo"),
                good_commit="0" * 40,
                bad_commit="d" * 40,
                first_bad_commit="c" * 40,
            )

        self.assertEqual(stats.total_candidates, 4)
        self.assertEqual(stats.good_side_candidates, 2)
        self.assertEqual(stats.bad_side_candidates, 2)
        self.assertAlmostEqual(stats.good_ratio, 0.5)
        self.assertAlmostEqual(stats.bad_ratio, 0.5)


class SummaryRenderingTests(unittest.TestCase):
    def test_select_lm_history_prefers_completed_then_longer_model_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            completed = root / "completed.json"
            in_progress = root / "in-progress.json"
            heuristic = root / "heuristic.json"
            completed.write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "status": "completed",
                        "scorer": "model",
                        "steps": [{"step": 1}, {"step": 2}],
                    }
                )
            )
            in_progress.write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "status": "in_progress",
                        "scorer": "model",
                        "steps": [{"step": 1}, {"step": 2}, {"step": 3}],
                    }
                )
            )
            heuristic.write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "status": "completed",
                        "scorer": "heuristic",
                        "steps": [{"step": 1}, {"step": 2}, {"step": 3}, {"step": 4}],
                    }
                )
            )

            path, history = generate_issue_summary.select_lm_history([in_progress, heuristic, completed])

        self.assertEqual(path, completed)
        self.assertEqual(history["status"], "completed")

    def test_select_lm_history_prefers_canonical_model_before_longer_alternative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            canonical = root / "canonical.json"
            opus = root / "opus.json"
            canonical.write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "status": "completed",
                        "scorer": "model",
                        "model_name": "gpt-5.4-mini",
                        "steps": [{"step": 1}, {"step": 2}],
                    }
                )
            )
            opus.write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "status": "completed",
                        "scorer": "model",
                        "model_name": "claude-opus-4-7",
                        "steps": [{"step": 1}, {"step": 2}, {"step": 3}],
                    }
                )
            )

            path, history = generate_issue_summary.select_lm_history([opus, canonical])

        self.assertEqual(path, canonical)
        self.assertEqual(history["model_name"], "gpt-5.4-mini")

    def test_generate_summary_writes_expected_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            results = root / "results"
            profiles_path = root / "profiles.json"
            llvm_dir = root / "llvm"
            issue_dir = results / "issues" / "demo"
            runs_dir = results / "lm_bisect_runs"
            issue_dir.mkdir(parents=True)
            runs_dir.mkdir(parents=True)
            llvm_dir.mkdir()

            good = "0" * 40
            bad = "f" * 40
            first_bad = "c" * 40
            profiles_path.write_text(
                json.dumps(
                    {
                        "demo": {
                            "issue_id": "demo",
                            "issue_url": "https://example.invalid/demo",
                            "title": "Demo crash",
                            "good_commit": good,
                            "good_ref": "demo-good",
                            "bad_commit": bad,
                            "bisect_log": "results/issues/demo/demo-bisect-log.txt",
                            "runner": "scripts/demo/bisect-runner.sh",
                            "bug_report_summary": "Demo crash in codegen.",
                            "keywords": ["crash"],
                            "relevant_paths": ["clang/lib/CodeGen"],
                            "high_risk_paths": ["clang/lib"],
                        }
                    }
                )
            )
            (issue_dir / "demo-bisect-log.txt").write_text(
                f"# good: [{good}] good endpoint\n"
                f"git bisect good {good}\n"
                f"# bad: [{first_bad}] first bad subject\n"
                f"git bisect bad {first_bad}\n"
                f"# first bad commit: [{first_bad}] first bad subject\n"
            )
            (runs_dir / "demo-model.json").write_text(
                json.dumps(
                    {
                        "issue": "demo",
                        "scorer": "model",
                        "model_name": "gpt-5.4-mini",
                        "search_policy": "calibrated-posterior",
                        "steps": [
                            {"step": 1, "sha": "a" * 40, "verdict": "good", "subject": "lm good"},
                            {"step": 2, "sha": first_bad, "verdict": "bad", "subject": "lm bad"},
                        ],
                    }
                )
            )

            def fake_git_lines(_repo: Path, args: list[str]) -> list[str]:
                if args[:2] == ["rev-list", "--reverse"]:
                    return ["a" * 40, "b" * 40, first_bad, bad]
                if args[0] == "show" and "--name-only" in args:
                    return [
                        first_bad,
                        "2026-01-01 00:00:00 +0000",
                        "first bad subject",
                        "",
                        "clang/lib/CodeGen/Demo.cpp",
                    ]
                return []

            with mock.patch.object(generate_issue_summary, "git_lines", side_effect=fake_git_lines):
                out = generate_issue_summary.generate_summary(
                    issue_id="demo",
                    profiles_path=profiles_path,
                    results_root=results,
                    llvm_dir=llvm_dir,
                )

            text = out.read_text()
            self.assertIn("# demo Summary", text)
            self.assertIn("Interval candidates: `4`", text)
            self.assertIn("Good-side candidates before first bad: `2`", text)
            self.assertIn("Bad-side candidates from first bad: `2`", text)
            self.assertIn("## Git Bisect Path", text)
            self.assertIn("Git path entries: `2`", text)
            self.assertIn("Git non-endpoint tested commits: `1`", text)
            self.assertIn("## LM-Bisect Path", text)
            self.assertIn("LM steps: `2`", text)


if __name__ == "__main__":
    unittest.main()
