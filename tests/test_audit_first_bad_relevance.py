from __future__ import annotations

import unittest

from tools import audit_first_bad_relevance


class CrashEvidenceTests(unittest.TestCase):
    def test_extracts_assertion_from_runner_output(self) -> None:
        evidence = audit_first_bad_relevance.extract_crash_evidence(
            "build output\nAssertion `Value && \"broken invariant\"' failed.\nStack dump:\n"
        )

        self.assertEqual(evidence.source, "assertion")
        self.assertIn("broken invariant", evidence.text)
        self.assertEqual(evidence.artifact, "")

    def test_labels_wrapper_only_runner_output(self) -> None:
        evidence = audit_first_bad_relevance.extract_crash_evidence(
            "=== validated-bisect-runner ===\nrepro exit code: 134\nverdict: bad"
        )

        self.assertEqual(evidence.source, "wrapper-only")
        self.assertIn("does not contain", evidence.text)


class RelevanceClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "keywords": ["crash", "MemorySSA", "removeFromLookups", "optimizer"],
            "relevant_paths": ["llvm/lib/Analysis/MemorySSA.cpp", "llvm/lib/Transforms/Scalar"],
        }

    def test_classifies_file_and_symbol_match_as_direct(self) -> None:
        assessment = audit_first_bad_relevance.assess_relevance(
            self.profile,
            "[MemorySSA] Update removeFromLookups",
            ["llvm/lib/Analysis/MemorySSA.cpp"],
            "void MemorySSA::removeFromLookups() {}",
        )

        self.assertEqual(assessment.classification, "direct")
        self.assertIn("MemorySSA", assessment.keyword_overlap)
        self.assertEqual(assessment.path_overlap, ["llvm/lib/Analysis/MemorySSA.cpp"])

    def test_classifies_no_overlap_as_unrelated(self) -> None:
        assessment = audit_first_bad_relevance.assess_relevance(
            self.profile,
            "[Docs] Update release notes",
            ["docs/ReleaseNotes.rst"],
            "Documentation only.",
        )

        self.assertEqual(assessment.classification, "unrelated")
        self.assertEqual(assessment.keyword_overlap, [])
        self.assertEqual(assessment.path_overlap, [])

    def test_does_not_treat_generic_assertion_word_as_direct_mechanism_match(self) -> None:
        assessment = audit_first_bad_relevance.assess_relevance(
            self.profile,
            "[Scalar] Generalize a transformation",
            ["llvm/lib/Transforms/Scalar/Example.cpp"],
            "assert(condition);",
        )

        self.assertEqual(assessment.classification, "partial")


class CausalInterpretationTests(unittest.TestCase):
    def test_scoped_cases_have_a_manual_causal_interpretation(self) -> None:
        self.assertEqual(
            set(audit_first_bad_relevance.CAUSAL_INTERPRETATIONS),
            set(audit_first_bad_relevance.SCOPED_ISSUES),
        )

    def test_indirect_case_explains_downstream_detector(self) -> None:
        interpretation = audit_first_bad_relevance.CAUSAL_INTERPRETATIONS["pr50585"]

        self.assertEqual(interpretation.role, "indirect-enabling")
        self.assertIn("verifier", interpretation.explanation)


if __name__ == "__main__":
    unittest.main()
