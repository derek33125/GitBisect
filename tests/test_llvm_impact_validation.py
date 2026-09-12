from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "benchmark" / "llvm-impact-runner.sh"
LAUNCHER = ROOT / "scripts" / "benchmark" / "run-llvm-impact-validation-bisect.sh"
QUEUE = ROOT / "scripts" / "benchmark" / "run-llvm-impact-validation-queue.sh"
MASTER50_QUEUES = (
    ROOT / "scripts" / "benchmark" / "run-ceg-scoped10-queue.sh",
    ROOT / "scripts" / "benchmark" / "run-ceg-master50-next10-queue.sh",
    ROOT / "scripts" / "benchmark" / "run-ceg-master50-remaining30-queue.sh",
)


class LlvmImpactValidationTests(unittest.TestCase):
    def test_approved_cases_are_outside_master50(self) -> None:
        master50: set[str] = set()
        for queue in MASTER50_QUEUES:
            master50.update(re.findall(r"\bpr\d+\b", queue.read_text()))

        self.assertEqual(len(master50), 50)
        self.assertTrue({"pr178777", "pr212819"}.isdisjoint(master50))

    def test_reproducers_are_frozen_reductions(self) -> None:
        cpp = ROOT / "scripts" / "impact" / "llvm178777" / "reproducer.cpp"
        ir = ROOT / "scripts" / "impact" / "llvm212819" / "reproducer.ll"

        self.assertIn("__builtin_mul_overflow", cpp.read_text())
        self.assertIn("fallback__wine_dbg_get_channel_flags", ir.read_text())
        self.assertLess(cpp.stat().st_size, 2_000)
        self.assertLess(ir.stat().st_size, 1_000)
        self.assertRegex(hashlib.sha256(cpp.read_bytes()).hexdigest(), r"^[0-9a-f]{64}$")
        self.assertRegex(hashlib.sha256(ir.read_bytes()).hexdigest(), r"^[0-9a-f]{64}$")

    def test_runner_preserves_commands_and_strict_skip_contract(self) -> None:
        text = RUNNER.read_text()

        self.assertIn('LLVM_ENABLE_ASSERTIONS="${LLVM_ENABLE_ASSERTIONS:-OFF}"', text)
        self.assertIn('"${clangxx_bin}" -O2 -c "${REPRODUCER}" -o "${object}"', text)
        self.assertRegex(
            text,
            re.compile(
                r'"\$\{clang_bin\}" -target aarch64-windows -mcpu=apple-m2 -O1 '
                r'\\\n\s+-x ir -c "\$\{REPRODUCER\}" -o "\$\{object\}"'
            ),
        )
        self.assertIn("nonmatching compiler failure; skipping commit", text)
        self.assertNotIn('elif [[ "${repro_rc}" -ge 128 ]]', text)

    def test_runner_classifies_only_complete_case_signatures_as_bad(self) -> None:
        cases = {
            "llvm178777": (
                "fatal error: error in backend: Cannot select\n"
                "i32 = any_extend\nv16i8 = X86ISD::PINSRB\ni8,i8 = udivrem\n",
                "fatal error: error in backend: Cannot select\n",
            ),
            "llvm212819": (
                "fatal error: error in backend: Failed to evaluate function length "
                "in SEH unwind info\nRunning pass 'AArch64 Assembly Printer'\n",
                "fatal error: error in backend: unrelated failure\n",
            ),
        }
        for issue, (matching, nonmatching) in cases.items():
            with self.subTest(issue=issue), tempfile.TemporaryDirectory() as tmp:
                err = Path(tmp) / "stderr"
                err.write_text(matching)
                matched = self._classify(issue, 70, err)
                err.write_text(nonmatching)
                skipped = self._classify(issue, 70, err)

                self.assertEqual(matched.stdout.strip(), "bad")
                self.assertEqual(skipped.stdout.strip(), "skip")

    def test_launcher_validates_both_endpoints_before_git_bisect(self) -> None:
        text = LAUNCHER.read_text()

        good_gate = text.index('validate_endpoint "good"')
        bad_gate = text.index('validate_endpoint "bad"')
        bisect_start = text.index('bisect start')
        self.assertLess(good_gate, bisect_start)
        self.assertLess(bad_gate, bisect_start)
        self.assertIn("merge-base --is-ancestor", text)
        self.assertIn('"reproducer_sha256"', text)
        self.assertIn('"author_name"', text)
        self.assertIn('"author_email"', text)
        self.assertIn('"first_bad_parent"', text)
        self.assertIn('"skip_count"', text)
        self.assertIn('results/impact-study/${ISSUE}', text)

    def test_queue_has_exactly_two_independent_lanes(self) -> None:
        text = QUEUE.read_text()

        launches = re.findall(r"^run_case (lane-[ab]) (llvm\d+)", text, re.MULTILINE)
        self.assertEqual(
            launches,
            [("lane-a", "llvm178777"), ("lane-b", "llvm212819")],
        )
        self.assertIn("impact_physical_lanes=2", text)
        self.assertNotRegex(text, re.compile(r"^run_case lane-[c-z]", re.MULTILINE))

    def test_shell_scripts_parse(self) -> None:
        for script in (RUNNER, LAUNCHER, QUEUE):
            result = subprocess.run(
                ["bash", "-n", str(script)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def _classify(issue: str, rc: int, stderr: Path) -> subprocess.CompletedProcess[str]:
        command = (
            "LLVM_IMPACT_RUNNER_LIBRARY_ONLY=1; "
            f"source {shlex.quote(str(RUNNER))}; "
            'classify_repro "$1" "$2" "$3"'
        )
        return subprocess.run(
            ["bash", "-c", command, "test", issue, str(rc), str(stderr)],
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
