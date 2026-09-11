from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import lm_bisect
from tools import scoped10_followup_analysis


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "scripts/benchmark/run-scoped10-followup-queue.sh"


class Scoped10FollowupExperimentTests(unittest.TestCase):
    def test_leave_one_in_controls_are_declared(self) -> None:
        self.assertIn("only-keywords", lm_bisect.HEURISTIC_ABLATION_FACTORS)
        self.assertIn("only-relevant-paths", lm_bisect.HEURISTIC_ABLATION_FACTORS)
        self.assertIn("only-high-risk-paths", lm_bisect.HEURISTIC_ABLATION_FACTORS)
        self.assertIn("only-risky-words", lm_bisect.HEURISTIC_ABLATION_FACTORS)
        self.assertIn("shuffle-signals", lm_bisect.HEURISTIC_ABLATION_FACTORS)

    def test_only_keywords_does_not_use_path_signal(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="good",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["poison"],
            relevant_paths=["llvm/lib/CodeGen"],
            high_risk_paths=["llvm/lib"],
        )

        score, evidence = lm_bisect.score_semantics(
            profile,
            subject="unrelated change",
            body="",
            files=["llvm/lib/CodeGen/Foo.cpp"],
            diff="poison",
            heuristic_ablation="only-keywords",
        )

        self.assertGreater(score, 0.05)
        self.assertIn("keyword hits", " ".join(evidence))
        self.assertNotIn("relevant paths", " ".join(evidence))
        self.assertNotIn("high-risk paths", " ".join(evidence))

    def test_shuffle_signals_uses_nonself_scoped10_sources(self) -> None:
        sources = lm_bisect.shuffled_signal_source_ids("pr204559")

        self.assertEqual(set(sources), set(lm_bisect.HEURISTIC_SIGNAL_FIELDS))
        self.assertTrue(all(source != "pr204559" for source in sources.values()))

    def test_shuffle_signals_reports_the_negative_control_not_a_disabled_factor(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="good",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=[],
            relevant_paths=[],
            high_risk_paths=[],
        )

        _, evidence = lm_bisect.score_semantics(
            profile,
            subject="unrelated change",
            body="",
            files=[],
            diff="",
            heuristic_ablation="shuffle-signals",
        )

        self.assertIn("heuristic negative control: issue signals shuffled", evidence)
        self.assertNotIn("heuristic ablation: shuffle-signals disabled", evidence)

    def test_runtime_gate_rejects_incomplete_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = Path(tmp) / "runtime.json"
            gate.write_text(
                json.dumps(
                    {
                        "scope": "master50-runtime-generation",
                        "status": "complete",
                        "expected_cases": 29,
                        "terminal_cases": 28,
                    }
                )
            )
            result = subprocess.run(
                ["bash", str(QUEUE), "test-lane"],
                cwd=ROOT,
                env={**__import__("os").environ, "RUNTIME_GATE_FILE": str(gate)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 75)
        self.assertIn("29/29", result.stderr)

    def test_followup_queue_supports_nonoverlapping_host_cohorts(self) -> None:
        text = QUEUE.read_text()

        self.assertIn('FOLLOWUP_COHORT', text)
        self.assertIn('standard|compat|all', text)
        self.assertIn('STANDARD_ISSUES', text)
        self.assertIn('COMPAT_ISSUES', text)

    def test_followup_queue_forwards_compatibility_flags_to_child_queue(self) -> None:
        text = QUEUE.read_text()

        self.assertIn('EXTRA_CMAKE_CXX_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}', text)
        self.assertIn('env+=("EXTRA_CMAKE_CXX_FLAGS=${EXTRA_CMAKE_CXX_FLAGS}")', text)

    def test_followup_queue_can_partition_arms_across_lanes(self) -> None:
        text = QUEUE.read_text()

        self.assertIn('FOLLOWUP_ARMS', text)
        self.assertIn('arm_requested()', text)
        self.assertIn('repeat-a|repeat-b|only-keywords|only-relevant-paths|only-high-risk-paths|only-risky-words|shuffle-signals', text)

    def test_counterfactual_report_marks_only_matched_prefix_as_paired(self) -> None:
        baseline = {
            "issue": "pr1",
            "run_label": "base",
            "status": "completed",
            "steps_executed": 3,
            "steps": [
                {"step": 1, "sha": "a", "unresolved_before": 8, "top_candidates": [{"sha": "a", "rank": 1}]},
                {"step": 2, "sha": "b", "unresolved_before": 4, "top_candidates": [{"sha": "b", "rank": 1}]},
                {"step": 3, "sha": "c", "unresolved_before": 2, "top_candidates": [{"sha": "c", "rank": 1}]},
            ],
        }
        variant = {
            "issue": "pr1",
            "run_label": "variant",
            "status": "completed",
            "steps_executed": 3,
            "steps": [
                {"step": 1, "sha": "a", "unresolved_before": 8, "top_candidates": [{"sha": "a", "rank": 1}]},
                {"step": 2, "sha": "x", "unresolved_before": 4, "top_candidates": [{"sha": "x", "rank": 1}]},
                {"step": 3, "sha": "y", "unresolved_before": 2, "top_candidates": [{"sha": "y", "rank": 1}]},
            ],
        }

        report = scoped10_followup_analysis.compare_history_pair(baseline, variant, "keywords")

        self.assertEqual(report["paired_steps"], 1)
        self.assertEqual(report["first_divergence_step"], 2)
        self.assertEqual(report["post_divergence_steps_not_paired"], 2)

    def test_counterfactual_report_compares_cross_ranks_at_first_shared_state_divergence(self) -> None:
        baseline = {
            "issue": "pr1",
            "run_label": "base",
            "status": "completed",
            "steps_executed": 2,
            "steps": [
                {"step": 1, "sha": "a", "unresolved_before": 8, "unresolved_after": 4, "top_candidates": [{"sha": "a", "rank": 1}]},
                {
                    "step": 2,
                    "sha": "b",
                    "unresolved_before": 4,
                    "unresolved_after": 2,
                    "top_candidates": [{"sha": "b", "rank": 1}, {"sha": "x", "rank": 2}],
                },
            ],
        }
        variant = {
            "issue": "pr1",
            "run_label": "variant",
            "status": "completed",
            "steps_executed": 2,
            "steps": [
                {"step": 1, "sha": "a", "unresolved_before": 8, "unresolved_after": 4, "top_candidates": [{"sha": "a", "rank": 1}]},
                {
                    "step": 2,
                    "sha": "x",
                    "unresolved_before": 4,
                    "unresolved_after": 1,
                    "top_candidates": [{"sha": "x", "rank": 1}, {"sha": "b", "rank": 3}],
                },
            ],
        }

        report = scoped10_followup_analysis.compare_history_pair(baseline, variant, "keywords")

        decision = report["first_divergence_decision"]
        self.assertEqual(decision["baseline_selected_rank_in_variant"], 3)
        self.assertEqual(decision["variant_selected_rank_in_baseline"], 2)
        self.assertTrue(decision["same_unresolved_before"])

    def test_offline_report_is_scoped_to_ten_issues(self) -> None:
        self.assertEqual(
            len(scoped10_followup_analysis.SCOPED10_ISSUES),
            10,
        )

    def test_pairs_manifest_resolves_relative_history_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_path = root / "baseline.json"
            variant_path = root / "variant.json"
            history = {
                "issue": "pr200987",
                "status": "completed",
                "steps_executed": 0,
                "steps": [],
            }
            baseline_path.write_text(json.dumps({**history, "run_label": "baseline"}))
            variant_path.write_text(json.dumps({**history, "run_label": "variant"}))
            manifest_path = root / "pairs.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "pair_sets": {
                            "keywords": [
                                {
                                    "issue": "pr200987",
                                    "baseline_path": "baseline.json",
                                    "variant_path": "variant.json",
                                }
                            ]
                        }
                    }
                )
            )

            pairs = scoped10_followup_analysis.load_pair_set(manifest_path, "keywords")

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "pr200987")
        self.assertEqual(pairs[0][1]["run_label"], "baseline")
        self.assertEqual(pairs[0][2]["run_label"], "variant")


if __name__ == "__main__":
    unittest.main()
