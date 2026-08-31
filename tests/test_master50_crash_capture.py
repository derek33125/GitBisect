from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import master50_crash_capture


class MasterFiftyCrashCaptureTests(unittest.TestCase):
    def test_queue_prints_dashed_banner_as_data(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn("printf '%s\\n' '--- runner output ---'", script)

    def test_queue_derives_worktree_root_from_base_repo(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn(
            'WORK_ROOT="${WORK_ROOT:-$(dirname "$BASE_REPO")/worktrees}"', script
        )

    def test_queue_cleans_worktree_when_capture_fails(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn("trap cleanup_worktree EXIT", script)

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        issue = "pr1"
        runner = root / "scripts" / issue / "bisect-runner.sh"
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/usr/bin/env bash\n")
        runner.chmod(0o755)
        (root / "tools").mkdir(exist_ok=True)
        (root / "tools" / "lm_bisect_profiles.json").write_text(
            json.dumps(
                {
                    issue: {
                        "bad_commit": "a" * 40,
                        "runner": "scripts/pr1/bisect-runner.sh",
                    }
                }
            )
        )
        evidence_root = root / "human_analysis" / "raw" / "master50-evidence-test"
        case_dir = evidence_root / "cases" / issue
        case_dir.mkdir(parents=True)
        (case_dir / "metadata.json").write_text(
            json.dumps({"issue": issue, "crash_evidence": {"kind": "missing"}})
        )
        return evidence_root, case_dir

    def test_capture_with_assertion_writes_artifact_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `value != nullptr' failed.\nStack dump:\n")

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="a" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertTrue(result.marker_detected)
            self.assertEqual(
                (case_dir / "crash-assertion.err").read_text(), log.read_text()
            )
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "assertion")
            self.assertEqual(metadata["crash_capture"]["tested_sha"], "a" * 40)
            self.assertEqual(metadata["crash_capture"]["source_log"], "results/issues/pr1/capture.log")

    def test_capture_without_marker_keeps_artifact_absent_and_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("program output: 0\nrepro exit code: 1\n")

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="a" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertFalse(result.marker_detected)
            self.assertFalse((case_dir / "crash-assertion.err").exists())
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "missing")
            self.assertFalse(metadata["crash_capture"]["marker_detected"])

    def test_capture_without_marker_preserves_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            metadata_path = case_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text())
            metadata["crash_evidence"] = {
                "kind": "assertion",
                "excerpt": "Previously captured assertion.",
                "source_artifact": "results/issues/pr1/previous.log",
            }
            metadata_path.write_text(json.dumps(metadata))
            artifact = case_dir / "crash-assertion.err"
            artifact.write_text("Assertion `saved' failed.\n")
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("program output: 0\n")

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="a" * 40,
                exit_code=0,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertEqual(artifact.read_text(), "Assertion `saved' failed.\n")
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "assertion")
            self.assertEqual(
                metadata["crash_evidence"]["source_artifact"],
                "results/issues/pr1/previous.log",
            )

    def test_loads_profile_bad_commit_and_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, _case_dir = self._fixture(root)

            metadata = master50_crash_capture.load_capture_metadata(
                root, evidence_root, "pr1"
            )

            self.assertEqual(metadata.bad_commit, "a" * 40)
            self.assertEqual(metadata.runner, Path("scripts/pr1/bisect-runner.sh"))


if __name__ == "__main__":
    unittest.main()
