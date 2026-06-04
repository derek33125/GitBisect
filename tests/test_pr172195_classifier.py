from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "scripts" / "pr172195" / "classify-compile-stderr.sh"


class Pr172195ClassifierTests(unittest.TestCase):
    def classify(self, payload: str) -> int:
        completed = subprocess.run(
            [str(CLASSIFIER)],
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return completed.returncode

    def test_expected_cost_trace_is_bad(self) -> None:
        payload = """
clang: error: clang frontend command failed with exit code 136
llvm::LoopVectorizationCostModel::expectedCost
"""
        self.assertEqual(self.classify(payload), 0)

    def test_compute_best_vf_trace_is_bad(self) -> None:
        payload = """
clang: error: clang frontend command failed with exit code 136
5  clang llvm::LoopVectorizationPlanner::computeBestVF() + 310
6  clang llvm::LoopVectorizePass::processLoop(llvm::Loop*) + 7025
"""
        self.assertEqual(self.classify(payload), 0)

    def test_loop_vectorize_running_pass_trace_is_bad(self) -> None:
        payload = """
Stack dump:
4.\tRunning pass "loop-vectorize<no-interleave-forced-only;no-vectorize-forced-only;>" on function "func_21"
"""
        self.assertEqual(self.classify(payload), 0)

    def test_unrelated_failure_is_not_bad(self) -> None:
        payload = """
ld.lld: error: undefined symbol: foo
clang: error: linker command failed with exit code 1
"""
        self.assertNotEqual(self.classify(payload), 0)


if __name__ == "__main__":
    unittest.main()
