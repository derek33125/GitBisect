from __future__ import annotations

import unittest

from tools import crash_signals


class CrashSignalTests(unittest.TestCase):
    def test_parses_assertion_source_symbols_and_clean_pass_tokens(self) -> None:
        report = """clang: ../llvm/lib/Analysis/MemorySSA.cpp:915: void {anonymous}::ClobberWalker::verifyOptResult(const OptznResult&) const: Assertion `all_of(R.OtherClobbers, predicate)' failed.
Stack dump:
0. Program arguments: /tmp/clang -O1 -c repro.c -o /dev/null
1. Running pass "loop-mssa(licm<allowspeculation>,simple-loop-unswitch<no-nontrivial;trivial>)" on function "main"
2 clang 0x123 llvm::MemorySSA::ClobberWalkerBase::getClobberingMemoryAccessBase(llvm::MemoryAccess*) + 42
"""

        signals = crash_signals.parse_crash_report(report)

        self.assertEqual(signals.kind, "assertion")
        self.assertEqual(signals.assertion, "all_of(R.OtherClobbers, predicate)")
        self.assertEqual(signals.assert_source_file, "llvm/lib/Analysis/MemorySSA.cpp")
        self.assertEqual(signals.assert_function, "verifyOptResult")
        self.assertEqual(signals.assert_class, "ClobberWalker")
        self.assertIn("simple-loop-unswitch", signals.pass_tokens)
        self.assertNotIn("no-nontrivial", signals.pass_tokens)
        self.assertIn(
            "llvm::MemorySSA::ClobberWalkerBase::getClobberingMemoryAccessBase",
            signals.symbols,
        )

    def test_baseline_parser_preserves_general_pass_pipeline_tokens(self) -> None:
        report = '''Running pass "cgscc(devirt<4>(inline),function-attrs,sroa,early-cse<memssa>,instcombine,loop-mssa(licm<allowspeculation>,simple-loop-unswitch<no-nontrivial;trivial>,loop),coro-split)" on module "repro.c"\n'''

        signals = crash_signals.parse_crash_report(report)

        self.assertEqual(
            signals.pass_tokens,
            [
                "cgscc",
                "devirt",
                "inline",
                "function-attrs",
                "sroa",
                "early-cse",
                "instcombine",
                "loop-mssa",
                "licm",
                "simple-loop-unswitch",
                "loop",
                "coro-split",
            ],
        )

    def test_human_study_payload_uses_stricter_pass_pipeline_normalization(self) -> None:
        report = '''Running pass "cgscc(devirt<4>(inline),function-attrs,sroa,early-cse<memssa>,instcombine,loop-mssa(licm<allowspeculation>,simple-loop-unswitch<no-nontrivial;trivial>,loop),coro-split)" on module "repro.c"\n'''

        payload = crash_signals.human_study_signal_payload(report)

        self.assertEqual(
            payload["pass_tokens"],
            ["coro-split", "simple-loop-unswitch", "loop-mssa", "early-cse", "inline", "cgscc"],
        )

    def test_derives_ordered_query_terms_from_crash_contract(self) -> None:
        signals = crash_signals.CrashSignals(
            kind="assertion",
            assertion="Symbol",
            assert_source_file="llvm/lib/MC/MCExpr.cpp",
            assert_function="MCSymbolRefExpr",
            assert_class="llvm",
            symbols=["llvm::AsmPrinter::emitXRayTable"],
            pass_tokens=["aarch64-asm-printer"],
        )

        terms = crash_signals.derive_query_terms(signals)

        self.assertEqual(terms[0].term, "MCSymbolRefExpr")
        self.assertEqual(terms[0].kind, "assertion-function")
        self.assertIn("emitXRayTable", [term.term for term in terms])
        self.assertIn("MCExpr", [term.term for term in terms])

    def test_demangles_itanium_stack_symbol_for_producer_retrieval(self) -> None:
        report = """Stack dump without symbol names:
/tmp/llc(_ZN4llvm10AsmPrinter13emitXRayTableEv+0x281) [0x123]
"""

        signals = crash_signals.parse_crash_report(report)

        self.assertIn("llvm::AsmPrinter::emitXRayTable", signals.symbols)

    def test_human_study_signal_payload_removes_framework_stack_frames(self) -> None:
        report = """1 clang 0x1 llvm::SimpleLoopUnswitchPass::run(llvm::Function&, llvm::AnalysisManager<llvm::Function>&) + 1
2 clang 0x2 llvm::PassManager::run(llvm::Function&, llvm::AnalysisManager<llvm::Function>&) + 1
3 clang 0x3 clang::FrontendAction::Execute() + 1
"""

        payload = crash_signals.human_study_signal_payload(report)

        self.assertEqual(
            payload["symbols"],
            ["llvm::SimpleLoopUnswitchPass::run", "llvm::PassManager::run"],
        )


if __name__ == "__main__":
    unittest.main()
