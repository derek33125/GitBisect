from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import master50_crash_capture


class MasterFiftyCrashCaptureTests(unittest.TestCase):
    def test_extracts_explicit_running_pass_and_normalizes_component(self) -> None:
        output = (
            "1. Running pass 'Function Pass Manager' on module 'input.ll'.\n"
            "2. Running pass \"loop-vectorize<no-interleave>\" on function \"main\"\n"
        )

        self.assertEqual(
            master50_crash_capture.extract_running_passes(output),
            [
                {
                    "pass": "Function Pass Manager",
                    "scope": "module",
                    "target": "input.ll",
                },
                {
                    "pass": "loop-vectorize<no-interleave>",
                    "scope": "function",
                    "target": "main",
                },
            ],
        )
        self.assertEqual(
            master50_crash_capture.running_pass_evidence(output),
            {
                "running_pass": "loop-vectorize<no-interleave>",
                "component": "loop-vectorize",
                "scope": "function",
                "target": "main",
                "pass_record_count": 2,
            },
        )

    def test_running_pass_parser_does_not_infer_from_unstructured_text(self) -> None:
        self.assertIsNone(
            master50_crash_capture.running_pass_evidence(
                "The running pass component is probably loop-vectorize.\n"
            )
        )

    def test_extracts_explicit_debug_pass_manager_record_without_guessing_scope(self) -> None:
        self.assertEqual(
            master50_crash_capture.extract_running_passes(
                "Running pass: InstCombinePass on main (4 instructions)\n"
            ),
            [
                {
                    "pass": "InstCombinePass",
                    "scope": "unknown",
                    "target": "main (4 instructions)",
                }
            ],
        )

    def test_extracts_decorated_stack_running_pass_record(self) -> None:
        self.assertEqual(
            master50_crash_capture.extract_running_passes(
                '*** Running pass "instcombine" on function "main" ***\n'
            ),
            [
                {
                    "pass": "instcombine",
                    "scope": "function",
                    "target": "main",
                }
            ],
        )

    def test_extracts_legacy_pass_execution_record_as_runtime_evidence(self) -> None:
        self.assertEqual(
            master50_crash_capture.extract_running_passes(
                "Executing Pass 'Loop Pass Manager' on Function 'main'...\n"
                "Executing Pass 'Loop Strength Reduction' on Function 'main'...\n"
            ),
            [
                {
                    "pass": "Loop Pass Manager",
                    "scope": "function",
                    "target": "main",
                },
                {
                    "pass": "Loop Strength Reduction",
                    "scope": "function",
                    "target": "main",
                },
            ],
        )

    def test_complete_runtime_signals_require_marker_stack_and_explicit_pass(self) -> None:
        complete = (
            "Assertion `value != nullptr' failed.\n"
            "Stack dump:\n"
            'Running pass "instcombine" on function "main"\n'
        )
        self.assertEqual(master50_crash_capture.runtime_signal_gaps(complete), ())
        self.assertEqual(
            master50_crash_capture.runtime_signal_gaps("Assertion `x' failed.\n"),
            ("stack_trace", "running_pass"),
        )
        self.assertEqual(
            master50_crash_capture.runtime_signal_presence("Assertion `x' failed.\n"),
            {"failure_marker": True, "stack_trace": False, "running_pass": False},
        )

    def test_marker_context_retains_stack_before_a_trailing_shell_signal(self) -> None:
        output = (
            "PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
            "Stack dump:\n"
            'Running pass "loop-vectorize" on function "main"\n'
            "server-validation.sh: line 50: 123 Segmentation fault (core dumped)\n"
        )

        marker = master50_crash_capture.find_crash_marker(output)

        self.assertEqual(marker, output.splitlines()[-1])
        artifact = master50_crash_capture.artifact_with_pass_evidence(output, marker, None)
        self.assertEqual(master50_crash_capture.runtime_signal_gaps(artifact), ())

    def test_marker_context_retains_stack_before_frontend_driver_failure(self) -> None:
        output = (
            "Stack dump:\n"
            '1. Running pass "instcombine" on function "main"\n'
            "0. clang crash frame\n"
            "clang: error: clang frontend command failed with exit code 134\n"
        )

        marker = master50_crash_capture.find_crash_marker(output)

        self.assertEqual(
            marker,
            "clang: error: clang frontend command failed with exit code 134",
        )
        artifact = master50_crash_capture.artifact_with_pass_evidence(output, marker, None)
        self.assertTrue(artifact.startswith("Stack dump:\n"))
        self.assertEqual(master50_crash_capture.runtime_signal_gaps(artifact), ())

    def test_backfill_promotes_only_explicit_packaged_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            evidence_root = Path(tmp_dir) / "evidence"
            with_pass = evidence_root / "cases" / "pr1"
            without_pass = evidence_root / "cases" / "pr2"
            for case_dir in (with_pass, without_pass):
                case_dir.mkdir(parents=True)
                (case_dir / "metadata.json").write_text(
                    json.dumps({"issue": case_dir.name, "crash_evidence": {"kind": "assertion"}})
                )
            (with_pass / "crash-assertion.err").write_text(
                "Assertion `x' failed.\n"
                '1. Running pass "sroa<modify-cfg>" on function "test"\n'
            )
            (without_pass / "crash-assertion.err").write_text("Assertion `x' failed.\n")

            report = master50_crash_capture.backfill_running_passes(evidence_root)

            self.assertEqual(report, {"cases": 2, "with_running_pass": 1, "updated": 2})
            evidence = json.loads((with_pass / "metadata.json").read_text())["crash_evidence"]
            self.assertEqual(evidence["running_pass"], "sroa<modify-cfg>")
            self.assertEqual(evidence["component"], "sroa")
            self.assertEqual(evidence["source"], "crash-assertion.err")
            self.assertEqual(
                evidence["runtime_signals"],
                {"failure_marker": True, "stack_trace": False, "running_pass": True},
            )
            self.assertEqual(evidence["runtime_signal_status"], "incomplete")
            self.assertNotIn(
                "running_pass",
                json.loads((without_pass / "metadata.json").read_text())["crash_evidence"],
            )

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

    def test_queue_defaults_to_the_current_clean_replay_bundle(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn("master50-evidence-20260903-r4", script)

    def test_queue_cleans_worktree_when_capture_fails(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn("trap cleanup_worktree EXIT", script)

    def test_queue_records_capture_failure_and_continues_after_a_claim(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn("capture_record_failed", script)
        self.assertIn("return 0", script)
        self.assertIn('issue="$(claim_next_issue)" || break', script)

    def test_queue_handles_empty_queue_without_unbound_variable(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn('issue=""', script)
        self.assertIn("while true; do", script)
        self.assertIn('issue="$(claim_next_issue)" || break', script)

    def test_queue_uses_first_bad_stderr_and_reproducer_inputs_with_run_id(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn('RUN_ID="$run_id" CAPTURE_REPRODUCER_DIR=', script)
        self.assertIn('"$runner" "$wt" >"$runner_log"', script)
        self.assertIn('temp_dir="$result_dir/tmp-git-bisect-$run_id"', script)
        self.assertIn("-name '*.err'", script)
        self.assertIn('source_log', script)
        self.assertIn('reproducers', script)
        self.assertIn('CAPTURE_REPRODUCER_DIR', script)

    def test_queue_prefers_primary_first_bad_stderr_over_pass_diagnostic_retry(self) -> None:
        queue = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            primary = tmp / "abcdef123456.err"
            diagnostic = tmp / "abcdef123456.passdiag.err"
            primary.write_text("Assertion `canonical' failed.\n")
            diagnostic.write_text("unknown argument: -debug-pass-manager\n")
            command = (
                "set -eu; "
                f"ROOT={shlex.quote(str(tmp))}; "
                f"QUEUE_FILE={shlex.quote(str(tmp / 'queue'))}; "
                f"DONE_FILE={shlex.quote(str(tmp / 'done'))}; "
                f"LOCK_FILE={shlex.quote(str(tmp / 'lock'))}; "
                f"WORK_ROOT={shlex.quote(str(tmp / 'worktrees'))}; "
                f"EVIDENCE_ROOT={shlex.quote(str(tmp / 'evidence'))}; "
                "MASTER50_CAPTURE_QUEUE_LIBRARY_ONLY=1; "
                f"source {shlex.quote(str(queue))}; "
                "select_primary_stderr \"$1\" \"$2\""
            )
            result = subprocess.run(
                ["bash", "-c", command, "test", str(tmp), str(primary)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(primary))


    def test_queue_passes_capture_only_diagnostic_mode(self) -> None:
        queue = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()
        runner = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "validated-bisect-runner.sh"
        ).read_text()

        self.assertIn('CAPTURE_RUNNING_PASS=1', queue)
        self.assertIn('CAPTURE_PASS_DIAGNOSTICS=1 run_repro', runner)
        self.assertIn('diagnostic_exit_code:', runner)

    def test_queue_uses_validated_runner_log_for_pass_evidence(self) -> None:
        queue = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn('validated_runner_log=', queue)
        self.assertIn('pass_log="$result_dir/${issue}-master50-pass-evidence-${run_id}.log"', queue)
        self.assertIn('if [[ -s "$validated_runner_log" ]]; then', queue)
        self.assertIn('cat "$validated_runner_log" >>"$pass_log"', queue)
        self.assertIn('cat "$runner_log" >>"$pass_log"', queue)
        self.assertIn('pass_source_log=Path(pass_log)', queue)
        self.assertIn('source_log="$validated_runner_log"', queue)

    def test_queue_keeps_pass_evidence_available_for_custom_runners(self) -> None:
        queue = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "master50-crash-capture-queue.sh"
        ).read_text()

        self.assertIn(': >"$pass_log"', queue)
        self.assertIn('if [[ -s "$validated_runner_log" ]]; then', queue)
        self.assertIn('cat "$validated_runner_log" >>"$pass_log"', queue)
        self.assertIn('cat "$runner_log" >>"$pass_log"', queue)

    def test_validated_runner_has_valid_shell_syntax(self) -> None:
        runner = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "benchmark"
            / "validated-bisect-runner.sh"
        )

        result = subprocess.run(
            ["bash", "-n", str(runner)], capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dynamic_runners_preserve_generated_sources_for_capture(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for issue in ("pr195788", "pr196244"):
            script = (root / "scripts" / issue / "bisect-runner.sh").read_text()
            self.assertIn('CAPTURE_REPRODUCER_DIR="${CAPTURE_REPRODUCER_DIR:-', script)
            self.assertIn('capture_dir / "reproducer.cpp"', script)

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
            json.dumps(
                {
                    "issue": issue,
                    "validated_first_bad": "b" * 40,
                    "crash_evidence": {"kind": "missing"},
                }
            )
        )
        return evidence_root, case_dir

    def test_capture_uses_validated_first_bad_not_profile_bad_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, _case_dir = self._fixture(root)

            metadata = master50_crash_capture.load_capture_metadata(
                root, evidence_root, "pr1"
            )

            self.assertEqual(metadata.first_bad_commit, "b" * 40)
            self.assertNotEqual(metadata.first_bad_commit, "a" * 40)

    def test_capture_with_assertion_writes_marker_focused_artifact_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "-- Configuring done\n"
                "[99/99] Linking CXX executable clang\n"
                "Assertion `value != nullptr' failed.\n"
                "Stack dump:\n"
                "0. Program arguments: clang repro.cpp\n"
            )
            reproducer = log.parent / "repro.cpp"
            reproducer.write_text("int main() { return 0; }\n")

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
                reproducer=reproducer,
            )

            self.assertTrue(result.marker_detected)
            self.assertEqual(
                (case_dir / "crash-assertion.err").read_text(),
                "Assertion `value != nullptr' failed.\n"
                "Stack dump:\n"
                "0. Program arguments: clang repro.cpp\n",
            )
            self.assertEqual((case_dir / "reproducer.cpp").read_text(), reproducer.read_text())
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "assertion")
            self.assertEqual(
                metadata["crash_evidence"]["excerpt"],
                "Assertion `value != nullptr' failed.",
            )
            self.assertEqual(metadata["crash_capture"]["tested_sha"], "b" * 40)
            self.assertEqual(metadata["crash_capture"]["validated_first_bad"], "b" * 40)
            self.assertEqual(metadata["crash_capture"]["source_log"], "results/issues/pr1/capture.log")
            self.assertEqual(
                metadata["crash_capture_attempts"][-1]["runtime_signal_status"],
                "incomplete",
            )
            self.assertEqual(
                metadata["crash_capture_attempts"][-1]["runtime_signals"],
                {"failure_marker": True, "stack_trace": True, "running_pass": False},
            )

    def test_capture_preserves_running_pass_from_complete_replay_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.err"
            full_log = root / "results" / "issues" / "pr1" / "capture.runner.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `value != nullptr' failed.\nStack dump:\n")
            full_log.write_text(
                '1. Running pass "instcombine" on function "main"\n'
                "build output\n"
                "Assertion `value != nullptr' failed.\n"
            )

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
                pass_source_log=full_log,
            )

            artifact = (case_dir / "crash-assertion.err").read_text()
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertIn('Running pass "instcombine" on function "main"', artifact)
            self.assertEqual(metadata["crash_evidence"]["running_pass"], "instcombine")
            self.assertEqual(metadata["crash_evidence"]["component"], "instcombine")
            self.assertEqual(
                metadata["crash_capture"]["pass_source_log"],
                "results/issues/pr1/capture.runner.log",
            )
            self.assertTrue(result.marker_detected)

    def test_capture_keeps_the_final_pass_records_when_debug_output_is_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.err"
            full_log = root / "results" / "issues" / "pr1" / "capture.runner.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `value != nullptr' failed.\n")
            full_log.write_text(
                ("Running pass: VeryLongPassName on main (4 instructions)\n" * 2000)
                + "Running pass: FinalPass on main (4 instructions)\n"
            )

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
                pass_source_log=full_log,
            )

            artifact = (case_dir / "crash-assertion.err").read_text()
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertLessEqual(
                len(artifact.encode("utf-8")), master50_crash_capture.MAX_ARTIFACT_BYTES
            )
            self.assertIn("Running pass: FinalPass on main", artifact)
            self.assertEqual(metadata["crash_evidence"]["running_pass"], "FinalPass")

    def test_capture_prefers_failing_assertion_over_an_earlier_stack_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "Stack dump:\n"
                "0. clang\n"
                "Assertion `real_failure' failed.\n"
                "frame after assertion\n"
            )

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(
                metadata["crash_evidence"]["excerpt"],
                "Assertion `real_failure' failed.",
            )
            self.assertTrue(
                (case_dir / "crash-assertion.err")
                .read_text()
                .startswith("Assertion `real_failure' failed.")
            )

    def test_capture_prefers_failing_assertion_over_bug_report_boilerplate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
                "Assertion `real_failure' failed.\n"
            )

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(
                metadata["crash_evidence"]["excerpt"],
                "Assertion `real_failure' failed.",
            )

    def test_capture_packages_multiple_reproducer_inputs_in_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `value != nullptr' failed.\n")
            source = log.parent / "source.cpp"
            header = log.parent / "header.h"
            source.write_text("#include \"header.h\"\n")
            header.write_text("int value;\n")

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
                reproducer=[source, header],
            )

            self.assertTrue((case_dir / "reproducer" / "source.cpp").is_file())
            self.assertTrue((case_dir / "reproducer" / "header.h").is_file())

    def test_capture_rejects_a_profile_bad_endpoint_when_it_is_not_first_bad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `value != nullptr' failed.\n")

            with self.assertRaisesRegex(ValueError, "validated first-bad"):
                master50_crash_capture.record_capture(
                    root=root,
                    evidence_root=evidence_root,
                    issue="pr1",
                    tested_sha="a" * 40,
                    exit_code=1,
                    started_at="2026-08-31T00:00:00+00:00",
                    finished_at="2026-08-31T00:01:00+00:00",
                    source_log=log,
                )

            self.assertFalse((case_dir / "crash-assertion.err").exists())

    def test_capture_without_marker_records_attempt_without_claiming_a_capture(self) -> None:
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
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertFalse(result.marker_detected)
            self.assertFalse((case_dir / "crash-assertion.err").exists())
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "missing")
            self.assertNotIn("crash_capture", metadata)
            self.assertEqual(metadata["crash_capture_attempts"][-1]["marker_detected"], False)

    def test_capture_ignores_cmake_assertions_mode_as_crash_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "-- LLVM_ENABLE_ASSERTIONS: ON\n"
                "-- Configuring done\n"
                "[99/99] Linking CXX executable clang\n"
            )

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=0,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertFalse(result.marker_detected)
            self.assertFalse((case_dir / "crash-assertion.err").exists())

    def test_capture_rejects_bug_report_boilerplate_without_a_failure_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
                "Stack dump:\n"
            )

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertFalse(result.marker_detected)
            self.assertFalse((case_dir / "crash-assertion.err").exists())

    def test_capture_uses_compiler_crash_exit_when_no_assertion_is_printed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "PLEASE submit a bug report to https://github.com/llvm/llvm-project/issues/\n"
                "clang++: error: clang frontend command failed with exit code 139\n"
            )

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertTrue(result.marker_detected)
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(
                metadata["crash_evidence"]["excerpt"],
                "clang++: error: clang frontend command failed with exit code 139",
            )

    def test_capture_labels_signal_only_diagnostics_as_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("Segmentation fault (core dumped)\n")

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=139,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "crash")

    def test_capture_uses_clangd_action_signal_as_the_failure_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                "-- LLVM_ENABLE_ASSERTIONS: ON\n"
                "Stack dump without symbol names\n"
                "Signalled during AST worker action: EnumerateTweaks\n"
            )

            result = master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertTrue(result.marker_detected)
            metadata = json.loads((case_dir / "metadata.json").read_text())
            self.assertEqual(
                metadata["crash_evidence"]["excerpt"],
                "Signalled during AST worker action: EnumerateTweaks",
            )

    def test_capture_without_marker_preserves_existing_canonical_evidence(self) -> None:
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
            metadata["crash_capture"] = {
                "tested_sha": "b" * 40,
                "validated_first_bad": "b" * 40,
                "capture_target": "validated-first-bad",
                "marker_detected": True,
                "source_log": "results/issues/pr1/previous.log",
            }
            metadata["packaged_reproducer"] = ["reproducer.cpp"]
            metadata_path.write_text(json.dumps(metadata))
            artifact = case_dir / "crash-assertion.err"
            artifact.write_text("Assertion `saved' failed.\n")
            reproducer = case_dir / "reproducer.cpp"
            reproducer.write_text("int main() { return 0; }\n")
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("program output: 0\n")

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=0,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertEqual(artifact.read_text(), "Assertion `saved' failed.\n")
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(metadata["crash_evidence"]["kind"], "assertion")
            self.assertEqual(
                metadata["crash_capture"]["capture_target"], "validated-first-bad"
            )
            self.assertTrue(metadata["crash_capture"]["marker_detected"])
            self.assertEqual(metadata["packaged_reproducer"], ["reproducer.cpp"])
            self.assertEqual(metadata["crash_capture_attempts"][-1]["marker_detected"], False)
            self.assertEqual(reproducer.read_text(), "int main() { return 0; }\n")

    def test_capture_without_reproducer_removes_stale_reproducer_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, case_dir = self._fixture(root)
            stale = case_dir / "reproducer.cpp"
            stale.write_text("stale input\n")
            metadata_path = case_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text())
            metadata["packaged_reproducer"] = [stale.name]
            metadata_path.write_text(json.dumps(metadata))
            log = root / "results" / "issues" / "pr1" / "capture.log"
            log.parent.mkdir(parents=True)
            log.write_text("Assertion `new failure' failed.\n")

            master50_crash_capture.record_capture(
                root=root,
                evidence_root=evidence_root,
                issue="pr1",
                tested_sha="b" * 40,
                exit_code=1,
                started_at="2026-08-31T00:00:00+00:00",
                finished_at="2026-08-31T00:01:00+00:00",
                source_log=log,
            )

            self.assertFalse(stale.exists())
            metadata = json.loads(metadata_path.read_text())
            self.assertNotIn("packaged_reproducer", metadata)

    def test_loads_validated_first_bad_and_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            evidence_root, _case_dir = self._fixture(root)

            metadata = master50_crash_capture.load_capture_metadata(
                root, evidence_root, "pr1"
            )

            self.assertEqual(metadata.first_bad_commit, "b" * 40)
            self.assertEqual(metadata.runner, Path("scripts/pr1/bisect-runner.sh"))


if __name__ == "__main__":
    unittest.main()
