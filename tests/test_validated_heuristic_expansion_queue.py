from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT = Path("scripts/benchmark/run-validated-heuristic-expansion-queue.sh")


class ValidatedHeuristicExpansionQueueTests(unittest.TestCase):
    def test_queue_reserves_capacity_and_preflights_before_heuristic_run(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("acquire_lane_reservation", text)
        self.assertIn("preflight_issue", text)
        self.assertIn('profile["good_commit"]', text)
        self.assertIn('profile["bad_commit"]', text)
        self.assertIn('profile["runner"]', text)
        self.assertIn('"${good_rc}" -eq 0 && "${bad_rc}" -eq 1', text)
        self.assertNotIn("server-validation-queue-20260613.sh\" crash", text)
        self.assertIn("usable_state", text)
        self.assertIn("--scorer heuristic", text)
        self.assertIn('--heuristic-version "${HEURISTIC_VERSION}"', text)
        self.assertLess(text.index("preflight_issue \"${issue}\""), text.index("run_heuristic \"${issue}\""))

    def test_queue_supports_the_two_baseline_heuristic_controls(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("[--heuristic-version tuned|general]", text)
        self.assertIn('HEURISTIC_VERSION="${HEURISTIC_VERSION:-tuned}"', text)
        self.assertIn('tuned|general)', text)

    def test_queue_reclaims_only_completed_worktrees(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("cleanup_issue_worktree", text)
        self.assertIn("cleanup_preflight_worktree", text)
        self.assertIn('git -C "${BASE_REPO}" worktree remove --force "${wt}"', text)
        self.assertIn('cleanup_issue_worktree "${issue}"', text)
        self.assertIn('cleanup_preflight_worktree "${wt}"', text)
        self.assertIn("reclaim_dead_lane_reservations", text)
        self.assertIn('kill -0 "${pid}"', text)
        self.assertNotIn("-mmin +10 -delete", text)
        self.assertGreater(
            text.index('cleanup_issue_worktree "${issue}"'),
            text.index('run_heuristic "${issue}"'),
        )

    def test_queue_does_not_count_a_reserved_controller_and_its_child_twice(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("active_unreserved_run_online_lanes", text)
        self.assertIn("reservation_owns_process", text)
        self.assertIn("unreserved + reserved < RUN_ONLINE_MAX_LANES", text)
        self.assertNotIn("active + reserved < 3", text)

    def test_queue_accepts_an_explicit_remote_lane_capacity(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn('RUN_ONLINE_MAX_LANES="${RUN_ONLINE_MAX_LANES:-3}"', text)
        self.assertIn('RUN_ONLINE_MAX_LANES must be a positive integer', text)
        self.assertIn('unreserved + reserved < RUN_ONLINE_MAX_LANES', text)


if __name__ == "__main__":
    unittest.main()
