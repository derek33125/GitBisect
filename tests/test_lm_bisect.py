from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tools import lm_bisect


def demo_profile(
    *,
    keywords: list[str] | None = None,
    relevant_paths: list[str] | None = None,
    high_risk_paths: list[str] | None = None,
) -> lm_bisect.IssueProfile:
    return lm_bisect.IssueProfile(
        issue_id="demo",
        issue_url="https://example.invalid",
        title="Demo",
        good_commit="g" * 40,
        good_ref="llvmorg-demo",
        bad_commit="b" * 40,
        bisect_log="results/demo.log",
        runner="scripts/demo.sh",
        bug_report_summary="demo",
        keywords=keywords or ["vectorize", "vplan"],
        relevant_paths=relevant_paths or ["llvm/lib/Transforms/Vectorize"],
        high_risk_paths=high_risk_paths or ["llvm/lib/Transforms"],
    )


class ComputeSelectionTests(unittest.TestCase):
    def test_direct_script_entrypoint_imports_crash_signals(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/lm_bisect.py", "--help"],
            cwd=lm_bisect.ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("run-online", completed.stdout)

    def test_probability_midpoint_can_beat_time_midpoint(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="first",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.05,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="second",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.10,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="c" * 40,
                subject="third",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.15,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="d" * 40,
                subject="fourth",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.45,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=5,
                sha="e" * 40,
                subject="fifth",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.15,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=6,
                sha="f" * 40,
                subject="sixth",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.10,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
        ]

        result = lm_bisect.compute_selection(records, lambda_weight=2.0)
        self.assertEqual(result.selected.index, 4)

    def test_build_risk_can_push_down_candidate(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="safe",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=2.0,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="risky",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=2.0,
                build_success_prob=0.20,
                suspicion_weight=0.0,
            ),
        ]

        result = lm_bisect.compute_selection(records, lambda_weight=2.0)
        self.assertEqual(result.selected.index, 1)

    def test_lower_build_success_power_reduces_safe_commit_advantage(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="safer but weaker",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.05,
                build_success_prob=0.30,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="riskier but stronger",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.10,
                build_success_prob=0.20,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="tail",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.05,
                build_success_prob=0.10,
                suspicion_weight=0.0,
            ),
        ]

        default_result = lm_bisect.compute_selection(records, lambda_weight=2.0, build_success_power=1.0)
        softened_result = lm_bisect.compute_selection(records, lambda_weight=2.0, build_success_power=0.5)

        self.assertEqual(default_result.selected.index, 1)
        self.assertEqual(softened_result.selected.index, 2)


class ScoringTests(unittest.TestCase):
    def test_semantic_scoring_prefers_issue_specific_terms(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["selectiondag", "poison", "pgo"],
            relevant_paths=["llvm/lib/CodeGen/SelectionDAG"],
            high_risk_paths=["llvm/lib/CodeGen"],
        )

        score, evidence = lm_bisect.score_semantics(
            profile,
            subject="DAG: Use poison when widening build_vector",
            body="A PGO-triggered SelectionDAG issue",
            files=["llvm/lib/CodeGen/SelectionDAG/LegalizeVectorTypes.cpp"],
            diff="poison build_vector selectiondag",
        )

        self.assertGreater(score, 3.0)
        self.assertTrue(any("keyword hits" in item for item in evidence))

    def test_semantic_scoring_matches_long_qualified_identifier(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["loopvectorizationcostmodel", "expectedcost"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        score, evidence = lm_bisect.score_semantics(
            profile,
            subject="vectorizer crash",
            body="Stack trace reaches llvm::LoopVectorizationCostModel::expectedCost",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="",
        )

        self.assertGreater(score, 2.0)
        self.assertTrue(any("keyword hits" in item for item in evidence))

    def test_semantic_scoring_v1_preserves_older_weaker_matching(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["loopvectorizationcostmodel", "expectedcost"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        tuned_score, _tuned_evidence = lm_bisect.score_semantics(
            profile,
            subject="vectorizer crash",
            body="Stack trace reaches llvm::LoopVectorizationCostModel::expectedCost",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="",
            heuristic_version="tuned",
        )
        v1_score, _v1_evidence = lm_bisect.score_semantics(
            profile,
            subject="vectorizer crash",
            body="Stack trace reaches llvm::LoopVectorizationCostModel::expectedCost",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="",
            heuristic_version="v1",
        )

        self.assertGreater(tuned_score, v1_score)

    def test_general_keyword_version_uses_shared_maintenance_vocabulary(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["highly-specialized-trigger"],
            relevant_paths=[],
            high_risk_paths=[],
        )

        tuned_score, _ = lm_bisect.score_semantics(
            profile,
            subject="Fix highly-specialized-trigger",
            body="",
            files=[],
            diff="",
            heuristic_version="tuned",
        )
        general_score, general_evidence = lm_bisect.score_semantics(
            profile,
            subject="Update test support documentation",
            body="",
            files=[],
            diff="",
            heuristic_version="general",
        )
        specialized_general_score, specialized_general_evidence = lm_bisect.score_semantics(
            profile,
            subject="Assertion in optimizer",
            body="",
            files=[],
            diff="",
            heuristic_version="general",
        )

        self.assertGreater(tuned_score, specialized_general_score)
        self.assertGreater(general_score, specialized_general_score)
        self.assertTrue(any("keyword hits" in item for item in general_evidence))
        self.assertFalse(any("keyword hits" in item for item in specialized_general_evidence))
        self.assertEqual(
            lm_bisect.effective_heuristic_keywords(profile, "general"),
            list(lm_bisect.GENERAL_KEYWORDS),
        )
        self.assertEqual(
            lm_bisect.effective_heuristic_keywords(profile, "tuned"),
            ["highly-specialized-trigger"],
        )

    def test_no_keyword_version_keeps_nonkeyword_semantic_signals(self) -> None:
        profile = demo_profile(
            keywords=["highly-specialized-trigger"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        score, evidence = lm_bisect.score_semantics(
            profile,
            subject="Fix highly-specialized-trigger vectorizer crash",
            body="",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="",
            heuristic_version="none",
        )

        self.assertEqual(lm_bisect.effective_heuristic_keywords(profile, "none"), [])
        self.assertGreater(score, 0.05)
        self.assertTrue(any("relevant paths" in item for item in evidence))
        self.assertTrue(any("high-risk paths" in item for item in evidence))
        self.assertFalse(any("keyword hits" in item for item in evidence))

    def test_neutral_heuristic_removes_profile_scoring_signals(self) -> None:
        profile = demo_profile(
            keywords=["highly-specialized-trigger"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        score, evidence = lm_bisect.score_semantics(
            profile,
            subject="Fix highly-specialized-trigger vectorizer crash",
            body="",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="risky assert crash fix",
            heuristic_version="neutral",
        )

        self.assertEqual(score, 0.05)
        self.assertEqual(evidence, ["neutral heuristic: no semantic guidance"])

        selection_profile = lm_bisect.heuristic_selection_profile(profile, "neutral")
        self.assertEqual(selection_profile.keywords, [])
        self.assertEqual(selection_profile.relevant_paths, [])
        self.assertEqual(selection_profile.high_risk_paths, [])

    def test_neutral_heuristic_disables_observation_feedback(self) -> None:
        profile = demo_profile(relevant_paths=[], high_risk_paths=[])
        record = lm_bisect.CommitRecord(
            index=1,
            sha="a" * 40,
            subject="unrelated change",
            body="",
            changed_files=["llvm/lib/Analysis/LoopInfo.cpp"],
            diff_text="loop analysis",
            semantic_score=0.05,
            build_success_prob=0.92,
            suspicion_weight=0.0,
            evidence=[],
            features=["path:llvm/lib/Analysis", "term:loop"],
        )
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="bad",
                features=["path:llvm/lib/Analysis", "term:loop"],
                evidence=[],
                log_excerpt="",
            )
        ]

        lm_bisect.apply_feedback_bias(profile, [record], observations, enabled=False)

        self.assertEqual(record.semantic_score, 0.05)
        self.assertEqual(record.feedback_bias, 0.0)

    def test_heuristic_ablation_removes_exactly_one_semantic_factor(self) -> None:
        profile = demo_profile(
            keywords=["VectorBug"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        subject = "Fix VectorBug vector regression"
        files = ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"]
        baseline_score, _ = lm_bisect.score_semantics(profile, subject, "", files, "")

        expected_deltas = {
            "keywords": 0.60,
            "relevant-paths": 1.20,
            "high-risk-paths": 0.50,
            "risky-words": 0.40,
        }
        for factor, expected_delta in expected_deltas.items():
            ablated_profile = lm_bisect.heuristic_ablation_profile(profile, factor)
            score, evidence = lm_bisect.score_semantics(
                ablated_profile,
                subject,
                "",
                files,
                "",
                heuristic_ablation=factor,
            )
            self.assertAlmostEqual(baseline_score - score, expected_delta)
            self.assertIn(f"heuristic ablation: {factor} disabled", evidence)

    def test_buildability_ablation_neutralizes_only_build_score(self) -> None:
        standard_score, _ = lm_bisect.score_build_probability(
            "Update build configuration",
            "",
            ["llvm/CMakeLists.txt"],
            "",
        )
        ablated_score, ablated_evidence = lm_bisect.score_build_probability(
            "Update build configuration",
            "",
            ["llvm/CMakeLists.txt"],
            "",
            heuristic_ablation="buildability",
        )

        self.assertLess(standard_score, 1.0)
        self.assertEqual(ablated_score, 1.0)
        self.assertEqual(ablated_evidence, ["heuristic ablation: buildability disabled"])

    def test_feedback_ablation_disables_only_observation_feedback(self) -> None:
        profile = demo_profile(relevant_paths=[], high_risk_paths=[])
        record = lm_bisect.CommitRecord(
            index=1,
            sha="a" * 40,
            subject="vector loop change",
            body="",
            changed_files=["llvm/lib/Analysis/LoopInfo.cpp"],
            diff_text="",
            semantic_score=1.0,
            build_success_prob=0.92,
            suspicion_weight=0.0,
            evidence=[],
            features=["path:llvm/lib/Analysis", "term:loop"],
        )
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="bad",
                features=["path:llvm/lib/Analysis", "term:loop"],
                evidence=[],
                log_excerpt="",
            )
        ]

        lm_bisect.apply_feedback_bias(
            profile,
            [record],
            observations,
            enabled=not lm_bisect.heuristic_ablation_disables_feedback("feedback"),
        )

        self.assertEqual(record.semantic_score, 1.0)
        self.assertEqual(record.feedback_bias, 0.0)

    def test_heuristic_ablation_history_config_isolation(self) -> None:
        history = lm_bisect.start_run_history_payload(
            issue_id="demo",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="results/obs.json",
            run_history_path="results/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=100,
            heuristic_ablation="keywords",
        )

        matching = lm_bisect.run_history_matches(
            history,
            "demo",
            "heuristic",
            None,
            "topk",
            heuristic_ablation="keywords",
        )
        mismatching = lm_bisect.run_history_matches(
            history,
            "demo",
            "heuristic",
            None,
            "topk",
            heuristic_ablation="relevant-paths",
        )

        self.assertTrue(matching)
        self.assertFalse(mismatching)

    def test_heuristic_ablation_argument_normalization_rejects_non_string_values(self) -> None:
        args = mock.Mock()
        args.heuristic_ablation = mock.Mock()

        self.assertEqual(lm_bisect.heuristic_ablation_from_args(args), "none")

        args.heuristic_ablation = "keywords"
        self.assertEqual(lm_bisect.heuristic_ablation_from_args(args), "keywords")

    def test_oracle_first_bad_keywords_use_first_bad_diff_symbols(self) -> None:
        with mock.patch.object(lm_bisect, "commit_subject", return_value="[Loop] Repair MagicVectorThing"), mock.patch.object(
            lm_bisect, "commit_body", return_value=""
        ), mock.patch.object(
            lm_bisect, "commit_changed_files", return_value=["llvm/lib/Transforms/Scalar/MagicVectorThing.cpp"]
        ), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            return_value="+static void repairMagicVectorThing() {\n+  TightLoopState State;\n+  general prose should not become a keyword;\n+  return;\n+}\n",
        ):
            keywords = lm_bisect.oracle_first_bad_keywords(Path("/tmp/repo"), "a" * 40)

        self.assertIn("MagicVectorThing", keywords)
        self.assertIn("repairMagicVectorThing", keywords)
        self.assertNotIn("return", keywords)
        self.assertNotIn("general", keywords)

    def test_oracle_first_bad_derivation_keeps_specific_changed_line_anchors(self) -> None:
        with mock.patch.object(lm_bisect, "commit_subject", return_value="[Loop] Repair MagicVectorThing"), mock.patch.object(
            lm_bisect, "commit_body", return_value=""
        ), mock.patch.object(
            lm_bisect, "commit_changed_files", return_value=["llvm/lib/Transforms/Scalar/MagicVectorThing.cpp"]
        ), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            return_value=(
                "+++ b/llvm/lib/Transforms/Scalar/MagicVectorThing.cpp\n"
                "+  State.setKnownInvariant(NewInvariant);\n"
                "+  // A comment is not a causal patch anchor.\n"
            ),
        ):
            derivation = lm_bisect.oracle_first_bad_keyword_derivation(Path("/tmp/repo"), "a" * 40)

        self.assertEqual(
            derivation["patch_anchors"],
            ["statesetknowninvariantnewinvariant"],
        )

    def test_oracle_first_bad_profile_replaces_only_authored_keywords(self) -> None:
        profile = demo_profile(
            keywords=["authored-only"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        selection_profile = lm_bisect.heuristic_selection_profile(
            profile,
            "oracle-first-bad",
            oracle_keywords=["MagicVectorThing", "TightLoopState"],
        )

        self.assertEqual(selection_profile.keywords, ["MagicVectorThing", "TightLoopState"])
        self.assertEqual(selection_profile.relevant_paths, profile.relevant_paths)
        self.assertEqual(selection_profile.high_risk_paths, profile.high_risk_paths)

    def test_oracle_major_keyword_score_dominates_nonkeyword_evidence(self) -> None:
        profile = demo_profile(
            keywords=["OracleCulpritSymbol"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )

        major_score, major_evidence = lm_bisect.score_semantics(
            profile,
            subject="Touch OracleCulpritSymbol",
            body="",
            files=[],
            diff="",
            heuristic_version="oracle-first-bad-major",
        )
        nonkeyword_score, _nonkeyword_evidence = lm_bisect.score_semantics(
            profile,
            subject="vector loop codegen target",
            body="",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="",
            heuristic_version="oracle-first-bad-major",
        )

        self.assertGreater(major_score, nonkeyword_score)
        self.assertTrue(any("oracle-major keyword hits" in item for item in major_evidence))

    def test_oracle_major_tuned_keeps_authored_keywords_and_boosts_oracle_terms(self) -> None:
        profile = demo_profile(
            keywords=["IssueAssertion", "OracleCulpritSymbol"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        selection_profile = lm_bisect.heuristic_selection_profile(
            profile,
            "oracle-first-bad-major-tuned",
            oracle_keywords=["OracleCulpritSymbol"],
        )

        self.assertEqual(selection_profile.keywords, profile.keywords)
        self.assertEqual(selection_profile.oracle_keywords, ["OracleCulpritSymbol"])

        tuned_score, tuned_evidence = lm_bisect.score_semantics(
            selection_profile,
            subject="Touch IssueAssertion",
            body="",
            files=[],
            diff="",
            heuristic_version="oracle-first-bad-major-tuned",
        )
        oracle_score, oracle_evidence = lm_bisect.score_semantics(
            selection_profile,
            subject="Touch OracleCulpritSymbol",
            body="",
            files=[],
            diff="",
            heuristic_version="oracle-first-bad-major-tuned",
        )

        self.assertGreater(oracle_score, tuned_score)
        self.assertTrue(any("1 keyword hits" in item for item in tuned_evidence))
        self.assertTrue(any("oracle-major keyword hits" in item for item in oracle_evidence))

    def test_oracle_patch_score_prefers_exact_first_bad_fingerprint(self) -> None:
        profile = demo_profile(
            keywords=["IssueAssertion"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        profile = replace(
            profile,
            oracle_keywords=["OracleCulpritSymbol"],
            oracle_patch_changed_files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            oracle_patch_anchors=["state.setknowninvariant(newinvariant);"],
        )

        exact_score, exact_evidence = lm_bisect.score_semantics(
            profile,
            subject="Refine LoopVectorize",
            body="",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="State.setKnownInvariant(NewInvariant);",
            heuristic_version="oracle-first-bad-major-tuned-patch",
        )
        sibling_score, _sibling_evidence = lm_bisect.score_semantics(
            profile,
            subject="Refine LoopVectorize",
            body="",
            files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            diff="State.setKnownInvariant(OldInvariant);",
            heuristic_version="oracle-first-bad-major-tuned-patch",
        )

        self.assertGreater(exact_score, sibling_score)
        self.assertTrue(any("oracle-patch fingerprint: 1 files, 1 changed lines" in item for item in exact_evidence))

    def test_parser_accepts_oracle_major_keyword_heuristic_with_explicit_sha(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad-major",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad-major")
        self.assertEqual(args.oracle_first_bad_sha, "a" * 40)

    def test_parser_accepts_causal_first_parent_context(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--scorer",
                "model",
                "--model-diff-extraction",
                "causal-llm",
                "--causal-context-parent-count",
                "5",
            ]
        )

        self.assertEqual(args.causal_context_parent_count, 5)

    def test_parser_accepts_combined_oracle_major_tuned_heuristic(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad-major-tuned",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad-major-tuned")
        self.assertEqual(args.oracle_first_bad_sha, "a" * 40)

    def test_refined_oracle_major_tuned_selection_prefers_oracle_semantic_rank(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="midpoint-oriented candidate",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.99,
                suspicion_weight=0.0,
                selection_score=0.99,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="oracle-derived symbol candidate",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=25.0,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                selection_score=0.50,
            ),
        ]

        decision = lm_bisect.oracle_major_semantic_selection(records)

        self.assertEqual(decision.selected.sha, "b" * 40)
        self.assertEqual(decision.selection_mode, "oracle-major-semantic")
        self.assertEqual([record.sha for record in decision.ranked_candidates], ["b" * 40, "a" * 40])

    def test_parser_accepts_refined_combined_oracle_major_tuned_heuristic(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad-major-tuned-semantic",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad-major-tuned-semantic")

    def test_parser_accepts_oracle_patch_fingerprint_heuristic(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad-major-tuned-patch",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad-major-tuned-patch")

    def test_oracle_patch_selection_prefers_fingerprint_score(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="same file sibling",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=101.0,
                build_success_prob=0.99,
                suspicion_weight=0.0,
                selection_score=0.99,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="exact leaked patch",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=201.0,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                selection_score=0.50,
            ),
        ]

        decision = lm_bisect.oracle_patch_semantic_selection(records)

        self.assertEqual(decision.selected.sha, "b" * 40)
        self.assertEqual(decision.selection_mode, "oracle-patch-semantic")

    def test_oracle_patch_parent_proof_requires_one_candidate(self) -> None:
        record = lm_bisect.CommitRecord(
            index=1,
            sha="a" * 40,
            subject="parent proof",
            body="",
            changed_files=[],
            diff_text="",
            semantic_score=1.0,
            build_success_prob=0.95,
            suspicion_weight=0.0,
        )

        decision = lm_bisect.oracle_patch_parent_selection([record])

        self.assertEqual(decision.selected.sha, "a" * 40)
        self.assertEqual(decision.selection_mode, "oracle-patch-parent-proof")
        with self.assertRaisesRegex(ValueError, "exactly one candidate"):
            lm_bisect.oracle_patch_parent_selection([record, record])

    def test_direct_oracle_anchor_selects_known_first_bad_over_higher_scored_commit(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="higher heuristic score",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=100.0,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="known first bad",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.1,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
        ]

        decision = lm_bisect.oracle_direct_anchor_selection(records, "b" * 40)

        self.assertEqual(decision.selected.sha, "b" * 40)
        self.assertEqual(decision.selection_mode, "oracle-direct-anchor")
        self.assertEqual([record.sha for record in decision.ranked_candidates], ["b" * 40])

    def test_direct_oracle_anchor_rejects_sha_outside_interval(self) -> None:
        record = lm_bisect.CommitRecord(
            index=1,
            sha="a" * 40,
            subject="only candidate",
            body="",
            changed_files=[],
            diff_text="",
            semantic_score=1.0,
            build_success_prob=0.95,
            suspicion_weight=0.0,
        )

        with self.assertRaisesRegex(ValueError, "not in the unresolved interval"):
            lm_bisect.oracle_direct_anchor_selection([record], "b" * 40)

    def test_direct_oracle_anchor_validation_rejects_sha_outside_full_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "not in the unresolved interval"):
            lm_bisect.validate_direct_oracle_anchor(["a" * 40], "b" * 40)

    def test_direct_oracle_anchor_validation_requires_resolved_sha(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires a resolved first-bad SHA"):
            lm_bisect.validate_direct_oracle_anchor(["a" * 40], None)

    def test_parser_accepts_direct_combined_oracle_anchor(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad-major-tuned-anchor",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad-major-tuned-anchor")


class AdaptiveTopKTests(unittest.TestCase):
    def test_adaptive_top_k_uses_large_frontier_above_threshold(self) -> None:
        config = lm_bisect.AdaptiveTopKConfig(threshold=5_000, large_k=12, small_k=3)

        self.assertEqual(lm_bisect.effective_model_top_k(5_001, 3, config), 12)

    def test_adaptive_top_k_uses_small_frontier_at_threshold(self) -> None:
        config = lm_bisect.AdaptiveTopKConfig(threshold=5_000, large_k=12, small_k=3)

        self.assertEqual(lm_bisect.effective_model_top_k(5_000, 12, config), 3)
        self.assertEqual(lm_bisect.effective_model_top_k(2, 12, config), 2)

    def test_parser_accepts_complete_adaptive_schedule(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--adaptive-top-k-threshold",
                "5000",
                "--adaptive-top-k-large",
                "12",
                "--adaptive-top-k-small",
                "3",
                "--model-cache-namespace",
                "adaptive-5000-12-3",
            ]
        )

        self.assertEqual(
            lm_bisect.adaptive_top_k_config_from_args(args),
            lm_bisect.AdaptiveTopKConfig(threshold=5_000, large_k=12, small_k=3),
        )
        self.assertEqual(args.model_cache_namespace, "adaptive-5000-12-3")

    def test_adaptive_schedule_requires_all_three_arguments(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            ["run-online", "--issue", "demo", "--adaptive-top-k-threshold", "5000"]
        )

        with self.assertRaises(ValueError):
            lm_bisect.adaptive_top_k_config_from_args(args)

    def test_adaptive_cache_namespace_and_score_context_are_isolated(self) -> None:
        profile = demo_profile()
        namespace = lm_bisect.resolved_model_cache_namespace(
            None,
            lm_bisect.AdaptiveTopKConfig(threshold=5_000, large_k=12, small_k=3),
        )
        context = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40],
            [],
            12,
            "topk",
            "parent",
            "llm",
            "trace-only",
        )
        other_context = dict(context, model_top_k=3)
        cache_path = lm_bisect.model_cache_path(profile.issue_id, "gpt-5.4-mini", namespace=namespace)

        self.assertIn("ns-adaptive-5000-12-3", str(cache_path))
        self.assertNotEqual(
            lm_bisect.model_score_cache_key("a" * 40, diff_extraction="llm", score_context=lm_bisect.model_score_context_id(context)),
            lm_bisect.model_score_cache_key("a" * 40, diff_extraction="llm", score_context=lm_bisect.model_score_context_id(other_context)),
        )

    def test_adaptive_schedule_prevents_history_resume_with_fixed_k_history(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="demo",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="/tmp/observations.json",
            run_history_path="/tmp/run-history.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=10_000,
            model_top_k=3,
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="demo",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="/tmp/observations.json",
            run_history_path="/tmp/run-history.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=10_000,
            candidate_file=None,
            model_top_k=3,
            adaptive_top_k={"threshold": 5_000, "large_k": 12, "small_k": 3},
            model_cache_namespace="adaptive-5000-12-3",
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["adaptive_top_k"]["large_k"], 12)


class ConfidenceAdaptiveFrontierTests(unittest.TestCase):
    def test_high_confidence_uses_semantic_topk_frontier(self) -> None:
        records = [
            lm_bisect.CommitRecord(index=1, sha="1" * 40, subject="strong", body="", changed_files=[], diff_text="", semantic_score=5.0, build_success_prob=0.9, suspicion_weight=0.0),
            lm_bisect.CommitRecord(index=2, sha="2" * 40, subject="runner up", body="", changed_files=[], diff_text="", semantic_score=2.0, build_success_prob=0.9, suspicion_weight=0.0),
            lm_bisect.CommitRecord(index=3, sha="3" * 40, subject="third", body="", changed_files=[], diff_text="", semantic_score=1.0, build_success_prob=0.9, suspicion_weight=0.0),
            lm_bisect.CommitRecord(index=4, sha="4" * 40, subject="fourth", body="", changed_files=[], diff_text="", semantic_score=0.5, build_success_prob=0.9, suspicion_weight=0.0),
        ]
        config = lm_bisect.ConfidenceAdaptiveFrontierConfig(threshold=0.35)

        decision = lm_bisect.resolve_model_frontier(
            demo_profile(), records, [], target_count=3, configured_frontier="topk", confidence_config=config
        )

        self.assertEqual(decision.effective_frontier, "topk")
        self.assertGreater(decision.confidence, config.threshold)
        self.assertEqual(decision.selected_shas, ["1" * 40, "2" * 40, "3" * 40])

    def test_low_confidence_uses_diverse_frontier(self) -> None:
        records = [
            lm_bisect.CommitRecord(index=index, sha=str(index) * 40, subject=f"candidate {index}", body="", changed_files=[], diff_text="", semantic_score=1.0, build_success_prob=0.9, suspicion_weight=0.0)
            for index in range(1, 7)
        ]
        config = lm_bisect.ConfidenceAdaptiveFrontierConfig(threshold=0.35)

        decision = lm_bisect.resolve_model_frontier(
            demo_profile(), records, [], target_count=3, configured_frontier="topk", confidence_config=config
        )

        self.assertEqual(decision.effective_frontier, "diverse")
        self.assertEqual(decision.confidence, 0.0)
        self.assertEqual(decision.selected_shas, lm_bisect.select_model_frontier_shas(records, 3, "diverse"))

    def test_observation_feedback_changes_the_low_confidence_frontier(self) -> None:
        records = [
            lm_bisect.CommitRecord(index=1, sha="1" * 40, subject="first", body="", changed_files=[], diff_text="", semantic_score=5.0, build_success_prob=0.9, suspicion_weight=0.0, features=["other"]),
            lm_bisect.CommitRecord(index=2, sha="2" * 40, subject="observed mechanism", body="", changed_files=[], diff_text="", semantic_score=4.9, build_success_prob=0.9, suspicion_weight=0.0, features=["mechanism"]),
            lm_bisect.CommitRecord(index=3, sha="3" * 40, subject="third", body="", changed_files=[], diff_text="", semantic_score=1.0, build_success_prob=0.9, suspicion_weight=0.0, features=["third"]),
            lm_bisect.CommitRecord(index=4, sha="4" * 40, subject="fourth", body="", changed_files=[], diff_text="", semantic_score=0.5, build_success_prob=0.9, suspicion_weight=0.0, features=["fourth"]),
        ]
        observation = lm_bisect.CommitObservation(
            sha="observed" * 5,
            verdict="bad",
            summary="matching mechanism",
            features=["mechanism"],
        )
        config = lm_bisect.ConfidenceAdaptiveFrontierConfig(threshold=0.35)

        without_feedback = lm_bisect.resolve_model_frontier(
            demo_profile(), records, [], target_count=3, configured_frontier="topk", confidence_config=config
        )
        with_feedback = lm_bisect.resolve_model_frontier(
            demo_profile(), records, [observation], target_count=3, configured_frontier="topk", confidence_config=config
        )

        self.assertEqual(without_feedback.effective_frontier, "diverse")
        self.assertEqual(with_feedback.effective_frontier, "topk")
        self.assertEqual(with_feedback.selected_shas[0], "2" * 40)

    def test_confidence_frontier_context_and_history_are_isolated(self) -> None:
        config = lm_bisect.ConfidenceAdaptiveFrontierConfig(threshold=0.35)
        payload = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40], [], 3, "topk", "parent", "llm", "trace-only", config.payload()
        )
        without_policy = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40], [], 3, "topk", "parent", "llm", "trace-only"
        )
        history = lm_bisect.start_run_history_payload(
            issue_id="demo", scorer="model", model_name="gpt-5.4-mini", model_frontier="topk",
            search_policy="calibrated-posterior", hybrid_switch_window=32, lambda_weight=2.0,
            max_steps=30, observation_path="/tmp/observations.json", run_history_path="/tmp/history.json",
            good_commit="g" * 40, bad_commit="b" * 40, initial_unresolved=10,
            model_top_k=3, confidence_adaptive_frontier=config.payload(),
        )

        self.assertNotEqual(lm_bisect.model_score_context_id(payload), lm_bisect.model_score_context_id(without_policy))
        self.assertEqual(history["confidence_adaptive_frontier"], config.payload())

    def test_confidence_policy_rejects_non_topk_base_frontier(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --model-frontier topk"):
            lm_bisect.validate_confidence_adaptive_frontier(
                "diverse", lm_bisect.ConfidenceAdaptiveFrontierConfig(threshold=0.35)
            )


class ObservationConditionedPosteriorTests(unittest.TestCase):
    def _record(self, index: int, sha: str, features: list[str]) -> lm_bisect.CommitRecord:
        path_feature = next((item[5:] for item in features if item.startswith("path:")), "llvm/lib/Support")
        return lm_bisect.CommitRecord(
            index=index,
            sha=sha * 40,
            subject=f"candidate {sha}",
            body="",
            changed_files=[f"{path_feature}/Candidate.cpp"],
            diff_text="",
            semantic_score=2.0,
            build_success_prob=0.9,
            suspicion_weight=0.0,
            evidence=["model-scored"],
            features=features,
        )

    def test_bad_observation_increases_related_candidate_posterior(self) -> None:
        records = [
            self._record(1, "a", ["path:llvm/lib/Transforms/Vectorize", "term:vplan"]),
            self._record(2, "b", ["path:llvm/lib/Analysis", "term:memoryssa"]),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="o" * 40,
                verdict="bad",
                summary="VPlan assertion",
                features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
            )
        ]

        probabilities = lm_bisect.observation_conditioned_posterior_probabilities(
            records,
            observations,
            lm_bisect.ObservationConditionedPosteriorConfig(),
        )

        self.assertGreater(probabilities[0], probabilities[1])
        self.assertGreater(records[0].observation_bad_similarity, 0.0)
        self.assertEqual(records[1].observation_bad_similarity, 0.0)

    def test_good_observation_suppresses_related_candidate_posterior(self) -> None:
        records = [
            self._record(1, "a", ["path:llvm/lib/Transforms/Vectorize", "term:vplan"]),
            self._record(2, "b", ["path:llvm/lib/Analysis", "term:memoryssa"]),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="o" * 40,
                verdict="good",
                summary="VPlan path is known good",
                features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
            )
        ]

        probabilities = lm_bisect.observation_conditioned_posterior_probabilities(
            records,
            observations,
            lm_bisect.ObservationConditionedPosteriorConfig(),
        )

        self.assertLess(probabilities[0], probabilities[1])
        self.assertLess(records[0].observation_posterior_evidence, 0.0)

    def test_unrelated_candidate_receives_no_positive_observation_evidence(self) -> None:
        record = self._record(1, "a", ["path:clang/lib/Sema", "term:openacc"])
        observation = lm_bisect.CommitObservation(
            sha="o" * 40,
            verdict="bad",
            summary="VPlan assertion",
            features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
        )

        lm_bisect.observation_conditioned_posterior_probabilities(
            [record],
            [observation],
            lm_bisect.ObservationConditionedPosteriorConfig(),
        )

        self.assertEqual(record.observation_bad_similarity, 0.0)
        self.assertEqual(record.observation_posterior_evidence, 0.0)

    def test_model_mechanism_feature_without_prefix_is_retained(self) -> None:
        record = self._record(1, "a", ["VPlan", "path:llvm/lib/Transforms/Vectorize"])
        observation = lm_bisect.CommitObservation(
            sha="o" * 40,
            verdict="bad",
            summary="VPlan assertion",
            features=["VPlan", "path:llvm/lib/Transforms/Vectorize"],
        )

        lm_bisect.observation_conditioned_posterior_probabilities(
            [record],
            [observation],
            lm_bisect.ObservationConditionedPosteriorConfig(),
        )

        self.assertGreater(record.observation_bad_similarity, 0.0)

    def test_policy_configuration_isolates_score_context_and_history(self) -> None:
        config = lm_bisect.ObservationConditionedPosteriorConfig()
        with_policy = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40],
            [],
            3,
            "topk",
            "parent",
            "llm",
            "trace-only",
            observation_conditioned_posterior=config.payload(),
        )
        without_policy = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40], [], 3, "topk", "parent", "llm", "trace-only"
        )
        history = lm_bisect.start_run_history_payload(
            issue_id="demo",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="/tmp/observations.json",
            run_history_path="/tmp/history.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=10,
            model_top_k=3,
            observation_conditioned_posterior=config.payload(),
        )

        self.assertNotEqual(
            lm_bisect.model_score_context_id(with_policy),
            lm_bisect.model_score_context_id(without_policy),
        )
        self.assertEqual(history["observation_conditioned_posterior"], config.payload())

    def test_policy_rejects_adaptive_or_confidence_combinations(self) -> None:
        config = lm_bisect.ObservationConditionedPosteriorConfig()

        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            lm_bisect.validate_observation_conditioned_posterior(
                scorer="model",
                search_policy="calibrated-posterior",
                configured_frontier="topk",
                model_top_k=3,
                model_diff_mode="parent",
                model_diff_extraction="llm",
                posterior_config=config,
                adaptive_config=lm_bisect.AdaptiveTopKConfig(5_000, 12, 3),
                confidence_config=None,
            )
        lm_bisect.validate_observation_conditioned_posterior(
            scorer="model",
            search_policy="calibrated-posterior",
            configured_frontier="topk",
            model_top_k=12,
            model_diff_mode="parent",
            model_diff_extraction="llm",
            posterior_config=config,
            adaptive_config=None,
            confidence_config=None,
        )

    def test_parser_accepts_general_keyword_heuristic_version(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            ["suggest", "--issue", "demo", "--heuristic-version", "general"]
        )

        self.assertEqual(args.heuristic_version, "general")

    def test_parser_accepts_no_keyword_heuristic_version(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            ["suggest", "--issue", "demo", "--heuristic-version", "none"]
        )

        self.assertEqual(args.heuristic_version, "none")

    def test_parser_accepts_neutral_heuristic_version(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            ["suggest", "--issue", "demo", "--heuristic-version", "neutral"]
        )

        self.assertEqual(args.heuristic_version, "neutral")

    def test_parser_accepts_oracle_first_bad_heuristic_with_explicit_sha(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--heuristic-version",
                "oracle-first-bad",
                "--oracle-first-bad-sha",
                "a" * 40,
            ]
        )

        self.assertEqual(args.heuristic_version, "oracle-first-bad")
        self.assertEqual(args.oracle_first_bad_sha, "a" * 40)

    def test_oracle_first_bad_history_does_not_resume_with_different_source(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="demo",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="/tmp/observations.json",
            run_history_path="/tmp/run-history.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=10_000,
            heuristic_version="oracle-first-bad",
            oracle_first_bad_sha="a" * 40,
        )

        _history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="demo",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=30,
            observation_path="/tmp/observations.json",
            run_history_path="/tmp/run-history.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=10_000,
            candidate_file=None,
            heuristic_version="oracle-first-bad",
            oracle_first_bad_sha="b" * 40,
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)

    def test_build_probability_drops_for_build_system_touch(self) -> None:
        score, evidence = lm_bisect.score_build_probability(
            subject="[clang] update build workflow",
            body="touch cmake and ci scripts",
            files=["llvm/CMakeLists.txt", ".github/workflows/premerge.yml"],
            diff="cmake workflow build ci",
        )

        self.assertLess(score, 0.92)
        self.assertTrue(evidence)


class ProfileTests(unittest.TestCase):
    def test_profiles_load(self) -> None:
        profiles = lm_bisect.load_profiles()
        self.assertIn("pr165445", profiles)
        self.assertIn("pr54556", profiles)
        self.assertIn("pr176682", profiles)
        self.assertIn("pr187875", profiles)
        self.assertIn("pr191581", profiles)
        self.assertIn("pr172195", profiles)
        self.assertIn("pr170421", profiles)
        self.assertIn("pr193932", profiles)
        self.assertIn("pr196244", profiles)
        self.assertIn("pr65982", profiles)
        self.assertIn("pr121365", profiles)
        self.assertIn("pr193164", profiles)
        self.assertIn("pr168912", profiles)
        self.assertIn("pr205971", profiles)
        self.assertIn("pr206007", profiles)
        self.assertIn("pr165246", profiles)
        self.assertIn("pr167514", profiles)
        self.assertIn("pr200648", profiles)
        self.assertIn("pr204561", profiles)
        self.assertEqual(profiles["pr121365"].runner, "scripts/pr121365/bisect-runner.sh")
        self.assertEqual(profiles["pr193164"].runner, "scripts/pr193164/bisect-runner.sh")
        self.assertEqual(profiles["pr168912"].runner, "scripts/pr168912/bisect-runner.sh")
        self.assertEqual(profiles["pr205971"].runner, "scripts/pr205971/bisect-runner.sh")
        self.assertEqual(profiles["pr206007"].runner, "scripts/pr206007/bisect-runner.sh")
        self.assertEqual(profiles["pr165246"].runner, "scripts/pr165246/bisect-runner.sh")
        self.assertEqual(profiles["pr167514"].runner, "scripts/pr167514/bisect-runner.sh")
        self.assertEqual(profiles["pr200648"].runner, "scripts/pr200648/bisect-runner.sh")
        self.assertEqual(profiles["pr204561"].runner, "scripts/pr204561/bisect-runner.sh")


class FeedbackTests(unittest.TestCase):
    def test_bad_observation_boosts_similar_commit(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["licm", "loop"],
            relevant_paths=["llvm/lib/Transforms/Scalar"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="LICM hoist writeonly calls",
                body="loop optimization",
                changed_files=["llvm/lib/Transforms/Scalar/LICM.cpp"],
                diff_text="licm hoist writeonly loop",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="unrelated doc update",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
                diff_text="docs note",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
            ),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="c" * 40,
                verdict="bad",
                summary="LICM-style bad commit",
                features=[
                    "path:llvm/lib/Transforms/Scalar",
                    "term:licm",
                    "term:hoist",
                    "term:loop",
                ],
                evidence=[],
                log_excerpt="",
            )
        ]

        lm_bisect.apply_feedback_bias(profile, records, observations)
        self.assertGreater(records[0].semantic_score, records[1].semantic_score)
        self.assertGreater(records[0].feedback_bias, records[1].feedback_bias)

    def test_good_observation_suppresses_similar_commit(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["vector", "selectiondag", "x86"],
            relevant_paths=["llvm/lib/CodeGen/SelectionDAG"],
            high_risk_paths=["llvm/lib/CodeGen"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="vector lowering change",
                body="selectiondag x86",
                changed_files=["llvm/lib/CodeGen/SelectionDAG/DAGCombiner.cpp"],
                diff_text="vector selectiondag x86",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
            )
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="d" * 40,
                verdict="good",
                summary="similar commit already known good",
                features=[
                    "path:llvm/lib/CodeGen/SelectionDAG",
                    "term:vector",
                    "term:x86",
                ],
                evidence=[],
                log_excerpt="",
            )
        ]

        original_score = records[0].semantic_score
        lm_bisect.apply_feedback_bias(profile, records, observations)
        self.assertLess(records[0].semantic_score, original_score)
        self.assertLess(records[0].feedback_bias, 1.0)

    def test_bad_observation_gate_caps_irrelevant_similarity_boost(self) -> None:
        profile = lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="https://example.invalid",
            title="Demo",
            good_commit="g" * 40,
            good_ref="llvmorg-demo",
            bad_commit="b" * 40,
            bisect_log="results/demo.log",
            runner="scripts/demo.sh",
            bug_report_summary="demo",
            keywords=["vplan", "vectorize"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="unrelated cuda header sync",
                body="",
                changed_files=["clang/lib/Headers/cuda_wrappers/new"],
                diff_text="cuda wrapper sync",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
                features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="vectorizer change",
                body="blockfrequencyinfo vplan",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/HLP.cpp"],
                diff_text="vectorize vplan blockfrequencyinfo",
                semantic_score=2.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
                features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
            ),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="c" * 40,
                verdict="bad",
                summary="similar bad vectorizer commit",
                features=["path:llvm/lib/Transforms/Vectorize", "term:vplan"],
            )
        ]

        lm_bisect.apply_feedback_bias(profile, records, observations)
        self.assertLessEqual(records[0].feedback_bias, 1.10)
        self.assertGreater(records[1].feedback_bias, records[0].feedback_bias)

    def test_skip_observation_penalizes_build_probability_not_semantic_score(self) -> None:
        profile = demo_profile(
            keywords=["tokencollector", "clangd"],
            relevant_paths=["clang-tools-extra/clangd"],
            high_risk_paths=["clang-tools-extra/clangd"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="clangd shutdown build fix",
                body="",
                changed_files=["clang-tools-extra/clangd/Shutdown.cpp"],
                diff_text="std abort shutdown",
                semantic_score=2.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
                features=[
                    "path:clang-tools-extra/clangd",
                    "path:clang-tools-extra/clangd/Shutdown.cpp",
                    "term:abort",
                ],
            )
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="s" * 40,
                verdict="skip",
                summary="build failed; skipping commit",
                features=[
                    "path:clang-tools-extra/clangd",
                    "path:clang-tools-extra/clangd/Shutdown.cpp",
                    "term:abort",
                ],
                source="runner",
                trace_excerpt="../clang-tools-extra/clangd/Shutdown.cpp:21:10: error: 'abort' is not a member of 'std'",
            )
        ]

        original_semantic = records[0].semantic_score
        original_build_prob = records[0].build_success_prob

        lm_bisect.apply_feedback_bias(profile, records, observations)

        self.assertEqual(records[0].semantic_score, original_semantic)
        self.assertLess(records[0].build_success_prob, original_build_prob)
        self.assertIn("skip-build-risk", " ".join(records[0].evidence or []))


class RankingHelperTests(unittest.TestCase):
    def test_rank_of_commit_uses_utility_order(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="a",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.8,
                suspicion_weight=0.2,
                utility=0.3,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="b",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.8,
                suspicion_weight=0.2,
                utility=0.9,
            ),
        ]

        rank_result = lm_bisect.rank_of_commit(records, "a" * 40)
        self.assertIsNotNone(rank_result)
        rank, _record = rank_result
        self.assertEqual(rank, 2)

    def test_select_next_commit_hybrid_switches_to_boundary_mode(self) -> None:
        profile = demo_profile()
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="a",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.2,
                utility=0.1,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="b",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.2,
                utility=0.9,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="c",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.2,
                utility=0.2,
            ),
        ]

        decision = lm_bisect.select_next_commit(
            profile,
            records,
            lambda_weight=2.0,
            search_policy="hybrid",
            hybrid_switch_window=3,
        )
        self.assertEqual(decision.selection_mode, "boundary")
        self.assertEqual(decision.selected.index, 2)

    def test_select_next_commit_ranked_keeps_existing_behavior(self) -> None:
        profile = demo_profile()
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="a",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.2,
                utility=0.1,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="b",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.2,
                utility=0.9,
            ),
        ]

        decision = lm_bisect.select_next_commit(profile, records, lambda_weight=2.0, search_policy="ranked")
        self.assertEqual(decision.selection_mode, "ranked")
        self.assertIn(decision.selected.index, {1, 2})

    def test_select_next_commit_posterior_prefers_posterior_split(self) -> None:
        profile = demo_profile()
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="a",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.45,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="b",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.05,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="c",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.05,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="4" * 40,
                subject="d",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.45,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
        ]

        decision = lm_bisect.select_next_commit(profile, records, lambda_weight=2.0, search_policy="posterior")
        self.assertEqual(decision.selection_mode, "posterior")
        self.assertEqual(decision.selected.index, 2)
        self.assertGreater(decision.selected.posterior_info_gain, 0.0)

    def test_binary_split_info_gain_matches_bruteforce_entropy_delta(self) -> None:
        probabilities = [0.10, 0.20, 0.05, 0.35, 0.30]
        current_entropy = lm_bisect.shannon_entropy(probabilities)
        cumulative = 0.0

        for idx, probability in enumerate(probabilities):
            cumulative += probability
            left = probabilities[: idx + 1]
            right = probabilities[idx + 1 :]
            p_bad = cumulative
            p_good = 1.0 - p_bad
            bad_entropy = lm_bisect.shannon_entropy((value / p_bad for value in left)) if p_bad > 0.0 else 0.0
            good_entropy = (
                lm_bisect.shannon_entropy((value / p_good for value in right)) if p_good > 0.0 else 0.0
            )
            brute_force = current_entropy - ((p_bad * bad_entropy) + (p_good * good_entropy))

            self.assertAlmostEqual(lm_bisect.binary_split_info_gain(cumulative), brute_force)

    def test_calibrated_prior_probabilities_sharpen_high_score_mass(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="low",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="high",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=4.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
        ]

        probabilities = lm_bisect.calibrated_prior_probabilities(records, prior_power=1.5)

        self.assertEqual(len(probabilities), 2)
        self.assertGreater(probabilities[1], 0.8)
        self.assertLess(probabilities[0], 0.2)

    def test_calibrated_prior_probabilities_use_stronger_softmax_for_model_scored_window(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="low",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=2.7,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="high",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=2.9,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
        ]

        probabilities = lm_bisect.calibrated_prior_probabilities(records, prior_power=1.35)

        self.assertGreater(probabilities[1], 0.6)

    def test_calibrated_posterior_prefers_relevant_commit_over_irrelevant_midpoint(self) -> None:
        profile = demo_profile(
            keywords=["vplan", "vectorize"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="early docs",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
                diff_text="docs",
                semantic_score=0.10,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="vectorizer update",
                body="vplan blockfrequencyinfo",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/HLP.cpp"],
                diff_text="vectorize vplan blockfrequencyinfo",
                semantic_score=0.78,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="midpoint cleanup",
                body="refactor",
                changed_files=["clang/docs/UsersManual.rst"],
                diff_text="cleanup",
                semantic_score=0.79,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="4" * 40,
                subject="late docs",
                body="",
                changed_files=["llvm/docs/CommandGuide/opt.rst"],
                diff_text="docs",
                semantic_score=0.08,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            ),
        ]

        decision = lm_bisect.select_next_commit(
            profile,
            records,
            lambda_weight=2.0,
            search_policy="calibrated-posterior",
            calibrated_prior_power=1.2,
            calibrated_prior_bonus=1.0,
            weak_relevance_penalty=0.15,
            weak_relevance_threshold=0.8,
        )

        self.assertEqual(decision.selection_mode, "calibrated-posterior")
        self.assertEqual(decision.selected.index, 2)
        self.assertGreater(decision.selected.calibrated_posterior_info_gain, 0.0)
        self.assertEqual(records[2].weak_relevance_penalty, 0.15 * records[2].build_success_prob)

    def test_calibrated_posterior_uses_model_rank_bias_on_near_tie(self) -> None:
        profile = demo_profile(
            keywords=["licm", "writeonly", "hoist"],
            relevant_paths=["llvm/lib/Transforms/Scalar"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="docs early",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
                diff_text="docs",
                semantic_score=0.6,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="midpoint cleanup",
                body="refactor",
                changed_files=["llvm/lib/Transforms/Scalar/Utils.cpp"],
                diff_text="cleanup",
                semantic_score=2.7,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="LICM hoist writeonly calls",
                body="alias checks hoisting",
                changed_files=["llvm/lib/Transforms/Scalar/LICM.cpp"],
                diff_text="licm writeonly hoist alias",
                semantic_score=2.9,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="4" * 40,
                subject="docs late",
                body="",
                changed_files=["llvm/docs/CommandGuide/opt.rst"],
                diff_text="docs",
                semantic_score=0.5,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
        ]

        decision = lm_bisect.select_next_commit(
            profile,
            records,
            lambda_weight=2.0,
            search_policy="calibrated-posterior",
            calibrated_prior_power=1.35,
            calibrated_prior_bonus=1.5,
            weak_relevance_penalty=0.05,
            weak_relevance_threshold=0.8,
        )

        self.assertEqual(decision.selected.index, 3)

    def test_calibrated_posterior_does_not_override_to_terminal_bad_end(self) -> None:
        profile = demo_profile(
            keywords=["vplan", "blockfrequencyinfo"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="uninvolved cleanup",
                body="",
                changed_files=["llvm/lib/Target/X86/X86ISelLowering.cpp"],
                diff_text="cleanup",
                semantic_score=0.2,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="good boundary probe",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/VPRecipeBuilder.h"],
                diff_text="probe",
                semantic_score=0.2,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="VPlan BlockFrequencyInfo regression",
                body="vectorize blockfrequencyinfo",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/VPlanRecipes.cpp"],
                diff_text="vplan blockfrequencyinfo",
                semantic_score=4.8,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
            ),
        ]

        decision = lm_bisect.select_next_commit(
            profile,
            records,
            lambda_weight=2.0,
            search_policy="calibrated-posterior",
            calibrated_prior_power=1.35,
            calibrated_prior_bonus=1.5,
            weak_relevance_penalty=0.05,
            weak_relevance_threshold=0.8,
        )

        self.assertNotEqual(decision.selected.index, 3)

    def test_calibrated_posterior_prefers_distinct_issue_mechanism_features(self) -> None:
        profile = demo_profile(
            keywords=[
                "crash",
                "pgo",
                "profile",
                "loop-vectorize",
                "loopvectorizationcostmodel",
                "expectedcost",
                "vplan",
                "blockfrequencyinfo",
                "costmodel",
                "vectorize",
            ],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="docs early",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
                diff_text="docs",
                semantic_score=0.2,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
                features=["docs"],
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="generic VPlan update",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/VPRecipeBuilder.h"],
                diff_text="vplan",
                semantic_score=2.8,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
                features=["vplan", "loop-vectorize", "profile-sensitive-vectorization"],
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="BFI cost divisor change",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/VPlanRecipes.cpp"],
                diff_text="bfi",
                semantic_score=2.7,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
                features=["loop-vectorize", "vplan", "blockfrequencyinfo", "costmodel", "pgo"],
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="4" * 40,
                subject="docs late",
                body="",
                changed_files=["llvm/docs/CommandGuide/opt.rst"],
                diff_text="docs",
                semantic_score=0.1,
                build_success_prob=0.95,
                suspicion_weight=0.0,
                evidence=["model-scored"],
                features=["docs"],
            ),
        ]

        decision = lm_bisect.select_next_commit(
            profile,
            records,
            lambda_weight=2.0,
            search_policy="calibrated-posterior",
            calibrated_prior_power=1.35,
            calibrated_prior_bonus=1.5,
            weak_relevance_penalty=0.05,
            weak_relevance_threshold=0.8,
        )

        self.assertEqual(decision.selected.index, 3)

    def test_select_model_frontier_shas_diverse_includes_structural_anchors(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=index,
                sha=str(index) * 40,
                subject=f"c{index}",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=score,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            )
            for index, score in [
                (1, 10.0),
                (2, 0.1),
                (3, 0.1),
                (4, 0.1),
                (5, 0.1),
                (6, 8.0),
            ]
        ]

        shas = lm_bisect.select_model_frontier_shas(records, target_count=3, frontier_mode="diverse")
        self.assertEqual(len(shas), 3)
        self.assertIn("1" * 40, shas)
        self.assertIn("3" * 40, shas)
        self.assertIn("6" * 40, shas)

    def test_evidence_diverse_frontier_uses_semantic_midpoint_and_component_roles(self) -> None:
        profile = demo_profile(
            relevant_paths=[
                "llvm/lib/Transforms/Vectorize",
                "llvm/lib/Analysis/MemorySSA",
            ],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="1" * 40,
                subject="semantic leader",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                diff_text="",
                semantic_score=9.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="2" * 40,
                subject="midpoint",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="3" * 40,
                subject="independent component",
                body="",
                changed_files=["llvm/lib/Analysis/MemorySSA/MemorySSA.cpp"],
                diff_text="",
                semantic_score=4.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
            lm_bisect.CommitRecord(
                index=4,
                sha="4" * 40,
                subject="unrelated",
                body="",
                changed_files=["clang/lib/Driver/Driver.cpp"],
                diff_text="",
                semantic_score=3.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            ),
        ]

        decision = lm_bisect.resolve_model_frontier(
            profile,
            records,
            [],
            target_count=3,
            configured_frontier="evidence-diverse",
        )

        self.assertEqual(decision.effective_frontier, "evidence-diverse")
        self.assertEqual(decision.selected_shas[0], "1" * 40)
        self.assertIn("2" * 40, decision.selected_shas)
        self.assertIn("3" * 40, decision.selected_shas)
        self.assertEqual(
            [item["role"] for item in decision.role_assignments],
            ["semantic-leader", "posterior-midpoint", "relevant-component"],
        )
        self.assertEqual(decision.role_assignments[2]["component"], "llvm/lib/Analysis/MemorySSA")

    def test_evidence_diverse_frontier_fills_requested_k_without_duplicates(self) -> None:
        profile = demo_profile(relevant_paths=["llvm/lib/Transforms/Vectorize"])
        records = [
            lm_bisect.CommitRecord(
                index=index,
                sha=str(index) * 40,
                subject=f"candidate {index}",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                diff_text="",
                semantic_score=float(20 - index),
                build_success_prob=0.9,
                suspicion_weight=0.0,
            )
            for index in range(1, 15)
        ]

        decision = lm_bisect.resolve_model_frontier(
            profile,
            records,
            [],
            target_count=12,
            configured_frontier="evidence-diverse",
        )

        self.assertEqual(len(decision.selected_shas), 12)
        self.assertEqual(len(set(decision.selected_shas)), 12)
        self.assertEqual(decision.role_assignments[2]["role"], "component-fallback")

    def test_evidence_diverse_frontier_changes_score_context(self) -> None:
        evidence_payload = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40],
            [],
            12,
            "evidence-diverse",
            "parent",
            "llm",
            "trace-only",
        )
        topk_payload = lm_bisect.model_score_context_payload(
            ["a" * 40, "b" * 40],
            [],
            12,
            "topk",
            "parent",
            "llm",
            "trace-only",
        )

        self.assertNotEqual(
            lm_bisect.model_score_context_id(evidence_payload),
            lm_bisect.model_score_context_id(topk_payload),
        )

    def test_select_model_frontier_shas_all_returns_full_window(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=index,
                sha=str(index) * 40,
                subject=f"c{index}",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            )
            for index in range(1, 5)
        ]

        shas = lm_bisect.select_model_frontier_shas(records, target_count=2, frontier_mode="all")
        self.assertEqual(shas, [record.sha for record in records])


class ModelPromptTests(unittest.TestCase):
    def test_deterministic_facts_prompt_uses_ordinal_schema(self) -> None:
        profile = demo_profile()
        item = {
            "sha": "a" * 40,
            "subject": "[LoopUnswitch] Update MemorySSA",
            "body": "",
            "files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            "causal_retrieval": {
                "crash_signals": {
                    "kind": "assertion",
                    "assertion": "MemorySSA dominance invariant",
                    "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                    "symbols": ["MemorySSAUpdater::applyUpdates"],
                    "pass_tokens": ["simple-loop-unswitch"],
                    "query_terms": ["MemorySSAUpdater"],
                },
                "repository_facts": {
                    "rare_checker": {
                        "path": "llvm/lib/Analysis/MemorySSA.cpp",
                        "touch_count": 6,
                        "polarity": "checker-penalty",
                    },
                    "contact_paths": [
                        "unswitchTrivialBranch -> MSSAU->applyUpdates"
                    ],
                    "no_call_path_found": False,
                },
                "selected_hunks": [
                    {
                        "path": "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
                        "header": "@@ static bool unswitchTrivialBranch",
                        "patch": "+ MSSAU->applyUpdates(Updates);",
                        "match_reasons": ["crash-query-term"],
                    }
                ],
                "contract_contexts": [
                    {
                        "path": "llvm/lib/Analysis/MemorySSA.cpp",
                        "symbol": "verifyOptResult",
                        "context": "assert(MSSA.dominates(A, B));",
                        "source_sha": "b" * 40,
                    }
                ],
            },
        }

        prompt = lm_bisect.build_deterministic_facts_ranking_prompt(profile, [item])

        self.assertIn("Structured crash evidence", prompt)
        self.assertIn("Repository facts", prompt)
        self.assertIn("unswitchTrivialBranch -> MSSAU->applyUpdates", prompt)
        self.assertIn('"rank"', prompt)
        self.assertIn('"mechanism"', prompt)
        self.assertIn('"explains_failure"', prompt)
        self.assertNotIn('"semantic_score"', prompt)
        self.assertNotIn('"build_success_prob"', prompt)

    def test_deterministic_facts_normalizes_ordinal_judgment(self) -> None:
        item = {
            "sha": "a" * 40,
            "causal_retrieval": {
                "repository_facts": {
                    "contact_paths": ["caller -> API->applyUpdates"],
                    "no_call_path_found": False,
                }
            },
        }
        judgment = lm_bisect.normalize_deterministic_facts_judgment(
            item,
            {
                "sha": "a" * 40,
                "rank": 1,
                "mechanism": "invariant-break",
                "explains_failure": True,
                "confidence": 0.8,
                "evidence": ["changed API call"],
            },
            candidate_count=3,
        )

        self.assertEqual(judgment["rank"], 1)
        self.assertEqual(judgment["mechanism"], "invariant-break")
        self.assertTrue(judgment["explains_failure"])
        self.assertEqual(judgment["confidence"], 0.8)
        calibrated = lm_bisect.deterministic_facts_model_result(item, judgment)
        self.assertGreater(calibrated["semantic_score"], 0.1)
        self.assertEqual(calibrated["build_success_prob"], 1.0)
        self.assertIn("ordinal-rank:1", calibrated["evidence"])

    def test_deterministic_facts_does_not_treat_false_string_as_failure_explanation(self) -> None:
        judgment = lm_bisect.normalize_deterministic_facts_judgment(
            {"sha": "a" * 40, "causal_retrieval": {}},
            {
                "sha": "a" * 40,
                "rank": 1,
                "mechanism": "unrelated",
                "explains_failure": "false",
                "confidence": 0.2,
            },
            candidate_count=1,
        )

        self.assertFalse(judgment["explains_failure"])

    def test_build_model_scoring_prompt_is_contrastive(self) -> None:
        profile = demo_profile(
            keywords=["licm", "writeonly", "hoist"],
            relevant_paths=["llvm/lib/Transforms/Scalar"],
            high_risk_paths=["llvm/lib/Transforms"],
        )
        commits = [
            {
                "sha": "1" * 40,
                "subject": "docs change",
                "body": "",
                "files": ["llvm/docs/ReleaseNotes.md"],
                "diff": "",
            },
            {
                "sha": "2" * 40,
                "subject": "LICM hoist writeonly calls",
                "body": "touches hoisting and alias checks",
                "files": ["llvm/lib/Transforms/Scalar/LICM.cpp"],
                "diff": "writeonly hoist licm alias",
            },
        ]

        prompt = lm_bisect.build_model_scoring_prompt(profile, commits)
        lowered = prompt.lower()

        self.assertIn("first bad boundary", lowered)
        self.assertIn("full score range", lowered)
        self.assertIn("same subsystem", lowered)

    def test_build_model_scoring_prompt_splits_bad_and_skip_observations(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="old crash",
                features=[],
                source="runner",
                evidence=["Stack dump:"],
                log_excerpt="",
                trace_excerpt="old trace",
            ),
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="skip",
                summary="middle build crash",
                features=[],
                source="runner",
                evidence=["error:"],
                log_excerpt="",
                trace_excerpt="middle trace",
                build_failure={
                    "phase": "build",
                    "primary_error": "std::string is not a member of std",
                    "failed_target": "LLVMDemangle",
                    "failed_header": "llvm/include/llvm/Demangle/MicrosoftDemangleNodes.h",
                },
            ),
            lm_bisect.CommitObservation(
                sha="c" * 40,
                verdict="bad",
                summary="latest crash",
                features=[],
                source="runner",
                evidence=["Running pass"],
                log_excerpt="",
                trace_excerpt="latest trace",
            ),
        ]

        prompt = lm_bisect.build_model_scoring_prompt(profile, commits, observations)

        self.assertIn("Observed crash evidence from the latest bad builds", prompt)
        self.assertIn("Observed skipped build/configuration failures", prompt)
        self.assertIn("old trace", prompt)
        self.assertIn("Observed skipped build, not a bug reproduction: " + "b" * 40, prompt)
        self.assertNotIn("Observed bad commit: " + "b" * 40, prompt)
        self.assertIn("LLVMDemangle", prompt)
        self.assertIn("middle trace", prompt)
        self.assertIn("latest trace", prompt)
        self.assertIn("strongest signal", prompt)
        self.assertIn("Use skip evidence mainly to reduce build_success_prob", prompt)

    def test_build_model_scoring_prompt_trace_only_uses_only_trace_excerpt(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="clang frontend command failed with exit code 136",
                features=[],
                source="runner",
                evidence=["PLEASE submit a bug report to llvm.org/PR"],
                log_excerpt='Running pass "loop-vectorize" on function "foo"',
                trace_excerpt="Stack dump:\nllvm::LoopVectorizationCostModel::expectedCost",
            )
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("Stack dump:", prompt)
        self.assertIn("LoopVectorizationCostModel::expectedCost", prompt)
        self.assertNotIn("PLEASE submit a bug report to llvm.org/PR", prompt)
        self.assertNotIn('Running pass "loop-vectorize" on function "foo"', prompt)
        self.assertNotIn("Summary: clang frontend command failed with exit code 136", prompt)

    def test_build_model_scoring_prompt_names_assertion_as_primary_evidence(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="clang abort",
                features=[],
                source="runner",
                trace_excerpt=(
                    'Assertion `!Name.empty() && "Must have a name!"\' failed.\n'
                    'Running pass "loop-vectorize" on function "func_21"'
                ),
            )
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("Observed crash evidence from the latest bad builds", prompt)
        self.assertIn("Primary crash/assertion evidence", prompt)
        self.assertIn('Assertion `!Name.empty() && "Must have a name!"\' failed.', prompt)
        self.assertIn("assertion message or fatal error text", prompt)

    def test_trace_only_prompt_collapses_repeated_assertion_signatures(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        same_assertion = 'Assertion `!Name.empty() && "Must have a name!"\' failed.'
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="old crash",
                features=[],
                source="runner",
                trace_excerpt=f"{same_assertion}\nRunning pass \"loop-vectorize\" on function \"func_21\"",
            ),
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="latest same crash",
                features=[],
                source="runner",
                trace_excerpt=f"{same_assertion}\nRunning pass \"loop-vectorize\" on function \"func_22\"",
            ),
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("Observed bad commit: " + "b" * 40, prompt)
        self.assertNotIn("Observed bad commit: " + "a" * 40, prompt)
        self.assertIn("same crash signature repeated 2 times", prompt)

    def test_trace_only_prompt_keeps_distinct_assertion_signatures(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="old name crash",
                features=[],
                source="runner",
                trace_excerpt='Assertion `!Name.empty() && "Must have a name!"\' failed.',
            ),
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="different crash",
                features=[],
                source="runner",
                trace_excerpt="fatal error: error in backend: cannot select",
            ),
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("Observed bad commit: " + "a" * 40, prompt)
        self.assertIn("Observed bad commit: " + "b" * 40, prompt)
        self.assertIn('Assertion `!Name.empty() && "Must have a name!"\' failed.', prompt)
        self.assertIn("fatal error: error in backend: cannot select", prompt)

    def test_trace_only_prompt_does_not_count_skip_against_bad_limit(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="old bad",
                features=[],
                source="runner",
                trace_excerpt="old bad trace",
            ),
            lm_bisect.CommitObservation(
                sha="s" * 40,
                verdict="skip",
                summary="build failed",
                features=[],
                source="runner",
                trace_excerpt="skip build trace",
            ),
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="new bad",
                features=[],
                source="runner",
                trace_excerpt="new bad trace",
            ),
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("old bad trace", prompt)
        self.assertIn("new bad trace", prompt)
        self.assertIn("skip build trace", prompt)
        self.assertIn("Observed skipped build, not a bug reproduction: " + "s" * 40, prompt)

    def test_build_model_scoring_prompt_trace_only_skips_observations_without_trace_excerpt(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="legacy-only crash",
                features=[],
                source="runner",
                evidence=["legacy bug summary line"],
                log_excerpt="legacy log excerpt",
                trace_excerpt="",
            )
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertNotIn("Observed crash evidence from the latest bad builds", prompt)
        self.assertNotIn("legacy bug summary line", prompt)
        self.assertNotIn("legacy log excerpt", prompt)

    def test_build_model_scoring_prompt_trace_only_caps_long_excerpts(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "1" * 40,
                "subject": "vectorizer change",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                "diff": "vectorize expectedCost",
            }
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="clang abort",
                features=[],
                source="runner",
                trace_excerpt="Stack dump:\n" + ("x" * 5000),
            )
        ]

        prompt = lm_bisect.build_model_scoring_prompt(
            profile,
            commits,
            observations,
            observation_prompt_mode="trace-only",
        )

        self.assertIn("Stack dump:", prompt)
        self.assertLess(prompt.count("x"), 5000)
        self.assertLessEqual(prompt.count("x"), lm_bisect.TRACE_PROMPT_MAX_CHARS)

    def test_compact_diff_for_prompt_marks_transition_diff(self) -> None:
        text = lm_bisect.compact_diff_for_prompt(
            ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            "+ changed body",
            diff_mode="last-tested",
            base_sha="a" * 40,
            candidate_sha="b" * 40,
        )

        self.assertIn("Diff mode: last-tested", text)
        self.assertIn("Base commit: " + "a" * 40, text)
        self.assertIn("Candidate commit: " + "b" * 40, text)
        self.assertIn("+ changed body", text)

    def test_build_model_scoring_prompt_includes_transition_diff_metadata(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "b" * 40,
                "subject": "candidate",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                "diff": "+ changed body",
                "diff_mode": "last-tested",
                "diff_base_sha": "a" * 40,
            }
        ]

        prompt = lm_bisect.build_model_scoring_prompt(profile, commits)

        self.assertIn("Diff mode: last-tested", prompt)
        self.assertIn("Base commit: " + "a" * 40, prompt)
        self.assertIn("Candidate commit: " + "b" * 40, prompt)

    def test_build_model_scoring_prompt_uses_extracted_diff_evidence(self) -> None:
        profile = demo_profile()
        commits = [
            {
                "sha": "b" * 40,
                "subject": "candidate",
                "body": "",
                "files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                "diff": "RAW_DIFF_SHOULD_NOT_APPEAR",
                "diff_mode": "parent",
                "diff_extraction": "llm",
                "diff_summary": "Summary: changed the recipe dominance check.",
            }
        ]

        prompt = lm_bisect.build_model_scoring_prompt(profile, commits)

        self.assertIn("Diff extraction: llm", prompt)
        self.assertIn("Summary: changed the recipe dominance check.", prompt)
        self.assertNotIn("RAW_DIFF_SHOULD_NOT_APPEAR", prompt)

    def test_plan_model_scoring_batches_keeps_small_topk_frontier_together(self) -> None:
        commits = [{"sha": str(index)} for index in range(12)]

        batches = lm_bisect.plan_model_scoring_batches(commits, frontier_mode="topk")

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 12)

    def test_plan_model_scoring_batches_chunks_large_all_frontier(self) -> None:
        commits = [{"sha": str(index)} for index in range(25)]

        batches = lm_bisect.plan_model_scoring_batches(commits, frontier_mode="all")

        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch) <= 12 for batch in batches))

    def test_score_model_batch_with_backfill_recovers_missing_commit(self) -> None:
        profile = demo_profile()
        commits = [
            {"sha": "a" * 40, "subject": "a", "body": "", "files": [], "diff": ""},
            {"sha": "b" * 40, "subject": "b", "body": "", "files": [], "diff": ""},
            {"sha": "c" * 40, "subject": "c", "body": "", "files": [], "diff": ""},
        ]
        calls: list[list[str]] = []

        def fake_score_fn(_profile, batch, _config):
            shas = [item["sha"] for item in batch]
            calls.append(shas)
            payload = {
                sha: {
                    "semantic_score": 2.0,
                    "build_success_prob": 0.9,
                    "evidence": ["ok"],
                    "features": ["term:test"],
                }
                for sha in shas
            }
            if len(shas) == 3:
                payload.pop("b" * 40)
            return payload

        recovered = lm_bisect.score_model_batch_with_backfill(profile, commits, None, fake_score_fn)

        self.assertEqual(set(recovered), {"a" * 40, "b" * 40, "c" * 40})
        self.assertEqual(calls[0], ["a" * 40, "b" * 40, "c" * 40])
        self.assertIn(["b" * 40], calls)

    def test_score_model_batch_with_backfill_falls_back_when_single_item_still_missing(self) -> None:
        profile = demo_profile(keywords=["licm", "writeonly", "hoist"], relevant_paths=["llvm/lib/Transforms/Scalar"])
        commits = [
            {
                "sha": "d" * 40,
                "subject": "LICM hoist writeonly calls",
                "body": "touches hoisting and alias checks",
                "files": ["llvm/lib/Transforms/Scalar/LICM.cpp"],
                "diff": "writeonly hoist licm alias",
            }
        ]

        def fake_score_fn(_profile, _batch, _config, _observations=None):
            return {}

        recovered = lm_bisect.score_model_batch_with_backfill(profile, commits, None, fake_score_fn)

        self.assertEqual(set(recovered), {"d" * 40})
        self.assertGreaterEqual(recovered["d" * 40]["semantic_score"], 0.1)
        self.assertGreaterEqual(recovered["d" * 40]["build_success_prob"], 0.05)
        self.assertTrue(recovered["d" * 40]["evidence"])
        self.assertIn("model-fallback", recovered["d" * 40]["evidence"][0])

    def test_parse_model_json_payload_accepts_fenced_json(self) -> None:
        content = """```json
[
  {
    "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "semantic_score": 3.5,
    "build_success_prob": 0.8,
    "evidence": ["reason"],
    "features": ["feat"]
  }
]
```"""

        payload = lm_bisect.parse_model_json_payload(content)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["sha"], "a" * 40)

    def test_parse_model_json_payload_escapes_raw_control_characters(self) -> None:
        content = '[{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","semantic_score":3.5,"build_success_prob":0.8,"evidence":["bad\x01reason"],"features":["feat"]}]'

        payload = lm_bisect.parse_model_json_payload(content)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["sha"], "a" * 40)
        self.assertEqual(payload[0]["evidence"][0], "bad\u0001reason")

    def test_parse_model_json_object_recovers_trailing_commas(self) -> None:
        content = '{"summary":"candidate mechanism", "changed_symbols":["Foo",],}'

        payload = lm_bisect.parse_model_json_object(content)

        self.assertEqual(payload["summary"], "candidate mechanism")
        self.assertEqual(payload["changed_symbols"], ["Foo"])

    def test_plan_diff_extraction_batches_respects_prompt_budget(self) -> None:
        profile = demo_profile()
        items = [
            {
                "sha": f"{index:040x}",
                "subject": f"candidate {index}",
                "files": ["llvm/lib/IR/Example.cpp"],
                "diff": "+" + ("x" * 120),
            }
            for index in range(3)
        ]

        batches = lm_bisect.plan_diff_extraction_batches(
            profile,
            items,
            batch_size=20,
            max_prompt_chars=len(lm_bisect.build_diff_extraction_batch_prompt(profile, items[:1])) + 20,
        )

        self.assertEqual([[item["sha"] for item in batch] for batch in batches], [[items[0]["sha"]], [items[1]["sha"]], [items[2]["sha"]]])

    def test_single_diff_extraction_prompt_respects_prompt_budget(self) -> None:
        profile = demo_profile()
        item = {
            "sha": "a" * 40,
            "subject": "large generated change",
            "files": ["llvm/lib/Transforms/Example.cpp"],
            "diff": "+" + ("x" * lm_bisect.DIFF_EXTRACTION_MAX_INPUT_CHARS),
        }

        prompt = lm_bisect.build_diff_extraction_prompt(profile, item)

        self.assertLessEqual(len(prompt), lm_bisect.DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS)
        self.assertIn('"raw_diff_truncated": true', prompt)

    def test_single_diff_extraction_batch_prompt_respects_prompt_budget(self) -> None:
        profile = demo_profile()
        item = {
            "sha": "a" * 40,
            "subject": "large generated change",
            "files": ["llvm/lib/Transforms/Example.cpp"],
            "diff": "+" + ("x" * lm_bisect.DIFF_EXTRACTION_MAX_INPUT_CHARS),
        }

        prompt = lm_bisect.build_diff_extraction_batch_prompt(profile, [item])

        self.assertLessEqual(len(prompt), lm_bisect.DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS)
        self.assertIn("Return strict JSON as an array.", prompt)
        self.assertIn("- raw diff truncated: true", prompt)

    def test_causal_retrieval_prefers_issue_matched_hunks_and_adds_function_context(self) -> None:
        profile = demo_profile(
            keywords=["vectorizer", "recipe"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
        )
        raw_diff = """diff --git a/llvm/lib/Support/Noise.cpp b/llvm/lib/Support/Noise.cpp
index 1..2 100644
--- a/llvm/lib/Support/Noise.cpp
+++ b/llvm/lib/Support/Noise.cpp
@@ -1 +1 @@ unrelated_helper
-old
+new
diff --git a/llvm/lib/Transforms/Vectorize/VPlan.cpp b/llvm/lib/Transforms/Vectorize/VPlan.cpp
index 3..4 100644
--- a/llvm/lib/Transforms/Vectorize/VPlan.cpp
+++ b/llvm/lib/Transforms/Vectorize/VPlan.cpp
@@ -10 +10 @@ VPlan::buildRecipe()
-return OldRecipe;
+return NewRecipe;
"""
        item = {
            "sha": "a" * 40,
            "files": ["llvm/lib/Support/Noise.cpp", "llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            "diff": "",
        }

        def fake_git_output(_repo, args, _max_chars):
            if "--" in args:
                return raw_diff
            return "void VPlan::buildRecipe() {\n  return NewRecipe;\n}\n"

        with mock.patch.object(lm_bisect, "git_limited_output", side_effect=fake_git_output) as git_output:
            retrieval = lm_bisect.retrieve_causal_diff_evidence(Path("/tmp/fake-llvm-project"), profile, item)

        self.assertEqual(retrieval["selected_hunks"][0]["path"], "llvm/lib/Transforms/Vectorize/VPlan.cpp")
        self.assertIn("relevant-path", retrieval["selected_hunks"][0]["match_reasons"])
        self.assertIn("buildRecipe", retrieval["function_contexts"][0]["symbol"])
        self.assertNotIn("llvm/lib/Support/Noise.cpp", retrieval["selected_files"])
        self.assertIn("--", git_output.call_args_list[0].args[1])
        self.assertIn("llvm/lib/Transforms/Vectorize/VPlan.cpp", git_output.call_args_list[0].args[1])
        self.assertNotIn("llvm/lib/Support/Noise.cpp", git_output.call_args_list[0].args[1])

    def test_causal_retrieval_range_retains_candidate_and_predecessor_provenance(self) -> None:
        profile = demo_profile(
            keywords=["vectorizer"],
            relevant_paths=["llvm/lib/Transforms/Vectorize"],
        )
        candidate = "a" * 40
        predecessor = "b" * 40
        item = {
            "sha": candidate,
            "subject": "candidate vectorizer change",
            "files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            "diff": """diff --git a/llvm/lib/Transforms/Vectorize/VPlan.cpp b/llvm/lib/Transforms/Vectorize/VPlan.cpp
--- a/llvm/lib/Transforms/Vectorize/VPlan.cpp
+++ b/llvm/lib/Transforms/Vectorize/VPlan.cpp
@@ -1 +1 @@ VPlan::buildRecipe()
-return OldCandidate;
+return NewCandidate;
""",
        }
        predecessor_diff = """diff --git a/llvm/lib/Transforms/Vectorize/VPlan.cpp b/llvm/lib/Transforms/Vectorize/VPlan.cpp
--- a/llvm/lib/Transforms/Vectorize/VPlan.cpp
+++ b/llvm/lib/Transforms/Vectorize/VPlan.cpp
@@ -1 +1 @@ VPlan::buildRecipe()
-return OldPrecursor;
+return VectorizerPrecursor;
"""

        with mock.patch.object(
            lm_bisect,
            "first_parent_commit_range",
            return_value=[candidate, predecessor],
        ), mock.patch.object(
            lm_bisect,
            "commit_subject_and_files",
            return_value=("predecessor vectorizer change", ["llvm/lib/Transforms/Vectorize/VPlan.cpp"]),
        ), mock.patch.object(
            lm_bisect,
            "commit_parent_diff_for_files",
            return_value=predecessor_diff,
        ), mock.patch.object(
            lm_bisect,
            "function_context_at_commit",
            return_value="",
        ):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                item,
                context_parent_count=1,
            )

        self.assertEqual(retrieval["context_parent_count_requested"], 1)
        self.assertEqual(retrieval["context_parent_count_observed"], 1)
        self.assertEqual([commit["sha"] for commit in retrieval["context_commits"]], [candidate, predecessor])
        self.assertEqual(retrieval["selected_hunks"][0]["source_sha"], candidate)
        self.assertEqual(retrieval["selected_hunks"][0]["source_distance"], 0)
        self.assertIn(predecessor, {hunk["source_sha"] for hunk in retrieval["selected_hunks"]})

    def test_implementation_first_causal_retrieval_prefers_source_but_keeps_test_fallback(self) -> None:
        profile = demo_profile(
            keywords=["vectorizer"],
            relevant_paths=["llvm"],
        )
        raw_diff = """diff --git a/llvm/test/Transforms/Vectorize/vectorizer.ll b/llvm/test/Transforms/Vectorize/vectorizer.ll
index 1..2 100644
--- a/llvm/test/Transforms/Vectorize/vectorizer.ll
+++ b/llvm/test/Transforms/Vectorize/vectorizer.ll
@@ -1 +1 @@ vectorizer-test
-old
+new
diff --git a/llvm/lib/Transforms/Vectorize/VPlan.cpp b/llvm/lib/Transforms/Vectorize/VPlan.cpp
index 3..4 100644
--- a/llvm/lib/Transforms/Vectorize/VPlan.cpp
+++ b/llvm/lib/Transforms/Vectorize/VPlan.cpp
@@ -10 +10 @@ VPlan::buildRecipe()
-return OldRecipe;
+return NewRecipe;
"""
        item = {
            "sha": "a" * 40,
            "files": [
                "llvm/test/Transforms/Vectorize/vectorizer.ll",
                "llvm/lib/Transforms/Vectorize/VPlan.cpp",
            ],
            "diff": raw_diff,
        }

        with mock.patch.object(lm_bisect, "function_context_at_commit", return_value=""):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                item,
                retrieval_policy="implementation-first",
            )

        self.assertEqual(retrieval["retrieval_policy"], "implementation-first")
        self.assertFalse(retrieval["test_fallback_used"])
        self.assertEqual(
            retrieval["selected_hunks"][0]["path"],
            "llvm/lib/Transforms/Vectorize/VPlan.cpp",
        )
        self.assertEqual(retrieval["selected_hunks"][0]["source_kind"], "implementation")
        self.assertEqual(retrieval["selected_hunks"][1]["source_kind"], "test")

    def test_implementation_first_causal_retrieval_uses_test_only_fallback(self) -> None:
        profile = demo_profile(keywords=["vectorizer"], relevant_paths=["llvm"])
        raw_diff = """diff --git a/llvm/test/Transforms/Vectorize/vectorizer.ll b/llvm/test/Transforms/Vectorize/vectorizer.ll
index 1..2 100644
--- a/llvm/test/Transforms/Vectorize/vectorizer.ll
+++ b/llvm/test/Transforms/Vectorize/vectorizer.ll
@@ -1 +1 @@ vectorizer-test
-old
+new
"""
        item = {
            "sha": "a" * 40,
            "files": ["llvm/test/Transforms/Vectorize/vectorizer.ll"],
            "diff": raw_diff,
        }

        with mock.patch.object(lm_bisect, "function_context_at_commit", return_value=""):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                item,
                retrieval_policy="implementation-first",
            )

        self.assertTrue(retrieval["test_fallback_used"])
        self.assertEqual(retrieval["selected_hunks"][0]["source_kind"], "test")

    def test_implementation_first_causal_retrieval_keeps_source_when_only_test_matches(self) -> None:
        profile = demo_profile(
            keywords=["vectorizer"],
            relevant_paths=["llvm/test/Transforms/Vectorize"],
        )
        raw_diff = """diff --git a/llvm/test/Transforms/Vectorize/vectorizer.ll b/llvm/test/Transforms/Vectorize/vectorizer.ll
index 1..2 100644
--- a/llvm/test/Transforms/Vectorize/vectorizer.ll
+++ b/llvm/test/Transforms/Vectorize/vectorizer.ll
@@ -1 +1 @@ vectorizer-test
-old
+new
diff --git a/llvm/lib/Analysis/MemorySSA.cpp b/llvm/lib/Analysis/MemorySSA.cpp
index 3..4 100644
--- a/llvm/lib/Analysis/MemorySSA.cpp
+++ b/llvm/lib/Analysis/MemorySSA.cpp
@@ -10 +10 @@ MemorySSA::removeFromLookups()
-old
+new
"""
        item = {
            "sha": "a" * 40,
            "files": [
                "llvm/test/Transforms/Vectorize/vectorizer.ll",
                "llvm/lib/Analysis/MemorySSA.cpp",
            ],
            "diff": raw_diff,
        }

        with mock.patch.object(lm_bisect, "function_context_at_commit", return_value=""):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                item,
                retrieval_policy="implementation-first",
            )

        self.assertFalse(retrieval["test_fallback_used"])
        self.assertEqual(retrieval["selected_hunks"][0]["path"], "llvm/lib/Analysis/MemorySSA.cpp")
        self.assertEqual(retrieval["selected_hunks"][0]["source_kind"], "implementation")
        self.assertEqual(retrieval["selected_hunks"][1]["source_kind"], "test")

    def test_implementation_first_file_selection_prefers_source_before_fetch(self) -> None:
        profile = demo_profile(keywords=["vectorizer"], relevant_paths=["llvm"])
        changed_files = [
            *[f"llvm/test/Transforms/Vectorize/case-{index}.ll" for index in range(20)],
            "llvm/lib/Transforms/Vectorize/VPlan.cpp",
        ]

        selected = lm_bisect.select_causal_retrieval_files(
            profile,
            changed_files,
            retrieval_policy="implementation-first",
        )

        self.assertEqual(selected[0], "llvm/lib/Transforms/Vectorize/VPlan.cpp")
        self.assertEqual(len(selected), lm_bisect.TRANSITION_DIFF_FILE_LIMIT)
        self.assertNotIn("llvm/test/Transforms/Vectorize/case-19.ll", selected)

    def test_implementation_first_file_selection_keeps_source_when_only_test_matches(self) -> None:
        profile = demo_profile(
            keywords=["vectorizer"],
            relevant_paths=["llvm/test/Transforms/Vectorize"],
        )

        selected = lm_bisect.select_causal_retrieval_files(
            profile,
            [
                "llvm/test/Transforms/Vectorize/vectorizer.ll",
                "llvm/lib/Analysis/MemorySSA.cpp",
            ],
            retrieval_policy="implementation-first",
        )

        self.assertEqual(selected[0], "llvm/lib/Analysis/MemorySSA.cpp")
        self.assertEqual(selected[1], "llvm/test/Transforms/Vectorize/vectorizer.ll")

    def test_human_guided_retrieval_prioritizes_direct_crash_signal_hunks(self) -> None:
        profile = demo_profile(
            keywords=["optimizer"],
            relevant_paths=["llvm/lib/Transforms"],
            high_risk_paths=["llvm/lib"],
        )
        direct = {
            "path": "llvm/lib/Analysis/MemorySSA.cpp",
            "header": "@@ verifyOptResult",
            "patch": "+ bool ClobberWalker::verifyOptResult();",
        }
        generic = {
            "path": "llvm/lib/Transforms/Scalar/Noise.cpp",
            "header": "@@ optimize",
            "patch": "+ void optimize();",
        }
        signals = {
            "kind": "assertion",
            "assertion": "OtherClobbers must dominate",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": ["ClobberWalker::verifyOptResult"],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": ["verifyOptResult", "ClobberWalker", "MemorySSA"],
        }

        with mock.patch.object(lm_bisect, "first_parent_commit_range", return_value=["a" * 40]), mock.patch.object(
            lm_bisect, "parse_unified_diff_hunks", return_value=[generic, direct]
        ), mock.patch.object(lm_bisect, "function_context_at_commit", return_value=""):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {
                    "sha": "a" * 40,
                    "subject": "candidate",
                    "files": [generic["path"], direct["path"]],
                    "diff": "candidate diff",
                },
                retrieval_policy="human-guided",
                crash_signal_payload=signals,
            )

        self.assertEqual(retrieval["retrieval_policy"], "human-guided")
        self.assertEqual(retrieval["selected_hunks"][0]["path"], direct["path"])
        self.assertIn("crash-source-path", retrieval["selected_hunks"][0]["match_reasons"])
        self.assertEqual(retrieval["signal_reachability"], "direct")
        self.assertEqual(retrieval["crash_signals"]["kind"], "assertion")

    def test_human_guided_retrieval_records_text_unreachable_negative_evidence(self) -> None:
        profile = demo_profile(keywords=[], relevant_paths=[], high_risk_paths=[])
        hunk = {
            "path": "llvm/lib/Support/Unrelated.cpp",
            "header": "@@ unrelated",
            "patch": "+ void unrelated();",
        }

        with mock.patch.object(lm_bisect, "first_parent_commit_range", return_value=["a" * 40]), mock.patch.object(
            lm_bisect, "parse_unified_diff_hunks", return_value=[hunk]
        ), mock.patch.object(lm_bisect, "function_context_at_commit", return_value=""):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {
                    "sha": "a" * 40,
                    "subject": "candidate",
                    "files": [hunk["path"]],
                    "diff": "candidate diff",
                },
                retrieval_policy="human-guided",
                crash_signal_payload={
                    "kind": "verifier",
                    "source_paths": ["llvm/lib/IR/Verifier.cpp"],
                    "symbols": ["Verifier::verify"],
                    "pass_tokens": [],
                    "query_terms": ["Verifier"],
                },
            )

        self.assertEqual(retrieval["signal_reachability"], "text-unreachable")
        self.assertIn("no crash-anchor contact found", retrieval["negative_evidence"])

    def test_crash_aware_retrieval_penalizes_rare_checker_and_uses_dependency_weight(self) -> None:
        profile = replace(
            demo_profile(
                keywords=[],
                relevant_paths=[],
                high_risk_paths=[],
            ),
            bad_commit="b" * 40,
        )
        checker = {
            "path": "llvm/lib/Analysis/MemorySSA.cpp",
            "header": "@@ verifyOptResult",
            "patch": "+ bool verifyOptResult();",
        }
        producer = {
            "path": "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
            "header": "@@ applyUpdates",
            "patch": "+ MSSAU->applyUpdates(Updates);",
        }
        signals = {
            "kind": "assertion",
            "assertion": "OtherClobbers must dominate",
            "assert_source_file": "llvm/lib/Analysis/MemorySSA.cpp",
            "assert_function": "verifyOptResult",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": ["llvm::SimpleLoopUnswitchPass::run"],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": ["MemorySSAUpdater"],
        }

        with mock.patch.object(
            lm_bisect,
            "first_parent_commit_range",
            return_value=["a" * 40],
        ), mock.patch.object(
            lm_bisect,
            "parse_unified_diff_hunks",
            return_value=[checker, producer],
        ), mock.patch.object(
            lm_bisect,
            "function_context_at_commit",
            return_value="",
        ):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {
                    "sha": "a" * 40,
                    "subject": "candidate",
                    "files": [checker["path"], producer["path"]],
                    "diff": "candidate diff",
                },
                retrieval_policy="crash-aware",
                crash_signal_payload=signals,
                crash_file_touch_count=5,
                dependency_usage={
                    "MemorySSAUpdater": {
                        producer["path"]: 7,
                        checker["path"]: 1,
                    }
                },
            )

        self.assertEqual(retrieval["retrieval_policy"], "crash-aware")
        self.assertEqual(retrieval["selected_hunks"][0]["path"], producer["path"])
        self.assertIn("rare-checker-file-penalty", retrieval["selected_hunks"][1]["match_reasons"])
        self.assertIn("dependency-api-use", retrieval["selected_hunks"][0]["match_reasons"])
        self.assertEqual(retrieval["crash_file_touch_count"], 5)
        self.assertGreater(
            retrieval["selected_hunks"][0]["retrieval_score"],
            retrieval["selected_hunks"][1]["retrieval_score"],
        )

    def test_crash_aware_retrieval_adds_assertion_contract_context(self) -> None:
        profile = replace(
            demo_profile(),
            bad_commit="b" * 40,
        )
        hunk = {
            "path": "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
            "header": "@@ run",
            "patch": "+ runUnswitch();",
        }
        signals = {
            "kind": "assertion",
            "assertion": "invariant",
            "assert_source_file": "llvm/lib/Analysis/MemorySSA.cpp",
            "assert_function": "verifyOptResult",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": [],
        }

        def context(_repo, sha, path, symbol):
            if (sha, path, symbol) == (
                profile.bad_commit,
                signals["assert_source_file"],
                signals["assert_function"],
            ):
                return "void verifyOptResult() { checkInvariant(); }"
            return ""

        with mock.patch.object(
            lm_bisect,
            "first_parent_commit_range",
            return_value=["a" * 40],
        ), mock.patch.object(
            lm_bisect,
            "parse_unified_diff_hunks",
            return_value=[hunk],
        ), mock.patch.object(
            lm_bisect,
            "function_context_at_commit",
            side_effect=context,
        ):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {
                    "sha": "a" * 40,
                    "subject": "candidate",
                    "files": [hunk["path"]],
                    "diff": "candidate diff",
                },
                retrieval_policy="crash-aware",
                crash_signal_payload=signals,
            )

        self.assertEqual(retrieval["contract_contexts"][0]["source_sha"], profile.bad_commit)
        self.assertEqual(retrieval["contract_contexts"][0]["path"], signals["assert_source_file"])
        self.assertIn("checkInvariant", retrieval["contract_contexts"][0]["context"])

    def test_deterministic_facts_retrieval_records_contact_path(self) -> None:
        profile = replace(demo_profile(), bad_commit="b" * 40)
        hunk = {
            "path": "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
            "header": "@@ static bool unswitchTrivialBranch(Loop &L)",
            "patch": "+ MSSAU->applyUpdates(Updates);",
        }
        signals = {
            "kind": "assertion",
            "assert_source_file": "llvm/lib/Analysis/MemorySSA.cpp",
            "assert_function": "verifyOptResult",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": ["MemorySSAUpdater"],
        }

        with mock.patch.object(lm_bisect, "first_parent_commit_range", return_value=["a" * 40]), mock.patch.object(
            lm_bisect, "parse_unified_diff_hunks", return_value=[hunk]
        ), mock.patch.object(
            lm_bisect, "function_context_at_commit", return_value="void verifyOptResult() {}"
        ), mock.patch.object(
            lm_bisect, "public_header_method_surface", return_value=["applyUpdates", "moveTo"]
        ):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {"sha": "a" * 40, "subject": "candidate", "files": [hunk["path"]], "diff": "candidate diff"},
                retrieval_policy="deterministic-facts",
                crash_signal_payload=signals,
            )

        facts = retrieval["repository_facts"]
        self.assertEqual(facts["destruction_surface"], ["applyUpdates", "moveTo"])
        self.assertEqual(facts["contact_paths"], ["unswitchTrivialBranch -> MSSAU->applyUpdates"])
        self.assertFalse(facts["no_call_path_found"])

    def test_deterministic_facts_retrieval_records_no_contact_path(self) -> None:
        profile = replace(demo_profile(), bad_commit="b" * 40)
        hunk = {
            "path": "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
            "header": "@@ static bool unswitchTrivialBranch(Loop &L)",
            "patch": "+ return Changed;",
        }
        signals = {
            "kind": "assertion",
            "assert_source_file": "llvm/lib/Analysis/MemorySSA.cpp",
            "assert_function": "verifyOptResult",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": [],
        }

        with mock.patch.object(lm_bisect, "first_parent_commit_range", return_value=["a" * 40]), mock.patch.object(
            lm_bisect, "parse_unified_diff_hunks", return_value=[hunk]
        ), mock.patch.object(
            lm_bisect, "function_context_at_commit", return_value=""
        ), mock.patch.object(
            lm_bisect, "public_header_method_surface", return_value=["applyUpdates"]
        ):
            retrieval = lm_bisect.retrieve_causal_diff_evidence(
                Path("/tmp/fake-llvm-project"),
                profile,
                {"sha": "a" * 40, "subject": "candidate", "files": [hunk["path"]], "diff": "candidate diff"},
                retrieval_policy="deterministic-facts",
                crash_signal_payload=signals,
            )

        facts = retrieval["repository_facts"]
        self.assertEqual(facts["contact_paths"], [])
        self.assertTrue(facts["no_call_path_found"])

    def test_crash_aware_context_counts_checker_touches_over_full_issue_interval(self) -> None:
        profile = replace(
            demo_profile(),
            good_commit="1" * 40,
            bad_commit="2" * 40,
        )
        crash_payload = {
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "query_terms": ["MemorySSAUpdater"],
        }

        with mock.patch.object(
            lm_bisect,
            "causal_crash_signal_payload",
            return_value=crash_payload,
        ), mock.patch.object(
            lm_bisect,
            "crash_file_touch_count_in_issue_interval",
            return_value=6,
        ) as touch_count, mock.patch.object(
            lm_bisect,
            "dynamic_dependency_usage",
            return_value={"MemorySSAUpdater": {"llvm/lib/Transforms/Scalar/Loop.cpp": 7}},
        ), mock.patch.object(
            lm_bisect,
            "public_header_method_surface",
            return_value=["applyUpdates"],
        ):
            context = lm_bisect.causal_crash_aware_retrieval_context(
                Path("/tmp/fake-llvm-project"),
                profile,
                "causal-llm-deterministic-facts",
            )

        touch_count.assert_called_once_with(
            Path("/tmp/fake-llvm-project"),
            profile,
            crash_payload,
        )
        self.assertEqual(context["crash_file_touch_count"], 6)
        self.assertEqual(context["destruction_surface"], ["applyUpdates"])

    def test_human_signal_pool_prefers_pass_match_over_rare_checker_file(self) -> None:
        profile = replace(
            demo_profile(
                relevant_paths=["llvm/lib/Transforms/Scalar"],
                high_risk_paths=["llvm/lib/Analysis", "llvm/lib/Transforms/Scalar"],
            ),
            issue_id="pr204559",
        )
        shas = ["a" * 40, "b" * 40, "c" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="MemorySSA checker maintenance",
                body="",
                changed_files=["llvm/lib/Analysis/MemorySSA.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Generalize loop unswitching",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            ),
            shas[2]: lm_bisect.CommitMetadata(
                sha=shas[2],
                subject="Unrelated scalar cleanup",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SROA.cpp"],
            ),
        }
        crash_payload = {
            "kind": "assertion",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": [],
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            shas,
            metadata,
            crash_payload,
        )

        self.assertEqual(pool["crash_file_touch_count"], 1)
        self.assertTrue(pool["rare_checker_file"])
        self.assertEqual(pool["candidate_shas"], [shas[1]])
        self.assertIn("crash-pass", pool["candidates"][0]["match_reasons"])
        self.assertNotIn(shas[0], pool["candidate_shas"])

    def test_human_signal_pool_records_dependency_anchor_matches(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204559",
        )
        sha = "a" * 40
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="Update MemorySSA user",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            )
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            [sha],
            metadata,
            {
                "kind": "assertion",
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": ["simple-loop-unswitch"],
                "query_terms": [{"term": "MemorySSAUpdater", "kind": "component-updater"}],
            },
            dependency_anchors={
                "MemorySSAUpdater": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"]
            },
        )

        self.assertEqual(pool["candidate_shas"], [sha])
        self.assertIn("dependency-anchor:MemorySSAUpdater", pool["candidates"][0]["match_reasons"])

    def test_human_signal_pool_preserves_complete_direct_signal_set_for_triage(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204559",
        )
        shas = [f"{index:040x}" for index in range(20)]
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject=f"simple-loop-unswitch update {index}",
                body="",
                changed_files=[f"llvm/lib/Transforms/Utils/SimpleLoopUnswitch{index}.cpp"],
            )
            for index, sha in enumerate(shas)
        }
        payload = {"source_paths": [], "symbols": [], "pass_tokens": ["simple-loop-unswitch"]}

        pool = lm_bisect.build_human_signal_pool(profile, shas, metadata, payload)

        self.assertEqual(pool["candidate_count_before_limit"], 20)
        self.assertEqual(pool["candidate_count"], 20)
        self.assertEqual(pool["candidate_shas"], shas)
        self.assertFalse(pool["pool_truncated"])

    def test_human_signal_pool_uses_documented_crash_pass_class(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204559",
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="Simple loop unswitch update",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Update the running early-cse pass",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/EarlyCSE.cpp"],
            ),
        }
        payload = {
            "source_paths": [],
            "symbols": [],
            "pass_tokens": ["early-cse", "simple-loop-unswitch"],
        }

        pool = lm_bisect.build_human_signal_pool(profile, shas, metadata, payload)

        self.assertEqual(pool["candidate_shas"], shas)
        self.assertEqual(
            pool["candidates"][1]["primary_signal_match"],
            {"kind": "crash-pass", "term": "early-cse"},
        )

    def test_human_signal_pool_uses_human_study_symbol_stem_rule(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr50304",
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="Constant fold implementation",
                body="",
                changed_files=["llvm/lib/Analysis/ConstantFolding.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Unrelated APFloat cleanup",
                body="",
                changed_files=["llvm/lib/Support/APFloat.cpp"],
            ),
        }
        payload = {
            "source_paths": [],
            "symbols": ["ConstantFoldCall", "SimplifyCall"],
            "pass_tokens": [],
        }

        pool = lm_bisect.build_human_signal_pool(profile, shas, metadata, payload)

        self.assertEqual(pool["candidate_shas"], [shas[0]])
        self.assertEqual(
            pool["candidates"][0]["primary_signal_match"],
            {"kind": "crash-symbol", "term": "ConstantFoldCall"},
        )

    def test_human_signal_pool_accepts_namespaced_symbol_policy_anchor(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204589",
        )
        sha = "a" * 40
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="Generalize loop unswitching",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            )
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            [sha],
            metadata,
            {
                "source_paths": [],
                "symbols": ["llvm::SimpleLoopUnswitchPass::run"],
                "pass_tokens": [],
            },
        )

        self.assertEqual(pool["candidate_shas"], [sha])

    def test_human_signal_pool_accepts_qualified_symbol_policy_suffix(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr52635",
        )
        sha = "a" * 40
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="Emit XRay table",
                body="",
                changed_files=["llvm/lib/CodeGen/AsmPrinter/AsmPrinter.cpp"],
            )
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            [sha],
            metadata,
            {
                "source_paths": [],
                "symbols": ["llvm::AsmPrinter::emitXRayTable"],
                "pass_tokens": [],
            },
        )

        self.assertEqual(pool["candidate_shas"], [sha])

    def test_human_signal_pool_excludes_unit_test_only_symbol_matches(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204589",
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="Unit test only",
                body="",
                changed_files=["llvm/unittests/Transforms/Utils/SimpleLoopUnswitchPass.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Source implementation",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitchPass.cpp"],
            ),
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            shas,
            metadata,
            {
                "source_paths": [],
                "symbols": ["llvm::SimpleLoopUnswitchPass::run"],
                "pass_tokens": [],
            },
        )

        self.assertEqual(pool["candidate_shas"], [shas[1]])

    def test_human_signal_pool_does_not_expand_primary_signal_with_broad_matches(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204559",
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="unrelated MemorySSA maintenance",
                body="",
                changed_files=["llvm/lib/Analysis/MemorySSA.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Generalize simple loop unswitching",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            ),
        }

        pool = lm_bisect.build_human_signal_pool(
            profile,
            shas,
            metadata,
            {
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": ["simple-loop-unswitch"],
                "query_terms": [{"term": "MemorySSA", "kind": "assertion-class"}],
            },
            dependency_anchors={"MemorySSAUpdater": ["llvm/lib/Analysis/MemorySSA.cpp"]},
        )

        self.assertEqual(pool["candidate_shas"], [shas[1]])
        self.assertIn("crash-pass:simple-loop-unswitch", pool["candidates"][0]["match_reasons"])

    def test_human_signal_pool_only_computes_broad_provenance_for_primary_matches(self) -> None:
        profile = replace(
            demo_profile(relevant_paths=[], high_risk_paths=[]),
            issue_id="pr204559",
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="unrelated MemorySSA maintenance",
                body="",
                changed_files=["llvm/lib/Analysis/MemorySSA.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="Generalize simple loop unswitching",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            ),
        }
        payload = {
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": [{"term": "MemorySSA", "kind": "assertion-class"}],
        }

        with mock.patch.object(
            lm_bisect,
            "human_signal_pool_candidate_reasons",
            wraps=lm_bisect.human_signal_pool_candidate_reasons,
        ) as candidate_reasons:
            pool = lm_bisect.build_human_signal_pool(profile, shas, metadata, payload)

        self.assertEqual(pool["candidate_shas"], [shas[1]])
        self.assertEqual(candidate_reasons.call_count, 1)

    def test_human_signal_pool_proof_failure_falls_back_with_valid_interval(self) -> None:
        unresolved = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

        after_candidate = lm_bisect.human_signal_pool_transition(
            unresolved,
            selected_sha="c" * 40,
            verdict="bad",
            phase="direct-candidate",
        )
        after_parent = lm_bisect.human_signal_pool_transition(
            after_candidate["unresolved"],
            selected_sha="b" * 40,
            verdict="bad",
            phase="parent-proof",
            candidate_sha="c" * 40,
        )

        self.assertEqual(after_candidate["phase"], "parent-proof")
        self.assertEqual(after_candidate["pending_candidate_sha"], "c" * 40)
        self.assertEqual(after_parent["phase"], "fallback")
        self.assertEqual(after_parent["fallback_reason"], "parent-proof-not-good:bad")
        self.assertEqual(after_parent["unresolved"], ["a" * 40, "b" * 40])

    def test_human_signal_pool_triage_prompt_uses_crash_provenance_without_diff(self) -> None:
        prompt = lm_bisect.build_human_signal_pool_triage_prompt(
            replace(demo_profile(), issue_id="pr204559"),
            {
                "kind": "assertion",
                "assertion": "MemorySSA invariant",
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": ["llvm::SimpleLoopUnswitchPass::run"],
                "pass_tokens": ["simple-loop-unswitch"],
            },
            [
                {
                    "sha": "a" * 40,
                    "subject": "Generalize loop unswitching",
                    "changed_files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                    "match_reasons": ["crash-pass", "dependency-anchor:MemorySSAUpdater"],
                    "prior_score": 4.5,
                }
            ],
        )

        self.assertIn("MemorySSA invariant", prompt)
        self.assertIn("crash-pass", prompt)
        self.assertIn("dependency-anchor:MemorySSAUpdater", prompt)
        self.assertIn("at most 3 candidates", prompt)
        self.assertNotIn("Diff:", prompt)

    def test_human_signal_pool_triage_prompt_supports_bounded_frontier_size(self) -> None:
        prompt = lm_bisect.build_human_signal_pool_triage_prompt(
            replace(demo_profile(), issue_id="pr204559"),
            {"kind": "assertion"},
            [],
            max_candidates=12,
        )

        self.assertIn("at most 12 candidates", prompt)

    def test_human_signal_pool_direct_selection_prefers_causal_model_score(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="weaker",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=5.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.9},
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="stronger",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=6.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.7},
            ),
        ]

        decision = lm_bisect.human_signal_pool_direct_selection(
            records,
            {"a" * 40: 0.9, "b" * 40: 0.2},
        )

        self.assertEqual(decision.selected.sha, "b" * 40)
        self.assertEqual(decision.selection_mode, "human-signal-pool-direct")

    def test_human_frontier_intersects_staged_crash_and_dependency_evidence(self) -> None:
        profile = replace(
            demo_profile(
                keywords=["unswitch"],
                relevant_paths=["llvm/lib/Transforms/Scalar"],
                high_risk_paths=["llvm/lib/Transforms"],
            ),
            issue_id="pr204559",
        )
        shas = [chr(ord("a") + index) * 40 for index in range(4)]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                shas[0], "Unswitch cleanup", "", ["llvm/lib/Transforms/Scalar/NoSignal.cpp"]
            ),
            shas[1]: lm_bisect.CommitMetadata(
                shas[1], "Unswitch producer", "", ["llvm/lib/Transforms/Scalar/SignalOnly.cpp"]
            ),
            shas[2]: lm_bisect.CommitMetadata(
                shas[2], "Unswitch producer", "", ["llvm/lib/Transforms/Scalar/Producer.cpp"]
            ),
            shas[3]: lm_bisect.CommitMetadata(
                shas[3], "unrelated", "", ["llvm/lib/Transforms/Scalar/NoSignal.cpp"]
            ),
        }
        payload = {
            "source_paths": [],
            "symbols": [],
            "pass_tokens": ["signal-only", "producer"],
            "query_terms": [{"term": "MemorySSAUpdater", "kind": "component-updater"}],
        }

        frontier = lm_bisect.build_human_frontier_pool(
            profile,
            shas,
            metadata,
            payload,
            anchor={"term": "MemorySSAUpdater", "kind": "component-updater"},
            dependency_paths=["llvm/lib/Transforms/Scalar/Producer.cpp"],
        )

        self.assertEqual(frontier["tiers"]["t1"]["count"], 4)
        self.assertEqual(frontier["tiers"]["t2"]["count"], 2)
        self.assertEqual(frontier["tiers"]["t3"]["count"], 1)
        self.assertEqual(frontier["candidate_shas"], [shas[2]])
        self.assertEqual(frontier["candidates"][0]["match_reasons"], ["keyword", "relevant-path", "high-risk-path", "crash-pass", "dependency-path"])

    def test_human_frontier_dependency_scan_matches_human_cpp_header_scope(self) -> None:
        profile = replace(demo_profile(), issue_id="pr204559", bad_commit="b" * 40)
        # The human script walks `llvm/lib` before `llvm/include`, then uses a
        # stable descending-count sort. Equal-use files therefore keep this
        # scan order at the top-20-percent cutoff.
        def fake_git(_repo, *args):
            if args[:4] != ("grep", "-c", "-F", "anchor"):
                self.fail(f"unexpected git command: {args}")
            scope = args[-1]
            if scope == "llvm/lib":
                return "\n".join(
                    [
                        f"{profile.bad_commit}:llvm/lib/Transforms/Scalar/Alpha.cpp:2",
                        f"{profile.bad_commit}:llvm/lib/Analysis/CMakeLists.txt:100",
                        f"{profile.bad_commit}:llvm/lib/Transforms/Scalar/Zeta.cpp:2",
                    ]
                )
            if scope == "llvm/include":
                return f"{profile.bad_commit}:llvm/include/llvm/Analysis/Anchor.h:1"
            self.fail(f"unexpected grep scope: {scope}")

        with mock.patch.object(lm_bisect, "git", side_effect=fake_git) as mocked_git:
            dependencies = lm_bisect.human_frontier_dependency_paths(
                Path("/repo"),
                profile,
                {"term": "anchor", "kind": "component-updater"},
            )

        self.assertEqual(
            dependencies["selected_paths"],
            ["llvm/lib/Transforms/Scalar/Alpha.cpp"],
        )
        self.assertEqual(
            [call.args[-1] for call in mocked_git.call_args_list],
            ["llvm/lib", "llvm/include"],
        )

    def test_human_frontier_selection_keeps_causal_evidence_primary(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="strong causal change",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=7.0,
                build_success_prob=0.10,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.4},
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="weaker causal change",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=6.0,
                build_success_prob=0.99,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.99},
            ),
        ]

        decision = lm_bisect.human_frontier_direct_selection(
            records,
            {"a" * 40: 0.1, "b" * 40: 0.9},
        )

        self.assertEqual(decision.selected.sha, "a" * 40)
        self.assertEqual(decision.selection_mode, "human-frontier-direct")
        self.assertEqual(decision.metadata["supporting_factors"], ["causal-confidence", "triage", "buildability", "tier-midpoint"])

    def test_human_frontier_proves_single_retained_bad_candidate_with_its_parent(self) -> None:
        unresolved = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

        after_search = lm_bisect.human_frontier_transition(
            unresolved,
            selected_sha="c" * 40,
            verdict="bad",
            phase="search",
            frontier_shas=["c" * 40],
            tested_frontier_shas=[],
        )
        after_parent = lm_bisect.human_frontier_transition(
            after_search["unresolved"],
            selected_sha="b" * 40,
            verdict="good",
            phase="parent-proof",
            frontier_shas=after_search["frontier_shas"],
            tested_frontier_shas=after_search["tested_frontier_shas"],
            candidate_sha="c" * 40,
        )

        self.assertEqual(after_search["phase"], "parent-proof")
        self.assertEqual(after_parent["phase"], "resolved")
        self.assertEqual(after_parent["unresolved"], ["c" * 40])

    def test_human_frontier_bad_probe_requires_parent_proof_even_with_other_candidates(self) -> None:
        unresolved = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

        transition = lm_bisect.human_frontier_transition(
            unresolved,
            selected_sha="c" * 40,
            verdict="bad",
            phase="search",
            frontier_shas=["b" * 40, "c" * 40],
            tested_frontier_shas=[],
        )

        self.assertEqual(transition["phase"], "parent-proof")
        self.assertEqual(transition["pending_candidate_sha"], "c" * 40)

    def test_human_signal_prior_keeps_full_interval_with_positive_floor(self) -> None:
        profile = replace(demo_profile(), issue_id="pr204559")
        shas = [chr(ord("a") + index) * 40 for index in range(4)]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(shas[0], "unrelated", "", ["clang/lib/Sema/SemaExpr.cpp"]),
            shas[1]: lm_bisect.CommitMetadata(
                shas[1], "transform", "", ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"]
            ),
            shas[2]: lm_bisect.CommitMetadata(
                shas[2], "checker", "", ["llvm/lib/Analysis/MemorySSA.cpp"]
            ),
            shas[3]: lm_bisect.CommitMetadata(shas[3], "other", "", ["llvm/lib/IR/Instructions.cpp"]),
        }

        prior = lm_bisect.build_human_signal_prior(
            profile,
            shas,
            metadata,
            {
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": ["simple-loop-unswitch"],
                "query_terms": [],
            },
        )

        self.assertEqual(prior["full_interval_count"], 4)
        self.assertEqual(set(prior["prior_by_sha"]), set(shas))
        self.assertTrue(all(score > 0.0 for score in prior["prior_by_sha"].values()))
        self.assertLess(prior["prior_by_sha"][shas[2]], prior["prior_by_sha"][shas[1]])
        self.assertFalse(prior["hard_pruning"])

    def test_human_signal_prior_applies_rare_checker_penalty_without_removing_floor(self) -> None:
        profile = replace(
            demo_profile(),
            issue_id="pr204559",
            relevant_paths=[],
            high_risk_paths=["llvm/lib/Analysis"],
        )
        shas = ["a" * 40, "b" * 40]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(
                shas[0], "checker change", "", ["llvm/lib/Analysis/MemorySSA.cpp"]
            ),
            shas[1]: lm_bisect.CommitMetadata(
                shas[1], "other analysis change", "", ["llvm/lib/Analysis/LoopInfo.cpp"]
            ),
        }

        prior = lm_bisect.build_human_signal_prior(
            profile,
            shas,
            metadata,
            {
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": [],
                "query_terms": [],
            },
        )

        self.assertGreater(prior["prior_by_sha"][shas[0]], 0.0)
        self.assertLess(prior["prior_by_sha"][shas[0]], prior["prior_by_sha"][shas[1]])

    def test_human_signal_prior_selection_clamps_weighted_midpoint(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=index + 1,
                sha=chr(ord("a") + index) * 40,
                subject=f"candidate {index}",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            )
            for index in range(10)
        ]
        priors = {record.sha: 0.1 for record in records}
        priors[records[0].sha] = 9.0

        decision = lm_bisect.human_signal_prior_selection(records, priors)

        self.assertEqual(decision.selected.sha, records[4].sha)
        self.assertEqual(decision.selection_mode, "human-signal-prior")
        self.assertEqual(decision.metadata["weighted_midpoint_sha"], records[0].sha)
        self.assertTrue(decision.metadata["chronological_clamp_applied"])
        self.assertEqual(decision.metadata["predicted_verdict"], "bad")

    def test_human_signal_prior_falls_back_after_two_direction_contradictions(self) -> None:
        state = {"phase": "prior", "consecutive_direction_contradictions": 1}

        updated, event = lm_bisect.update_human_signal_prior_after_verdict(
            state,
            predicted_verdict="bad",
            verdict="good",
        )

        self.assertTrue(event["contradiction"])
        self.assertEqual(updated["consecutive_direction_contradictions"], 2)
        self.assertEqual(updated["phase"], "fallback-bcr")
        self.assertEqual(updated["fallback_reason"], "two-prior-direction-contradictions")

    def test_human_signal_prior_uses_model_evidence_without_excluding_other_commits(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=index + 1,
                sha=chr(ord("a") + index) * 40,
                subject=f"candidate {index}",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=8.0 if index == 2 else 1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=["model-scored"] if index == 2 else [],
            )
            for index in range(5)
        ]

        decision = lm_bisect.human_signal_prior_selection(
            records,
            {record.sha: 1.0 for record in records},
        )

        self.assertEqual(decision.selected.sha, records[2].sha)
        self.assertEqual(decision.metadata["model_adjusted_candidate_count"], 1)

    def test_dynamic_human_evidence_recomputes_rare_checker_polarity_for_current_interval(self) -> None:
        profile = replace(
            demo_profile(),
            issue_id="pr204559",
            relevant_paths=[],
            high_risk_paths=[],
        )
        source_path = "llvm/lib/Analysis/MemorySSA.cpp"
        full_shas = [chr(ord("a") + index) * 40 for index in range(11)]
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha,
                f"candidate {index}",
                "",
                [source_path] if index < 10 else ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
            )
            for index, sha in enumerate(full_shas)
        }
        signals = {
            "source_paths": [source_path],
            "symbols": [],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": [],
        }

        full = lm_bisect.build_dynamic_human_evidence(
            profile,
            full_shas,
            metadata,
            signals,
            dependency_usage={},
        )
        narrowed_shas = [full_shas[0], full_shas[-1]]
        narrowed = lm_bisect.build_dynamic_human_evidence(
            profile,
            narrowed_shas,
            metadata,
            signals,
            dependency_usage={},
        )

        self.assertFalse(full["rare_checker_file"])
        self.assertTrue(narrowed["rare_checker_file"])
        self.assertIn("crash-source-file", full["candidate_by_sha"][full_shas[0]]["reasons"])
        self.assertIn(
            "rare-checker-file-penalty",
            narrowed["candidate_by_sha"][full_shas[0]]["reasons"],
        )

    def test_dynamic_human_evidence_uses_general_crash_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "crash.err"
            artifact.write_text("synthetic crash artifact\n")
            profile = replace(
                demo_profile(),
                issue_id="pr204559",
                crash_artifact=str(artifact),
            )
            parsed_payload = {
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": ["simple-loop-unswitch"],
                "query_terms": [],
            }
            parsed_signals = mock.Mock()
            parsed_signals.payload.return_value = parsed_payload
            with mock.patch.object(
                lm_bisect.crash_signals,
                "parse_crash_report",
                return_value=parsed_signals,
            ) as parse_crash_report, mock.patch.object(
                lm_bisect.crash_signals,
                "human_study_signal_payload",
            ) as human_study_signal_payload, mock.patch.object(
                lm_bisect,
                "ROOT_DIR",
                Path(tmpdir),
            ):
                payload = lm_bisect.profile_crash_signal_payload(
                    profile,
                    use_human_study_normalization=False,
                )

        parse_crash_report.assert_called_once_with("synthetic crash artifact\n")
        human_study_signal_payload.assert_not_called()
        self.assertEqual(payload["artifact_status"], "loaded")
        self.assertNotIn("normalization", payload)

    def test_artifact_complete_lookup_uses_master50_crash_report_before_scoped_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            master_artifact = (
                root
                / "human_analysis/raw/master50-evidence-20260821/cases/pr204559/crash-assertion.err"
            )
            master_artifact.parent.mkdir(parents=True)
            master_artifact.write_text("master fifty crash artifact\n")
            parsed_signals = mock.Mock()
            parsed_signals.payload.return_value = {"source_paths": [], "query_terms": []}
            with mock.patch.object(lm_bisect, "ROOT_DIR", root), mock.patch.object(
                lm_bisect.crash_signals,
                "parse_crash_report",
                return_value=parsed_signals,
            ) as parse_crash_report:
                payload = lm_bisect.profile_crash_signal_payload(
                    replace(demo_profile(), issue_id="pr204559"),
                    use_human_study_normalization=False,
                    artifact_lookup="master50",
                )

        parse_crash_report.assert_called_once_with("master fifty crash artifact\n")
        self.assertEqual(payload["artifact_status"], "loaded")
        self.assertEqual(payload["artifact_origin"], "master50-evidence")

    def test_dynamic_causal_retrieval_uses_online_crash_signal_normalization(self) -> None:
        profile = replace(demo_profile(), issue_id="pr204559")
        with mock.patch.object(
            lm_bisect,
            "profile_crash_signal_payload",
            return_value={"query_terms": []},
        ) as profile_crash_signal_payload:
            lm_bisect.causal_crash_signal_payload(
                profile,
                "causal-llm-human-dynamic",
            )

        profile_crash_signal_payload.assert_called_once_with(
            profile,
            use_human_study_normalization=False,
        )

    def test_causal_prompt_explains_dynamic_checker_and_producer_rules(self) -> None:
        prompt = lm_bisect.build_causal_diff_extraction_prompt(
            demo_profile(),
            {
                "sha": "a" * 40,
                "subject": "candidate",
                "causal_retrieval": {
                    "crash_signals": {},
                    "dynamic_interval_evidence": {
                        "rare_checker_file": True,
                        "candidate": {
                            "reasons": ["rare-checker-file-penalty", "dependency-api-use"]
                        },
                    },
                },
            },
        )

        self.assertIn("rare checker", prompt)
        self.assertIn("independent producer, API-use, stack, pass", prompt)

    def test_dynamic_human_evidence_keeps_every_current_candidate_eligible(self) -> None:
        profile = replace(demo_profile(), issue_id="pr204559")
        shas = [chr(ord("a") + index) * 40 for index in range(4)]
        metadata = {
            shas[0]: lm_bisect.CommitMetadata(shas[0], "unrelated", "", ["clang/lib/Sema/SemaExpr.cpp"]),
            shas[1]: lm_bisect.CommitMetadata(shas[1], "pass", "", ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"]),
            shas[2]: lm_bisect.CommitMetadata(shas[2], "checker", "", ["llvm/lib/Analysis/MemorySSA.cpp"]),
            shas[3]: lm_bisect.CommitMetadata(shas[3], "other", "", ["llvm/lib/IR/Instructions.cpp"]),
        }

        evidence = lm_bisect.build_dynamic_human_evidence(
            profile,
            shas,
            metadata,
            {
                "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                "symbols": [],
                "pass_tokens": ["simple-loop-unswitch"],
                "query_terms": [],
            },
            dependency_usage={},
        )

        self.assertEqual(set(evidence["prior_by_sha"]), set(shas))
        self.assertEqual(set(evidence["prior_probability_by_sha"]), set(shas))
        self.assertTrue(all(value > 0.0 for value in evidence["prior_probability_by_sha"].values()))
        self.assertFalse(evidence["hard_pruning"])

    def test_dynamic_human_frontier_is_evidence_led_with_bounded_support(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=index + 1,
                sha=chr(ord("a") + index) * 40,
                subject=f"candidate {index}",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.20 + 0.10 * index,
                suspicion_weight=0.0,
            )
            for index in range(6)
        ]
        evidence = {
            "prior_by_sha": {
                record.sha: float(6 - record.index)
                for record in records
            },
            "candidate_by_sha": {
                record.sha: {"sha": record.sha, "index": record.index, "prior_score": float(6 - record.index), "reasons": ["crash-query-term"]}
                for record in records
            },
        }

        decision = lm_bisect.resolve_dynamic_human_evidence_frontier(
            records,
            evidence,
            target_count=5,
        )

        self.assertEqual(decision.effective_frontier, "dynamic-human-evidence")
        self.assertEqual(decision.selected_shas[0], records[0].sha)
        self.assertEqual(len(decision.selected_shas), 5)
        self.assertIn("evidence-top", {item["role"] for item in decision.role_assignments})
        self.assertIn("prior-mass-midpoint", {item["role"] for item in decision.role_assignments})
        self.assertIn("buildability-support", {item["role"] for item in decision.role_assignments})

    def test_dynamic_human_evidence_changes_model_score_cache_context(self) -> None:
        arguments = (
            ["a" * 40, "b" * 40],
            [],
            12,
            "topk",
            "parent",
            "causal-llm-human-dynamic",
            "trace-only",
        )
        first = lm_bisect.model_score_context_payload(
            *arguments,
            dynamic_human_evidence={"evidence_sha256": "first"},
        )
        second = lm_bisect.model_score_context_payload(
            *arguments,
            dynamic_human_evidence={"evidence_sha256": "second"},
        )

        self.assertNotEqual(
            lm_bisect.model_score_context_id(first),
            lm_bisect.model_score_context_id(second),
        )

    def test_dynamic_human_evidence_history_preserves_current_interval_provenance(self) -> None:
        payload = lm_bisect.dynamic_human_evidence_history_payload(
            {
                "candidate_window_sha256": "window-digest",
                "crash_file_touch_count": 5,
                "rare_checker_file": True,
                "prior_floor": 0.25,
                "candidate_evidence": [
                    {"sha": "a" * 40, "index": 1, "prior_score": 0.25, "reasons": []}
                ],
                "dependency_usage_sha256": "dependency-digest",
            }
        )

        self.assertEqual(payload["candidate_window_sha256"], "window-digest")
        self.assertEqual(payload["candidate_evidence"][0]["sha"], "a" * 40)
        self.assertTrue(payload["rare_checker_file"])

    def test_dynamic_human_evidence_requires_isolated_terra_k12_parent_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "--model-top-k 12"):
            lm_bisect.validate_dynamic_human_evidence_run_config(
                issue_id="pr204559",
                scorer="model",
                model_diff_mode="parent",
                model_top_k=3,
                run_label="dynamic",
                model_cache_namespace="dynamic-cache",
            )

    def test_dynamic_human_evidence_runtime_records_only_surviving_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = [chr(ord("a") + index) * 40 for index in range(6)]
            profile = replace(demo_profile(), issue_id="pr204559")
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "pr204559",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "model",
                    "--model-name",
                    "test-model",
                    "--model-top-k",
                    "12",
                    "--model-cache-namespace",
                    "dynamic-runtime-cache",
                    "--model-diff-mode",
                    "parent",
                    "--model-diff-extraction",
                    "causal-llm-human-dynamic",
                    "--search-policy",
                    "calibrated-posterior",
                    "--observations",
                    str(observations),
                    "--run-label",
                    "dynamic-runtime",
                    "--max-steps",
                    "2",
                ]
            )

            def records_for_window(*_args, **kwargs):
                candidate_shas = kwargs["candidate_shas"]
                dynamic = kwargs["dynamic_human_evidence"]
                decision = lm_bisect.resolve_dynamic_human_evidence_frontier(
                    [
                        lm_bisect.CommitRecord(
                            index=index + 1,
                            sha=sha,
                            subject=f"candidate {index}",
                            body="",
                            changed_files=[],
                            diff_text="",
                            semantic_score=1.0,
                            build_success_prob=0.9,
                            suspicion_weight=0.0,
                        )
                        for index, sha in enumerate(candidate_shas)
                    ],
                    dynamic,
                    target_count=min(12, len(candidate_shas)),
                )
                kwargs["model_frontier_decision_out"].append(decision)
                return (
                    [
                        lm_bisect.CommitRecord(
                            index=index + 1,
                            sha=sha,
                            subject=f"candidate {index}",
                            body="",
                            changed_files=[],
                            diff_text="",
                            semantic_score=1.0,
                            build_success_prob=0.9,
                            suspicion_weight=0.0,
                            evidence=["model-scored"] if sha in decision.selected_shas else [],
                        )
                        for index, sha in enumerate(candidate_shas)
                    ],
                    {"before_count": len(candidate_shas), "after_count": len(candidate_shas), "applied": False},
                )

            metadata = {
                sha: lm_bisect.CommitMetadata(
                    sha,
                    f"candidate {index}",
                    "",
                    ["llvm/lib/Analysis/MemorySSA.cpp"]
                    if index < 5
                    else ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                )
                for index, sha in enumerate(shas)
            }
            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=profile
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect,
                "load_model_config",
                return_value=lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test-model"),
            ), mock.patch.object(
                lm_bisect, "load_model_cache", return_value={}
            ), mock.patch.object(
                lm_bisect, "profile_crash_signal_payload", return_value={
                    "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
                    "symbols": [],
                    "pass_tokens": ["simple-loop-unswitch"],
                    "query_terms": [],
                }
            ) as profile_crash_signal_payload, mock.patch.object(
                lm_bisect, "dynamic_dependency_usage", return_value={}
            ), mock.patch.object(
                lm_bisect, "load_commit_metadata", return_value=metadata
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=records_for_window
            ), mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                side_effect=[
                    ("bad", "first probe bad", "", []),
                    ("good", "second probe good", "", []),
                ],
            ), mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        profile_crash_signal_payload.assert_called_once_with(
            profile,
            use_human_study_normalization=False,
        )
        self.assertEqual(len(saved_history["steps"]), 2)
        first, second = saved_history["steps"]
        self.assertEqual(first["dynamic_human_evidence"]["current_interval_count"], 6)
        self.assertLess(second["dynamic_human_evidence"]["current_interval_count"], 6)
        surviving = {
            entry["sha"] for entry in second["dynamic_human_evidence"]["candidate_evidence"]
        }
        self.assertNotIn(shas[-1], surviving)
        with self.assertRaisesRegex(ValueError, "five-case crash-signal cohort"):
            lm_bisect.validate_dynamic_human_evidence_run_config(
                issue_id="pr193164",
                scorer="model",
                model_diff_mode="parent",
                model_top_k=12,
                run_label="dynamic",
                model_cache_namespace="dynamic-cache",
            )

    def test_human_signal_prior_runtime_falls_back_after_two_wrong_directions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = [chr(ord("a") + index) * 40 for index in range(17)]
            profile = replace(demo_profile(), issue_id="pr204559")
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "pr204559",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "model",
                    "--model-name",
                    "test-model",
                    "--model-top-k",
                    "3",
                    "--model-cache-namespace",
                    "human-prior-runtime-test-cache",
                    "--model-diff-mode",
                    "parent",
                    "--model-diff-extraction",
                    "causal-llm-human-prior",
                    "--search-policy",
                    "calibrated-posterior",
                    "--observations",
                    str(observations),
                    "--run-label",
                    "human-prior-runtime-test",
                    "--max-steps",
                    "3",
                ]
            )

            def records_for_window(*_args, **kwargs):
                candidate_shas = kwargs["candidate_shas"]
                return (
                    [
                        lm_bisect.CommitRecord(
                            index=index + 1,
                            sha=sha,
                            subject=f"candidate {index}",
                            body="",
                            changed_files=[],
                            diff_text="",
                            semantic_score=1.0,
                            build_success_prob=0.9,
                            suspicion_weight=0.0,
                        )
                        for index, sha in enumerate(candidate_shas)
                    ],
                    {"before_count": len(candidate_shas), "after_count": len(candidate_shas), "applied": False},
                )

            prior_payload = {
                "full_interval_count": len(shas),
                "prior_floor": 0.25,
                "prior_by_sha": {sha: 1.0 for sha in shas},
                "candidates": [],
            }
            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=profile
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect,
                "load_model_config",
                return_value=lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test-model"),
            ), mock.patch.object(
                lm_bisect, "load_model_cache", return_value={}
            ), mock.patch.object(
                lm_bisect, "profile_crash_signal_payload", return_value={}
            ), mock.patch.object(
                lm_bisect, "human_dependency_anchor_files", return_value={}
            ), mock.patch.object(
                lm_bisect,
                "load_commit_metadata",
                return_value={sha: lm_bisect.CommitMetadata(sha, "", "", []) for sha in shas},
            ), mock.patch.object(
                lm_bisect, "build_human_signal_prior", return_value=prior_payload
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=records_for_window
            ), mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                side_effect=[
                    ("good", "first probe good", "", []),
                    ("good", "second probe good", "", []),
                    ("bad", "fallback probe bad", "", []),
                ],
            ), mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertEqual(saved_history["human_signal_prior"]["phase"], "fallback-bcr")
        self.assertEqual(
            saved_history["human_signal_prior"]["fallback_reason"],
            "two-prior-direction-contradictions",
        )
        self.assertEqual(
            [step["selection_mode"] for step in saved_history["steps"]],
            ["human-signal-prior", "human-signal-prior", "calibrated-posterior"],
        )

    def test_human_signal_pool_triage_keeps_only_model_returned_frontier(self) -> None:
        profile = replace(demo_profile(), issue_id="pr204559")
        candidates = [
            {"sha": "a" * 40, "index": 1, "subject": "first", "changed_files": [], "match_reasons": [], "prior_score": 1.0},
            {"sha": "b" * 40, "index": 2, "subject": "selected", "changed_files": [], "match_reasons": [], "prior_score": 1.0},
        ]
        response = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content=json.dumps(
                            [{"sha": "b" * 40, "triage_score": 0.8, "evidence": ["path"], "mechanism": "producer"}]
                        )
                    )
                )
            ]
        )
        config = lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test")

        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = lambda **_kwargs: object()
        with mock.patch.dict(sys.modules, {"openai": fake_openai}), mock.patch.object(
            lm_bisect, "model_completion_with_retry", return_value=response
        ):
            ranked = lm_bisect.human_signal_pool_triage_with_model(profile, {}, candidates, config)

        self.assertEqual([entry["sha"] for entry in ranked], ["b" * 40])

    def test_parser_accepts_human_signal_pool_causal_extraction(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--scorer",
                "model",
                "--model-diff-extraction",
                "causal-llm-human-pool",
            ]
        )

        self.assertEqual(args.model_diff_extraction, "causal-llm-human-pool")

    def test_parser_accepts_human_signal_prior_causal_extraction(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--scorer",
                "model",
                "--model-diff-extraction",
                "causal-llm-human-prior",
            ]
        )

        self.assertEqual(args.model_diff_extraction, "causal-llm-human-prior")

    def test_human_signal_pool_requires_isolated_model_parent_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "scorer=model"):
            lm_bisect.validate_human_signal_pool_run_config(
                issue_id="pr204559",
                scorer="heuristic",
                model_diff_mode="parent",
                run_label="pilot",
                model_cache_namespace="pilot-cache",
            )
        with self.assertRaisesRegex(ValueError, "parent diffs"):
            lm_bisect.validate_human_signal_pool_run_config(
                issue_id="pr204559",
                scorer="model",
                model_diff_mode="last-tested",
                run_label="pilot",
                model_cache_namespace="pilot-cache",
            )
        with self.assertRaisesRegex(ValueError, "retrospective good-signal cohort"):
            lm_bisect.validate_human_signal_pool_run_config(
                issue_id="pr193164",
                scorer="model",
                model_diff_mode="parent",
                run_label="pilot",
                model_cache_namespace="pilot-cache",
            )
        with self.assertRaisesRegex(ValueError, "distinct --run-label"):
            lm_bisect.validate_human_signal_pool_run_config(
                issue_id="pr204559",
                scorer="model",
                model_diff_mode="parent",
                run_label=None,
                model_cache_namespace="pilot-cache",
            )
        with self.assertRaisesRegex(ValueError, "distinct --model-cache-namespace"):
            lm_bisect.validate_human_signal_pool_run_config(
                issue_id="pr204559",
                scorer="model",
                model_diff_mode="parent",
                run_label="pilot",
                model_cache_namespace=None,
            )

    def test_human_signal_prior_requires_isolated_model_parent_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "scorer=model"):
            lm_bisect.validate_human_signal_prior_run_config(
                issue_id="pr204559",
                scorer="heuristic",
                model_diff_mode="parent",
                run_label="prior",
                model_cache_namespace="prior-cache",
            )
        with self.assertRaisesRegex(ValueError, "parent diffs"):
            lm_bisect.validate_human_signal_prior_run_config(
                issue_id="pr204559",
                scorer="model",
                model_diff_mode="last-tested",
                run_label="prior",
                model_cache_namespace="prior-cache",
            )
        with self.assertRaisesRegex(ValueError, "retrospective good-signal cohort"):
            lm_bisect.validate_human_signal_prior_run_config(
                issue_id="pr193164",
                scorer="model",
                model_diff_mode="parent",
                run_label="prior",
                model_cache_namespace="prior-cache",
            )

    def test_human_signal_prior_online_run_rejects_nonposterior_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "pr204559",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "model",
                    "--model-diff-extraction",
                    "causal-llm-human-prior",
                    "--model-cache-namespace",
                    "prior-cache",
                    "--run-label",
                    "prior",
                    "--search-policy",
                    "ranked",
                ]
            )
            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=replace(demo_profile(), issue_id="pr204559")
            ):
                with self.assertRaisesRegex(ValueError, "requires --search-policy calibrated-posterior"):
                    lm_bisect.command_run_online(args)

    def test_parser_accepts_human_guided_causal_extraction(self) -> None:
        args = lm_bisect.build_parser().parse_args(
            [
                "run-online",
                "--issue",
                "demo",
                "--scorer",
                "model",
                "--model-diff-extraction",
                "causal-llm-human",
            ]
        )

        self.assertEqual(args.model_diff_extraction, "causal-llm-human")

    def test_causal_extraction_prompt_requires_structured_linkage_without_raw_diff_prefix(self) -> None:
        profile = demo_profile()
        item = {
            "sha": "a" * 40,
            "subject": "Vectorize recipe update",
            "files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            "diff": "RAW_DIFF_PREFIX_MUST_NOT_APPEAR",
            "causal_retrieval": {
                "selected_files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                "selected_hunks": [
                    {
                        "path": "llvm/lib/Transforms/Vectorize/VPlan.cpp",
                        "match_reasons": ["relevant-path"],
                        "symbols": ["VPlan::buildRecipe"],
                        "patch": "+return NewRecipe;",
                    }
                ],
                "function_contexts": [
                    {
                        "path": "llvm/lib/Transforms/Vectorize/VPlan.cpp",
                        "symbol": "VPlan::buildRecipe",
                        "context": "void VPlan::buildRecipe() { return NewRecipe; }",
                    }
                ],
                "omitted_hunk_count": 4,
                "raw_diff_chars": 1000,
                "raw_diff_truncated": False,
            },
        }

        prompt = lm_bisect.build_causal_diff_extraction_prompt(profile, item)

        self.assertIn('"changed_symbols"', prompt)
        self.assertIn('"behavioral_change"', prompt)
        self.assertIn('"issue_link"', prompt)
        self.assertIn('"confidence"', prompt)
        self.assertIn("VPlan::buildRecipe", prompt)
        self.assertNotIn("RAW_DIFF_PREFIX_MUST_NOT_APPEAR", prompt)
        self.assertNotIn("Diff excerpt:", prompt)

    def test_normalized_causal_evidence_preserves_inspectable_artifact_and_features(self) -> None:
        item = {
            "sha": "a" * 40,
            "causal_retrieval": {
                "selected_files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
                "selected_hunks": [],
                "function_contexts": [],
                "omitted_hunk_count": 2,
                "raw_diff_chars": 1234,
                "raw_diff_truncated": False,
            },
        }
        payload = lm_bisect.normalize_causal_diff_evidence(
            item,
            {
                "summary": "Changes vector recipe construction.",
                "changed_symbols": ["VPlan::buildRecipe"],
                "behavioral_change": ["selects a new vector recipe"],
                "issue_link": {
                    "assertion_or_trace": ["VPlan assertion"],
                    "reproducer": ["vector loop reproducer"],
                    "pass_or_subsystem": ["LoopVectorize"],
                    "explanation": "The changed recipe path can reach the asserted invariant.",
                },
                "confidence": 0.8,
                "build_risk": ["none"],
            },
        )

        self.assertEqual(payload["changed_symbols"], ["VPlan::buildRecipe"])
        self.assertEqual(payload["issue_link"]["pass_or_subsystem"], ["LoopVectorize"])
        self.assertEqual(payload["confidence"], 0.8)
        self.assertIn("term:vplanbuildrecipe", lm_bisect.causal_evidence_features(payload))
        formatted = lm_bisect.format_causal_diff_evidence(payload)
        self.assertIn("Causal confidence: 0.80", formatted)
        self.assertIn("LoopVectorize", formatted)

    def test_causal_summary_keeps_dynamic_interval_provenance_for_scoring(self) -> None:
        formatted = lm_bisect.format_causal_diff_evidence(
            {
                "summary": "candidate change",
                "confidence": 0.5,
                "retrieval": {
                    "dynamic_interval_evidence": {
                        "rare_checker_file": True,
                        "candidate": {
                            "prior_score": 3.25,
                            "reasons": ["crash-pass", "dependency-api-use"],
                        },
                    }
                },
            }
        )

        self.assertIn("Dynamic interval evidence: rare-checker=true", formatted)
        self.assertIn("crash-pass, dependency-api-use", formatted)

    def test_model_scoring_prompt_treats_dynamic_interval_evidence_as_soft_prior(self) -> None:
        prompt = lm_bisect.build_model_scoring_prompt(
            demo_profile(),
            [
                {
                    "sha": "a" * 40,
                    "subject": "candidate",
                    "body": "",
                    "files": [],
                    "diff_summary": "Dynamic interval evidence: rare-checker=true",
                    "diff_extraction": "causal-llm-human-dynamic",
                }
            ],
        )

        self.assertIn("dynamic interval evidence, it is a soft prior", prompt)
        self.assertIn("checker-only touch", prompt)

    def test_causal_extraction_fallback_preserves_retrieval_evidence(self) -> None:
        retrieval = {
            "selected_files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            "selected_hunks": [
                {
                    "symbols": ["VPlan::buildRecipe", "LoopVectorize"],
                    "source_kind": "implementation",
                }
            ],
        }
        item = {
            "subject": "Adjust VPlan recipe construction",
            "causal_retrieval": retrieval,
        }

        payload = lm_bisect.local_causal_diff_evidence_fallback(item, ValueError("malformed JSON"))

        self.assertEqual(payload["confidence"], 0.0)
        self.assertEqual(payload["changed_symbols"], ["VPlan::buildRecipe", "LoopVectorize"])
        self.assertEqual(payload["retrieval"], retrieval)
        self.assertEqual(payload["fallback"]["mode"], "local-retrieval")
        self.assertIn("ValueError", payload["fallback"]["reason"])

    def test_model_completion_retries_transient_server_error(self) -> None:
        class TransientError(Exception):
            status_code = 503

        calls = 0

        class FakeCompletions:
            def create(self, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise TransientError("temporary overload")
                return "recovered"

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        with mock.patch.object(lm_bisect.time, "sleep") as sleep:
            response = lm_bisect.model_completion_with_retry(client, config, "prompt", "diff extraction")

        self.assertEqual(response, "recovered")
        self.assertEqual(calls, 2)
        sleep.assert_called_once_with(lm_bisect.DEFAULT_MODEL_RETRY_BACKOFF_SECONDS)

    def test_model_completion_does_not_retry_permanent_client_error(self) -> None:
        class PermanentError(Exception):
            status_code = 403

        class FakeCompletions:
            def create(self, **_kwargs):
                raise PermanentError("invalid key")

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        with mock.patch.object(lm_bisect.time, "sleep") as sleep:
            with self.assertRaises(PermanentError):
                lm_bisect.model_completion_with_retry(client, config, "prompt", "scoring")

        sleep.assert_not_called()

    def test_model_completion_sends_requested_reasoning_effort(self) -> None:
        captured: dict[str, object] = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return "ok"

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.6-terra",
            reasoning_effort="high",
        )

        response = lm_bisect.model_completion_with_retry(client, config, "prompt", "scoring")

        self.assertEqual(response, "ok")
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertNotIn("temperature", captured)

    def test_extract_diff_evidence_batch_falls_back_when_response_has_no_choices(self) -> None:
        profile = demo_profile()
        items = [
            {
                "sha": "a" * 40,
                "subject": "candidate a",
                "files": ["a.cpp"],
                "diff": "+a",
            },
            {
                "sha": "b" * 40,
                "subject": "candidate b",
                "files": ["b.cpp"],
                "diff": "+b",
            },
        ]
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        class FakeCompletions:
            def create(self, **_kwargs):
                return types.SimpleNamespace(choices=None)

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        fake_openai = types.SimpleNamespace(OpenAI=FakeOpenAI)

        with mock.patch.dict(sys.modules, {"openai": fake_openai}), mock.patch.object(
            lm_bisect,
            "extract_diff_evidence_with_model",
            side_effect=lambda _profile, item, _config, _usage_summary=None: f"fallback {item['sha'][:1]}",
        ) as fallback:
            extracted = lm_bisect.extract_diff_evidence_batch_with_model(
                profile,
                items,
                model_config,
                batch_size=20,
            )

        self.assertEqual(extracted, {"a" * 40: "fallback a", "b" * 40: "fallback b"})
        self.assertEqual(fallback.call_count, 2)

    def test_model_cache_path_includes_scoring_version(self) -> None:
        path = lm_bisect.model_cache_path("pr172195", "gpt-5.4-mini")
        self.assertTrue(str(path).endswith("pr172195-gpt-5.4-mini-v7-first-bad-risk-guidance.json"))

    def test_resolved_model_scoring_version_separates_trace_only_mode(self) -> None:
        self.assertEqual(lm_bisect.resolved_model_scoring_version("legacy"), "v7-first-bad-risk-guidance")
        self.assertEqual(
            lm_bisect.resolved_model_scoring_version("trace-only"),
            "v7-first-bad-risk-guidance-obs-trace-only",
        )


class BuildFailureSummaryTests(unittest.TestCase):
    def test_extract_build_failure_summary_from_ninja_compile_error(self) -> None:
        output = """
[5/1557] Building CXX object lib/Demangle/CMakeFiles/LLVMDemangle.dir/MicrosoftDemangle.cpp.o
/usr/bin/c++ -c /repo/llvm/lib/Demangle/MicrosoftDemangle.cpp
In file included from ../llvm/lib/Demangle/MicrosoftDemangle.cpp:16:
../llvm/include/llvm/Demangle/MicrosoftDemangleNodes.h:259:8: error: 'string' in namespace 'std' does not name a type
../llvm/include/llvm/Demangle/MicrosoftDemangleNodes.h:19:1: note: 'std::string' is defined in header '<string>'; did you forget to '#include <string>'?
ninja: build stopped: subcommand failed.
build failed; skipping commit
"""

        summary = lm_bisect.extract_build_failure_summary(output)

        self.assertEqual(summary["phase"], "build")
        self.assertEqual(summary["ninja_edge"], "5/1557")
        self.assertEqual(summary["failed_target"], "LLVMDemangle")
        self.assertEqual(summary["failed_source"], "llvm/lib/Demangle/MicrosoftDemangle.cpp")
        self.assertEqual(summary["failed_header"], "llvm/include/llvm/Demangle/MicrosoftDemangleNodes.h")
        self.assertEqual(summary["missing_include"], "<string>")
        self.assertIn("std", summary["primary_error"])

    def test_extract_build_failure_summary_from_cmake_error(self) -> None:
        output = """
CMake Error: CMAKE_C_COMPILER not set, after EnableLanguage
CMake Error at /usr/share/cmake-3.28/Modules/CheckSymbolExists.cmake:140 (try_compile):
  Failed to configure test project build system.
configure failed; skipping commit
"""

        summary = lm_bisect.extract_build_failure_summary(output)

        self.assertEqual(summary["phase"], "configure")
        self.assertEqual(summary["primary_error"], "CMake Error: CMAKE_C_COMPILER not set, after EnableLanguage")
        self.assertIn("CheckSymbolExists.cmake:140", summary["cmake_stack"])

    def test_skip_output_can_be_enriched_from_runner_log_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_results_dir = lm_bisect.DEFAULT_ISSUE_RESULTS_DIR
            original_run_id = os.environ.get("RUN_ID")
            lm_bisect.DEFAULT_ISSUE_RESULTS_DIR = Path(tmpdir)
            os.environ["RUN_ID"] = "unit-test"
            try:
                profile = lm_bisect.IssueProfile(
                    issue_id="pr-unit",
                    issue_url="https://example.invalid/pr-unit",
                    title="unit",
                    good_commit="a" * 40,
                    good_ref="unit-good",
                    bad_commit="b" * 40,
                    bisect_log="results/pr-unit.log",
                    runner="runner.sh",
                    bug_report_summary="unit",
                    relevant_paths=[],
                    high_risk_paths=[],
                    keywords=[],
                )
                log_dir = Path(tmpdir) / "pr-unit"
                log_dir.mkdir(parents=True)
                log_path = log_dir / "pr-unit-git-bisect-runner-unit-test.log"
                log_path.write_text(
                    """
=== validated-bisect-runner ===
FAILED: [code=1] lib/Support/CMakeFiles/LLVMSupport.dir/Signals.cpp.o
../llvm/include/llvm/Support/Signals.h:119:24: error: 'uintptr_t' was not declared in this scope
../llvm/include/llvm/Support/Signals.h:18:1: note: 'uintptr_t' is defined in header '<cstdint>'
ninja: build stopped: subcommand failed.
build failed; skipping commit
"""
                )

                enriched = lm_bisect.append_runner_log_tail(
                    "=== validated-bisect-runner ===\nbuild failed; skipping commit",
                    profile,
                    "skip",
                )
            finally:
                lm_bisect.DEFAULT_ISSUE_RESULTS_DIR = original_results_dir
                if original_run_id is None:
                    os.environ.pop("RUN_ID", None)
                else:
                    os.environ["RUN_ID"] = original_run_id

        self.assertIn("runner log tail", enriched)
        self.assertIn("uintptr_t", enriched)
        self.assertIn("LLVMSupport", enriched)
        summary = lm_bisect.extract_build_failure_summary(enriched)
        self.assertEqual(summary["failed_target"], "LLVMSupport")
        self.assertEqual(summary["failed_header"], "llvm/include/llvm/Support/Signals.h")
        self.assertEqual(summary["missing_include"], "<cstdint>")


class CandidateFileTests(unittest.TestCase):
    def test_load_candidate_commits_from_file_preserves_order(self) -> None:
        commits = lm_bisect.load_candidate_commits_from_file(
            lm_bisect.ROOT_DIR / "results" / "issues" / "pr172195" / "pr172195-email-commits-subset.json"
        )
        self.assertEqual(len(commits), 4)
        self.assertEqual(commits[0], "86c5539aa89ac61058e3ba4fc0ae578c2879bf9e")

    def test_load_candidate_commits_from_file_accepts_plain_sha_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "window.json"
            path.write_text(
                '[\n'
                '  "a000000000000000000000000000000000000000",\n'
                '  "b000000000000000000000000000000000000000"\n'
                ']\n'
            )
            commits = lm_bisect.load_candidate_commits_from_file(path)

        self.assertEqual(
            commits,
            [
                "a000000000000000000000000000000000000000",
                "b000000000000000000000000000000000000000",
            ],
        )

    def test_load_candidate_commits_from_file_deduplicates_in_linear_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "window.json"
            path.write_text(
                json.dumps(
                    [
                        {"sha": "a000000000000000000000000000000000000000"},
                        {"sha": "b000000000000000000000000000000000000000"},
                        {"sha": "a000000000000000000000000000000000000000"},
                    ]
                )
            )
            commits = lm_bisect.load_candidate_commits_from_file(path)

        self.assertEqual(
            commits,
            [
                "a000000000000000000000000000000000000000",
                "b000000000000000000000000000000000000000",
            ],
        )

    def test_method_label_includes_nondefault_heuristic_version(self) -> None:
        label = lm_bisect.method_label(
            scorer="model",
            model_name="gpt-5.4-mini",
            search_policy="ranked",
            model_frontier="topk",
            candidate_pruning="off",
            heuristic_version="v1",
        )
        self.assertIn("hv1", label)


class CandidatePruningTests(unittest.TestCase):
    def test_conservative_pruning_drops_obviously_irrelevant_commit(self) -> None:
        profile = lm_bisect.load_profiles()["pr172195"]
        shas = ["a" * 40, "b" * 40, "c" * 40]
        metadata = {
            "a" * 40: lm_bisect.CommitMetadata(
                sha="a" * 40,
                subject="docs only",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
            ),
            "b" * 40: lm_bisect.CommitMetadata(
                sha="b" * 40,
                subject="build logic",
                body="",
                changed_files=["llvm/CMakeLists.txt"],
            ),
            "c" * 40: lm_bisect.CommitMetadata(
                sha="c" * 40,
                subject="vectorizer change",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan/HLP.cpp"],
            ),
        }

        kept, summary = lm_bisect.apply_candidate_pruning(profile, shas, metadata, "conservative")

        self.assertEqual(kept, ["b" * 40, "c" * 40])
        self.assertEqual(summary["before_count"], 3)
        self.assertEqual(summary["after_count"], 2)
        self.assertEqual(summary["pruned_count"], 1)
        self.assertEqual(summary["pruned_examples"][0]["sha"], "a" * 40)

    def test_conservative_pruning_keeps_ambiguous_core_llvm_path(self) -> None:
        profile = lm_bisect.load_profiles()["pr172195"]
        shas = ["a" * 40]
        metadata = {
            "a" * 40: lm_bisect.CommitMetadata(
                sha="a" * 40,
                subject="object layer update",
                body="",
                changed_files=["llvm/lib/Object/IRSymtab.cpp"],
            )
        }

        kept, summary = lm_bisect.apply_candidate_pruning(profile, shas, metadata, "conservative")

        self.assertEqual(kept, ["a" * 40])
        self.assertEqual(summary["pruned_count"], 0)
        self.assertGreaterEqual(summary["kept_reason_counts"].get("ambiguous-core", 0), 1)

    def test_conservative_pruning_uses_issue_specific_target_closure(self) -> None:
        profile = lm_bisect.load_profiles()["pr187875"]
        shas = ["a" * 40, "b" * 40]
        metadata = {
            "a" * 40: lm_bisect.CommitMetadata(
                sha="a" * 40,
                subject="clang driver only",
                body="",
                changed_files=["clang/lib/Driver/ToolChains/Clang.cpp"],
            ),
            "b" * 40: lm_bisect.CommitMetadata(
                sha="b" * 40,
                subject="loop vectorizer",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            ),
        }

        kept, summary = lm_bisect.apply_candidate_pruning(profile, shas, metadata, "conservative")

        self.assertEqual(kept, ["b" * 40])
        self.assertEqual(summary["pruned_count"], 1)
        self.assertEqual(summary["prune_reason_counts"]["outside-target-closure"], 1)

    def test_candidate_pruning_off_preserves_full_window(self) -> None:
        profile = lm_bisect.load_profiles()["pr172195"]
        shas = ["a" * 40]
        metadata = {
            "a" * 40: lm_bisect.CommitMetadata(
                sha="a" * 40,
                subject="docs only",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
            )
        }

        kept, summary = lm_bisect.apply_candidate_pruning(profile, shas, metadata, "off")

        self.assertEqual(kept, shas)
        self.assertEqual(summary["pruned_count"], 0)
        self.assertEqual(summary["mode"], "off")


class SimulationHelpersTests(unittest.TestCase):
    def test_partition_interval_good_bad_skip(self) -> None:
        commits = ["a", "b", "c", "d"]
        self.assertEqual(lm_bisect.partition_interval(commits, "b", "good"), ["c", "d"])
        self.assertEqual(lm_bisect.partition_interval(commits, "c", "bad"), ["a", "b", "c"])
        self.assertEqual(lm_bisect.partition_interval(commits, "b", "skip"), ["a", "c", "d"])

    def test_select_non_noop_candidate_skips_cached_bad_boundary(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="c" * 40,
                subject="known bad boundary",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="candidate two",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.8,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=-0.01,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="c" * 40,
                subject="candidate three",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.7,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=-0.02,
            ),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="c" * 40,
                verdict="bad",
                summary="cached bad",
                features=[],
            )
        ]

        selected, cached = lm_bisect.select_non_noop_candidate(
            records,
            observations,
            ["a" * 40, "b" * 40, "c" * 40],
        )

        self.assertEqual(selected.sha, "b" * 40)
        self.assertIsNone(cached)

    def test_select_non_noop_candidate_keeps_cached_commit_when_it_shrinks_window(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="a" * 40,
                subject="known good boundary",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=0.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="candidate two",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.8,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=-0.01,
            ),
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="good",
                summary="cached good",
                features=[],
            )
        ]

        selected, cached = lm_bisect.select_non_noop_candidate(
            records,
            observations,
            ["a" * 40, "b" * 40],
        )

        self.assertEqual(selected.sha, "a" * 40)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.verdict, "good")

    def test_select_non_noop_candidate_rejects_cached_endpoint_noop(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="known bad endpoint",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=1.0,
            )
        ]
        observations = [
            lm_bisect.CommitObservation(
                sha="b" * 40,
                verdict="bad",
                summary="cached bad endpoint",
                features=[],
            )
        ]

        with self.assertRaises(lm_bisect.NoProgressCandidateError):
            lm_bisect.select_non_noop_candidate(
                records,
                observations,
                ["a" * 40, "b" * 40],
            )

    def test_select_non_noop_candidate_skips_uncached_bad_endpoint(self) -> None:
        records = [
            lm_bisect.CommitRecord(
                index=1,
                sha="c" * 40,
                subject="bad endpoint",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=5.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=1.0,
            ),
            lm_bisect.CommitRecord(
                index=2,
                sha="b" * 40,
                subject="middle candidate",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.8,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=0.2,
            ),
            lm_bisect.CommitRecord(
                index=3,
                sha="a" * 40,
                subject="good endpoint",
                body="",
                changed_files=[],
                diff_text="",
                semantic_score=0.2,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                selection_score=0.1,
            ),
        ]

        selected, cached = lm_bisect.select_non_noop_candidate(
            records,
            [],
            ["a" * 40, "b" * 40, "c" * 40],
        )

        self.assertEqual(selected.sha, "b" * 40)
        self.assertIsNone(cached)

    def test_apply_cached_interval_prepass_trims_good_bad_and_skip(self) -> None:
        commits = ["a", "b", "c", "d", "e"]
        observations = [
            lm_bisect.CommitObservation(sha="b", verdict="good", summary="known good", features=[]),
            lm_bisect.CommitObservation(sha="d", verdict="bad", summary="known bad", features=[]),
            lm_bisect.CommitObservation(sha="c", verdict="skip", summary="known skip", features=[]),
        ]
        pruned, events, contradiction = lm_bisect.apply_cached_interval_prepass(commits, observations)
        self.assertFalse(contradiction)
        self.assertEqual(pruned, ["d"])
        self.assertEqual([event["type"] for event in events], ["cached-good-cut", "cached-bad-cut", "cached-skip-drop"])

    def test_apply_cached_interval_prepass_detects_contradiction(self) -> None:
        commits = ["a", "b", "c", "d"]
        observations = [
            lm_bisect.CommitObservation(sha="b", verdict="bad", summary="early bad", features=[]),
            lm_bisect.CommitObservation(sha="d", verdict="good", summary="late good", features=[]),
        ]
        pruned, events, contradiction = lm_bisect.apply_cached_interval_prepass(commits, observations)
        self.assertTrue(contradiction)
        self.assertEqual(pruned, commits)
        self.assertEqual(events[0]["type"], "cached-contradiction")

    def test_verdict_from_runner_exit_code(self) -> None:
        self.assertEqual(lm_bisect.verdict_from_runner_exit_code(0), "good")
        self.assertEqual(lm_bisect.verdict_from_runner_exit_code(1), "bad")
        self.assertEqual(lm_bisect.verdict_from_runner_exit_code(125), "skip")
        with self.assertRaises(RuntimeError):
            lm_bisect.verdict_from_runner_exit_code(42)

    def test_runner_missing_issue_definition_is_not_classified_as_bad(self) -> None:
        profile = demo_profile()
        completed = subprocess.CompletedProcess(
            args=["runner"],
            returncode=1,
            stdout="missing issue definition: demo\n",
        )

        with mock.patch.object(lm_bisect, "runner_path_for_issue", return_value=Path("/tmp/runner")), mock.patch(
            "subprocess.run", return_value=completed
        ):
            verdict, summary, _output, evidence = lm_bisect.run_issue_runner(profile, Path("/tmp/repo"))

        self.assertEqual(verdict, "skip")
        self.assertEqual(summary, "missing issue definition: demo")
        self.assertIn("runner configuration error", evidence)


class ObservationHelpersTests(unittest.TestCase):
    def test_find_observation_by_sha(self) -> None:
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="good",
                summary="cached good",
                features=["x"],
                source="runner",
                evidence=[],
                log_excerpt="",
            )
        ]
        found = lm_bisect.find_observation_by_sha(observations, "a" * 40)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.verdict, "good")
        self.assertEqual(found.source, "runner")
        self.assertIsNone(lm_bisect.find_observation_by_sha(observations, "b" * 40))

    def test_update_observation_replaces_existing_sha(self) -> None:
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="good",
                summary="old",
                features=["x"],
                source="manual",
                evidence=[],
                log_excerpt="",
            )
        ]
        replacement = lm_bisect.CommitObservation(
            sha="a" * 40,
            verdict="bad",
            summary="new",
            features=["y"],
            source="runner",
            evidence=["error: reproduced"],
            log_excerpt="full output",
        )
        updated = lm_bisect.update_observation(observations, replacement)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].verdict, "bad")
        self.assertEqual(updated[0].summary, "new")
        self.assertEqual(updated[0].source, "runner")

    def test_save_and_load_observations_preserves_runner_details(self) -> None:
        observations = [
            lm_bisect.CommitObservation(
                sha="a" * 40,
                verdict="bad",
                summary="expectedCost crash",
                features=["term:vectorize", "path:llvm/lib/Transforms/Vectorize"],
                source="runner",
                evidence=[
                    'Running pass "loop-vectorize"',
                    "llvm::LoopVectorizationCostModel::expectedCost",
                ],
                log_excerpt="line one\nline two",
                trace_excerpt="Stack dump:\nLoopVectorizationCostModel::expectedCost",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "observations.json"
            lm_bisect.save_observations(path, observations)
            loaded = lm_bisect.load_observations(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(
            loaded[0].evidence,
            [
                'Running pass "loop-vectorize"',
                "llvm::LoopVectorizationCostModel::expectedCost",
            ],
        )
        self.assertEqual(loaded[0].log_excerpt, "line one\nline two")
        self.assertEqual(
            loaded[0].trace_excerpt,
            "Stack dump:\nLoopVectorizationCostModel::expectedCost",
        )

    def test_load_observations_defaults_missing_trace_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "observations.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "sha": "a" * 40,
                            "verdict": "bad",
                            "summary": "old-format",
                            "features": [],
                            "source": "runner",
                            "evidence": ["Stack dump:"],
                            "log_excerpt": "line one",
                        }
                    ]
                )
            )
            loaded = lm_bisect.load_observations(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].trace_excerpt, "")

    def test_extract_trace_excerpt_prefers_stack_dump_region(self) -> None:
        output = """
noise
PLEASE submit a bug report
Stack dump:
1. Running pass "loop-vectorize" on function "func_21"
2. llvm::LoopVectorizationCostModel::expectedCost
3. clang frontend command failed with exit code 136
tail
"""

        excerpt = lm_bisect.extract_trace_excerpt(output, max_lines=4)

        self.assertIn("Stack dump:", excerpt)
        self.assertIn("loop-vectorize", excerpt)
        self.assertIn("expectedCost", excerpt)
        self.assertNotIn("noise", excerpt)

    def test_extract_trace_excerpt_prioritizes_assertion_and_filters_noise(self) -> None:
        output = """
Print function names: func_1 func_2 func_3
PLEASE submit a bug report
Stack dump:
0. Program arguments: clang -c testcase.c
1. Running pass "loop-vectorize" on function "func_21"
clang: ../llvm/lib/Support/Unix/Program.inc:64: Assertion `!Name.empty() && "Must have a name!"' failed.
Command terminated by signal 6
"""

        excerpt = lm_bisect.extract_trace_excerpt(output, max_lines=5)
        first_line = excerpt.splitlines()[0]

        self.assertIn("Assertion `!Name.empty()", first_line)
        self.assertIn('Running pass "loop-vectorize"', excerpt)
        self.assertNotIn("Print function names", excerpt)
        self.assertNotIn("PLEASE submit a bug report", excerpt)

    def test_extract_trace_excerpt_keeps_large_excerpt_with_cap(self) -> None:
        payload = "Stack dump:\n" + ("x" * 11050)

        excerpt = lm_bisect.extract_trace_excerpt(payload, max_lines=20, max_chars=10000)

        self.assertEqual(len(excerpt), 10000)
        self.assertTrue(excerpt.startswith("Stack dump:"))


class RunHistoryTests(unittest.TestCase):
    def test_method_specific_paths_are_distinct(self) -> None:
        heuristic_path = lm_bisect.run_history_path_for_issue("pr176682", "heuristic", None)
        model_path = lm_bisect.run_history_path_for_issue("pr176682", "model", "gpt-5.4-mini")
        heuristic_window = lm_bisect.unresolved_window_path_for_issue("pr176682", "heuristic", None)
        model_window = lm_bisect.unresolved_window_path_for_issue("pr176682", "model", "gpt-5.4-mini")
        hybrid_model_path = lm_bisect.run_history_path_for_issue("pr176682", "model", "gpt-5.4-mini", "hybrid")
        hybrid_model_window = lm_bisect.unresolved_window_path_for_issue("pr176682", "model", "gpt-5.4-mini", "hybrid")
        posterior_model_path = lm_bisect.run_history_path_for_issue("pr176682", "model", "gpt-5.4-mini", "posterior")
        posterior_model_window = lm_bisect.unresolved_window_path_for_issue("pr176682", "model", "gpt-5.4-mini", "posterior")
        calibrated_model_path = lm_bisect.run_history_path_for_issue(
            "pr176682", "model", "gpt-5.4-mini", "calibrated-posterior"
        )
        calibrated_model_window = lm_bisect.unresolved_window_path_for_issue(
            "pr176682", "model", "gpt-5.4-mini", "calibrated-posterior"
        )
        trace_only_model_path = lm_bisect.run_history_path_for_issue(
            "pr176682",
            "model",
            "gpt-5.4-mini",
            "calibrated-posterior",
            "topk",
            "off",
            "tuned",
            "trace-only",
        )
        trace_only_model_window = lm_bisect.unresolved_window_path_for_issue(
            "pr176682",
            "model",
            "gpt-5.4-mini",
            "calibrated-posterior",
            "topk",
            "off",
            "tuned",
            "trace-only",
        )
        diverse_model_path = lm_bisect.run_history_path_for_issue("pr176682", "model", "gpt-5.4-mini", "ranked", "diverse")
        diverse_model_window = lm_bisect.unresolved_window_path_for_issue("pr176682", "model", "gpt-5.4-mini", "ranked", "diverse")

        self.assertTrue(str(heuristic_path).endswith("pr176682-heuristic.json"))
        self.assertTrue(str(model_path).endswith("pr176682-model-gpt-5.4-mini.json"))
        self.assertTrue(str(heuristic_window).endswith("pr176682-heuristic-unresolved-window.json"))
        self.assertTrue(str(model_window).endswith("pr176682-model-gpt-5.4-mini-unresolved-window.json"))
        self.assertTrue(str(hybrid_model_path).endswith("pr176682-model-gpt-5.4-mini-hybrid.json"))
        self.assertTrue(
            str(hybrid_model_window).endswith("pr176682-model-gpt-5.4-mini-hybrid-unresolved-window.json")
        )
        self.assertTrue(str(posterior_model_path).endswith("pr176682-model-gpt-5.4-mini-posterior.json"))
        self.assertTrue(
            str(posterior_model_window).endswith("pr176682-model-gpt-5.4-mini-posterior-unresolved-window.json")
        )
        self.assertTrue(
            str(calibrated_model_path).endswith("pr176682-model-gpt-5.4-mini-calibrated-posterior.json")
        )
        self.assertTrue(
            str(calibrated_model_window).endswith(
                "pr176682-model-gpt-5.4-mini-calibrated-posterior-unresolved-window.json"
            )
        )
        self.assertTrue(
            str(trace_only_model_path).endswith("pr176682-model-gpt-5.4-mini-calibrated-posterior-obs-trace-only.json")
        )
        self.assertTrue(
            str(trace_only_model_window).endswith(
                "pr176682-model-gpt-5.4-mini-calibrated-posterior-obs-trace-only-unresolved-window.json"
            )
        )
        self.assertTrue(str(diverse_model_path).endswith("pr176682-model-gpt-5.4-mini-diverse.json"))
        self.assertTrue(
            str(diverse_model_window).endswith("pr176682-model-gpt-5.4-mini-diverse-unresolved-window.json")
        )

    def test_save_and_load_run_history_preserves_steps(self) -> None:
        payload = {
            "issue": "pr187875",
            "scorer": "heuristic",
            "status": "in_progress",
            "steps": [
                {
                    "step": 1,
                    "sha": "a" * 40,
                    "verdict": "good",
                    "source": "runner",
                    "unresolved_before": 100,
                    "unresolved_after": 50,
                    "summary": "vectorized rc: 55",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-history.json"
            lm_bisect.save_run_history(path, payload)
            loaded = lm_bisect.load_run_history(path)

        self.assertEqual(loaded["issue"], "pr187875")
        self.assertEqual(loaded["status"], "in_progress")
        self.assertEqual(len(loaded["steps"]), 1)
        self.assertEqual(loaded["steps"][0]["sha"], "a" * 40)
        self.assertEqual(loaded["steps"][0]["unresolved_after"], 50)

    def test_save_run_history_is_atomic(self) -> None:
        payload = {
            "issue": "pr-demo",
            "scorer": "heuristic",
            "status": "in_progress",
            "steps": [{"step": 1, "sha": "a" * 40, "verdict": "good"}],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-history.json"
            path.write_text('{"old": true}\n')
            lm_bisect.save_run_history(path, payload)
            loaded = json.loads(path.read_text())
            tmp_path = Path(str(path) + ".tmp")

        self.assertEqual(loaded["issue"], "pr-demo")
        self.assertFalse(tmp_path.exists())

    def test_append_run_history_step_updates_status_and_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-history.json"
            history = lm_bisect.start_run_history_payload(
                issue_id="pr172195",
                scorer="heuristic",
                model_name=None,
                model_frontier="topk",
                search_policy="ranked",
                hybrid_switch_window=32,
                lambda_weight=2.0,
                max_steps=12,
                observation_path="/tmp/obs.json",
                run_history_path=str(path),
                good_commit="g" * 40,
                bad_commit="b" * 40,
                initial_unresolved=200,
            )
            lm_bisect.append_run_history_step(
                history,
                {
                    "step": 1,
                    "sha": "a" * 40,
                    "verdict": "bad",
                    "source": "cache",
                    "unresolved_before": 200,
                    "unresolved_after": 80,
                    "summary": "cached bad",
                },
            )
            history["status"] = "completed"
            lm_bisect.save_run_history(path, history)
            loaded = lm_bisect.load_run_history(path)

        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(loaded["steps"][0]["source"], "cache")
        self.assertEqual(loaded["steps"][0]["unresolved_before"], 200)
        self.assertEqual(loaded["steps"][0]["summary"], "cached bad")

    def test_runner_duration_summary_counts_only_runner_steps(self) -> None:
        history = {
            "steps": [
                {"source": "runner", "runner_duration_sec": 2.0},
                {"source": "cache", "runner_duration_sec": 8.0},
                {"source": "runner", "runner_duration_sec": 4.0},
            ]
        }

        lm_bisect.update_runner_duration_summary(history)

        self.assertEqual(history["runner_build_count"], 2)
        self.assertEqual(history["runner_build_avg_duration_sec"], 3.0)

    def test_run_history_step_can_store_ranking_context(self) -> None:
        history = lm_bisect.start_run_history_payload(
            issue_id="pr187875",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="ranked",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=300,
        )
        lm_bisect.append_run_history_step(
            history,
            {
                "step": 2,
                "sha": "c" * 40,
                "verdict": "good",
                "source": "runner",
                "summary": "vectorized rc: 55",
                "unresolved_before": 300,
                "unresolved_after": 140,
                "selection": {
                    "utility": 1.23,
                    "semantic_score": 2.5,
                    "build_success_prob": 0.91,
                    "evidence": ["2 keyword hits", "relevant paths: llvm/lib/Transforms/Vectorize"],
                },
                "top_candidates": [
                    {"rank": 1, "sha": "c" * 40, "utility": 1.23},
                    {"rank": 2, "sha": "d" * 40, "utility": 1.05},
                ],
            },
        )

        self.assertEqual(history["steps"][0]["selection"]["utility"], 1.23)
        self.assertEqual(history["steps"][0]["top_candidates"][1]["rank"], 2)
        self.assertEqual(history["steps"][0]["top_candidates"][1]["sha"], "d" * 40)

    def test_candidate_payloads_preserve_extracted_diff_summary(self) -> None:
        record = lm_bisect.CommitRecord(
            index=1,
            sha="a" * 40,
            subject="candidate",
            body="",
            changed_files=["candidate-file.cpp"],
            diff_text="raw diff",
            semantic_score=4.0,
            build_success_prob=0.9,
            suspicion_weight=0.0,
            evidence=["model-scored"],
            features=["feature-a"],
            diff_mode="last-tested",
            diff_extraction="llm",
            diff_base_sha="b" * 40,
            diff_summary="Summary: extracted evidence",
        )
        record.utility = 1.25
        record.selection_score = 1.25

        selection = lm_bisect.selection_payload(record)
        compact = lm_bisect.compact_candidate_view(record, rank=1)

        self.assertEqual(selection["diff_mode"], "last-tested")
        self.assertEqual(selection["diff_extraction"], "llm")
        self.assertEqual(selection["diff_base_sha"], "b" * 40)
        self.assertIn("Summary: extracted evidence", selection["diff_summary"])
        self.assertEqual(compact["diff_extraction"], "llm")
        self.assertIn("Summary: extracted evidence", compact["diff_summary"])

    def test_start_run_history_payload_records_policy_fields(self) -> None:
        history = lm_bisect.start_run_history_payload(
            issue_id="pr172195",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="diverse",
            search_policy="hybrid",
            hybrid_switch_window=24,
            lambda_weight=2.0,
            max_steps=20,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=100,
            model_top_k=2000,
        )
        self.assertEqual(history["search_policy"], "hybrid")
        self.assertEqual(history["hybrid_switch_window"], 24)
        self.assertEqual(history["model_frontier"], "diverse")
        self.assertEqual(history["model_top_k"], 2000)
        self.assertEqual(history["calibrated_prior_power"], lm_bisect.DEFAULT_CALIBRATED_PRIOR_POWER)
        self.assertEqual(history["weak_relevance_threshold"], lm_bisect.DEFAULT_WEAK_RELEVANCE_THRESHOLD)

    def test_model_reasoning_effort_isolated_in_history_and_cache_version(self) -> None:
        history = lm_bisect.start_run_history_payload(
            issue_id="pr172195",
            scorer="model",
            model_name="gpt-5.6-terra",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=20,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=100,
            model_reasoning_effort="high",
        )

        self.assertEqual(history["model_reasoning_effort"], "high")
        self.assertFalse(
            lm_bisect.run_history_matches(
                history,
                "pr172195",
                "model",
                "gpt-5.6-terra",
                "topk",
                model_reasoning_effort=None,
            )
        )
        self.assertTrue(
            lm_bisect.resolved_model_scoring_version("trace-only", "high").endswith("reasoning-high")
        )
        self.assertIn(
            "reasoning-high",
            lm_bisect.run_history_path_for_issue(
                "pr172195",
                "model",
                "gpt-5.6-terra",
                model_reasoning_effort="high",
            ).name,
        )

    def test_prepare_run_history_does_not_resume_when_model_top_k_changes(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
            model_top_k=3,
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=150,
            candidate_file="/tmp/window.json",
            model_top_k=2000,
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["model_top_k"], 2000)

    def test_prepare_run_history_does_not_resume_when_model_diff_extraction_changes(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
            model_diff_extraction="raw",
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=150,
            candidate_file="/tmp/window.json",
            model_diff_extraction="llm",
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["model_diff_extraction"], "llm")

    def test_prepare_run_history_does_not_resume_when_calibrated_settings_change(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
            calibrated_prior_power=1.35,
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=150,
            candidate_file="/tmp/window.json",
            calibrated_prior_power=1.6,
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["calibrated_prior_power"], 1.6)

    def test_prepare_run_history_does_not_resume_when_observation_prompt_mode_changes(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
            observation_prompt_mode="legacy",
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=150,
            candidate_file="/tmp/window.json",
            observation_prompt_mode="trace-only",
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["observation_prompt_mode"], "trace-only")

    def test_build_parser_accepts_calibrated_policy_flags(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "simulate-online",
                "--issue",
                "pr172195",
                "--first-bad-sha",
                "e8219e5ce84db26fd521ce5091d18e75c7afbc6a",
                "--search-policy",
                "calibrated-posterior",
                "--calibrated-prior-power",
                "1.6",
                "--calibrated-prior-bonus",
                "2.0",
                "--weak-relevance-penalty",
                "0.2",
                "--weak-relevance-threshold",
                "0.9",
            ]
        )

        self.assertEqual(args.search_policy, "calibrated-posterior")
        self.assertEqual(args.calibrated_prior_power, 1.6)
        self.assertEqual(args.calibrated_prior_bonus, 2.0)
        self.assertEqual(args.weak_relevance_penalty, 0.2)
        self.assertEqual(args.weak_relevance_threshold, 0.9)

    def test_build_parser_accepts_build_success_power(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "simulate-online",
                "--issue",
                "pr172195",
                "--first-bad-sha",
                "e8219e5ce84db26fd521ce5091d18e75c7afbc6a",
                "--build-success-power",
                "0.5",
            ]
        )

        self.assertEqual(args.build_success_power, 0.5)

    def test_build_parser_accepts_observation_prompt_mode(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "run-online",
                "--issue",
                "pr172195",
                "--scorer",
                "model",
                "--observation-prompt-mode",
                "trace-only",
            ]
        )

        self.assertEqual(args.observation_prompt_mode, "trace-only")

    def test_build_parser_accepts_model_reasoning_effort(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "run-online",
                "--issue",
                "pr172195",
                "--scorer",
                "model",
                "--model-name",
                "gpt-5.6-terra",
                "--model-reasoning-effort",
                "high",
            ]
        )

        self.assertEqual(args.model_reasoning_effort, "high")

    def test_build_parser_accepts_model_diff_mode(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "run-online",
                "--issue",
                "pr172195",
                "--scorer",
                "model",
                "--model-diff-mode",
                "last-tested",
                "--model-top-k",
                "2000",
            ]
        )

        self.assertEqual(args.model_diff_mode, "last-tested")
        self.assertEqual(args.model_top_k, 2000)

    def test_build_parser_accepts_model_diff_extraction(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "run-online",
                "--issue",
                "pr172195",
                "--scorer",
                "model",
                "--model-diff-extraction",
                "llm",
            ]
        )

        self.assertEqual(args.model_diff_extraction, "llm")

    def test_build_parser_accepts_deprecated_heuristic_top_k_as_noop(self) -> None:
        parser = lm_bisect.build_parser()
        args = parser.parse_args(
            [
                "run-online",
                "--issue",
                "pr172195",
                "--scorer",
                "heuristic",
                "--heuristic-top-k",
                "600",
            ]
        )

        self.assertEqual(args.heuristic_top_k, 600)

    def test_history_step_payload_preserves_runner_skip_evidence(self) -> None:
        observation = lm_bisect.CommitObservation(
            sha="a" * 40,
            verdict="skip",
            summary="build failed; skipping commit",
            features=["path:llvm/include/llvm/Support"],
            source="runner",
            evidence=["error: uintptr_t was not declared"],
            log_excerpt="[123/2241] Building CXX object\nerror: uintptr_t was not declared",
            trace_excerpt="../llvm/include/llvm/Support/Signals.h:119:24: error: 'uintptr_t' was not declared",
            build_failure={
                "phase": "build",
                "failed_target": "LLVMSupport",
                "failed_header": "llvm/include/llvm/Support/Signals.h",
                "primary_error": "error: 'uintptr_t' was not declared",
            },
        )

        payload = lm_bisect.runner_observation_history_fields(observation)

        self.assertEqual(payload["evidence"], ["error: uintptr_t was not declared"])
        self.assertIn("uintptr_t", payload["trace_excerpt"])
        self.assertIn("Building CXX object", payload["log_excerpt"])
        self.assertEqual(payload["build_failure"]["failed_target"], "LLVMSupport")

    def test_run_label_makes_distinct_online_artifact_paths(self) -> None:
        path = lm_bisect.run_history_path_for_issue(
            "pr172195",
            "model",
            "gpt-5.4-mini",
            "calibrated-posterior",
            "topk",
            "off",
            "tuned",
            "trace-only",
            "distinct-trace-v5",
        )
        window_path = lm_bisect.unresolved_window_path_for_issue(
            "pr172195",
            "model",
            "gpt-5.4-mini",
            "calibrated-posterior",
            "topk",
            "off",
            "tuned",
            "trace-only",
            "distinct-trace-v5",
        )

        self.assertTrue(str(path).endswith("pr172195-model-gpt-5.4-mini-calibrated-posterior-obs-trace-only-distinct-trace-v5.json"))
        self.assertTrue(str(window_path).endswith("pr172195-model-gpt-5.4-mini-calibrated-posterior-obs-trace-only-distinct-trace-v5-unresolved-window.json"))

    def test_prepare_run_history_does_not_resume_when_run_label_changes(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
            observation_prompt_mode="trace-only",
            run_label="old-label",
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=150,
            candidate_file="/tmp/window.json",
            observation_prompt_mode="trace-only",
            run_label="new-label",
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["run_label"], "new-label")

    def test_prepare_run_history_resumes_same_method_run(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="ranked",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
        )
        lm_bisect.append_run_history_step(
            existing,
            {
                "step": 1,
                "sha": "a" * 40,
                "verdict": "good",
                "source": "runner",
                "summary": "cached good",
                "unresolved_before": 200,
                "unresolved_after": 100,
            },
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="ranked",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=100,
            candidate_file="/tmp/window.json",
        )

        self.assertTrue(resumed)
        self.assertEqual(completed_steps, 1)
        self.assertEqual(len(history["steps"]), 1)
        self.assertEqual(history["resume_events"][0]["remaining_unresolved_at_resume"], 100)

    def test_prepare_run_history_resumes_same_method_without_candidate_file(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr191581",
            scorer="model",
            model_name="claude-opus-4-7",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=20,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=500,
        )
        lm_bisect.append_run_history_step(
            existing,
            {
                "step": 1,
                "sha": "a" * 40,
                "verdict": "bad",
                "source": "runner",
                "summary": "first step",
                "unresolved_before": 500,
                "unresolved_after": 250,
            },
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr191581",
            scorer="model",
            model_name="claude-opus-4-7",
            model_frontier="topk",
            search_policy="calibrated-posterior",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=20,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=250,
            candidate_file=None,
        )

        self.assertTrue(resumed)
        self.assertEqual(completed_steps, 1)
        self.assertEqual(len(history["steps"]), 1)
        self.assertNotIn("candidate_file", history["resume_events"][0])
        self.assertEqual(history["resume_events"][0]["remaining_unresolved_at_resume"], 250)

    def test_replay_history_steps_reconstructs_unresolved_window(self) -> None:
        commits = ["a", "b", "c", "d", "e"]
        history_steps = [
            {"step": 1, "sha": "b", "verdict": "good"},
            {"step": 2, "sha": "d", "verdict": "bad"},
        ]

        unresolved, events = lm_bisect.replay_history_steps_over_interval(commits, history_steps)

        self.assertEqual(unresolved, ["c", "d"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["unresolved_before"], 5)
        self.assertEqual(events[0]["unresolved_after"], 3)
        self.assertEqual(events[1]["unresolved_before"], 3)
        self.assertEqual(events[1]["unresolved_after"], 2)

    def test_replay_history_steps_ignores_steps_outside_current_window(self) -> None:
        commits = ["c", "d", "e"]
        history_steps = [
            {"step": 1, "sha": "b", "verdict": "good"},
            {"step": 2, "sha": "d", "verdict": "bad"},
        ]

        unresolved, events = lm_bisect.replay_history_steps_over_interval(commits, history_steps)

        self.assertEqual(unresolved, ["c", "d"])
        self.assertEqual(events[0]["type"], "history-step-outside-window")
        self.assertEqual(events[1]["type"], "history-replay")

    def test_prepare_run_history_does_not_resume_different_method(self) -> None:
        existing = lm_bisect.start_run_history_payload(
            issue_id="pr176682",
            scorer="heuristic",
            model_name=None,
            model_frontier="topk",
            search_policy="ranked",
            hybrid_switch_window=32,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=200,
        )
        lm_bisect.append_run_history_step(
            existing,
            {
                "step": 1,
                "sha": "a" * 40,
                "verdict": "good",
                "source": "runner",
                "summary": "cached good",
                "unresolved_before": 200,
                "unresolved_after": 100,
            },
        )

        history, completed_steps, resumed = lm_bisect.prepare_run_history(
            existing_history=existing,
            issue_id="pr176682",
            scorer="model",
            model_name="gpt-5.4-mini",
            model_frontier="diverse",
            search_policy="hybrid",
            hybrid_switch_window=24,
            lambda_weight=2.0,
            max_steps=12,
            observation_path="/tmp/obs.json",
            run_history_path="/tmp/run.json",
            good_commit="g" * 40,
            bad_commit="b" * 40,
            initial_unresolved=100,
            candidate_file="/tmp/window.json",
        )

        self.assertFalse(resumed)
        self.assertEqual(completed_steps, 0)
        self.assertEqual(history["scorer"], "model")
        self.assertEqual(history["steps"], [])

    def test_save_unresolved_window_writes_json_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "window.json"
            commits = ["a" * 40, "b" * 40]
            lm_bisect.save_unresolved_window(path, commits)
            loaded = json.loads(path.read_text())

        self.assertEqual(loaded, commits)

    def test_run_online_does_not_forward_heuristic_top_k_to_make_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = ["a" * 40, "b" * 40, "c" * 40]
            args = mock.Mock(
                llvm_dir=str(repo),
                issue="demo",
                scorer="heuristic",
                run_label="test-heuristic-topk",
                candidate_file=None,
                observations=str(observations),
                model_name=None,
                search_policy="calibrated-posterior",
                model_frontier="topk",
                candidate_pruning="off",
                heuristic_version="general",
                observation_prompt_mode="trace-only",
                hybrid_switch_window=32,
                lambda_weight=2.0,
                max_steps=1,
                calibrated_prior_power=lm_bisect.DEFAULT_CALIBRATED_PRIOR_POWER,
                calibrated_prior_bonus=lm_bisect.DEFAULT_CALIBRATED_PRIOR_BONUS,
                weak_relevance_penalty=lm_bisect.DEFAULT_WEAK_RELEVANCE_PENALTY,
                weak_relevance_threshold=lm_bisect.DEFAULT_WEAK_RELEVANCE_THRESHOLD,
                build_success_power=lm_bisect.DEFAULT_BUILD_SUCCESS_POWER,
                model_diff_mode="parent",
                model_diff_extraction="raw",
                model_top_k=3,
            )

            def stop_after_make_records(*_args, **_kwargs):
                raise RuntimeError("stop after make_records")

            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=demo_profile()
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=stop_after_make_records
            ) as make_records:
                with self.assertRaisesRegex(RuntimeError, "stop after make_records"):
                    lm_bisect.command_run_online(args)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertNotIn("heuristic_top_k", make_records.call_args.kwargs)
        self.assertEqual(saved_history["heuristic_keywords"], list(lm_bisect.GENERAL_KEYWORDS))

    def test_direct_oracle_anchor_persists_one_runner_build_at_known_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            anchor_sha = "b" * 40
            candidate_shas = ["a" * 40, anchor_sha, "c" * 40]
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "demo",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "heuristic",
                    "--heuristic-version",
                    "oracle-first-bad-major-tuned-anchor",
                    "--oracle-first-bad-sha",
                    anchor_sha,
                    "--observations",
                    str(observations),
                    "--run-label",
                    "oracle-anchor-test",
                    "--max-steps",
                    "1",
                ]
            )
            record = lm_bisect.CommitRecord(
                index=2,
                sha=anchor_sha,
                subject="known first bad",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
                diff_text="anchor change",
                semantic_score=0.0,
                build_success_prob=0.95,
                suspicion_weight=0.0,
            )
            pruning_summary = {"before_count": 1, "after_count": 1, "applied": False}

            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=demo_profile()
            ), mock.patch.object(
                lm_bisect, "oracle_first_bad_sha_from_args", return_value=anchor_sha
            ), mock.patch.object(
                lm_bisect,
                "resolved_heuristic_selection_profile",
                return_value=(demo_profile(), {"keywords": ["anchor"]}),
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=candidate_shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect, "make_records", return_value=([record], pruning_summary)
            ) as make_records, mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                return_value=("bad", "reproduced assertion", "assertion failure", ["reproducer failed"]),
            ) as run_runner, mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertEqual(make_records.call_args.kwargs["candidate_shas"], [anchor_sha])
        run_runner.assert_called_once()
        self.assertEqual(saved_history["status"], "completed")
        self.assertEqual(saved_history["runner_build_count"], 1)
        self.assertEqual(saved_history["first_bad_commit"], anchor_sha)
        self.assertEqual(saved_history["final_unresolved_window"], [anchor_sha])
        self.assertEqual(len(saved_history["steps"]), 1)
        self.assertEqual(saved_history["steps"][0]["sha"], anchor_sha)
        self.assertEqual(saved_history["steps"][0]["verdict"], "bad")
        self.assertEqual(saved_history["steps"][0]["source"], "runner")
        self.assertEqual(saved_history["oracle_diagnostic"]["selection_contract"], "direct-sha-anchor")
        self.assertTrue(saved_history["oracle_diagnostic"]["normal_search_bypassed"])
        self.assertTrue(saved_history["oracle_diagnostic"]["anchor_validation_passed"])

    def test_human_signal_pool_proves_triaged_candidate_with_its_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
            profile = replace(demo_profile(), issue_id="pr204559")
            pool_candidates = [
                {
                    "sha": sha,
                    "index": index + 1,
                    "subject": f"candidate {index}",
                    "changed_files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                    "match_reasons": ["crash-pass"],
                    "prior_score": 2.0,
                }
                for index, sha in enumerate(shas[1:])
            ]
            triage = [pool_candidates[1], pool_candidates[0], pool_candidates[2]]
            direct_records = [
                lm_bisect.CommitRecord(
                    index=index + 1,
                    sha=candidate["sha"],
                    subject=str(candidate["subject"]),
                    body="",
                    changed_files=list(candidate["changed_files"]),
                    diff_text="",
                    semantic_score=10.0 if candidate["sha"] == shas[2] else 1.0,
                    build_success_prob=0.9,
                    suspicion_weight=0.0,
                    causal_evidence={"confidence": 0.8},
                )
                for index, candidate in enumerate(triage)
            ]
            parent_record = lm_bisect.CommitRecord(
                index=1,
                sha=shas[1],
                subject="immediate parent",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.0},
            )
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "pr204559",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "model",
                    "--model-name",
                    "test-model",
                    "--model-top-k",
                    "3",
                    "--model-cache-namespace",
                    "human-pool-proof-test-cache",
                    "--model-diff-mode",
                    "parent",
                    "--model-diff-extraction",
                    "causal-llm-human-pool",
                    "--observations",
                    str(observations),
                    "--run-label",
                    "human-pool-proof-test",
                    "--max-steps",
                    "2",
                ]
            )

            def records_for_pool(*_args, **kwargs):
                candidate_shas = kwargs["candidate_shas"]
                if candidate_shas == [entry["sha"] for entry in triage]:
                    return direct_records, {"before_count": 3, "after_count": 3, "applied": False}
                self.fail(f"unexpected human pool candidates: {candidate_shas}")

            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=profile
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect,
                "load_model_config",
                return_value=lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test-model"),
            ), mock.patch.object(
                lm_bisect, "load_model_cache", return_value={}
            ), mock.patch.object(
                lm_bisect, "human_dependency_anchor_files", return_value={}
            ), mock.patch.object(
                lm_bisect,
                "profile_crash_signal_payload",
                return_value={"source_paths": [], "symbols": [], "pass_tokens": ["simple-loop-unswitch"]},
            ), mock.patch.object(
                lm_bisect,
                "load_commit_metadata",
                return_value={sha: lm_bisect.CommitMetadata(sha, "", "", []) for sha in shas},
            ), mock.patch.object(
                lm_bisect,
                "build_human_signal_pool",
                return_value={
                    "candidate_count": 3,
                    "candidate_count_before_limit": 3,
                    "candidates": pool_candidates,
                    "candidate_shas": [entry["sha"] for entry in pool_candidates],
                },
            ), mock.patch.object(
                lm_bisect, "human_signal_pool_triage_with_model", return_value=triage
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=records_for_pool
            ) as make_records, mock.patch.object(
                lm_bisect, "build_commit_record", return_value=parent_record
            ) as build_parent_record, mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                side_effect=[
                    ("bad", "candidate reproduces", "candidate bad", ["assertion"]),
                    ("good", "parent passes", "parent good", ["no assertion"]),
                ],
            ) as run_runner, mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertEqual(make_records.call_args_list[0].kwargs["candidate_shas"], [entry["sha"] for entry in triage])
        self.assertEqual(make_records.call_count, 1)
        self.assertEqual(build_parent_record.call_args.args[2], shas[1])
        self.assertFalse(build_parent_record.call_args.kwargs["load_diff"])
        self.assertEqual(run_runner.call_count, 2)
        self.assertEqual(saved_history["first_bad_commit"], shas[2])
        self.assertEqual(saved_history["human_signal_pool"]["phase"], "resolved")
        self.assertEqual(saved_history["human_signal_pool"]["direct_proof"]["parent_sha"], shas[1])
        self.assertEqual(
            [step["human_signal_pool_phase"] for step in saved_history["steps"]],
            ["direct-candidate", "parent-proof"],
        )

    def test_human_signal_pool_bad_parent_falls_back_to_full_interval_bcr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
            profile = replace(demo_profile(), issue_id="pr204559")
            triaged_candidate = {
                "sha": shas[2],
                "index": 3,
                "subject": "triaged candidate",
                "changed_files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                "match_reasons": ["crash-pass"],
                "prior_score": 2.0,
                "triage_score": 0.9,
            }
            direct_record = lm_bisect.CommitRecord(
                index=3,
                sha=shas[2],
                subject="triaged candidate",
                body="",
                changed_files=list(triaged_candidate["changed_files"]),
                diff_text="",
                semantic_score=10.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.9},
            )
            parent_record = lm_bisect.CommitRecord(
                index=2,
                sha=shas[1],
                subject="bad parent",
                body="",
                changed_files=list(triaged_candidate["changed_files"]),
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                causal_evidence={"confidence": 0.0},
            )
            fallback_record = lm_bisect.CommitRecord(
                index=1,
                sha=shas[0],
                subject="fallback first bad",
                body="",
                changed_files=list(triaged_candidate["changed_files"]),
                diff_text="",
                semantic_score=10.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
                evidence=[],
                causal_evidence={"confidence": 0.9},
            )
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue",
                    "pr204559",
                    "--llvm-dir",
                    str(repo),
                    "--scorer",
                    "model",
                    "--model-name",
                    "test-model",
                    "--model-top-k",
                    "3",
                    "--model-cache-namespace",
                    "human-pool-fallback-test-cache",
                    "--model-diff-mode",
                    "parent",
                    "--model-diff-extraction",
                    "causal-llm-human-pool",
                    "--observations",
                    str(observations),
                    "--run-label",
                    "human-pool-fallback-test",
                    "--max-steps",
                    "3",
                ]
            )

            def records_for_phase(*_args, **kwargs):
                candidate_shas = kwargs["candidate_shas"]
                if candidate_shas == [shas[2]]:
                    return [direct_record], {"before_count": 1, "after_count": 1, "applied": False}
                if candidate_shas == shas[:2]:
                    return [fallback_record], {"before_count": 2, "after_count": 2, "applied": False}
                self.fail(f"unexpected fallback candidates: {candidate_shas}")

            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=profile
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect,
                "load_model_config",
                return_value=lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test-model"),
            ), mock.patch.object(
                lm_bisect, "load_model_cache", return_value={}
            ), mock.patch.object(
                lm_bisect, "human_dependency_anchor_files", return_value={}
            ), mock.patch.object(
                lm_bisect,
                "profile_crash_signal_payload",
                return_value={"source_paths": [], "symbols": [], "pass_tokens": ["simple-loop-unswitch"]},
            ), mock.patch.object(
                lm_bisect,
                "load_commit_metadata",
                return_value={sha: lm_bisect.CommitMetadata(sha, "", "", []) for sha in shas},
            ), mock.patch.object(
                lm_bisect,
                "build_human_signal_pool",
                return_value={
                    "candidate_count": 1,
                    "candidate_count_before_limit": 1,
                    "candidates": [triaged_candidate],
                    "candidate_shas": [shas[2]],
                },
            ), mock.patch.object(
                lm_bisect, "human_signal_pool_triage_with_model", return_value=[triaged_candidate]
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=records_for_phase
            ) as make_records, mock.patch.object(
                lm_bisect, "build_commit_record", return_value=parent_record
            ) as build_parent_record, mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                side_effect=[
                    ("bad", "candidate reproduces", "candidate bad", ["assertion"]),
                    ("bad", "parent also reproduces", "parent bad", ["assertion"]),
                    ("bad", "fallback candidate reproduces", "fallback bad", ["assertion"]),
                ],
            ), mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertEqual([call.kwargs["candidate_shas"] for call in make_records.call_args_list], [[shas[2]], shas[:2]])
        self.assertEqual(build_parent_record.call_args.args[2], shas[1])
        self.assertEqual(saved_history["human_signal_pool"]["phase"], "fallback")
        self.assertEqual(saved_history["human_signal_pool"]["fallback_reason"], "parent-proof-not-good:bad")
        self.assertEqual(saved_history["first_bad_commit"], shas[0])
        self.assertEqual(saved_history["steps"][2]["human_signal_pool_phase"], "fallback")

    def test_human_frontier_triages_then_proves_causal_candidate_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            run_history = tmp / "run-history.json"
            unresolved_window = tmp / "window.json"
            observations = tmp / "observations.json"
            shas = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
            profile = replace(demo_profile(), issue_id="pr204559")
            frontier_candidates = [
                {
                    "sha": shas[1],
                    "index": 2,
                    "subject": "weaker frontier candidate",
                    "changed_files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                    "match_reasons": ["crash-pass", "dependency-path"],
                    "prior_score": 2.0,
                },
                {
                    "sha": shas[2],
                    "index": 3,
                    "subject": "causal frontier candidate",
                    "changed_files": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                    "match_reasons": ["crash-pass", "dependency-path"],
                    "prior_score": 2.0,
                },
            ]
            # The model ranks the later candidate first, but the direct proof
            # frontier must retain the staged tier's chronological order so
            # midpoint support is meaningful rather than a triage-order proxy.
            triage = [
                {**frontier_candidates[1], "triage_score": 0.95},
                {**frontier_candidates[0], "triage_score": 0.50},
            ]
            direct_records = [
                lm_bisect.CommitRecord(
                    index=index + 1,
                    sha=candidate["sha"],
                    subject=str(candidate["subject"]),
                    body="",
                    changed_files=list(candidate["changed_files"]),
                    diff_text="",
                    semantic_score=7.0 if candidate["sha"] == shas[2] else 3.0,
                    build_success_prob=0.9,
                    suspicion_weight=0.0,
                    causal_evidence={"confidence": 0.8},
                )
                for index, candidate in enumerate(frontier_candidates)
            ]
            parent_record = lm_bisect.CommitRecord(
                index=2,
                sha=shas[1],
                subject="immediate parent",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"],
                diff_text="",
                semantic_score=1.0,
                build_success_prob=0.9,
                suspicion_weight=0.0,
            )
            args = lm_bisect.build_parser().parse_args(
                [
                    "run-online",
                    "--issue", "pr204559",
                    "--llvm-dir", str(repo),
                    "--scorer", "model",
                    "--model-name", "test-model",
                    "--model-top-k", "12",
                    "--model-cache-namespace", "human-frontier-runtime-test-cache",
                    "--model-diff-mode", "parent",
                    "--model-diff-extraction", "causal-llm-human-frontier",
                    "--search-policy", "calibrated-posterior",
                    "--observations", str(observations),
                    "--run-label", "human-frontier-runtime-test",
                    "--max-steps", "2",
                ]
            )

            def records_for_frontier(*_args, **kwargs):
                self.assertEqual(kwargs["candidate_shas"], [shas[1], shas[2]])
                return direct_records, {"before_count": 2, "after_count": 2, "applied": False}

            with mock.patch.object(lm_bisect, "load_profiles", return_value={}), mock.patch.object(
                lm_bisect, "load_issue_profile", return_value=profile
            ), mock.patch.object(
                lm_bisect, "list_candidate_commits", return_value=shas
            ), mock.patch.object(
                lm_bisect, "run_history_path_for_issue", return_value=run_history
            ), mock.patch.object(
                lm_bisect, "unresolved_window_path_for_issue", return_value=unresolved_window
            ), mock.patch.object(
                lm_bisect, "git", return_value="h" * 40
            ), mock.patch.object(
                lm_bisect, "checkout_commit"
            ), mock.patch.object(
                lm_bisect,
                "load_model_config",
                return_value=lm_bisect.ModelConfig("key", "https://example.invalid/v1", "test-model"),
            ), mock.patch.object(
                lm_bisect, "load_model_cache", return_value={}
            ), mock.patch.object(
                lm_bisect,
                "profile_crash_signal_payload",
                return_value={"query_terms": [{"term": "MemorySSAUpdater", "kind": "component-updater"}]},
            ), mock.patch.object(
                lm_bisect,
                "human_frontier_dependency_paths",
                return_value={"selected_paths": ["llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp"]},
            ), mock.patch.object(
                lm_bisect,
                "load_commit_metadata",
                return_value={sha: lm_bisect.CommitMetadata(sha, "", "", []) for sha in shas},
            ), mock.patch.object(
                lm_bisect,
                "build_human_frontier_pool",
                return_value={
                    "candidate_count": 2,
                    "candidate_shas": [shas[1], shas[2]],
                    "candidates": frontier_candidates,
                },
            ), mock.patch.object(
                lm_bisect, "human_frontier_triage_with_model", return_value=triage
            ), mock.patch.object(
                lm_bisect, "make_records", side_effect=records_for_frontier
            ) as make_records, mock.patch.object(
                lm_bisect, "build_commit_record", return_value=parent_record
            ) as build_parent_record, mock.patch.object(
                lm_bisect,
                "run_issue_runner",
                side_effect=[
                    ("bad", "candidate reproduces", "candidate bad", ["assertion"]),
                    ("good", "parent passes", "parent good", ["no assertion"]),
                ],
            ), mock.patch.object(
                lm_bisect, "save_issue_artifact_bundle", return_value=tmp / "bundle"
            ):
                self.assertEqual(lm_bisect.command_run_online(args), 0)

            saved_history = lm_bisect.load_run_history(run_history)

        self.assertEqual(make_records.call_count, 1)
        self.assertEqual(build_parent_record.call_args.args[2], shas[1])
        self.assertEqual(saved_history["first_bad_commit"], shas[2])
        self.assertEqual(saved_history["human_frontier"]["phase"], "resolved")
        self.assertEqual(saved_history["human_frontier"]["direct_proof"]["parent_sha"], shas[1])
        self.assertEqual(
            [step["human_frontier_phase"] for step in saved_history["steps"]],
            ["search", "parent-proof"],
        )
        self.assertEqual(
            saved_history["steps"][1]["selection_mode"],
            "human-frontier-parent-proof",
        )


class MetadataLoadingTests(unittest.TestCase):
    def test_commit_diff_text_tolerates_non_utf8_patch_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            lm_bisect.subprocess.run(["git", "init"], cwd=repo, check=True, stdout=lm_bisect.subprocess.PIPE)
            lm_bisect.subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            lm_bisect.subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            (repo / "binary-ish.txt").write_bytes(b"ok\n")
            lm_bisect.subprocess.run(["git", "add", "binary-ish.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
            (repo / "binary-ish.txt").write_bytes(b"ok\nbad-\x92-byte\n")
            lm_bisect.subprocess.run(["git", "add", "binary-ish.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "non utf8 diff"], cwd=repo, check=True)
            sha = lm_bisect.git(repo, "rev-parse", "HEAD").strip()

            diff = lm_bisect.commit_diff_text(repo, sha)

        self.assertIn("binary-ish.txt", diff)
        self.assertIn("bad-", diff)

    def test_commit_transition_diff_text_compares_base_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            lm_bisect.subprocess.run(["git", "init"], cwd=repo, check=True, stdout=lm_bisect.subprocess.PIPE)
            lm_bisect.subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            lm_bisect.subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n")
            lm_bisect.subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
            base = lm_bisect.git(repo, "rev-parse", "HEAD").strip()
            tracked.write_text("base\nmiddle\n")
            lm_bisect.subprocess.run(["git", "commit", "-am", "middle"], cwd=repo, check=True)
            tracked.write_text("base\nmiddle\ncandidate\n")
            lm_bisect.subprocess.run(["git", "commit", "-am", "candidate"], cwd=repo, check=True)
            candidate = lm_bisect.git(repo, "rev-parse", "HEAD").strip()

            diff = lm_bisect.commit_transition_diff_text(repo, base, candidate)

        self.assertIn("+middle", diff)
        self.assertIn("+candidate", diff)

    def test_commit_transition_changed_files_compares_base_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            lm_bisect.subprocess.run(["git", "init"], cwd=repo, check=True, stdout=lm_bisect.subprocess.PIPE)
            lm_bisect.subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            lm_bisect.subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            first = repo / "first.txt"
            first.write_text("base\n")
            lm_bisect.subprocess.run(["git", "add", "first.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
            base = lm_bisect.git(repo, "rev-parse", "HEAD").strip()
            second = repo / "second.txt"
            second.write_text("candidate\n")
            lm_bisect.subprocess.run(["git", "add", "second.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "candidate"], cwd=repo, check=True)
            candidate = lm_bisect.git(repo, "rev-parse", "HEAD").strip()

            files = lm_bisect.commit_transition_changed_files(repo, base, candidate)

        self.assertEqual(files, ["second.txt"])

    def test_commit_transition_diff_for_files_scopes_to_candidate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            lm_bisect.subprocess.run(["git", "init"], cwd=repo, check=True, stdout=lm_bisect.subprocess.PIPE)
            lm_bisect.subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            lm_bisect.subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
                stdout=lm_bisect.subprocess.PIPE,
            )
            (repo / "candidate.txt").write_text("base\n")
            (repo / "unrelated.txt").write_text("base\n")
            lm_bisect.subprocess.run(["git", "add", "candidate.txt", "unrelated.txt"], cwd=repo, check=True)
            lm_bisect.subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
            base = lm_bisect.git(repo, "rev-parse", "HEAD").strip()
            (repo / "unrelated.txt").write_text("base\nunrelated change\n")
            lm_bisect.subprocess.run(["git", "commit", "-am", "middle"], cwd=repo, check=True)
            (repo / "candidate.txt").write_text("base\ncandidate change\n")
            lm_bisect.subprocess.run(["git", "commit", "-am", "candidate"], cwd=repo, check=True)
            candidate = lm_bisect.git(repo, "rev-parse", "HEAD").strip()

            diff = lm_bisect.commit_transition_diff_for_files(repo, base, candidate, ["candidate.txt"])

        self.assertIn("+candidate change", diff)
        self.assertNotIn("unrelated change", diff)

    def test_load_commit_metadata_subject_and_files_only(self) -> None:
        repo = Path("/home/derek331/research/gitbisect-work/llvm-project")
        shas = [
            "5079260ec73506e57f8cd8baf903595efca34c73",
            "86c5539aa89ac61058e3ba4fc0ae578c2879bf9e",
        ]

        metadata = lm_bisect.load_commit_metadata(repo, shas, include_body=False)
        self.assertEqual(set(metadata), set(shas))
        self.assertEqual(
            metadata["86c5539aa89ac61058e3ba4fc0ae578c2879bf9e"].subject,
            "[IR][RISCV] Remove @llvm.experimental.vp.splat (#171084)",
        )
        self.assertTrue(
            any(
                path == "llvm/lib/CodeGen/SelectionDAG/LegalizeVectorTypes.cpp"
                for path in metadata["86c5539aa89ac61058e3ba4fc0ae578c2879bf9e"].changed_files
            )
        )
        self.assertEqual(metadata["5079260ec73506e57f8cd8baf903595efca34c73"].body, "")

    def test_load_commit_metadata_with_body(self) -> None:
        repo = Path("/home/derek331/research/gitbisect-work/llvm-project")
        sha = "86c5539aa89ac61058e3ba4fc0ae578c2879bf9e"

        metadata = lm_bisect.load_commit_metadata(repo, [sha], include_body=True)
        self.assertIn("RISCVVLOptimizer", metadata[sha].body)
        self.assertTrue(metadata[sha].changed_files)

    def test_make_records_reuses_metadata_cache(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        shas = ["a" * 40, "b" * 40]
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {}
        loaded_once = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="first",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/A.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="second",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/B.cpp"],
            ),
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=loaded_once) as load_metadata, mock.patch.object(
            lm_bisect, "commit_diff_text", return_value=""
        ):
            records_first, summary_first = lm_bisect.make_records(
                repo,
                profile,
                scorer="heuristic",
                candidate_shas=shas,
                metadata_cache=metadata_cache,
            )
            records_second, summary_second = lm_bisect.make_records(
                repo,
                profile,
                scorer="heuristic",
                candidate_shas=shas,
                metadata_cache=metadata_cache,
            )

        self.assertEqual(load_metadata.call_count, 1)
        self.assertEqual(set(metadata_cache), set(shas))
        self.assertEqual([record.sha for record in records_first], shas)
        self.assertEqual([record.sha for record in records_second], shas)
        self.assertEqual(summary_first["before_count"], 2)
        self.assertEqual(summary_second["before_count"], 2)

    def test_make_records_heuristic_keeps_full_candidate_window(self) -> None:
        profile = demo_profile(keywords=["vplan"])
        repo = Path("/tmp/fake-llvm-project")
        shas = ["a" * 40, "b" * 40, "c" * 40]
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="docs cleanup",
                body="",
                changed_files=["llvm/docs/ReleaseNotes.md"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="vplan fix",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            ),
            shas[2]: lm_bisect.CommitMetadata(
                sha=shas[2],
                subject="loop vectorize vplan",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/LoopVectorize.cpp"],
            ),
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            return_value="",
        ):
            records, summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="heuristic",
                candidate_shas=shas,
                metadata_cache={},
            )

        self.assertEqual([record.sha for record in records], shas)
        self.assertNotIn("heuristic_top_k", summary)
        self.assertNotIn("before_heuristic_top_k", summary)
        self.assertNotIn("after_heuristic_top_k", summary)

    def test_make_records_reuses_model_cache_without_reloading(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        shas = ["a" * 40, "b" * 40]
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="first",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/A.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="second",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/B.cpp"],
            ),
        }
        model_cache: dict[str, dict] = {
            shas[0]: {
                "semantic_score": 4.0,
                "build_success_prob": 0.9,
                "evidence": ["cached"],
                "features": ["feature-a"],
            }
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        with mock.patch.object(lm_bisect, "load_model_cache") as load_model_cache:
            load_model_cache.side_effect = AssertionError("load_model_cache should not be called")
            with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
                lm_bisect,
                "commit_diff_text",
                return_value="diff",
            ):
                records, _summary = lm_bisect.make_records(
                    repo,
                    profile,
                    scorer="model",
                    model_config=model_config,
                    candidate_shas=shas,
                    model_top_k=1,
                    metadata_cache={},
                    model_cache=model_cache,
                )

        self.assertEqual(records[0].semantic_score, 4.0)
        self.assertEqual(records[0].features, ["feature-a"])

    def test_model_usage_summary_accumulates_response_usage_by_kind(self) -> None:
        summary: dict[str, object] = {}
        response = mock.Mock()
        response.usage = mock.Mock(prompt_tokens=10, completion_tokens=3, total_tokens=13)

        lm_bisect.record_model_usage(summary, "diff_extraction", response)
        lm_bisect.record_model_usage(
            summary,
            "scoring",
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        )
        lm_bisect.record_model_usage(summary, "diff_extraction", None)

        self.assertEqual(
            summary,
            {
                "requests": 2,
                "prompt_tokens": 30,
                "completion_tokens": 8,
                "total_tokens": 38,
                "by_kind": {
                    "diff_extraction": {
                        "requests": 1,
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                    },
                    "scoring": {
                        "requests": 1,
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                        "total_tokens": 25,
                    },
                },
            },
        )

    def test_make_records_last_tested_mode_uses_candidate_files_for_transition_diff(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        last_tested_sha = "a" * 40
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["candidate-file.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )
        model_cache: dict[str, dict] = {}

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual(batch[0]["files"], ["candidate-file.cpp"])
            self.assertEqual(batch[0]["diff_mode"], "last-tested")
            self.assertEqual(batch[0]["diff_base_sha"], last_tested_sha)
            return {
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["transition-scored"],
                    "features": ["feature-a"],
                }
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_is_ancestor",
            return_value=True,
        ), mock.patch.object(
            lm_bisect,
            "commit_transition_diff_for_files",
            return_value="transition diff",
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_mode="last-tested",
                last_tested_sha=last_tested_sha,
            )

        self.assertEqual(records[0].semantic_score, 4.0)
        self.assertIn("transition-scored", records[0].evidence)

    def test_make_records_last_tested_mode_uses_parent_diff_for_older_candidate(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        candidate_sha = "b" * 40
        last_tested_sha = "c" * 40
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            candidate_sha: lm_bisect.CommitMetadata(
                sha=candidate_sha,
                subject="older candidate",
                body="",
                changed_files=["candidate-file.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )
        model_cache: dict[str, dict] = {}

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual(batch[0]["diff_mode"], "parent")
            self.assertNotIn("diff_base_sha", batch[0])
            self.assertEqual(batch[0]["diff"], "parent diff")
            return {
                candidate_sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["parent-fallback-scored"],
                    "features": ["feature-a"],
                }
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_is_ancestor",
            return_value=False,
        ) as is_ancestor, mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            return_value="parent diff",
        ) as commit_diff, mock.patch.object(
            lm_bisect,
            "commit_transition_diff_for_files",
            side_effect=AssertionError("older candidates must not use reverse transition diff"),
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[candidate_sha],
                model_top_k=1,
                metadata_cache={},
                model_cache=model_cache,
                model_diff_mode="last-tested",
                last_tested_sha=last_tested_sha,
            )

        is_ancestor.assert_called_once_with(repo, last_tested_sha, candidate_sha)
        self.assertEqual(commit_diff.call_args.args[:2], (repo, candidate_sha))
        self.assertEqual(records[0].diff_mode, "parent")
        self.assertIsNone(records[0].diff_base_sha)
        self.assertIn("parent-fallback-scored", records[0].evidence)
        self.assertIn(lm_bisect.model_score_cache_key(candidate_sha, "parent", None, "raw"), model_cache)

    def test_make_records_last_tested_mode_keeps_transition_diff_for_newer_candidate(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        candidate_sha = "c" * 40
        last_tested_sha = "b" * 40
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            candidate_sha: lm_bisect.CommitMetadata(
                sha=candidate_sha,
                subject="newer candidate",
                body="",
                changed_files=["candidate-file.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual(batch[0]["diff_mode"], "last-tested")
            self.assertEqual(batch[0]["diff_base_sha"], last_tested_sha)
            self.assertEqual(batch[0]["diff"], "forward transition diff")
            return {
                candidate_sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["transition-scored"],
                    "features": ["feature-a"],
                }
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_is_ancestor",
            return_value=True,
        ) as is_ancestor, mock.patch.object(
            lm_bisect,
            "commit_transition_diff_for_files",
            return_value="forward transition diff",
        ) as transition_diff, mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            side_effect=AssertionError("newer candidates should use transition diff"),
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[candidate_sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_mode="last-tested",
                last_tested_sha=last_tested_sha,
            )

        is_ancestor.assert_called_once_with(repo, last_tested_sha, candidate_sha)
        transition_diff.assert_called_once()
        self.assertEqual(records[0].diff_mode, "last-tested")
        self.assertEqual(records[0].diff_base_sha, last_tested_sha)
        self.assertIn("transition-scored", records[0].evidence)

    def test_model_score_cache_key_distinguishes_diff_extraction(self) -> None:
        sha = "b" * 40

        raw_key = lm_bisect.model_score_cache_key(sha, "parent", None, "raw")
        extracted_key = lm_bisect.model_score_cache_key(sha, "parent", None, "llm")
        causal_key = lm_bisect.model_score_cache_key(sha, "parent", None, "causal-llm")
        causal_impl_key = lm_bisect.model_score_cache_key(sha, "parent", None, "causal-llm-impl")
        deterministic_facts_key = lm_bisect.model_score_cache_key(
            sha,
            "parent",
            None,
            "causal-llm-deterministic-facts",
        )
        artifact_complete_facts_key = lm_bisect.model_score_cache_key(
            sha,
            "parent",
            None,
            "causal-llm-deterministic-facts-artifact",
        )
        last_tested_key = lm_bisect.model_score_cache_key(sha, "last-tested", "a" * 40, "llm")

        self.assertEqual(raw_key, sha)
        self.assertNotEqual(raw_key, extracted_key)
        self.assertNotEqual(extracted_key, causal_key)
        self.assertNotEqual(causal_key, causal_impl_key)
        self.assertNotEqual(causal_key, deterministic_facts_key)
        self.assertNotEqual(deterministic_facts_key, artifact_complete_facts_key)
        self.assertIn("extract:llm", extracted_key)
        self.assertIn("extract:causal-llm", causal_key)
        self.assertIn(lm_bisect.CAUSAL_DIFF_EXTRACTION_VERSION, causal_key)
        self.assertIn(lm_bisect.CAUSAL_IMPL_DIFF_EXTRACTION_VERSION, causal_impl_key)
        self.assertIn(lm_bisect.CAUSAL_DETERMINISTIC_FACTS_DIFF_EXTRACTION_VERSION, deterministic_facts_key)
        self.assertIn(
            lm_bisect.CAUSAL_DETERMINISTIC_FACTS_ARTIFACT_DIFF_EXTRACTION_VERSION,
            artifact_complete_facts_key,
        )
        self.assertIn("diff:last-tested-candidate-files", last_tested_key)
        self.assertIn("extract:llm", last_tested_key)

    def test_model_score_cache_key_distinguishes_causal_parent_context(self) -> None:
        sha = "b" * 40
        without_context = lm_bisect.model_score_cache_key(
            sha,
            diff_extraction="causal-llm",
            causal_context_parent_count=0,
        )
        with_context = lm_bisect.model_score_cache_key(
            sha,
            diff_extraction="causal-llm",
            causal_context_parent_count=5,
        )

        self.assertNotEqual(without_context, with_context)
        self.assertIn("causal-first-parent-context:5", with_context)

    def test_make_records_causal_extraction_persists_evidence_and_features(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        metadata_cache = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["llvm/lib/Transforms/Vectorize/VPlan.cpp"],
            )
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )
        causal_evidence = {
            "summary": "Changes recipe construction.",
            "changed_symbols": ["VPlan::buildRecipe"],
            "behavioral_change": ["selects a new vector recipe"],
            "issue_link": {
                "assertion_or_trace": ["VPlan assertion"],
                "reproducer": [],
                "pass_or_subsystem": ["LoopVectorize"],
                "explanation": "The recipe path reaches the asserted invariant.",
            },
            "confidence": 0.8,
            "build_risk": [],
            "retrieval": {"selected_files": ["llvm/lib/Transforms/Vectorize/VPlan.cpp"]},
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            side_effect=AssertionError("causal extraction must not fetch a broad parent diff"),
        ) as commit_diff, mock.patch.object(
            lm_bisect,
            "retrieve_causal_diff_evidence",
            return_value=causal_evidence["retrieval"],
        ), mock.patch.object(
            lm_bisect,
            "extract_causal_diff_evidence_batch_with_model",
            return_value={sha: causal_evidence},
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            return_value={
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["causal-scored"],
                    "features": ["term:model"],
                }
            },
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_extraction="causal-llm",
            )

        self.assertEqual(records[0].diff_extraction, "causal-llm")
        commit_diff.assert_not_called()

    def test_make_records_human_causal_extraction_dispatches_human_guided_retrieval(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        metadata_cache = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["llvm/lib/Analysis/MemorySSA.cpp"],
            )
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.6-terra",
        )
        crash_payload = {"kind": "assertion", "query_terms": ["verifyOptResult"]}
        causal_evidence = {
            "summary": "Changes clobber verification.",
            "changed_symbols": ["ClobberWalker::verifyOptResult"],
            "behavioral_change": ["updates clobber selection"],
            "issue_link": {
                "assertion_or_trace": ["verifyOptResult assertion"],
                "reproducer": [],
                "pass_or_subsystem": ["MemorySSA"],
                "explanation": "The changed path reaches the asserted invariant.",
            },
            "confidence": 0.8,
            "build_risk": [],
            "retrieval": {"selected_files": ["llvm/lib/Analysis/MemorySSA.cpp"]},
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "profile_crash_signal_payload",
            return_value=crash_payload,
        ), mock.patch.object(
            lm_bisect,
            "retrieve_causal_diff_evidence",
            return_value=causal_evidence["retrieval"],
        ) as retrieve, mock.patch.object(
            lm_bisect,
            "extract_causal_diff_evidence_batch_with_model",
            return_value={sha: causal_evidence},
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            return_value={
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["causal-scored"],
                    "features": ["term:model"],
                }
            },
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_extraction="causal-llm-human",
            )

        self.assertEqual(records[0].diff_extraction, "causal-llm-human")
        self.assertEqual(retrieve.call_args.kwargs["retrieval_policy"], "human-guided")
        self.assertEqual(retrieve.call_args.kwargs["crash_signal_payload"], crash_payload)
        self.assertEqual(records[0].causal_evidence, causal_evidence)
        self.assertIn("term:clobberwalkerverifyoptresult", records[0].features)
        self.assertIn("Causal confidence: 0.80", records[0].diff_summary)
        self.assertEqual(lm_bisect.selection_payload(records[0])["causal_evidence"], causal_evidence)

    def test_make_records_crash_aware_extraction_uses_retrieval_without_dynamic_selection(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/Loop.cpp"],
            )
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.6-terra",
        )
        crash_payload = {
            "kind": "assertion",
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "query_terms": ["MemorySSAUpdater"],
        }
        causal_evidence = {
            "summary": "candidate evidence",
            "changed_symbols": [],
            "behavioral_change": [],
            "issue_link": {},
            "confidence": 0.5,
            "build_risk": [],
            "retrieval": {},
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata), mock.patch.object(
            lm_bisect,
            "profile_crash_signal_payload",
            return_value=crash_payload,
        ), mock.patch.object(
            lm_bisect,
            "dynamic_dependency_usage",
            return_value={"MemorySSAUpdater": {"llvm/lib/Transforms/Scalar/Loop.cpp": 3}},
        ), mock.patch.object(
            lm_bisect,
            "retrieve_causal_diff_evidence",
            return_value=causal_evidence["retrieval"],
        ) as retrieve, mock.patch.object(
            lm_bisect,
            "extract_causal_diff_evidence_batch_with_model",
            return_value={sha: causal_evidence},
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            return_value={
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["causal-scored"],
                    "features": [],
                }
            },
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_extraction="causal-llm-crash-aware",
            )

        self.assertEqual(records[0].diff_extraction, "causal-llm-crash-aware")
        self.assertEqual(retrieve.call_args.kwargs["retrieval_policy"], "crash-aware")
        self.assertEqual(retrieve.call_args.kwargs["crash_signal_payload"], crash_payload)
        self.assertEqual(retrieve.call_args.kwargs["crash_file_touch_count"], 0)
        self.assertEqual(
            retrieve.call_args.kwargs["dependency_usage"],
            {"MemorySSAUpdater": {"llvm/lib/Transforms/Scalar/Loop.cpp": 3}},
        )

    def test_make_records_deterministic_facts_uses_single_ordinal_model_call(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        metadata = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["llvm/lib/Transforms/Scalar/Loop.cpp"],
            )
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.6-terra",
        )
        retrieval = {
            "selected_files": ["llvm/lib/Transforms/Scalar/Loop.cpp"],
            "selected_hunks": [],
            "crash_signals": {"kind": "assertion"},
            "repository_facts": {
                "contact_paths": ["Loop -> MSSAU->applyUpdates"],
                "no_call_path_found": False,
                "destruction_surface": ["applyUpdates"],
                "crash_file": {"polarity": "hot-file-support", "paths": []},
                "dependency_api_use": {"Loop.cpp": 3},
            },
            "contract_contexts": [],
        }
        model_result = {
            sha: {
                "semantic_score": 3.0,
                "build_success_prob": 0.9,
                "evidence": ["ordinal-rank:1"],
                "features": ["term:invariant-break"],
                "ordinal_judgment": {"rank": 1, "mechanism": "invariant-break"},
                "repository_facts": retrieval["repository_facts"],
            }
        }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata), mock.patch.object(
            lm_bisect,
            "profile_crash_signal_payload",
            return_value={"kind": "assertion", "query_terms": ["MemorySSAUpdater"]},
        ), mock.patch.object(
            lm_bisect,
            "retrieve_causal_diff_evidence",
            return_value=retrieval,
        ) as retrieve, mock.patch.object(
            lm_bisect,
            "deterministic_facts_rank_commits",
            return_value=model_result,
        ) as rank, mock.patch.object(
            lm_bisect,
            "extract_causal_diff_evidence_batch_with_model",
            side_effect=AssertionError("v15 must not run causal extraction"),
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=AssertionError("v15 must not run absolute-score scorer"),
        ), mock.patch.object(
            lm_bisect,
            "score_build_probability",
            return_value=(0.9, ["deterministic buildability"]),
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_extraction="causal-llm-deterministic-facts",
            )

        self.assertEqual(records[0].diff_extraction, "causal-llm-deterministic-facts")
        self.assertEqual(records[0].semantic_score, 3.0)
        self.assertEqual(records[0].build_success_prob, 0.9)
        retrieve.assert_called_once()
        self.assertEqual(retrieve.call_args.kwargs["retrieval_policy"], "deterministic-facts")
        rank.assert_called_once()

    def test_make_records_rejects_last_tested_causal_extraction(self) -> None:
        with self.assertRaisesRegex(ValueError, "parent diffs"):
            lm_bisect.make_records(
                Path("/tmp/fake-llvm-project"),
                demo_profile(),
                scorer="model",
                model_config=lm_bisect.ModelConfig(
                    api_key="k",
                    base_url="http://example.invalid",
                    model_name="gpt-5.4-mini",
                ),
                candidate_shas=["a" * 40],
                model_diff_mode="last-tested",
                model_diff_extraction="causal-llm",
            )

    def test_make_records_parent_mode_can_use_llm_diff_extraction(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["candidate-file.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )
        model_cache: dict[str, dict] = {}

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual(batch[0]["diff_mode"], "parent")
            self.assertEqual(batch[0]["diff_extraction"], "llm")
            self.assertEqual(batch[0]["diff"], "raw parent diff")
            self.assertIn("Extracted parent summary", batch[0]["diff_summary"])
            return {
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["extracted-parent-scored"],
                    "features": ["feature-a"],
                }
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            return_value="raw parent diff",
        ) as commit_diff, mock.patch.object(
            lm_bisect,
            "extract_diff_evidence_with_model",
            return_value="Extracted parent summary",
        ) as extract_diff, mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache=model_cache,
                model_diff_extraction="llm",
            )

        self.assertEqual(extract_diff.call_count, 1)
        self.assertEqual(commit_diff.call_args.kwargs["max_chars"], lm_bisect.DIFF_EXTRACTION_MAX_INPUT_CHARS)
        self.assertEqual(extract_diff.call_args.args[1]["diff_mode"], "parent")
        self.assertEqual(records[0].semantic_score, 4.0)
        self.assertIn("extracted-parent-scored", records[0].evidence)
        self.assertEqual(records[0].diff_extraction, "llm")
        self.assertIn("Extracted parent summary", records[0].diff_summary)
        cache_key = lm_bisect.model_score_cache_key(sha, "parent", None, "llm")
        self.assertIn("Extracted parent summary", model_cache[cache_key]["diff_summary"])

    def test_make_records_uses_batch_llm_diff_extraction_for_multiple_candidates(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        shas = ["b" * 40, "c" * 40]
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            shas[0]: lm_bisect.CommitMetadata(
                sha=shas[0],
                subject="candidate b",
                body="",
                changed_files=["candidate-b.cpp"],
            ),
            shas[1]: lm_bisect.CommitMetadata(
                sha=shas[1],
                subject="candidate c",
                body="",
                changed_files=["candidate-c.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )
        model_cache: dict[str, dict] = {}

        def fake_extract_batch(profile_arg, items, model_config_arg):
            self.assertEqual([item["sha"] for item in items], shas)
            return {item["sha"]: f"Extracted summary for {item['sha'][:1]}" for item in items}

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual([item["diff_summary"] for item in batch], ["Extracted summary for b", "Extracted summary for c"])
            return {
                item["sha"]: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["batch-extracted"],
                    "features": ["feature-a"],
                }
                for item in batch
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_diff_text",
            side_effect=["raw parent diff b", "raw parent diff c"],
        ), mock.patch.object(
            lm_bisect,
            "extract_diff_evidence_batch_with_model",
            side_effect=fake_extract_batch,
        ) as extract_batch, mock.patch.object(
            lm_bisect,
            "extract_diff_evidence_with_model",
            side_effect=AssertionError("per-candidate extraction should not be used"),
        ), mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=shas,
                model_top_k=2,
                metadata_cache={},
                model_cache=model_cache,
                model_diff_extraction="llm",
            )

        self.assertEqual(extract_batch.call_count, 1)
        self.assertEqual([record.diff_summary for record in records], ["Extracted summary for b", "Extracted summary for c"])
        self.assertEqual(set(model_cache), {lm_bisect.model_score_cache_key(sha, "parent", None, "llm") for sha in shas})

    def test_make_records_last_tested_mode_can_use_llm_diff_extraction(self) -> None:
        profile = demo_profile()
        repo = Path("/tmp/fake-llvm-project")
        sha = "b" * 40
        last_tested_sha = "a" * 40
        metadata_cache: dict[str, lm_bisect.CommitMetadata] = {
            sha: lm_bisect.CommitMetadata(
                sha=sha,
                subject="candidate",
                body="",
                changed_files=["candidate-file.cpp"],
            ),
        }
        model_config = lm_bisect.ModelConfig(
            api_key="k",
            base_url="http://example.invalid",
            model_name="gpt-5.4-mini",
        )

        def fake_score_model_batch_with_backfill(
            profile_arg,
            batch,
            model_config_arg,
            scorer_arg,
            observations_arg,
            observation_prompt_mode_arg,
        ):
            self.assertEqual(batch[0]["diff_mode"], "last-tested")
            self.assertEqual(batch[0]["diff_base_sha"], last_tested_sha)
            self.assertEqual(batch[0]["diff_extraction"], "llm")
            self.assertEqual(batch[0]["diff"], "raw transition diff")
            self.assertIn("Extracted transition summary", batch[0]["diff_summary"])
            return {
                sha: {
                    "semantic_score": 4.0,
                    "build_success_prob": 0.9,
                    "evidence": ["extracted-transition-scored"],
                    "features": ["feature-a"],
                }
            }

        with mock.patch.object(lm_bisect, "load_commit_metadata", return_value=metadata_cache), mock.patch.object(
            lm_bisect,
            "commit_is_ancestor",
            return_value=True,
        ), mock.patch.object(
            lm_bisect,
            "commit_transition_diff_for_files",
            return_value="raw transition diff",
        ) as transition_diff, mock.patch.object(
            lm_bisect,
            "extract_diff_evidence_with_model",
            return_value="Extracted transition summary",
        ) as extract_diff, mock.patch.object(
            lm_bisect,
            "score_model_batch_with_backfill",
            side_effect=fake_score_model_batch_with_backfill,
        ):
            records, _summary = lm_bisect.make_records(
                repo,
                profile,
                scorer="model",
                model_config=model_config,
                candidate_shas=[sha],
                model_top_k=1,
                metadata_cache={},
                model_cache={},
                model_diff_mode="last-tested",
                last_tested_sha=last_tested_sha,
                model_diff_extraction="llm",
            )

        self.assertEqual(extract_diff.call_count, 1)
        self.assertEqual(transition_diff.call_args.kwargs["max_chars"], lm_bisect.DIFF_EXTRACTION_MAX_INPUT_CHARS)
        self.assertEqual(extract_diff.call_args.args[1]["diff_mode"], "last-tested")
        self.assertEqual(records[0].semantic_score, 4.0)
        self.assertIn("extracted-transition-scored", records[0].evidence)
        self.assertEqual(records[0].diff_mode, "last-tested")
        self.assertEqual(records[0].diff_extraction, "llm")
        self.assertEqual(records[0].diff_base_sha, last_tested_sha)
        self.assertIn("Extracted transition summary", records[0].diff_summary)


class IssueProfileCoverageTests(unittest.TestCase):
    def test_pr204178_endpoint_validated_case_has_online_profile(self) -> None:
        profiles = lm_bisect.load_profiles()

        profile = lm_bisect.load_issue_profile(profiles, "pr204178")

        self.assertEqual(profile.good_commit, "d0b54bb50e5110a004b41fc06dadf3fee70834b7")
        self.assertEqual(profile.bad_commit, "3b5b5c1ec4a3095ab096dd780e84d7ab81f3d7ff")
        self.assertEqual(profile.runner, "scripts/pr204178/bisect-runner.sh")
        self.assertTrue(Path(profile.runner).is_file())
        self.assertIn("ItaniumMangle", profile.bug_report_summary)
        self.assertIn("clang/lib/AST/ItaniumMangle.cpp", profile.relevant_paths)


class IssueArtifactBundleTests(unittest.TestCase):
    def test_save_issue_artifact_bundle_mirrors_global_lm_json_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            issue_dir = root / "results" / "issues"
            obs = root / "results" / "lm_bisect_observations" / "prx.json"
            run = root / "results" / "lm_bisect_runs" / "prx-model-run.json"
            window = root / "results" / "issues" / "prx" / "prx-window.json"
            cache = root / "results" / "lm_bisect_model_cache" / "prx-model-cache.json"
            for path in (obs, run, window, cache):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"name": path.name}) + "\n")

            with mock.patch.object(lm_bisect, "DEFAULT_ISSUE_RESULTS_DIR", issue_dir):
                bundle_dir = lm_bisect.save_issue_artifact_bundle(
                    issue_id="prx",
                    observation_path=obs,
                    run_history_path=run,
                    unresolved_window_path=window,
                    model_cache_path_value=cache,
                )

            self.assertEqual(bundle_dir, issue_dir / "prx" / "report-trace-model-guided")
            self.assertTrue((bundle_dir / obs.name).is_file())
            self.assertTrue((bundle_dir / run.name).is_file())
            self.assertTrue((bundle_dir / window.name).is_file())
            self.assertTrue((bundle_dir / cache.name).is_file())

            manifest = json.loads((bundle_dir / "prx-artifact-bundle-manifest.json").read_text())
            self.assertEqual(manifest["issue"], "prx")
            self.assertIn("run_history", manifest["copied_paths"])
            self.assertIn("observations", manifest["copied_paths"])
            self.assertIn("model_cache", manifest["copied_paths"])


if __name__ == "__main__":
    unittest.main()
