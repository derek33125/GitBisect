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

    def test_supports_single_factor_tuned_heuristic_ablation_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('heuristic-ablation)', text)
        self.assertIn('HEURISTIC_ABLATION_FACTOR is required for heuristic-ablation mode', text)
        self.assertIn('--heuristic-version tuned', text)
        self.assertIn('--heuristic-ablation "${HEURISTIC_ABLATION_FACTOR}"', text)

    def test_supports_terra_causal_parent_range_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('terra-causal-parent-range-k12|terra-causal-parent-range-k12-compat)', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('--model-top-k 12', text)
        self.assertIn('--model-diff-extraction causal-llm', text)
        self.assertIn('--causal-context-parent-count 5', text)
        self.assertIn('terra-bcr-parent-range5-k12', text)

    def test_supports_human_guided_causal_parent_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-k12)', text)
        self.assertIn('--model-diff-extraction causal-llm-human', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('terra-bcr-human-crash-v1-k12', text)

    def test_supports_human_signal_pool_proof_pilot_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-pool-k3|causal-parent-human-pool-k3-compat)', text)
        self.assertIn('--model-diff-extraction causal-llm-human-pool', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('--model-top-k 3', text)
        self.assertIn('terra-human-signal-pool-v1-k3', text)

    def test_supports_cxx_only_compatibility_human_signal_pool_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-pool-k3|causal-parent-human-pool-k3-compat)', text)
        self.assertIn('if [[ "${MODE}" == "causal-parent-human-pool-k3-compat" ]]; then', text)
        self.assertIn('terra-human-signal-pool-v1-k3-compat', text)

    def test_supports_human_signal_soft_prior_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-prior-k3|causal-parent-human-prior-k3-compat)', text)
        self.assertIn('--model-diff-extraction causal-llm-human-prior', text)
        self.assertIn('--model-top-k 3', text)
        self.assertIn('terra-human-soft-prior-v1-k3', text)
        self.assertIn('causal-parent-human-prior-k3-compat', text)

    def test_human_soft_prior_mode_uses_its_isolated_cache_default(self) -> None:
        text = SCRIPT.read_text()

        default_start = text.index('default_model_cache_namespace() {')
        default_end = text.index('\n}\n', default_start)
        default_function = text[default_start:default_end]
        self.assertIn('causal-parent-human-prior-k3-compat)', default_function)
        self.assertIn('terra-human-soft-prior-v1-k3-compat', default_function)
        self.assertIn('MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-$(default_model_cache_namespace)}"', text)

    def test_supports_human_staged_frontier_bcr_pilot_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-frontier|causal-parent-human-frontier-compat)', text)
        self.assertIn('--model-diff-extraction causal-llm-human-frontier', text)
        self.assertIn('--model-top-k 12', text)
        self.assertIn('terra-human-staged-frontier-v1', text)
        self.assertIn('causal-parent-human-frontier-compat', text)

    def test_supports_dynamic_human_evidence_bcr_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-human-dynamic-k12|causal-parent-human-dynamic-k12-compat)', text)
        self.assertIn('--model-diff-extraction causal-llm-human-dynamic', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('--model-top-k 12', text)
        self.assertIn('terra-human-dynamic-evidence-v4-k12', text)

    def test_supports_crash_aware_production_bcr_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-crash-aware-k12|causal-parent-crash-aware-k12-compat)', text)
        self.assertIn('--model-diff-extraction causal-llm-crash-aware', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('--model-top-k 12', text)
        self.assertIn('terra-bcr-crash-aware-v12-k12', text)

    def test_supports_v15_deterministic_facts_bcr_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('causal-parent-deterministic-facts-k12)', text)
        self.assertIn('--model-diff-extraction causal-llm-deterministic-facts', text)
        self.assertIn('--model-name gpt-5.6-terra', text)
        self.assertIn('--model-reasoning-effort high', text)
        self.assertIn('--model-top-k 12', text)
        self.assertIn('terra-bcr-deterministic-facts-v15-k12', text)

    def test_supports_artifact_complete_deterministic_facts_mode_with_compatibility_arm(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn(
            'causal-parent-deterministic-facts-artifact-k12|causal-parent-deterministic-facts-artifact-k12-compat)',
            text,
        )
        self.assertIn('--model-diff-extraction causal-llm-deterministic-facts-artifact', text)
        self.assertIn('terra-bcr-deterministic-facts-v16-artifacts-k12', text)
        self.assertIn('if [[ "${MODE}" == "causal-parent-deterministic-facts-artifact-k12-compat" ]]; then', text)
        self.assertIn('terra-bcr-deterministic-facts-v16-artifacts-k12-compat', text)

    def test_supports_artifact_complete_deterministic_facts_parent_window_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn(
            'causal-parent-deterministic-facts-artifact-range-k12|causal-parent-deterministic-facts-artifact-range-k12-compat)',
            text,
        )
        self.assertIn('--model-diff-extraction causal-llm-deterministic-facts-artifact', text)
        self.assertIn('--causal-context-parent-count 5', text)
        self.assertIn('terra-bcr-deterministic-facts-v16-artifacts-range5-k12', text)
        self.assertIn(
            'if [[ "${MODE}" == "causal-parent-deterministic-facts-artifact-range-k12-compat" ]]; then',
            text,
        )

        mode_start = text.index(
            'causal-parent-deterministic-facts-artifact-range-k12|causal-parent-deterministic-facts-artifact-range-k12-compat)'
        )
        mode_end = text.index('\n      ;;', mode_start)
        mode = text[mode_start:mode_end]
        self.assertIn(
            'export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"',
            mode,
        )

    def test_reclaims_reservations_owned_by_dead_or_unrelated_processes(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('reclaim_dead_lane_reservations()', text)
        self.assertIn('ps -p "${pid}" -o args=', text)
        self.assertIn('run-comparison-queue-20260630.sh', text)
        self.assertIn('run-validated-heuristic-expansion-queue.sh', text)

    def test_counts_a_controller_and_its_runner_as_one_lane(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('reservation_owns_process()', text)
        self.assertIn('active_unreserved_run_online_lanes()', text)
        self.assertIn('if ! reservation_owns_process "${process_pid}"; then', text)
        self.assertIn('unreserved + reserved < RUN_ONLINE_MAX_LANES', text)

    def test_enumerates_run_online_process_ids_for_reservation_ownership(self) -> None:
        text = SCRIPT.read_text()

        start = text.index('active_run_online_pids() {')
        end = text.index('\n}\n', start)
        function = text[start:end]

        self.assertIn('ps -eo pid=,args=', function)
        self.assertIn('$2 ~ /(^|\\/)python([0-9.]*)?$/', function)
        self.assertIn('$3 == "tools/lm_bisect.py"', function)
        self.assertIn('$4 == "run-online"', function)

    def test_queue_accepts_an_explicit_remote_lane_capacity(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}"', text)
        self.assertIn('RUN_ONLINE_MAX_LANES must be a positive integer', text)
        self.assertIn('unreserved + reserved < RUN_ONLINE_MAX_LANES', text)

    def test_supports_cxx_only_compatibility_parent_range_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('terra-causal-parent-range-k12-compat)', text)
        self.assertIn('terra-causal-parent-range-k12|terra-causal-parent-range-k12-compat)', text)
        self.assertIn('export EXTRA_CMAKE_CXX_FLAGS="${EXTRA_CMAKE_CXX_FLAGS:--include cstdint}"', text)
        self.assertIn('export MODEL_CACHE_NAMESPACE="${MODEL_CACHE_NAMESPACE:-terra-bcr-parent-range5-k12-compat}"', text)

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

    def test_supports_oracle_patch_fingerprint_mode(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('oracle-major-tuned-patch-heuristic)', text)
        self.assertIn('--heuristic-version oracle-first-bad-major-tuned-patch', text)

    def test_preflights_runner_bundle_before_creating_a_worktree(self) -> None:
        text = SCRIPT.read_text()

        runner_lookup = text.index('profile_runner()')
        preflight = text.index('preflight_issue_runner "${issue}"')
        worktree_add = text.index('git -C "${BASE_REPO}" worktree add')

        self.assertLess(runner_lookup, preflight)
        self.assertLess(preflight, worktree_add)
        self.assertIn('runner bundle missing issue runner for ${issue}', text)
        self.assertIn('runner bundle missing shared queue library', text)

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
                "  payload=$(cat)\n"
                "  if [[ \"$payload\" == *'profiles[issue][\"runner\"]'* ]]; then\n"
                "    printf 'scripts/%s/bisect-runner.sh\\n' \"$3\"\n"
                "    exit 0\n"
                "  fi\n"
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
            queue_library = root / "results" / "issues" / "server-jobs" / "server-validation-queue-20260613.sh"
            queue_library.parent.mkdir(parents=True)
            queue_library.write_text("#!/usr/bin/env bash\n")
            for issue in ("pr204559", "pr204589"):
                runner = root / "scripts" / issue / "bisect-runner.sh"
                runner.parent.mkdir(parents=True)
                runner.write_text("#!/usr/bin/env bash\nexit 0\n")
                runner.chmod(0o755)
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
