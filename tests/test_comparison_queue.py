from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path("scripts/benchmark/run-comparison-queue-20260630.sh")


class ComparisonQueueTests(unittest.TestCase):
    def test_runs_project_relative_commands_from_resolved_root(self) -> None:
        text = SCRIPT.read_text()

        root_assignment = text.index('ROOT="${ROOT:-${DEFAULT_ROOT}}"')
        root_change = text.index('cd "${ROOT}"')
        command = text.index('"${PY}" tools/lm_bisect.py run-online')

        self.assertLess(root_assignment, root_change)
        self.assertLess(root_change, command)

    def test_supports_adaptive_parent_extraction_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('adaptive-parent-extract)', text)
        self.assertIn('--adaptive-top-k-threshold "${ADAPTIVE_TOP_K_THRESHOLD}"', text)
        self.assertIn('--adaptive-top-k-large "${ADAPTIVE_TOP_K_LARGE}"', text)
        self.assertIn('--adaptive-top-k-small "${ADAPTIVE_TOP_K_SMALL}"', text)
        self.assertIn('--model-cache-namespace "${MODEL_CACHE_NAMESPACE}"', text)

    def test_supports_oracle_first_bad_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-first-bad-heuristic)', text)
        self.assertIn('oracle_first_bad_commit()', text)
        self.assertIn('--oracle-first-bad-sha "${oracle_bad}"', text)

    def test_supports_oracle_major_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major', text)

    def test_supports_combined_oracle_major_tuned_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-tuned-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned', text)

    def test_supports_refined_combined_oracle_major_tuned_keyword_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-tuned-semantic-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned-semantic', text)

    def test_supports_direct_combined_oracle_anchor_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-anchor-major-tuned-keyword-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned-anchor', text)

    def test_direct_anchor_failure_does_not_drop_remaining_diagnostics(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('if wait "${child_pid}"; then', text)
        self.assertIn('RUN_ISSUE_EXIT_CODE=$?', text)
        self.assertIn('if (( RUN_ISSUE_EXIT_CODE != 0 )); then', text)
        self.assertIn('retained failed direct-anchor validation for ${issue} exit=${RUN_ISSUE_EXIT_CODE}', text)
        self.assertIn('[[ "${MODE}" == "oracle-anchor-major-tuned-keyword-heuristic" ]]', text)

    def test_direct_anchor_queue_continues_after_failed_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            root = tmp / "root"
            base_repo = tmp / "base-repo"
            work_root = tmp / "worktrees"
            fake_bin = tmp / "bin"
            fake_python = root / ".venv" / "bin" / "python"
            run_log = tmp / "runner-invocations.log"
            root.mkdir()
            base_repo.mkdir()
            fake_bin.mkdir()
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"-\" ]]; then\n"
                "  printf '%040d\\n' 1\n"
                "  exit 0\n"
                "fi\n"
                "printf '%s\\n' \"$*\" >> \"$RUN_LOG\"\n"
                "exit 42\n"
            )
            fake_python.chmod(0o755)
            fake_git = fake_bin / "git"
            fake_git.write_text("#!/usr/bin/env bash\nexit 0\n")
            fake_git.chmod(0o755)
            environment = os.environ | {
                "ROOT": str(root),
                "BASE_REPO": str(base_repo),
                "WORK_ROOT": str(work_root),
                "PY": str(fake_python),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RUN_LOG": str(run_log),
                "STARTUP_GRACE_SECONDS": "0",
            }

            completed = subprocess.run(
                [
                    "bash",
                    str(SCRIPT.resolve()),
                    "oracle-anchor-major-tuned-keyword-heuristic",
                    "test-direct-anchor",
                    "pr204559",
                    "pr204589",
                ],
                cwd=tmp,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            queue_log = (root / "results" / "issues" / "server-jobs" / "test-direct-anchor.log").read_text()
            run_log_text = run_log.read_text()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(run_log_text.count("run-online"), 2)
        self.assertEqual(queue_log.count("retained failed direct-anchor validation"), 2)
        self.assertIn("exit=42", queue_log)

    def test_model_modes_accept_environment_model_configuration(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('MODEL_NAME="${MODEL_NAME:-}"', text)
        self.assertIn('MODEL_REASONING_EFFORT="${MODEL_REASONING_EFFORT:-}"', text)
        self.assertIn('"${MODEL_ARGS[@]}"', text)


if __name__ == "__main__":
    unittest.main()
