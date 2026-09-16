from __future__ import annotations

import unittest

from tools import ceg_fusion, lm_bisect


def judgment(rank=1, confidence=0.83, mechanism="invariant-break", explains=True):
    return {
        "rank": rank,
        "confidence": confidence,
        "mechanism": mechanism,
        "explains_failure": explains,
        "ordinal_permutation_valid": True,
    }


class FrontierFusionTests(unittest.TestCase):
    def test_single_supported_candidate_can_overcome_misleading_prior(self):
        prior = {"checker": 0.70, "producer": 0.01, "unknown": 0.19, "outside": 0.10}
        judgments = {
            "producer": judgment(),
            "checker": judgment(2, 0.8, "unrelated", False),
            "unknown": judgment(3, 0.0, "unknown", False),
        }
        fused, audit = ceg_fusion.coverage_weighted_fusion(prior, judgments)
        self.assertGreater(fused["producer"], prior["producer"] * 10)
        self.assertEqual(fused["outside"], prior["outside"])
        self.assertAlmostEqual(sum(fused.values()), 1.0)
        self.assertAlmostEqual(audit["frontier_mass_before"], audit["frontier_mass_after"])
        self.assertTrue(all(fused[sha] >= prior[sha] * 0.5 for sha in prior))

    def test_influence_grows_with_remaining_interval_coverage(self):
        judgments = {"producer": judgment(), "checker": judgment(2, 0.8, "unrelated", False)}
        small = {"producer": 0.01, "checker": 0.89, "other": 0.1}
        large = {"producer": 0.01, "checker": 0.89, **{str(i): 0.001 for i in range(100)}}
        _, late = ceg_fusion.coverage_weighted_fusion(small, judgments)
        _, early = ceg_fusion.coverage_weighted_fusion(large, judgments)
        self.assertGreater(late["mixture_weight"], early["mixture_weight"])

    def test_all_unknown_or_negative_judgments_leave_prior_unchanged(self):
        prior = {"a": 0.9, "b": 0.1}
        for mechanism in ("unknown", "unrelated"):
            with self.subTest(mechanism=mechanism):
                fused, audit = ceg_fusion.coverage_weighted_fusion(prior, {
                    "a": judgment(1, 0.99, mechanism, False),
                    "b": judgment(2, 0.99, mechanism, False),
                })
                self.assertEqual(fused, prior)
                self.assertEqual(audit["mixture_weight"], 0)

    def test_invalid_or_incomplete_permutation_cannot_drive_update(self):
        prior = {"a": 0.9, "b": 0.1}
        for bad_rank in (1, 3, None, "invalid", 1.5, True):
            with self.subTest(rank=bad_rank):
                fused, audit = ceg_fusion.coverage_weighted_fusion(prior, {
                    "a": judgment(), "b": judgment(bad_rank),
                })
                self.assertEqual(fused, prior)
                self.assertFalse(audit["ordinal_permutation_valid"])

    def test_repaired_permutation_and_nonfinite_confidence_are_rejected(self):
        prior = {"a": 0.9, "b": 0.1}
        for patch in ({"ordinal_permutation_valid": False}, {"confidence": float("nan")}, {"confidence": float("inf")}):
            with self.subTest(patch=patch):
                fused, _ = ceg_fusion.coverage_weighted_fusion(prior, {
                    "a": judgment(2), "b": {**judgment(), **patch},
                })
                self.assertEqual(fused, prior)

    def test_low_confidence_does_not_get_normalized_into_full_trust(self):
        prior = {"a": 0.99, "b": 0.01}
        fused, audit = ceg_fusion.coverage_weighted_fusion(prior, {
            "a": judgment(2, 0, "unknown", False), "b": judgment(1, 0.01),
        })
        self.assertLessEqual(audit["mixture_weight"], 0.005)
        self.assertLess(fused["b"], 0.02)

    def test_more_than_one_supported_candidate_uses_rank_and_confidence(self):
        fused, _ = ceg_fusion.coverage_weighted_fusion(
            {"a": 0.05, "b": 0.05, "c": 0.9},
            {"a": judgment(), "b": judgment(2), "c": judgment(3, 0.9, "unrelated", False)},
        )
        self.assertGreater(fused["a"], fused["b"])
        self.assertGreater(fused["b"], 0.05)

    def test_invalid_prior_fails_explicitly(self):
        for prior in ({}, {"a": 0}, {"a": -1, "b": 2}, {"a": float("nan")}):
            with self.subTest(prior=prior):
                with self.assertRaises(ValueError):
                    ceg_fusion.coverage_weighted_fusion(prior, {})

    def test_experiment_namespace_is_separate_and_idempotent(self):
        historical = "terra-ceg-v6"
        self.assertEqual(ceg_fusion.isolated_namespace(historical, "legacy"), historical)
        experimental = ceg_fusion.isolated_namespace(historical, ceg_fusion.POLICY)
        self.assertNotEqual(experimental, historical)
        self.assertEqual(ceg_fusion.isolated_namespace(experimental, ceg_fusion.POLICY), experimental)


class FusionSelectionTests(unittest.TestCase):
    def make_inputs(self):
        records = [lm_bisect.CommitRecord(
            index=i + 1, sha=f"{i:040x}", subject="candidate", body="",
            changed_files=[], diff_text="", semantic_score=0.0,
            build_success_prob=1.0, suspicion_weight=0.0,
        ) for i in range(8)]
        masses = [0.005, 0.005, 0.005, 0.005, 0.47, 0.005, 0.5, 0.005]
        for i, record in enumerate(records):
            record.causal_evidence = {"ordinal_judgment": judgment(
                1 if i == 2 else (i + 2 if i < 2 else i + 1),
                0.83 if i == 2 else 0.2,
                "invariant-break" if i == 2 else "unrelated", i == 2,
            )}
        evidence = {
            "prior_mass_by_sha": {r.sha: p for r, p in zip(records, masses)},
            "candidate_by_sha": {r.sha: {"mass": p, "score": p, "hits": []} for r, p in zip(records, masses)},
        }
        profile = lm_bisect.IssueProfile(
            issue_id="test", issue_url="", title="", good_commit="", good_ref="",
            bad_commit="", bisect_log="", runner="", bug_report_summary="",
            keywords=[], relevant_paths=[], high_risk_paths=[],
        )
        return profile, records, evidence

    def test_new_fusion_selects_supported_low_prior_candidate(self):
        profile, records, evidence = self.make_inputs()
        old = lm_bisect.causal_evidence_guided_selection(profile, records, evidence)
        new = lm_bisect.causal_evidence_guided_selection(
            profile, records, evidence, fusion_policy="coverage-mixture-v1",
        )
        self.assertNotEqual(old.selected.sha, records[2].sha)
        self.assertEqual(new.selected.sha, records[2].sha)
        self.assertEqual(new.selection_mode, "causal-evidence-guided-calibrated-posterior")

    def test_existing_direct_and_parent_shortcut_survives(self):
        profile, records, evidence = self.make_inputs()
        records[2].causal_evidence["ordinal_judgment"]["confidence"] = 0.97
        decision = lm_bisect.causal_evidence_guided_selection(
            profile, records, evidence, fusion_policy="coverage-mixture-v1",
        )
        self.assertEqual(decision.selection_mode, "causal-evidence-guided-high-confidence-probe")
        transition = lm_bisect.causal_evidence_guided_transition(
            [r.sha for r in records], selected_sha=decision.selected.sha, verdict="bad",
            phase="search", high_confidence_probe_selected=True, candidate_parent_sha=records[1].sha,
        )
        self.assertEqual(transition["phase"], "parent-validation")

    def test_invalid_batch_cannot_leak_into_bonus_or_direct_probe(self):
        profile, records, evidence = self.make_inputs()
        records[2].causal_evidence["ordinal_judgment"]["confidence"] = 0.99
        records[0].causal_evidence["ordinal_judgment"]["rank"] = 1
        decision = lm_bisect.causal_evidence_guided_selection(
            profile, records, evidence, fusion_policy=ceg_fusion.POLICY,
        )
        self.assertEqual(decision.selection_mode, "causal-evidence-guided-calibrated-posterior")
        self.assertFalse(decision.metadata["fusion"]["ordinal_permutation_valid"])

    def test_explicit_frontier_requires_every_frontier_judgment(self):
        profile, records, evidence = self.make_inputs()
        frontier = {record.sha for record in records[:4]}
        records[3].causal_evidence = None
        decision = lm_bisect.causal_evidence_guided_selection(
            profile, records, evidence, fusion_policy=ceg_fusion.POLICY,
            frontier_shas=frontier,
        )
        self.assertEqual(decision.metadata["fusion"]["reason"], "missing-or-invalid-ordinal-permutation")

    def test_direct_probe_is_limited_to_the_scored_frontier(self):
        profile, records, evidence = self.make_inputs()
        records[6].causal_evidence["ordinal_judgment"] = judgment(
            1, 0.99, "invariant-break", True,
        )
        decision = lm_bisect.causal_evidence_guided_selection(
            profile, records, evidence, fusion_policy=ceg_fusion.POLICY,
            frontier_shas={record.sha for record in records[:4]},
        )
        self.assertNotEqual(decision.selection_mode, "causal-evidence-guided-high-confidence-probe")

    def test_cli_defaults_to_historical_policy(self):
        parser = lm_bisect.build_parser()
        args = parser.parse_args(["run-online", "--issue", "demo"])
        self.assertEqual(args.ceg_fusion_policy, "legacy")
        args = parser.parse_args(["run-online", "--issue", "demo", "--ceg-fusion-policy", "coverage-mixture-v1"])
        self.assertEqual(args.ceg_fusion_policy, "coverage-mixture-v1")


if __name__ == "__main__":
    unittest.main()
