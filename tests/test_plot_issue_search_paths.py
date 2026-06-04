from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import plot_issue_search_paths


class GitBisectParsingTests(unittest.TestCase):
    def test_parse_git_bisect_log_skips_known_endpoints(self) -> None:
        good_endpoint = "1" * 40
        bad_endpoint = "2" * 40
        tested_good = "3" * 40
        tested_bad = "4" * 40
        log_text = f"""git bisect start
# bad: [{bad_endpoint}] bad endpoint
git bisect bad {bad_endpoint}
# good: [{good_endpoint}] good endpoint
git bisect good {good_endpoint}
# good: [{tested_good}] tested good subject
git bisect good {tested_good}
# bad: [{tested_bad}] tested bad subject
git bisect bad {tested_bad}
# first bad commit: [{tested_bad}] tested bad subject
"""

        steps, first_bad = plot_issue_search_paths.parse_git_bisect_log(
            log_text,
            good_endpoint=good_endpoint,
            bad_endpoint=bad_endpoint,
        )

        self.assertEqual([row["sha"] for row in steps], [tested_good, tested_bad])
        self.assertEqual([row["verdict"] for row in steps], ["good", "bad"])
        self.assertEqual(first_bad["sha"], tested_bad)
        self.assertEqual(first_bad["subject"], "tested bad subject")


class OnlineLogParsingTests(unittest.TestCase):
    def test_merge_online_log_segments_keeps_order_and_final_window(self) -> None:
        base_log = """Issue: prX
Scorer: heuristic
Tested path:
- good aaaa1111bbbb unresolved=100 source=runner first subject
- bad cccc2222dddd unresolved=50 source=runner second subject
Final unresolved window:
  - cccc2222dddd
  - eeee3333ffff
"""
        continue_log = """Issue: prX
Scorer: heuristic
Tested path:
- good eeee3333ffff unresolved=2 source=runner continuation subject
Final unresolved window:
  - 9999aaaabbbb
"""

        merged_steps, final_window = plot_issue_search_paths.merge_online_log_segments(
            [
                ("base.log", base_log),
                ("continue.log", continue_log),
            ]
        )

        self.assertEqual([row["step"] for row in merged_steps], [1, 2, 3])
        self.assertEqual(
            [row["sha"] for row in merged_steps],
            ["aaaa1111bbbb", "cccc2222dddd", "eeee3333ffff"],
        )
        self.assertEqual(final_window, ["9999aaaabbbb"])


class RunHistorySelectionTests(unittest.TestCase):
    def test_find_best_run_history_prefers_longest_matching_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            short_heuristic = root / "pr-demo-heuristic-short.json"
            long_heuristic = root / "pr-demo-heuristic-long.json"
            model_file = root / "pr-demo-model.json"

            short_heuristic.write_text(
                json.dumps(
                    {
                        "issue": "pr-demo",
                        "scorer": "heuristic",
                        "model_name": None,
                        "steps": [{"step": 1, "sha": "a" * 40, "verdict": "good", "subject": "one"}],
                    }
                )
            )
            long_heuristic.write_text(
                json.dumps(
                    {
                        "issue": "pr-demo",
                        "scorer": "heuristic",
                        "model_name": None,
                        "steps": [
                            {"step": 1, "sha": "a" * 40, "verdict": "good", "subject": "one"},
                            {"step": 2, "sha": "b" * 40, "verdict": "bad", "subject": "two"},
                        ],
                    }
                )
            )
            model_file.write_text(
                json.dumps(
                    {
                        "issue": "pr-demo",
                        "scorer": "model",
                        "model_name": "gpt-5.4-mini",
                        "steps": [
                            {"step": 1, "sha": "c" * 40, "verdict": "good", "subject": "one"},
                            {"step": 2, "sha": "d" * 40, "verdict": "bad", "subject": "two"},
                            {"step": 3, "sha": "e" * 40, "verdict": "good", "subject": "three"},
                        ],
                    }
                )
            )

            heuristic_path, heuristic_history = plot_issue_search_paths.find_best_run_history(
                root,
                issue_id="pr-demo",
                scorer="heuristic",
            )
            model_path, model_history = plot_issue_search_paths.find_best_run_history(
                root,
                issue_id="pr-demo",
                scorer="model",
            )

        self.assertEqual(heuristic_path, long_heuristic)
        self.assertEqual(len(heuristic_history["steps"]), 2)
        self.assertEqual(model_path, model_file)
        self.assertEqual(len(model_history["steps"]), 3)

    def test_prefer_log_trace_only_when_history_is_missing_or_trivial(self) -> None:
        self.assertTrue(plot_issue_search_paths.prefer_log_trace([], [1]))
        self.assertTrue(plot_issue_search_paths.prefer_log_trace([1], [1, 2]))
        self.assertFalse(plot_issue_search_paths.prefer_log_trace([1, 2], [1, 2, 3]))

    def test_list_matching_run_history_artifacts_returns_all_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "pr-demo-a.json").write_text(
                json.dumps({"issue": "pr-demo", "scorer": "heuristic", "steps": []})
            )
            (root / "pr-demo-b.json").write_text(
                json.dumps({"issue": "pr-demo", "scorer": "heuristic", "steps": [{"step": 1}]})
            )
            (root / "pr-demo-model.json").write_text(
                json.dumps({"issue": "pr-demo", "scorer": "model", "steps": []})
            )
            (root / "other.json").write_text(json.dumps({"issue": "other", "scorer": "heuristic", "steps": []}))

            paths = plot_issue_search_paths.list_matching_run_history_artifacts(
                root,
                issue_id="pr-demo",
                scorer="heuristic",
            )

        self.assertEqual(paths, [str(root / "pr-demo-a.json"), str(root / "pr-demo-b.json")])

    def test_find_best_run_history_can_filter_by_search_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "pr-demo-base.json").write_text(
                json.dumps(
                    {
                        "issue": "pr-demo",
                        "scorer": "model",
                        "model_name": "gpt-5.4-mini",
                        "search_policy": None,
                        "steps": [{"step": 1, "sha": "a" * 40, "verdict": "good", "subject": "one"}],
                    }
                )
            )
            calibrated = root / "pr-demo-cal.json"
            calibrated.write_text(
                json.dumps(
                    {
                        "issue": "pr-demo",
                        "scorer": "model",
                        "model_name": "gpt-5.4-mini",
                        "search_policy": "calibrated-posterior",
                        "steps": [
                            {"step": 1, "sha": "b" * 40, "verdict": "good", "subject": "one"},
                            {"step": 2, "sha": "c" * 40, "verdict": "bad", "subject": "two"},
                        ],
                    }
                )
            )

            path, history = plot_issue_search_paths.find_best_run_history(
                root,
                issue_id="pr-demo",
                scorer="model",
                search_policy="calibrated-posterior",
                model_name="gpt-5.4-mini",
            )

        self.assertEqual(path, calibrated)
        self.assertEqual(len(history["steps"]), 2)


class MethodSetFilteringTests(unittest.TestCase):
    def test_filter_methods_keeps_original_set(self) -> None:
        methods = [
            {"label": "Git bisect"},
            {"label": "Real Heuristic LM-bisect"},
            {"label": "Real Model-Guided LM-bisect"},
            {"label": "Sim Heuristic Calibrated Posterior"},
            {"label": "Sim Model Calibrated Posterior"},
        ]

        filtered = plot_issue_search_paths.filter_methods(methods, "original")

        self.assertEqual(
            [row["label"] for row in filtered],
            [
                "Git bisect",
                "Real Heuristic LM-bisect",
                "Real Model-Guided LM-bisect",
            ],
        )

    def test_filter_methods_keeps_new_set(self) -> None:
        methods = [
            {"label": "Git bisect"},
            {"label": "Real Heuristic LM-bisect"},
            {"label": "Real Model-Guided LM-bisect"},
            {"label": "Real Heuristic Calibrated Posterior"},
            {"label": "Real Model Calibrated Posterior"},
            {"label": "Sim Heuristic Calibrated Posterior"},
            {"label": "Sim Model Calibrated Posterior"},
        ]

        filtered = plot_issue_search_paths.filter_methods(methods, "new")

        self.assertEqual(
            [row["label"] for row in filtered],
            [
                "Git bisect",
                "Real Heuristic Calibrated Posterior",
                "Real Model Calibrated Posterior",
                "Sim Heuristic Calibrated Posterior",
                "Sim Model Calibrated Posterior",
            ],
        )


class CalibratedTraceSelectionTests(unittest.TestCase):
    def test_build_trace_renumbers_steps(self) -> None:
        trace = plot_issue_search_paths.build_trace(
            label="X",
            steps=[
                {"step": 7, "sha": "a" * 40, "verdict": "good", "subject": "one"},
                {"step": 11, "sha": "b" * 40, "verdict": "bad", "subject": "two"},
            ],
            final_unresolved_window=["b" * 40],
            source_kind="demo",
            source_artifacts=["x.log"],
        )

        self.assertEqual([row["step"] for row in trace["steps"]], [1, 2])

    def test_choose_real_calibrated_heuristic_pr172195_trace_reconstructs_final_step(self) -> None:
        trace = plot_issue_search_paths.choose_real_calibrated_heuristic_pr172195_trace()

        self.assertIsNotNone(trace)
        self.assertEqual(trace["label"], "Real Heuristic Calibrated Posterior")
        self.assertEqual(len(trace["steps"]), 10)
        self.assertEqual(trace["steps"][-1]["sha"], "dd0621439497")
        self.assertEqual(trace["steps"][-1]["verdict"], "good")
        self.assertEqual(trace["final_unresolved_window"], ["e8219e5ce84db26fd521ce5091d18e75c7afbc6a"])
        self.assertEqual(len(trace["source_artifacts"]), 2)


if __name__ == "__main__":
    unittest.main()
