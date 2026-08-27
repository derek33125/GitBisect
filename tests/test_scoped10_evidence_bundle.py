from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import scoped10_evidence_bundle


class ScopedTenEvidenceBundleTests(unittest.TestCase):
    def init_repo(self, root: Path) -> tuple[str, str]:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test User"], check=True)
        source = root / "llvm" / "lib" / "Example.cpp"
        source.parent.mkdir(parents=True)
        source.write_text("int value = 0;\n")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "good"], check=True)
        good = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        source.write_text("int value = 1;\n")
        subprocess.run(["git", "-C", str(root), "commit", "-am", "bad", "-q"], check=True)
        bad = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        return good, bad

    def test_build_bundle_keeps_only_curated_case_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            llvm_dir = root / "llvm-project"
            good, bad = self.init_repo(llvm_dir)
            issue_dir = root / "results" / "issues" / "pr1" / "run"
            issue_dir.mkdir(parents=True)
            (issue_dir / "repro.c").write_text("int main(void) { return 0; }\n")
            (issue_dir / f"{bad[:12]}.err").write_text("Assertion `value && \"bad\"' failed.\n")
            (issue_dir / "notes.md").write_text("must not be packaged\n")

            profile = {
                "title": "example assertion",
                "good_commit": good,
                "bad_commit": bad,
                "bug_report_summary": "example crash",
                "keywords": ["Example"],
                "relevant_paths": ["llvm/lib/Example.cpp"],
                "high_risk_paths": ["llvm/lib"],
                "runner": "scripts/pr1/bisect-runner.sh",
            }
            output_dir = root / "out"
            bundle = scoped10_evidence_bundle.build_bundle(
                llvm_dir=llvm_dir,
                source_root=root,
                profiles={"pr1": profile},
                issues=("pr1",),
                output_dir=output_dir,
                label="test",
            )

            self.assertTrue(bundle.archive_path.exists())
            self.assertTrue(bundle.manifest_path.exists())
            manifest = json.loads(bundle.manifest_path.read_text())
            self.assertEqual(manifest["case_count"], 1)
            self.assertEqual(manifest["cases"][0]["issue"], "pr1")
            self.assertEqual(manifest["cases"][0]["crash_evidence"]["kind"], "assertion")
            with zipfile.ZipFile(bundle.archive_path) as archive:
                names = sorted(archive.namelist())

            self.assertIn("scoped10-evidence-test/manifest.json", names)
            self.assertIn("scoped10-evidence-test/cases/pr1/metadata.json", names)
            self.assertIn("scoped10-evidence-test/cases/pr1/first-bad.patch", names)
            self.assertIn("scoped10-evidence-test/cases/pr1/crash-assertion.err", names)
            self.assertIn("scoped10-evidence-test/cases/pr1/reproducer.c", names)
            self.assertNotIn("scoped10-evidence-test/cases/pr1/notes.md", names)

    def test_scoped_cases_have_manual_static_analysis(self) -> None:
        self.assertEqual(len(scoped10_evidence_bundle.SCOPED_ISSUES), 10)
        self.assertEqual(
            set(scoped10_evidence_bundle.SCOPED_ISSUES),
            set(scoped10_evidence_bundle.MANUAL_STATIC_ANALYSIS),
        )

    def test_falls_back_to_retained_runner_history_when_no_trace_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_dir = root / "web-presentation-data" / "scoped10" / "raw" / "aws"
            raw_dir.mkdir(parents=True)
            history = raw_dir / "pr1-model-topk3-fresh.json"
            history.write_text('{"steps": [{"verdict": "bad"}]}\n')

            self.assertEqual(
                scoped10_evidence_bundle.find_verdict_artifact(root, "pr1"),
                history,
            )


if __name__ == "__main__":
    unittest.main()
