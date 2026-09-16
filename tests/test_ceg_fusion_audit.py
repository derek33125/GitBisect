import unittest

from tools import ceg_fusion_audit


class FusionAuditTests(unittest.TestCase):
    def case(self):
        return {
            "issue": "demo", "run_label": "frozen-demo", "first_bad_commit": "b",
            "steps": [{
                "step": 1, "candidate_count": 10, "after": 5, "verdict": "bad",
                "selection_mode": "causal-evidence-guided-calibrated-posterior",
                "selected_sha": "a", "old_fusion_count": 1,
                "frontier_shas": ["a", "b"],
                "frontier": {
                    "a": {"mass": 0.8, "judgment": {
                        "rank": 2, "confidence": 0.9, "mechanism": "unrelated",
                        "explains_failure": False, "ordinal_permutation_valid": True,
                    }},
                    "b": {"mass": 0.01, "judgment": {
                        "rank": 1, "confidence": 0.83, "mechanism": "invariant-break",
                        "explains_failure": True, "ordinal_permutation_valid": True,
                    }},
                },
            }],
        }

    def test_mass_only_audit_uses_full_candidate_count(self):
        report = ceg_fusion_audit.analyze({"cases": [self.case()]})
        step = report["cases"][0]["steps"][0]
        self.assertAlmostEqual(step["fusion"]["candidate_coverage"], 0.2)
        self.assertGreater(step["supported_candidates"][0]["new_mass"], 0.01)
        self.assertFalse(report["is_full_search_replay"])

    def test_parent_proofs_and_skips_do_not_inflate_posterior_denominator(self):
        case = self.case()
        base = case["steps"][0]
        case["steps"] += [
            {**base, "selection_mode": "causal-evidence-guided-parent-validation", "old_fusion_count": 0},
            {**base, "verdict": "skip", "old_fusion_count": 0},
        ]
        stats = ceg_fusion_audit.analyze({"cases": [case]})["summary"]
        self.assertEqual(stats["posterior_directional_steps"], 1)
        self.assertEqual(stats["posterior_zero_or_singleton_fusion"], 1)

    def test_missing_frontier_judgments_are_excluded_explicitly(self):
        case = self.case()
        del case["steps"][0]["frontier"]["a"]
        report = ceg_fusion_audit.analyze({"cases": [case]})
        self.assertEqual(report["summary"]["incomplete_frontiers"], 1)
        self.assertIsNone(report["cases"][0]["steps"][0]["fusion"])


if __name__ == "__main__":
    unittest.main()
