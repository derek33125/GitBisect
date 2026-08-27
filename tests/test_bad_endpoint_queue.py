from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from tools import bad_endpoint_queue


class BadEndpointQueueTests(unittest.TestCase):
    def test_reads_bad_commit_from_run_bisect_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            issue_dir = root / "scripts" / "pr1"
            issue_dir.mkdir(parents=True)
            runner = issue_dir / "bisect-runner.sh"
            runner.write_text("#!/usr/bin/env bash\n")
            runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
            (issue_dir / "run-bisect.sh").write_text('BAD_COMMIT="abc123"\n')

            metadata = bad_endpoint_queue.load_issue_metadata(root, "pr1")

            self.assertEqual(metadata.issue, "pr1")
            self.assertEqual(metadata.bad_ref, "abc123")
            self.assertEqual(metadata.runner, Path("scripts/pr1/bisect-runner.sh"))

    def test_falls_back_to_validate_endpoints_default_bad_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            issue_dir = root / "scripts" / "pr2"
            issue_dir.mkdir(parents=True)
            runner = issue_dir / "bisect-runner.sh"
            runner.write_text("#!/usr/bin/env bash\n")
            runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
            (issue_dir / "validate-endpoints.sh").write_text(
                'BAD_COMMIT="${BAD_COMMIT:-def456}"\n'
            )

            metadata = bad_endpoint_queue.load_issue_metadata(root, "pr2")

            self.assertEqual(metadata.bad_ref, "def456")

    def test_rejects_issue_without_executable_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            issue_dir = root / "scripts" / "pr3"
            issue_dir.mkdir(parents=True)
            (issue_dir / "run-bisect.sh").write_text('BAD_COMMIT="abc123"\n')

            with self.assertRaises(FileNotFoundError):
                bad_endpoint_queue.load_issue_metadata(root, "pr3")

    def test_pr204790_bad_endpoint_metadata_is_wired(self) -> None:
        metadata = bad_endpoint_queue.load_issue_metadata(Path("."), "pr204790")

        self.assertEqual(metadata.issue, "pr204790")
        self.assertEqual(metadata.bad_ref, "ca7933e47d3a3451d81e72ac174dcb5aa28b59d1")
        self.assertEqual(metadata.runner, Path("scripts/pr204790/bisect-runner.sh"))

    def test_pr153916_bad_endpoint_metadata_is_wired(self) -> None:
        metadata = bad_endpoint_queue.load_issue_metadata(Path("."), "pr153916")

        self.assertEqual(metadata.issue, "pr153916")
        self.assertEqual(metadata.bad_ref, "3623fe661ae35c6c80ac221f14d85be76aa870f1")
        self.assertEqual(metadata.runner, Path("scripts/pr153916/bisect-runner.sh"))

    def test_pr157334_bad_endpoint_metadata_is_wired(self) -> None:
        metadata = bad_endpoint_queue.load_issue_metadata(Path("."), "pr157334")

        self.assertEqual(metadata.issue, "pr157334")
        self.assertEqual(metadata.bad_ref, "f628a5467addf2f8a597141ab01f7e7453e6d9a7^")
        self.assertEqual(metadata.runner, Path("scripts/pr157334/bisect-runner.sh"))


if __name__ == "__main__":
    unittest.main()
