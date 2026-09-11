from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.audit_first_bad_relevance import CANONICAL_FIRST_BAD
from tools.export_master50_evidence_bundle import (
    PROFILE_EXCLUSIONS,
    first_bad_map,
    select_issues,
)
from tools.master50_evidence_audit import audit_bundle
from tools.refresh_master50_evidence_bundle import render_review_report


ROOT = Path(__file__).resolve().parents[1]


class MasterFiftyEvidenceBundleTests(unittest.TestCase):
    def test_audit_requires_metadata_to_match_packaged_running_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            sha = "a" * 40
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": sha,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                            "running_pass": "instcombine",
                            "component": "instcombine",
                            "scope": "function",
                            "target": "main",
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "reproducer.c").write_text("int main(void) {}\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n"
                '1. Running pass "instcombine" on function "main"\n'
            )

            self.assertTrue(audit_bundle(bundle_root, expected_case_count=1).ok)
            metadata = json.loads((case_dir / "metadata.json").read_text())
            metadata["crash_evidence"]["component"] = "loop-vectorize"
            (case_dir / "metadata.json").write_text(json.dumps(metadata))
            report = audit_bundle(bundle_root, expected_case_count=1)
            self.assertFalse(report.ok)
            self.assertTrue(any("Running pass facts" in failure for failure in report.failures))

    def test_audit_rejects_stale_runtime_signal_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                            "runtime_signals": {
                                "failure_marker": True,
                                "stack_trace": True,
                                "running_pass": True,
                            },
                            "runtime_signal_status": "complete",
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "reproducer.c").write_text("int main(void) {}\n")
            (case_dir / "crash-assertion.err").write_text("Assertion `condition' failed.\n")

            report = audit_bundle(bundle_root, expected_case_count=1)

            self.assertFalse(report.ok)
            self.assertTrue(any("runtime signals" in failure for failure in report.failures))

    def test_refresh_review_report_labels_replays_and_signal_only_crashes(self) -> None:
        cases = [
            {
                "issue": "pr1",
                "title": "First",
                "validated_first_bad": "a" * 40,
                "first_bad_subject": "subject",
                "crash_evidence": {
                    "kind": "assertion",
                    "excerpt": "Assertion `x' failed.",
                    "runtime_signals": {
                        "failure_marker": True,
                        "stack_trace": True,
                        "running_pass": True,
                    },
                    "runtime_signal_status": "complete",
                },
                "crash_capture": {"capture_target": "validated-first-bad"},
                "manual_static_analysis": {
                    "dependency_chain": "producer -> detector",
                    "inspection_focus": "inspect producer",
                },
                "static_overlap": {"classification": "direct", "path_overlap": ["a.cpp"]},
            },
            {
                "issue": "pr2",
                "title": "Second",
                "validated_first_bad": "b" * 40,
                "first_bad_subject": "subject",
                "crash_evidence": {
                    "kind": "crash",
                    "excerpt": "SIGSEGV",
                    "runtime_signals": {
                        "failure_marker": True,
                        "stack_trace": False,
                        "running_pass": False,
                    },
                    "runtime_signal_status": "incomplete",
                },
                "manual_static_analysis": {},
                "static_overlap": {},
            },
        ]

        report = render_review_report(cases)

        self.assertIn("fresh first-bad replay", report)
        self.assertIn("curated first-bad artifact", report)
        self.assertIn("crash-signal confirmation", report)
        self.assertIn("Concrete signal-only crash artifacts: **1/2**", report)
        self.assertIn("Complete marker/stack/pass triplets: **1/2**", report)
        self.assertIn("present: marker; missing: stack, pass", report)

    def test_evidence_audit_cli_runs_from_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            (bundle_root / "cases").mkdir(parents=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "master50_evidence_audit.py"),
                    str(bundle_root),
                    "--expected-case-count",
                    "0",
                ],
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("case_count=0", result.stdout)

    def test_audit_requires_every_case_to_have_concrete_crash_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            cases_root = bundle_root / "cases"
            for issue in ("pr1", "pr2"):
                case_dir = cases_root / issue
                case_dir.mkdir(parents=True)
                (case_dir / "metadata.json").write_text(
                    json.dumps(
                        {
                            "issue": issue,
                            "validated_first_bad": "a" * 40,
                            "crash_evidence": {
                                "kind": "assertion",
                                "excerpt": "Assertion `condition' failed.",
                            },
                            "crash_capture": {
                                "tested_sha": "a" * 40,
                                "validated_first_bad": "a" * 40,
                                "capture_target": "validated-first-bad",
                                "marker_detected": True,
                            },
                        }
                    )
                )
                (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
                (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
                (case_dir / "crash-assertion.err").write_text(
                    "Assertion `condition' failed.\nStack dump:\n"
                )
                (case_dir / "reproducer.cpp").write_text("int main() {}\n")

            report = audit_bundle(bundle_root, expected_case_count=2)

            self.assertTrue(report.ok)
            self.assertEqual(report.evidence_count, 2)
            (cases_root / "pr2" / "crash-assertion.err").write_text("normal output\n")
            self.assertFalse(audit_bundle(bundle_root, expected_case_count=2).ok)

    def test_strict_runtime_audit_requires_stack_and_running_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": "a" * 40,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "reproducer.c").write_text("int main(void) {}\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n"
            )

            report = audit_bundle(
                bundle_root, expected_case_count=1, require_runtime_signals=True
            )

            self.assertFalse(report.ok)
            self.assertTrue(any("stack_trace" in failure for failure in report.failures))
            self.assertTrue(any("running_pass" in failure for failure in report.failures))

    def test_audit_rejects_missing_reproducer_or_endpoint_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": "b" * 40,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                        },
                        "crash_capture": {"tested_sha": "a" * 40},
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n"
            )

            report = audit_bundle(bundle_root, expected_case_count=1)

            self.assertFalse(report.ok)
            self.assertTrue(any("reproducer" in failure for failure in report.failures))
            self.assertTrue(any("first-bad" in failure for failure in report.failures))

    def test_audit_accepts_curated_first_bad_evidence_without_a_replay_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": "a" * 40,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                            "source_artifact": "results/issues/pr1/abc.err",
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n"
            )
            (case_dir / "reproducer.c").write_text("int main(void) {}\n")

            report = audit_bundle(bundle_root, expected_case_count=1)

            self.assertTrue(report.ok, report.failures)

    def test_audit_rejects_generic_excerpt_and_oversized_capture_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            sha = "a" * 40
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": sha,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "A crash/assertion marker was captured at the profile bad endpoint.",
                        },
                        "crash_capture": {
                            "tested_sha": sha,
                            "validated_first_bad": sha,
                            "capture_target": "validated-first-bad",
                            "marker_detected": True,
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "reproducer.cpp").write_text("int main() {}\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n" + "x" * (64 * 1024)
            )

            report = audit_bundle(bundle_root, expected_case_count=1)

            self.assertFalse(report.ok)
            self.assertTrue(any("too large" in failure for failure in report.failures))
            self.assertTrue(any("generic" in failure for failure in report.failures))

    def test_audit_accepts_a_multi_file_reproducer_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_root = Path(tmp_dir) / "master50-evidence"
            case_dir = bundle_root / "cases" / "pr1"
            case_dir.mkdir(parents=True)
            sha = "a" * 40
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "issue": "pr1",
                        "validated_first_bad": sha,
                        "crash_evidence": {
                            "kind": "assertion",
                            "excerpt": "Assertion `condition' failed.",
                        },
                        "crash_capture": {
                            "tested_sha": sha,
                            "validated_first_bad": sha,
                            "capture_target": "validated-first-bad",
                            "marker_detected": True,
                        },
                    }
                )
            )
            (case_dir / "first-bad.patch").write_text("diff --git a/a b/a\n")
            (case_dir / "first-bad-compact.diff").write_text("diff --git a/a b/a\n")
            (case_dir / "crash-assertion.err").write_text(
                "Assertion `condition' failed.\n"
            )
            (case_dir / "reproducer").mkdir()
            (case_dir / "reproducer" / "source.cpp").write_text("int main() {}\n")
            (case_dir / "reproducer" / "header.h").write_text("int value;\n")

            report = audit_bundle(bundle_root, expected_case_count=1)

            self.assertTrue(report.ok, report.failures)

    def test_profile_cohort_is_exactly_fifty(self) -> None:
        profiles = json.loads((ROOT / "tools" / "lm_bisect_profiles.json").read_text(encoding="utf-8"))
        issues = select_issues(profiles)
        self.assertEqual(len(issues), 50)
        self.assertTrue(set(issues).isdisjoint(PROFILE_EXCLUSIONS))
        self.assertIn("pr203519", issues)
        self.assertIn("pr194590", issues)
        self.assertNotIn("pr176682", issues)
        self.assertNotIn("pr191581", issues)
        self.assertTrue(set(CANONICAL_FIRST_BAD).issubset(issues))

    def test_scoped_first_bads_keep_canonical_shas(self) -> None:
        profiles = json.loads((ROOT / "tools" / "lm_bisect_profiles.json").read_text(encoding="utf-8"))
        mapping = first_bad_map(profiles, ROOT / "web-presentation-data" / "data" / "site-data.json")
        for issue, sha in CANONICAL_FIRST_BAD.items():
            self.assertEqual(mapping[issue], sha)
        self.assertEqual(mapping["pr203519"], "a460c8e8dafc28fef240fce44dcbed043fe56a71")
        self.assertEqual(mapping["pr194590"], "d19e954b83cb497c03cccb0e9874cb9f1a51b18d")


if __name__ == "__main__":
    unittest.main()
