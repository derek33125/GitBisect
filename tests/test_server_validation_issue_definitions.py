from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT = Path("results/issues/server-jobs/server-validation-queue-20260613.sh")


class ServerValidationIssueDefinitionTests(unittest.TestCase):
    def assertMatches(self, text: str, pattern: str) -> None:
        self.assertIsNotNone(re.search(pattern, text, re.S), pattern)

    def test_configure_helper_allows_extra_cmake_arguments(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"EXTRA_CMAKE_ARGS")
        self.assertMatches(text, r"extra_cmake_args")
        self.assertIn('cfg+=("${extra_cmake_args[@]}")', text)

    def test_pr199640_is_wired_for_riscv_bad_endpoint_validation(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr199640\)\s+BAD_REF=\"d0646cbe307dd2bd10686f923061a7d981263e40\"")
        self.assertMatches(text, r"pr199640\)\s+.*?LLVM_TARGETS_TO_BUILD=RISCV")
        self.assertMatches(text, r"pr199640\)\s+.*?LANG=c")
        self.assertMatches(text, r"pr199640\)\s+.*?APInt")
        self.assertMatches(text, r"pr199640\)\s+cat >\"\$out\" <<'SRC'.*?void c\(_Bool d")

    def test_local_benchmark_expansion_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr50190": "24d48d45cc302a6abeab139d87ba87f7a2335323",
            "pr50304": "4aa1c141bd674564aaee83516b7e338aa3aae9e3",
            "pr50585": "0a1ca2ad4ce239abc8d844f33048af58648edb80",
            "pr56560": "4a40fa82c07238ea28e041dc0e28b4f2953e509e",
            "pr169485": "bb78728826ff57f3df859e79bfd857b5a175bb6d",
        }
        for issue, bad_ref in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?BAD_REGEX=")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

    def test_second_local_expansion_batch_is_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr203278": "llvmorg-22.1.0",
            "pr203261": "llvmorg-22.1.0",
            "pr204589": "baad7c3238b3203c974787fc4881453b6b23a0b6",
            "pr204559": "baad7c3238b3203c974787fc4881453b6b23a0b6",
            "pr172208": "f2dff15995b38dc4649a258ead3865c0b79a1abe^",
            "pr170433": "73ebadaa837e039a1c07c9d373841cfb1eb0eb2b^",
            "pr193164": "24be43f5c5f1^",
        }
        for issue, bad_ref in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{re.escape(bad_ref)}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?BAD_REGEX=")
                if issue != "pr193164":
                    self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

        self.assertMatches(
            text,
            r"pr172208\)\s+.*?GOOD_REFS=\([^)]*llvmorg-19\.1\.7[^)]*llvmorg-18\.1\.8",
        )

    def test_pr193164_uses_full_github_attachment_reproducer(self) -> None:
        text = SCRIPT.read_text()
        reproducer = Path("scripts/pr193164/crash-62b480.cpp")

        self.assertTrue(reproducer.exists())
        self.assertGreater(len(reproducer.read_text().splitlines()), 1000)
        self.assertMatches(
            text,
            r"pr193164\)\s+.*?cat \"\$ROOT/scripts/pr193164/crash-62b480\.cpp\" >\"\$out\"",
        )

    def test_pr201444_same_history_good_anchor_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr201444\)\s+BAD_REF=\"fb6153a65039b06319cb4b9ce2fafa84d75daa6b\"")
        self.assertMatches(text, r"pr201444\)\s+.*?GOOD_REFS=\(e9f758a59b2f887acb07e26e2d480c7369a72009")
        self.assertMatches(text, r"pr201444\)\s+.*?LLVM_TARGETS_TO_BUILD=X86")
        self.assertMatches(text, r"pr201444\)\s+.*?low bits constraint must survive width adjustment")
        self.assertMatches(text, r"pr201444\)\s+cat >\"\$out\" <<'SRC'.*?safe_lshift_func_int16_t_s_s")

    def test_pr203856_clang_tidy_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr203856\)\s+BAD_REF=\"fd015fa0da49e4bd05246c6291d56836455e0e42\^\"")
        self.assertMatches(text, r"pr203856\)\s+.*?LLVM_ENABLE_PROJECTS=\"clang;clang-tools-extra\"")
        self.assertMatches(text, r"pr203856\)\s+.*?BUILD_TARGET=clang-tidy")
        self.assertMatches(text, r"pr203856\)\s+.*?TOOL=clang-tidy")
        self.assertMatches(text, r"clang-tidy:cxx\)")
        self.assertMatches(text, r"pr203856\)\s+.*?DeclGroup.h|pr203856\)\s+.*?isSingleDecl")
        self.assertMatches(text, r"pr203856\)\s+cat >\"\$out\" <<'SRC'.*?int i = 0, j = 0")

    def test_pr204178_mangling_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr204178\)\s+BAD_REF=\"llvmorg-18\.1\.8\"")
        self.assertMatches(text, r"pr204178\)\s+.*?GOOD_REFS=\(llvmorg-17\.0\.6")
        self.assertMatches(text, r"pr204178\)\s+.*?FLAGS=\(-std=c\+\+20 -S\)")
        self.assertMatches(text, r"pr204178\)\s+.*?ItaniumMangle")
        self.assertMatches(text, r"pr204178\)\s+cat >\"\$out\" <<'SRC'.*?abi_tag")

    def test_pr203722_coff_comdat_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr203722\)\s+BAD_REF=\"llvmorg-20\.1\.8\"")
        self.assertMatches(text, r"pr203722\)\s+.*?GOOD_REFS=\(llvmorg-19\.1\.7")
        self.assertMatches(text, r"pr203722\)\s+.*?LANG=ll")
        self.assertMatches(text, r"pr203722\)\s+.*?--target=x86_64-pc-windows-msvc")
        self.assertMatches(text, r"pr203722\)\s+.*?Associative COMDAT symbol")
        self.assertMatches(text, r"pr203722\)\s+cat >\"\$out\" <<'SRC'.*?missing_leader")

    def test_pr204790_clang_tidy_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr204790\)\s+BAD_REF=\"ca7933e47d3a3451d81e72ac174dcb5aa28b59d1\"")
        self.assertMatches(text, r"pr204790\)\s+.*?LLVM_ENABLE_PROJECTS=\"clang;clang-tools-extra\"")
        self.assertMatches(text, r"pr204790\)\s+.*?BUILD_TARGET=clang-tidy")
        self.assertMatches(text, r"pr204790\)\s+.*?TOOL=clang-tidy")
        self.assertMatches(text, r"pr204790\)\s+.*?readability-identifier-naming")
        self.assertMatches(text, r"pr204790\)\s+cat >\"\$out\" <<'SRC'.*?struct A<const T>")

    def test_pr153916_c23_ast_equivalence_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr153916\)\s+BAD_REF=\"3623fe661ae35c6c80ac221f14d85be76aa870f1\"")
        self.assertMatches(text, r"pr153916\)\s+.*?LANG=c")
        self.assertMatches(text, r"pr153916\)\s+.*?FLAGS=\(-std=c23 -fsyntax-only\)")
        self.assertMatches(text, r"pr153916\)\s+.*?ASTStructuralEquivalence")
        self.assertMatches(text, r"pr153916\)\s+cat >\"\$out\" <<'SRC'.*?gnu::unused")

    def test_pr157334_globalopt_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr157334\)\s+BAD_REF=\"f628a5467addf2f8a597141ab01f7e7453e6d9a7\^\"")
        self.assertMatches(text, r"pr157334\)\s+.*?LLVM_ENABLE_PROJECTS=\"\"")
        self.assertMatches(text, r"pr157334\)\s+.*?BUILD_TARGET=opt")
        self.assertMatches(text, r"pr157334\)\s+.*?TOOL=opt")
        self.assertMatches(text, r"pr157334\)\s+.*?FLAGS=\(-passes=globalopt -S\)")
        self.assertMatches(text, r"pr157334\)\s+.*?Invalid Cast Combination")
        self.assertMatches(text, r"pr157334\)\s+cat >\"\$out\" <<'SRC'.*?ptrtoaddr")

    def test_third_expansion_batch_is_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr166389": ("llvmorg-21.1.0", "loop-fusion"),
            "pr48154": ("070af1b7887f\\^", "attributor-enable=module"),
            "pr49535": ("15a42339fe5f\\^", "ValueTracking"),
            "pr51366": ("00200dbda316\\^", "LoopVectorize"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")
        self.assertMatches(text, r"pr166389\)\s+.*?LLVM_TARGETS_TO_BUILD=AArch64")

    def test_pr121365_aarch64_globalisel_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(
            text,
            r"pr121365\)\s+BAD_REF=\"f035351af785b7349ab7bcd55149c781ceca24cb\"",
        )
        self.assertMatches(text, r"pr121365\)\s+.*?LLVM_ENABLE_PROJECTS=\"\"")
        self.assertMatches(text, r"pr121365\)\s+.*?LLVM_TARGETS_TO_BUILD=AArch64")
        self.assertMatches(text, r"pr121365\)\s+.*?BUILD_TARGET=llc")
        self.assertMatches(text, r"pr121365\)\s+.*?TOOL=llc")
        self.assertMatches(text, r"pr121365\)\s+.*?FLAGS=\(-global-isel -O0\)")
        self.assertMatches(text, r"pr121365\)\s+.*?invalid extend/trunc")
        self.assertMatches(text, r"pr121365\)\s+cat >\"\$out\" <<'SRC'.*?shufflevector <8 x i1>")

    def test_fourth_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr194000": ("c7586233c0eddff6003ce2bbb4a873600df0aec4\\^", "createPartialReductionExpression"),
            "pr204633": ("ae35674951e4d20d45ed6202e641976c9c055f22", "ModuleMap"),
            "pr156249": ("b03d16c80faf061f9b3cea9d2f9a6baabf3ceb9e\\^", "LiveIntervals"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"[^\"]*{bad_ref}[^\"]*\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

    def test_pr171879_powerpc_selectiondag_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(text, r"pr171879\)\s+BAD_REF=\"llvmorg-21\.1\.8\"")
        self.assertMatches(text, r"pr171879\)\s+.*?GOOD_REFS=\(llvmorg-21\.1\.0")
        self.assertMatches(text, r"pr171879\)\s+.*?LLVM_ENABLE_PROJECTS=\"\"")
        self.assertMatches(text, r"pr171879\)\s+.*?LLVM_TARGETS_TO_BUILD=PowerPC")
        self.assertMatches(text, r"pr171879\)\s+.*?BUILD_TARGET=llc")
        self.assertMatches(text, r"pr171879\)\s+.*?TOOL=llc")
        self.assertMatches(text, r"pr171879\)\s+.*?Cannot BITCAST between types of different sizes")
        self.assertMatches(text, r"pr171879\)\s+cat >\"\$out\" <<'SRC'.*?powerpc64le-unknown-linux5\.10\.0-musl")

    def test_pr52635_aarch64_xray_candidate_is_wired(self) -> None:
        text = SCRIPT.read_text()

        self.assertMatches(
            text,
            r"pr52635\)\s+BAD_REF=\"10bc12588dac532fad044b2851dde8e7b9121e88\"",
        )
        self.assertMatches(text, r"pr52635\)\s+.*?GOOD_REFS=\(llvmorg-10\.0\.1")
        self.assertMatches(text, r"pr52635\)\s+.*?LLVM_ENABLE_PROJECTS=\"\"")
        self.assertMatches(text, r"pr52635\)\s+.*?LLVM_TARGETS_TO_BUILD=AArch64")
        self.assertMatches(text, r"pr52635\)\s+.*?BUILD_TARGET=llc")
        self.assertMatches(text, r"pr52635\)\s+.*?TOOL=llc")
        self.assertMatches(text, r"pr52635\)\s+.*?MCSymbolRefExpr")
        self.assertMatches(text, r"pr52635\)\s+cat >\"\$out\" <<'SRC'.*?patchable-function-entry")

    def test_fifth_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr170828": ("9730d3128435350c5ef90c198979df25004ccc1f\\^", "slp-vectorizer"),
            "pr165039": ("078e99ef017cac3899e5dbc2ed917f173c9eedad", "Unexpected placeholder builtin type"),
            "pr50655": ("40650f27b5df95b2f96d25ea03976d8136804441\\^", "ARMExpandPseudo"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

    def test_sixth_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr203519": ("bae51e7ffb767d08d684290d34515b91f2cbb2ed\\^", "getAllocationSize"),
            "pr203695": ("07d8a5991a27259a69b914176490216281b1a80c\\^", "stripAndAccumulateConstantOffsets"),
            "pr192963": ("1d6c9b8dceaf85ac7997ffa0246254ae337aa2ca\\^", "Number of scalars must be divisible by NumParts"),
            "pr204799": ("5c97397c2c414d7ac8e43e87c6d2a79a87e802fb", "replaceAllUsesWith"),
            "pr202463": ("26c550852a90c0eb1732ce3fd48bdf25124eaa4b\\^", "incrementUnscheduledDeps"),
            "pr202560": ("60dd90f3047bc1e0d727696b802da8fabd349dfc", "ShrinkWrap"),
            "pr203701": ("origin/main", "Name is not a simple identifier"),
            "pr203891": ("origin/main", "should not see dependent types here"),
            "pr205827": ("origin/main", "!D1 == !D2"),
            "pr189247": ("7d5b73ca2a379c5240d13137d52ac6d98985b05f\\^", "CheckTemplateArgument"),
            "pr190457": ("8506466bbf5daa0c79831a116cd4df618d9955ba\\^", "CStringChecker"),
            "pr197699": ("origin/main", "LowerSTOREi1"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

        self.assertMatches(text, r"pr197699\)\s+.*?LLVM_TARGETS_TO_BUILD=NVPTX")
        self.assertMatches(text, r"pr202560\)\s+.*?LLVM_TARGETS_TO_BUILD=AArch64")

    def test_seventh_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr169017": ("3fec26e3294ae0f276ff08fd810850421444588c\\^", "#Elements of a VectorType must be greater than 0"),
            "pr168912": ("e16cc8ed4636c36fc6e4e95289faf94048ec79b2\\^", "Unexpected vector MULL size"),
            "pr142531": ("b3ce9883f32e3b5b16e2b5fa54c3d98e85b66869\\^", "Cannot lower calls with arbitrary operand bundles"),
            "pr165713": ("63e6373efd82b0ccd53955a10ced9fa9b6a8db77\\^", "Unexpected out of bounds negative value"),
            "pr177741": ("f7e9d48a73dd68c8b652692d8a9e559a6ceb722e", "Illegal param #"),
            "pr177943": ("245043fad49ecf989e462d1a82a380570165dd95\\^", "isThisDeclarationADemotedDefinition"),
            "pr194590": ("1af4350a0604e7e83b01c894ea9ecc1600084449\\^", "Each occurrence should contribute a value"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

        self.assertMatches(text, r"pr169017\)\s+.*?LLVM_TARGETS_TO_BUILD=RISCV")
        self.assertMatches(text, r"pr168912\)\s+.*?LLVM_TARGETS_TO_BUILD=AArch64")
        self.assertMatches(text, r"pr165713\)\s+.*?LLVM_TARGETS_TO_BUILD=WebAssembly")

    def test_eighth_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr205971": ("origin/main", "Only member templates can be member template specializations"),
            "pr205718": ("origin/main", "TransformFunctionTypeParams"),
            "pr205484": ("origin/main", "type should never be variably-modified"),
            "pr204563": ("origin/main", "ibm128 not implemented on this target"),
            "pr203434": ("origin/main", "ICE cannot be evaluated"),
            "pr206007": ("origin/main", "Loop block has no in-loop successors"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

        self.assertMatches(text, r"pr206007\)\s+.*?BUILD_TARGET=opt")

    def test_ninth_expansion_bad_endpoint_candidates_are_wired(self) -> None:
        text = SCRIPT.read_text()
        expected = {
            "pr165246": ("origin/main", "AnnotatePreviousCachedTokens"),
            "pr167514": ("llvmorg-21\\.1\\.0", "getTemplateArgumentPackPatternForRewrite"),
            "pr200648": ("llvmorg-22\\.1\\.0", "VarDecl::evaluateValueImpl"),
            "pr202130": ("llvmorg-22\\.1\\.0", "ASTContext::getExtVectorType"),
            "pr204561": ("origin/main", "hasAcceptableMemberSpecialization"),
        }
        for issue, (bad_ref, signal) in expected.items():
            with self.subTest(issue=issue):
                self.assertMatches(text, rf"{issue}\)\s+BAD_REF=\"{bad_ref}\"")
                self.assertMatches(text, rf"{issue}\)\s+.*?GOOD_REFS=\(")
                self.assertMatches(text, rf"{issue}\)\s+.*?{signal}")
                self.assertMatches(text, rf"{issue}\)\s+cat >\"\$out\" <<'SRC'")

        self.assertMatches(text, r"pr165246\)\s+cat >\"\$out\" <<'SRC'.*?int decltype")
        self.assertMatches(text, r"pr167514\)\s+cat >\"\$out\" <<'SRC'.*?concept C = true")
        self.assertMatches(text, r"pr200648\)\s+cat >\"\$out\" <<'SRC'.*?lambda_in_trailing_decltype")
        self.assertMatches(text, r"pr202130\)\s+cat >\"\$out\" <<'SRC'.*?__builtin_masked_load")
        self.assertMatches(text, r"pr204561\)\s+cat >\"\$out\" <<'SRC'.*?A<long>::B")


if __name__ == "__main__":
    unittest.main()
