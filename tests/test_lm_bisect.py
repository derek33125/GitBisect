from __future__ import annotations

import json
import tempfile
import unittest
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
        )
        self.assertEqual(history["search_policy"], "hybrid")
        self.assertEqual(history["hybrid_switch_window"], 24)
        self.assertEqual(history["model_frontier"], "diverse")
        self.assertEqual(history["calibrated_prior_power"], lm_bisect.DEFAULT_CALIBRATED_PRIOR_POWER)
        self.assertEqual(history["weak_relevance_threshold"], lm_bisect.DEFAULT_WEAK_RELEVANCE_THRESHOLD)

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


class MetadataLoadingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
