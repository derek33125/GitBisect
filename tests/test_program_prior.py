from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest import mock

from tools import crash_signals, lm_bisect, program_pass_graph, program_prior


class ProgramPriorTests(unittest.TestCase):
    def candidate(
        self,
        index: int,
        char: str,
        subject: str,
        *files: str,
    ) -> program_prior.ProgramPriorCandidate:
        return program_prior.ProgramPriorCandidate(
            sha=char * 40,
            index=index,
            subject=subject,
            changed_files=tuple(files),
        )

    def test_prior_keeps_every_commit_and_uses_only_program_evidence(self) -> None:
        candidates = [
            self.candidate(1, "a", "cleanup", "llvm/lib/IR/Verifier.cpp"),
            self.candidate(
                2,
                "b",
                "Update MemorySSA users",
                "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp",
            ),
            self.candidate(3, "c", "docs", "llvm/docs/ReleaseNotes.rst"),
        ]
        crash = {
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": ["llvm::MemorySSAUpdater::applyUpdates"],
            "pass_tokens": ["simple-loop-unswitch"],
            "query_terms": [{"term": "MemorySSAUpdater"}],
        }
        dependency_usage = {
            "MemorySSAUpdater": {
                "llvm/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp": 7,
                "llvm/lib/Transforms/Utils/LoopUtils.cpp": 1,
            }
        }

        result = program_prior.build_program_prior(
            candidates,
            crash,
            dependency_usage,
        )

        self.assertEqual(result["candidate_count"], 3)
        self.assertFalse(result["hard_pruning"])
        self.assertEqual(
            result["implementation_status"],
            "implemented-pending-online-validation",
        )
        self.assertIn("dependency-usage-tier", result["implemented_channels"])
        self.assertIn("pass-graph-self-pred-dep", result["implemented_channels"])
        self.assertEqual(result["deferred_parity_channels"], [])
        self.assertEqual(set(result["prior_mass_by_sha"]), {item.sha for item in candidates})
        self.assertAlmostEqual(sum(result["prior_mass_by_sha"].values()), 1.0)
        self.assertGreater(
            result["prior_score_by_sha"]["b" * 40],
            result["prior_score_by_sha"]["c" * 40],
        )
        self.assertGreater(result["prior_score_by_sha"]["c" * 40], 0.0)

    def test_group_max_does_not_double_count_correlated_signals(self) -> None:
        candidates = [
            self.candidate(1, "a", "first", "llvm/lib/A.cpp"),
            self.candidate(2, "b", "second", "llvm/lib/B.cpp"),
        ]
        first = program_prior.ProgramSignal(
            "stack:A",
            "crash-stack",
            frozenset({"a" * 40}),
            "crash",
        )
        duplicate = program_prior.ProgramSignal(
            "stack:A::method",
            "crash-stack",
            frozenset({"a" * 40}),
            "crash",
        )

        one = program_prior.score_program_prior(candidates, [first])
        two = program_prior.score_program_prior(candidates, [first, duplicate])

        self.assertAlmostEqual(
            one["prior_score_by_sha"]["a" * 40],
            two["prior_score_by_sha"]["a" * 40],
        )

    def test_message_variants_share_one_group_and_short_prefixes_do_not_expand(
        self,
    ) -> None:
        candidates = [
            self.candidate(
                1,
                "a",
                "Simplify Replicate::execute action",
                "llvm/lib/Transforms/Vectorize/VPlanRecipes.cpp",
            ),
            self.candidate(2, "b", "unrelated", "llvm/lib/IR/Value.cpp"),
        ]
        single = program_prior.build_program_prior(
            candidates,
            {
                "source_paths": [],
                "symbols": ["clang::FrontendAction::Execute"],
                "pass_tokens": [],
                "query_terms": [],
            },
            {},
        )
        variants = program_prior.build_program_prior(
            candidates,
            {
                "source_paths": [],
                "symbols": [
                    "clang::FrontendAction::Execute",
                    "clang::CompilerInstance::ExecuteAction",
                    "clang::ExecuteCompilerInvocation",
                ],
                "pass_tokens": [],
                "query_terms": [],
            },
            {},
        )

        message_groups = {
            signal["group"]
            for signal in variants["signals"]
            if signal["channel"] == "message"
        }
        self.assertEqual(message_groups, {"commit-message"})
        self.assertAlmostEqual(
            single["prior_score_by_sha"]["a" * 40],
            variants["prior_score_by_sha"]["a" * 40],
        )

    def test_reproducer_arch_and_tool_derive_interval_path_signals(self) -> None:
        candidates = [
            self.candidate(
                1,
                "a",
                "riscv",
                "llvm/lib/Target/RISCV/RISCVISelLowering.cpp",
            ),
            self.candidate(
                2,
                "b",
                "x86",
                "llvm/lib/Target/X86/X86ISelLowering.cpp",
            ),
            self.candidate(
                3,
                "c",
                "clangd",
                "clang-tools-extra/clangd/XRefs.cpp",
            ),
        ]
        crash = {
            "source_paths": [],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": [],
            "reproducer_terms": ["riscv64"],
            "tool": "clangd",
        }

        signals = program_prior.derive_program_signals(candidates, crash, {})
        by_name = {signal.name: signal for signal in signals}

        self.assertIn("dir:llvm/lib/Target/RISCV", by_name)
        self.assertEqual(by_name["dir:llvm/lib/Target/RISCV"].members, {"a" * 40})
        self.assertIn("dir:clang-tools-extra/clangd", by_name)
        self.assertEqual(by_name["dir:clang-tools-extra/clangd"].members, {"c" * 40})

    def test_rare_checker_is_soft_negative_and_producer_evidence_can_flip(self) -> None:
        candidates = [
            self.candidate(1, "a", "checker", "llvm/lib/Analysis/Checker.cpp"),
            self.candidate(2, "b", "producer", "llvm/lib/Transforms/Producer.cpp"),
            self.candidate(3, "c", "other", "llvm/lib/Support/Other.cpp"),
        ]
        crash = {
            "source_paths": ["llvm/lib/Analysis/Checker.cpp"],
            "rare_checker_paths": ["llvm/lib/Analysis/Checker.cpp"],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": [{"term": "MemoryState"}],
        }
        result = program_prior.build_program_prior(
            candidates,
            crash,
            {"MemoryState": {"llvm/lib/Transforms/Producer.cpp": 8}},
        )
        by_sha = result["candidate_by_sha"]
        checker_signal = next(
            signal
            for signal in result["signals"]
            if signal["name"] == "file:llvm/lib/Analysis/Checker.cpp"
        )

        self.assertEqual(checker_signal["polarity"], -1)
        self.assertEqual(
            by_sha["a" * 40]["score"],
            program_prior.DEFAULT_PRIOR_FLOOR,
        )
        self.assertGreater(
            by_sha["b" * 40]["score"],
            by_sha["a" * 40]["score"],
        )
        self.assertTrue(all(float(value) > 0 for value in result["prior_mass_by_sha"].values()))

    def test_volume_correction_reduces_large_commit_signal_weight(self) -> None:
        candidates = [
            self.candidate(1, "a", "small", "llvm/lib/A.cpp"),
            self.candidate(
                2,
                "b",
                "large",
                *[f"llvm/lib/Large/File{index}.cpp" for index in range(10)],
            ),
            self.candidate(3, "c", "none", "llvm/lib/C.cpp"),
        ]
        signal = program_prior.ProgramSignal(
            "dependency:X:mention",
            "dependency:X",
            frozenset({"a" * 40, "b" * 40}),
            "dependency",
        )

        result = program_prior.score_program_prior(candidates, [signal])

        self.assertGreater(
            result["prior_score_by_sha"]["a" * 40],
            result["prior_score_by_sha"]["b" * 40],
        )

    def test_ordinal_fusion_preserves_frontier_mass_and_outside_mass(self) -> None:
        prior = {"a": 0.2, "b": 0.3, "c": 0.5}

        fused, metadata = program_prior.mass_preserving_ordinal_fusion(
            prior,
            {"a": 2, "b": 1},
        )

        self.assertAlmostEqual(fused["a"] + fused["b"], 0.5)
        self.assertAlmostEqual(fused["c"], 0.5)
        self.assertGreater(fused["b"], fused["a"])
        self.assertAlmostEqual(metadata["frontier_mass_before"], metadata["frontier_mass_after"])

    def test_frozen_js_python_rank_and_channel_parity(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "ceg_program_prior_golden.json"
            ).read_text(encoding="utf-8")
        )
        candidates = [
            program_prior.ProgramPriorCandidate(
                sha=str(item["sha"]),
                index=int(item["index"]),
                subject="",
                changed_files=tuple(
                    f"{item['sha']}-{index}.cpp"
                    for index in range(int(item["changed_file_count"]))
                ),
            )
            for item in fixture["candidates"]
        ]
        signals = [
            program_prior.ProgramSignal(
                name=str(item["name"]),
                group=str(item["group"]),
                channel=str(item["channel"]),
                members=frozenset(str(sha) for sha in item["members"]),
            )
            for item in fixture["signals"]
        ]
        result = program_prior.score_program_prior(candidates, signals)
        actual = {
            str(item["sha"]): item
            for item in result["candidates"]
        }
        for expected in fixture["expected"]:
            row = actual[str(expected["sha"])]
            self.assertAlmostEqual(
                float(row["score"]) - program_prior.DEFAULT_PRIOR_FLOOR,
                float(expected["score_without_floor"]),
                places=12,
            )
            self.assertEqual(row["rank"], expected["rank"])
            self.assertEqual(row["hits"], expected["hits"])
        coverage = {
            channel: len(
                {
                    sha
                    for signal in signals
                    if signal.channel == channel
                    for sha in signal.members
                }
            )
            for channel in fixture["expected_channel_coverage"]
        }
        self.assertEqual(coverage, fixture["expected_channel_coverage"])


class ProgramPassGraphTests(unittest.TestCase):
    def test_pipeline_parser_and_registry_keep_declared_structure(self) -> None:
        self.assertEqual(
            program_pass_graph.flatten_pipeline(
                "function<eager-inv>(instcombine<max-iterations=1>,loop(licm),verify)"
            ),
            [
                "function<eager-inv>",
                "instcombine<max-iterations=1>",
                "loop",
                "licm",
                "verify",
            ],
        )
        parsed = program_pass_graph.parse_pass_registry(
            """
            FUNCTION_PASS("foo", AlphaPass())
            FUNCTION_ANALYSIS("baz", GammaAnalysis())
            """
        )
        self.assertEqual(parsed["by_name"]["foo"]["class_name"], "AlphaPass")
        self.assertEqual(parsed["analyses"]["GammaAnalysis"], "baz")
        legacy = program_pass_graph.parse_legacy_registry(
            """
            #define PASS_ARG "register-coalescer"
            #define PASS_NAME "Register Coalescer"
            INITIALIZE_PASS(RegisterCoalescer, PASS_ARG, PASS_NAME, false, false)
            """,
            path="llvm/lib/CodeGen/RegisterCoalescer.cpp",
        )
        self.assertEqual(
            legacy["by_display"]["Register Coalescer"]["class_name"],
            "RegisterCoalescer",
        )
        self.assertEqual(
            legacy["by_arg"]["register-coalescer"]["file"],
            "llvm/lib/CodeGen/RegisterCoalescer.cpp",
        )

    def test_bad_tree_pass_graph_and_phase_paths_are_answer_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            files = {
                "llvm/lib/Passes/PassRegistry.def": (
                    'FUNCTION_PASS("foo", AlphaPass())\n'
                    'FUNCTION_PASS("bar", BetaPass())\n'
                    'FUNCTION_ANALYSIS("baz", GammaAnalysis())\n'
                ),
                "llvm/lib/Transforms/Alpha.cpp": (
                    "void AlphaPass::run() { AM.getResult<GammaAnalysis>(); }\n"
                ),
                "llvm/lib/Transforms/Beta.cpp": "void BetaPass::run() {}\n",
                "llvm/lib/Analysis/Gamma.cpp": "void GammaAnalysis::run() {}\n",
                "clang/lib/Sema/Sema.cpp": (
                    'const char *Phase = "instantiating function definition";\n'
                ),
                "llvm/lib/Target/AArch64/AArch64ISelLowering.cpp": "void lower() {}\n",
                "clang-tools-extra/clangd/XRefs.cpp": "void refs() {}\n",
                "clang-tools-extra/clangd/readability/ContainerSizeEmptyCheck.cpp": (
                    "void check() {}\n"
                ),
            }
            for relative, text in files.items():
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=CEG Test",
                    "-c",
                    "user.email=ceg@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            bad_sha = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            crash = {
                "passes": ["function(bar,foo)", "foo"],
                "pass_records": [
                    {"pass": "function(bar,foo)", "on": "module"},
                    {"pass": "foo", "on": "@f"},
                ],
                "declared_pass": "foo",
                "legacy_pass_format": False,
                "tool": "clangd",
                "flags": ["--checks=readability-container-size-empty"],
                "reproducer_terms": ["aarch64"],
                "source_paths": ["clang/lib/Sema/Sema.cpp"],
                "rare_checker_paths": ["clang/lib/Sema/Sema.cpp"],
                "phases": [
                    {"index": 1, "text": "parser at end of file"},
                    {"index": 2, "text": "instantiating function definition"},
                ],
            }
            tree_paths, tree_by_stem = program_pass_graph._tree_index(repo, bad_sha)
            self.assertIn("llvm/lib/Transforms/Alpha.cpp", tree_paths)
            self.assertIn("Alpha", tree_by_stem)
            self.assertEqual(
                program_pass_graph._files_for_class(
                    "AlphaPass",
                    tree_by_stem,
                    project="llvm",
                    implementations_only=True,
                ),
                ["llvm/lib/Transforms/Alpha.cpp"],
            )

            graph = program_pass_graph.build_pass_graph_path_signals(
                repo,
                bad_sha,
                crash,
            )
            phases = program_pass_graph.build_phase_path_signals(
                repo,
                bad_sha,
                crash,
            )
            paths = program_pass_graph.build_bad_tree_path_signals(
                repo,
                bad_sha,
                crash,
            )

        signal_paths = {
            item["name"]: set(item["paths"])
            for item in graph["signals"]
        }
        self.assertIn("pipe:self:foo:file", signal_paths, graph)
        self.assertIn("llvm/lib/Transforms/Alpha.cpp", signal_paths["pipe:self:foo:file"])
        self.assertIn("llvm/lib/Transforms/Beta.cpp", signal_paths["pipe:pred:fall"])
        self.assertIn("llvm/lib/Analysis/Gamma.cpp", signal_paths["pipe:dep:baz:file"])
        self.assertTrue(
            any(
                "clang/lib/Sema/Sema.cpp" in item["paths"]
                for item in phases["signals"]
            )
        )
        path_names = {item["name"] for item in paths["signals"]}
        self.assertIn("dir:llvm/lib/Target/AArch64", path_names)
        self.assertIn("dir:clang-tools-extra/clangd", path_names)
        self.assertIn("dir:clang-tools-extra/clangd/readability", path_names)
        self.assertNotIn("dir:clang/lib/Sema", path_names)


class CausalEvidenceGuidedIntegrationTests(unittest.TestCase):
    def profile(
        self,
        *,
        keywords: list[str] | None = None,
        relevant_paths: list[str] | None = None,
        high_risk_paths: list[str] | None = None,
    ) -> lm_bisect.IssueProfile:
        return lm_bisect.IssueProfile(
            issue_id="demo",
            issue_url="",
            title="authored title",
            good_commit="a" * 40,
            good_ref="",
            bad_commit="b" * 40,
            bisect_log="",
            runner="",
            bug_report_summary="authored summary",
            keywords=list(keywords or []),
            relevant_paths=list(relevant_paths or []),
            high_risk_paths=list(high_risk_paths or []),
        )

    def record(self, index: int, char: str) -> lm_bisect.CommitRecord:
        return lm_bisect.CommitRecord(
            index=index,
            sha=char * 40,
            subject=char,
            body="",
            changed_files=[f"llvm/lib/{char}.cpp"],
            diff_text="",
            semantic_score=0.1,
            build_success_prob=1.0,
            suspicion_weight=0.0,
        )

    def test_ceg_prompt_excludes_researcher_authored_issue_prose(self) -> None:
        profile = self.profile(
            keywords=["secret-keyword"],
            relevant_paths=["secret/relevant"],
            high_risk_paths=["secret/high-risk"],
        )
        prompt = lm_bisect.build_deterministic_facts_ranking_prompt(
            profile,
            [
                {
                    "sha": "c" * 40,
                    "subject": "candidate",
                    "files": ["llvm/lib/Candidate.cpp"],
                    "causal_retrieval": {
                        "crash_signals": {"assertion": "runtime contract"},
                        "repository_facts": {},
                        "selected_hunks": [],
                        "contract_contexts": [],
                    },
                }
            ],
            include_authored_issue_prose=False,
        )

        self.assertNotIn(f"- title: {profile.title}", prompt)
        self.assertNotIn(f"- summary: {profile.bug_report_summary}", prompt)
        self.assertNotIn("secret-keyword", prompt)
        self.assertNotIn("secret/relevant", prompt)
        self.assertNotIn("c" * 40, prompt)
        self.assertIn("- id: C01", prompt)
        self.assertIn("runtime contract", prompt)
        self.assertIn("no researcher-authored title", prompt)

    def test_ceg_input_requires_manifest_verified_bad_endpoint_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "cases/demo"
            case_dir.mkdir(parents=True)
            artifact = case_dir / "crash-assertion.err"
            artifact.write_text(
                "clang: llvm/lib/IR/Verifier.cpp:10: Assertion `broken' failed.\n",
                encoding="utf-8",
            )
            reproducer = case_dir / "reproducer.ll"
            reproducer.write_text("define void @f() { ret void }\n", encoding="utf-8")
            manifest = {
                "protocol": lm_bisect.CAUSAL_EVIDENCE_GUIDED_INPUT_PROTOCOL,
                "cases": [
                    {
                        "issue": "demo",
                        "capture_role": "bad-endpoint",
                        "capture_commit": "b" * 40,
                        "crash_artifact": "cases/demo/crash-assertion.err",
                        "crash_artifact_sha256": hashlib.sha256(
                            artifact.read_bytes()
                        ).hexdigest(),
                        "reproducer_sha256": {
                            "cases/demo/reproducer.ll": hashlib.sha256(
                                reproducer.read_bytes()
                            ).hexdigest()
                        },
                    }
                ],
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            payload = lm_bisect.ceg_bad_endpoint_signal_payload(
                self.profile(),
                root,
            )
            self.assertEqual(payload["capture_role"], "bad-endpoint")
            self.assertEqual(payload["capture_commit"], "b" * 40)
            self.assertEqual(payload["artifact_lookup"], "ceg-bad-endpoint")

            manifest["cases"][0]["capture_commit"] = "c" * 40
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "bad endpoint"):
                lm_bisect.ceg_bad_endpoint_signal_payload(
                    self.profile(),
                    root,
                )

    def test_frontier_keeps_causal_prior_and_split_support_only(self) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 7)]
        scores = {record.sha: float(7 - record.index) for record in records}
        total = sum(scores.values())
        evidence = {
            "prior_score_by_sha": scores,
            "prior_mass_by_sha": {sha: score / total for sha, score in scores.items()},
        }

        decision = lm_bisect.resolve_causal_evidence_guided_frontier(
            records,
            evidence,
            target_count=5,
        )

        roles = {item["role"] for item in decision.role_assignments}
        self.assertIn("program-prior-top", roles)
        self.assertIn("prior-mass-midpoint", roles)
        self.assertIn("chronological-midpoint-support", roles)
        self.assertNotIn("buildability-support", roles)

    def test_selection_uses_mass_preserving_ordinal_fusion(self) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 4)]
        records[2].semantic_score = 99.0
        records[2].evidence = ["authored-profile-score"]
        records[0].causal_evidence = {"ordinal_judgment": {"rank": 2}}
        records[1].causal_evidence = {"ordinal_judgment": {"rank": 1}}
        evidence = {
            "version": program_prior.PROGRAM_PRIOR_VERSION,
            "evidence_sha256": "digest",
            "prior_mass_by_sha": {
                records[0].sha: 0.2,
                records[1].sha: 0.3,
                records[2].sha: 0.5,
            },
            "candidate_by_sha": {
                record.sha: {
                    "score": 1.0,
                    "mass": mass,
                    "hits": ["signal"],
                }
                for record, mass in zip(records, (0.2, 0.3, 0.5))
            },
        }

        decision = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )

        fusion = decision.metadata["fusion"]
        self.assertAlmostEqual(fusion["frontier_mass_before"], fusion["frontier_mass_after"])
        self.assertEqual(
            decision.selection_mode,
            "causal-evidence-guided-calibrated-posterior",
        )
        self.assertEqual(
            decision.metadata["selector"]["implementation"],
            "shared-calibrated-posterior",
        )
        self.assertEqual(records[2].semantic_score, 1.0)
        self.assertNotIn("authored-profile-score", records[2].evidence)
        self.assertEqual(records[1].llm_ordinal_rank, 1)

    def test_strong_rank_one_judgment_triggers_bounded_parent_validation(self) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 6)]
        culprit = records[3]
        culprit.semantic_score = 3.4
        culprit.causal_evidence = {
            "ordinal_judgment": {
                "rank": 1,
                "mechanism": "invariant-break",
                "explains_failure": True,
                "confidence": 0.96,
            }
        }
        evidence = {
            "version": program_prior.PROGRAM_PRIOR_VERSION,
            "evidence_sha256": "digest",
            "prior_mass_by_sha": {record.sha: 0.2 for record in records},
            "candidate_by_sha": {
                record.sha: {
                    "score": float(index),
                    "mass": 0.2,
                    "hits": ["signal"],
                }
                for index, record in enumerate(records, 1)
            },
        }

        decision = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )
        self.assertEqual(decision.selected.sha, culprit.sha)
        self.assertEqual(
            decision.selection_mode,
            "causal-evidence-guided-high-confidence-probe",
        )
        after_bad = lm_bisect.causal_evidence_guided_transition(
            [record.sha for record in records],
            selected_sha=culprit.sha,
            verdict="bad",
            phase="search",
            high_confidence_probe_selected=True,
            candidate_parent_sha=records[2].sha,
        )
        self.assertEqual(after_bad["phase"], "parent-validation")
        after_parent = lm_bisect.causal_evidence_guided_transition(
            list(after_bad["unresolved"]),
            selected_sha=records[2].sha,
            verdict="good",
            phase="parent-validation",
            candidate_sha=culprit.sha,
            candidate_parent_sha=records[2].sha,
        )
        self.assertEqual(after_parent["phase"], "resolved")
        self.assertEqual(after_parent["unresolved"], [culprit.sha])

    def test_parent_validation_uses_declared_git_parent_not_list_adjacency(
        self,
    ) -> None:
        unresolved = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
        after_bad = lm_bisect.causal_evidence_guided_transition(
            unresolved,
            selected_sha="d" * 40,
            verdict="bad",
            phase="search",
            high_confidence_probe_selected=True,
            candidate_parent_sha="b" * 40,
        )

        self.assertEqual(after_bad["pending_parent_sha"], "b" * 40)
        resolved = lm_bisect.causal_evidence_guided_transition(
            list(after_bad["unresolved"]),
            selected_sha="b" * 40,
            verdict="good",
            phase="parent-validation",
            candidate_sha="d" * 40,
            candidate_parent_sha="b" * 40,
        )
        self.assertEqual(resolved["phase"], "resolved")

    def test_repaired_ordinal_permutation_cannot_trigger_direct_probe(self) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 4)]
        records[0].causal_evidence = {
            "ordinal_judgment": {
                "rank": 1,
                "mechanism": "invariant-break",
                "explains_failure": True,
                "confidence": 0.99,
                "ordinal_permutation_valid": False,
            }
        }
        evidence = {
            "version": program_prior.PROGRAM_PRIOR_VERSION,
            "evidence_sha256": "digest",
            "prior_mass_by_sha": {record.sha: 1 / 3 for record in records},
            "candidate_by_sha": {
                record.sha: {"score": 1.0, "mass": 1 / 3, "hits": []}
                for record in records
            },
        }

        decision = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )
        self.assertEqual(
            decision.selection_mode,
            "causal-evidence-guided-calibrated-posterior",
        )

    def test_high_confidence_endpoint_skips_rebuild_and_reserves_parent_budget(
        self,
    ) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 5)]
        endpoint = records[-1]
        endpoint.causal_evidence = {
            "ordinal_judgment": {
                "rank": 1,
                "mechanism": "precondition-violation",
                "explains_failure": True,
                "confidence": 0.97,
            }
        }
        evidence = {
            "version": program_prior.PROGRAM_PRIOR_VERSION,
            "evidence_sha256": "digest",
            "prior_mass_by_sha": {record.sha: 0.25 for record in records},
            "candidate_by_sha": {
                record.sha: {
                    "score": 1.0,
                    "mass": 0.25,
                    "hits": [],
                }
                for record in records
            },
        }

        direct = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )
        without_parent_budget = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
            allow_direct_probe=False,
        )

        self.assertEqual(direct.selected.sha, endpoint.sha)
        self.assertEqual(
            direct.selection_mode,
            "causal-evidence-guided-high-confidence-probe",
        )
        self.assertEqual(
            without_parent_budget.selection_mode,
            "causal-evidence-guided-calibrated-posterior",
        )
        endpoint_shortcut = (
            lm_bisect.causal_evidence_guided_known_bad_parent_validation(
                direct,
                records,
                [record.sha for record in records],
                [],
                records[-2].sha,
            )
        )
        self.assertIsNotNone(endpoint_shortcut)
        assert endpoint_shortcut is not None
        parent_decision, candidate_sha = endpoint_shortcut
        self.assertEqual(candidate_sha, endpoint.sha)
        self.assertEqual(parent_decision.selected.sha, records[-2].sha)
        self.assertEqual(
            parent_decision.metadata["known_bad_candidate_reused"]["source"],
            "declared-bad-endpoint",
        )

        endpoint.causal_evidence["ordinal_judgment"]["confidence"] = 0.87
        low_confidence = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )
        self.assertEqual(
            low_confidence.selection_mode,
            "causal-evidence-guided-calibrated-posterior",
        )

    def test_observed_bad_direct_candidate_jumps_to_parent_without_rebuild(
        self,
    ) -> None:
        records = [self.record(index, chr(96 + index)) for index in range(1, 5)]
        culprit = records[2]
        culprit.causal_evidence = {
            "ordinal_judgment": {
                "rank": 1,
                "mechanism": "invariant-break",
                "explains_failure": True,
                "confidence": 0.98,
            }
        }
        evidence = {
            "version": program_prior.PROGRAM_PRIOR_VERSION,
            "evidence_sha256": "digest",
            "prior_mass_by_sha": {record.sha: 0.25 for record in records},
            "candidate_by_sha": {
                record.sha: {
                    "score": 1.0,
                    "mass": 0.25,
                    "hits": [],
                }
                for record in records
            },
        }
        direct = lm_bisect.causal_evidence_guided_selection(
            self.profile(),
            records,
            evidence,
        )
        observation = lm_bisect.CommitObservation(
            sha=culprit.sha,
            verdict="bad",
            summary="already reproduced",
            features=[],
        )

        shortcut = (
            lm_bisect.causal_evidence_guided_known_bad_parent_validation(
                direct,
                records,
                [record.sha for record in records],
                [
                    {
                        "sha": culprit.sha,
                        "verdict": "bad",
                        "summary": observation.summary,
                        "source": "runner",
                    }
                ],
                records[1].sha,
            )
        )

        self.assertIsNotNone(shortcut)
        assert shortcut is not None
        parent_decision, candidate_sha = shortcut
        self.assertEqual(candidate_sha, culprit.sha)
        self.assertEqual(parent_decision.selected.sha, records[1].sha)
        self.assertEqual(
            parent_decision.selection_mode,
            "causal-evidence-guided-parent-validation",
        )
        self.assertIsNone(
            lm_bisect.causal_evidence_guided_known_bad_parent_validation(
                direct,
                records,
                [record.sha for record in records],
                [],
                records[1].sha,
            )
        )

    def test_ceg_normalization_removes_framework_symbols_only(self) -> None:
        normalized = crash_signals.causal_evidence_guided_signal_payload(
            {
                "symbols": [
                    "clang::FrontendAction::Execute",
                    "clang::driver::Compilation::ExecuteJobs",
                    "llvm::MemorySSAUpdater::applyUpdates",
                ],
                "query_terms": [
                    {"term": "Execute", "kind": "stack-symbol"},
                    {"term": "ExecuteJobs", "kind": "stack-symbol"},
                    {"term": "applyUpdates", "kind": "stack-symbol"},
                    {"term": "MemorySSA", "kind": "assertion-class"},
                ],
            }
        )

        self.assertEqual(
            normalized["symbols"],
            ["llvm::MemorySSAUpdater::applyUpdates"],
        )
        self.assertEqual(
            normalized["query_terms"],
            [
                {"term": "applyUpdates", "kind": "stack-symbol"},
                {"term": "MemorySSA", "kind": "assertion-class"},
            ],
        )
        self.assertEqual(
            normalized["normalization"],
            "ceg-v3-framework-filter",
        )

    def test_ceg_retrieval_reuses_enriched_crash_payload(self) -> None:
        enriched = {
            "query_terms": [{"term": "shufflevector", "source": "reproducer-ir"}],
            "normalization": "ceg-v3-framework-filter",
        }

        actual = lm_bisect.resolved_causal_retrieval_crash_signal_payload(
            self.profile(),
            "causal-llm-ceg-bisect",
            {"crash_signals": enriched},
        )

        self.assertEqual(actual, enriched)
        self.assertIsNot(actual, enriched)

    def test_ceg_retrieval_builds_deterministic_repository_facts(self) -> None:
        candidate_sha = "c" * 40
        with (
            mock.patch.object(
                lm_bisect,
                "first_parent_commit_range",
                return_value=[candidate_sha],
            ),
            mock.patch.object(
                lm_bisect,
                "select_causal_retrieval_files",
                return_value=[],
            ),
            mock.patch.object(
                lm_bisect,
                "commit_parent_diff_for_files",
                return_value="",
            ),
            mock.patch.object(
                lm_bisect,
                "deterministic_repository_facts",
                return_value={"marker": "ceg-facts"},
            ) as facts,
        ):
            result = lm_bisect.retrieve_causal_diff_evidence(
                Path("."),
                self.profile(),
                {
                    "sha": candidate_sha,
                    "subject": "candidate",
                    "files": [],
                    "diff": "",
                },
                retrieval_policy="ceg-bisect",
                crash_signal_payload={"query_terms": []},
                destruction_surface=[],
            )

        self.assertEqual(result["repository_facts"], {"marker": "ceg-facts"})
        facts.assert_called_once()

    def test_parent_window_hunks_are_context_not_candidate_facts(self) -> None:
        candidate_path = "llvm/lib/Transforms/Candidate.cpp"
        context_path = "llvm/lib/Transforms/Predecessor.cpp"
        hunks = [
            {
                "path": candidate_path,
                "header": "void candidate()",
                "patch": "+ state->destroy();",
                "source_distance": 0,
            },
            {
                "path": context_path,
                "header": "void predecessor()",
                "patch": "+ state->destroy();",
                "source_distance": 2,
            },
        ]

        facts = lm_bisect.deterministic_repository_facts(
            Path("."),
            self.profile(),
            hunks,
            {
                "source_paths": [],
                "rare_checker_paths": [],
            },
            None,
            {
                "state": {
                    candidate_path: 3,
                    context_path: 7,
                }
            },
            ["destroy"],
        )
        prompt = lm_bisect.build_deterministic_facts_ranking_prompt(
            self.profile(),
            [
                {
                    "sha": "c" * 40,
                    "subject": "candidate",
                    "files": [candidate_path],
                    "causal_retrieval": {
                        "selected_hunks": hunks,
                        "repository_facts": facts,
                    },
                }
            ],
        )

        self.assertEqual(facts["candidate_files"], [candidate_path])
        self.assertEqual(facts["predecessor_context_files"], [context_path])
        self.assertEqual(facts["dependency_api_use"], {candidate_path: 3})
        self.assertIn("candidate -> state->destroy", facts["contact_paths"])
        self.assertNotIn("predecessor -> state->destroy", facts["contact_paths"])
        self.assertIn(
            "predecessor context at distance 2; do not attribute this patch",
            prompt,
        )

    def test_ceg_retrieval_does_not_use_authored_profile_fields(self) -> None:
        changed_files = [
            "llvm/lib/Authored/KeywordPath.cpp",
            "llvm/lib/Analysis/MemorySSA.cpp",
        ]
        crash = {
            "source_paths": ["llvm/lib/Analysis/MemorySSA.cpp"],
            "symbols": [],
            "pass_tokens": [],
            "query_terms": [{"term": "MemorySSA"}],
        }
        authored = self.profile(
            keywords=["KeywordPath"],
            relevant_paths=["llvm/lib/Authored"],
            high_risk_paths=["llvm/lib/Authored"],
        )
        empty = self.profile()

        selected_with_authored = lm_bisect.select_causal_retrieval_files(
            authored,
            changed_files,
            retrieval_policy="ceg-bisect",
            crash_signal_payload=crash,
        )
        selected_without_authored = lm_bisect.select_causal_retrieval_files(
            empty,
            changed_files,
            retrieval_policy="ceg-bisect",
            crash_signal_payload=crash,
        )
        reasons = lm_bisect.causal_hunk_match_reasons(
            authored,
            changed_files[0],
            "KeywordPath();",
            crash,
            include_authored_profile=False,
        )

        self.assertEqual(selected_with_authored, selected_without_authored)
        self.assertEqual(selected_with_authored[0], "llvm/lib/Analysis/MemorySSA.cpp")
        self.assertNotIn("relevant-path", reasons)
        self.assertNotIn("high-risk-path", reasons)
        self.assertNotIn("issue-keyword", reasons)

    def test_ceg_query_terms_include_diagnostic_and_ir_opcode_anchors(self) -> None:
        opcodes = lm_bisect.reproducer_ir_opcodes(
            'target triple = "riscv64-unknown-linux-gnu"\n'
            "define void @f() {\n"
            "  %v = shufflevector <4 x i32> undef, <4 x i32> undef, <4 x i32> zeroinitializer\n"
            "  ret void\n"
            "}\n"
        )
        payload = {
            "assertion": 'Width >= BitWidth && "friendly explanation"',
            "verifier_message": "Instruction does not dominate all uses!",
            "query_terms": [],
            "reproducer_ir_opcodes": opcodes,
            "reproducer_source_constructs": (
                lm_bisect.reproducer_source_constructs(
                    'asm goto("" : : : : target);'
                )
            ),
        }

        lm_bisect.add_ceg_program_query_terms(payload)
        entries = {
            str(item["term"]): item
            for item in payload["query_terms"]
        }

        self.assertIn("BitWidth", entries)
        self.assertIn("dominate", entries)
        self.assertIn("shufflevector", entries)
        self.assertIn("asm goto", entries)
        self.assertTrue(entries["shufflevector"]["case_insensitive"])
        self.assertNotIn("define", entries)
        self.assertNotIn("friendly", entries)

    def test_ceg_dependency_scope_comes_from_crash_and_bad_tree_paths(self) -> None:
        paths = lm_bisect.ceg_dependency_search_paths(
            {"source_paths": ["clang/lib/Sema/Sema.cpp"]},
            {
                "signals": [
                    {
                        "paths": ["clang-tools-extra/clangd"],
                        "provenance": {"derived_from": ["tool"]},
                    }
                ]
            },
        )

        self.assertIn("llvm/lib", paths)
        self.assertIn("clang", paths)
        self.assertIn("clang-tools-extra/clangd", paths)

    def test_master50_lookup_prefers_newest_bundle_and_records_reproducer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_case = root / "human_analysis/raw/master50-evidence-20260821/cases/demo"
            new_case = root / "human_analysis/raw/master50-evidence-20260903-r4/cases/demo"
            old_case.mkdir(parents=True)
            new_case.mkdir(parents=True)
            (old_case / "crash-assertion.err").write_text("old crash\n")
            (new_case / "crash-assertion.err").write_text("new crash\n")
            (new_case / "reproducer.ll").write_text(
                'target triple = "riscv64-unknown-linux-gnu"\n'
            )
            (new_case.parent.parent / "manifest.json").write_text(
                """{
                  "cases": [{
                    "issue": "demo",
                    "crash_evidence": {
                      "running_pass": "New Pass",
                      "component": "NewPass",
                      "scope": "function",
                      "pass_record_count": 1
                    }
                  }]
                }"""
            )
            parsed = mock.Mock()
            parsed.payload.return_value = {
                "source_paths": [],
                "symbols": [],
                "passes": ["New Pass"],
                "pass_tokens": [],
                "query_terms": [],
            }
            with mock.patch.object(lm_bisect, "ROOT_DIR", root), mock.patch.object(
                lm_bisect.crash_signals,
                "parse_crash_report",
                return_value=parsed,
            ) as parse:
                payload = lm_bisect.profile_crash_signal_payload(
                    self.profile(),
                    use_human_study_normalization=False,
                    artifact_lookup="master50",
                )

        parse.assert_called_once_with("new crash\n")
        self.assertEqual(payload["artifact_package"], "master50-evidence-20260903-r4")
        self.assertEqual(payload["reproducer_terms"], ["riscv64"])
        self.assertEqual(len(payload["reproducer_paths"]), 1)
        self.assertEqual(payload["declared_pass"], "New Pass")
        self.assertTrue(payload["declared_pass_agrees"])

    def test_crash_parser_keeps_ordered_pass_and_phase_facts(self) -> None:
        parsed = crash_signals.parse_crash_report(
            """Stack dump:
0. Program arguments: clang -cc1 repro.cpp
1. <eof> parser at end of file
2. repro.cpp:26:15: instantiating function definition 'Foo<int>::bar'
3. Running pass 'SimpleLoopUnswitchPass' on function '@f'
"""
        )

        self.assertEqual(parsed.passes, ["SimpleLoopUnswitchPass"])
        self.assertEqual(
            parsed.pass_records,
            [{"pass": "SimpleLoopUnswitchPass", "on": "@f"}],
        )
        self.assertEqual(
            parsed.phases,
            [
                {"index": 1, "text": "parser at end of file"},
                {"index": 2, "text": "instantiating function definition"},
            ],
        )

    def test_crash_parser_handles_darwin_assert_and_tool_prefix(self) -> None:
        darwin = crash_signals.parse_crash_report(
            "Assertion failed: (Node != nullptr), function visitNode, "
            "file /src/llvm-project/clang/lib/AST/Visitor.cpp, line 42"
        )
        prefixed = crash_signals.parse_crash_report(
            "clangd: ../clang-tools-extra/clangd/XRefs.cpp:17: void lookup(): "
            "Assertion `Ready' failed."
        )

        self.assertEqual(darwin.kind, "assertion")
        self.assertEqual(darwin.assert_function, "visitNode")
        self.assertEqual(darwin.assert_source_file, "clang/lib/AST/Visitor.cpp")
        self.assertEqual(prefixed.tool, "clangd")
        self.assertEqual(
            prefixed.assert_source_file,
            "clang-tools-extra/clangd/XRefs.cpp",
        )


if __name__ == "__main__":
    unittest.main()
