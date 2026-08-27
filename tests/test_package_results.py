from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import package_results


class PackageResultsTests(unittest.TestCase):
    def test_default_remote_sources_target_current_result_roots(self) -> None:
        sources = {source.name: source for source in package_results.default_remote_sources()}

        self.assertEqual(
            sources["edu-server"].remote_results,
            "/home/derek/gitbisect-work/k12-runner-20260724/results",
        )
        self.assertEqual(
            sources["aws-server"].remote_results,
            "/home/ubuntu/gitbisect-work/k12-runner-20260725-causal-impl-v2/results",
        )

    def test_package_filters_intermediate_docs_and_keeps_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "results"
            output = root / "packages"

            keep_files = [
                source / "issues" / "pr1" / "run.outer.log",
                source / "issues" / "pr1" / "bisect.txt",
                source / "issues" / "pr1" / "summary.tsv",
                source / "issues" / "pr1" / "tmp" / "trace.err",
                source / "lm_bisect_runs" / "run.json",
                source / "lm_bisect_observations" / "obs.json",
            ]
            drop_files = [
                source / "issue-status-report.md",
                source / "issues" / "pr1" / "notes.md",
                source / "reports" / "summary.json",
                source / "packages" / "old.zip",
                source / "package-staging" / "edu-server" / "results" / "issues" / "remote.log",
                source / "issues" / "local-jobs" / "queue.todo",
                source / "issues" / "local-jobs" / "queue.done",
            ]
            for path in keep_files + drop_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{path.name}\n")

            package_path, packaged_files = package_results.build_package(
                source_root=source,
                source_name="local",
                output_dir=output,
                timestamp="20260627T170000Z",
            )

            self.assertEqual(package_path.name, "gitbisect-results-local-20260627T170000Z.zip")
            self.assertEqual(
                sorted(packaged_files),
                [
                    "issues/pr1/bisect.txt",
                    "issues/pr1/run.outer.log",
                    "issues/pr1/summary.tsv",
                    "issues/pr1/tmp/trace.err",
                    "lm_bisect_observations/obs.json",
                    "lm_bisect_runs/run.json",
                ],
            )
            with zipfile.ZipFile(package_path) as archive:
                names = sorted(archive.namelist())

            self.assertIn("local/package-manifest.json", names)
            self.assertIn("local/issues/pr1/run.outer.log", names)
            self.assertNotIn("local/issue-status-report.md", names)
            self.assertNotIn("local/reports/summary.json", names)
            self.assertNotIn("local/packages/old.zip", names)
            self.assertNotIn("local/package-staging/edu-server/results/issues/remote.log", names)


if __name__ == "__main__":
    unittest.main()
