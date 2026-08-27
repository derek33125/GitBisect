#!/usr/bin/env python3
"""Create a compact scoped-ten first-bad evidence bundle for manual review."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.audit_first_bad_relevance import (
    CANONICAL_FIRST_BAD,
    CAUSAL_INTERPRETATIONS,
    SCOPED_ISSUES,
    RelevanceAssessment,
    assess_relevance,
    compact_diff_evidence,
    commit_details,
    extract_crash_evidence,
    stored_trace_evidence,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES_PATH = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "results" / "manual-review"

# These are source-review hypotheses, not runtime features or causal proofs.
MANUAL_STATIC_ANALYSIS: dict[str, dict[str, str]] = {
    "pr204559": {
        "dependency_chain": "SimpleLoopUnswitch CFG/latch rewrite -> changed loop exits and dominance relations -> MemorySSA clobber-walker query -> OtherClobbers dominance assertion.",
        "inspection_focus": "Read the added latch-exit path and each MemorySSA update/DT preservation decision. Compare generated IR before the clobber-walker assertion, not just MemorySSA.cpp.",
    },
    "pr204589": {
        "dependency_chain": "SimpleLoopUnswitch CFG/latch rewrite -> transformed memory-access graph -> stale MemorySSA use relationship -> removeFromLookups use_empty assertion.",
        "inspection_focus": "Trace MemoryAccess creation/removal around the unswitched latch and identify the user that survives removal. The detector is downstream of the changed pass.",
    },
    "pr201444": {
        "dependency_chain": "X86 BT/BTR/BTS/BTC combine -> peekThroughBitPosExtTrunc wrapper stripping -> width adjustment -> low-bit mask invariant assertion in X86ISelLowering.",
        "inspection_focus": "Review the newly introduced helper and every caller in combineBTToBitOpFlag; validate trunc/extend width assumptions against the reproducer DAG.",
    },
    "pr193164": {
        "dependency_chain": "VPlan canonical-IV construction -> VPRecipe ordering/storage -> loop/epilogue plan preparation -> phi-like recipe ordering assertion.",
        "inspection_focus": "Inspect the canonical-IV region insertion and all changed VPlan consumers. A raw assertion trace was not retained, so reproduce locally before claiming an exact call chain.",
    },
    "pr50304": {
        "dependency_chain": "ConstantFolding APFloat conversion -> floating-point semantic selection -> representability check -> APFloat IEEE-double assertion.",
        "inspection_focus": "Review APFloat conversion and result construction paths introduced by the rewrite, then compare semantic widths in the reduced C reproducer.",
    },
    "pr50585": {
        "dependency_chain": "DivRemPairs common-predecessor hoist -> rewritten SSA placement -> operand no longer dominates a use -> IR verifier Broken function report.",
        "inspection_focus": "Inspect the hoist placement and dominance updates for the quotient/remainder pair. The verifier report is a detector, so absence of Verifier.cpp in the patch is expected.",
    },
    "pr48154": {
        "dependency_chain": "BuildLibCalls attribute inference -> argmemonly/related attribute state -> InstCombine or Attributor propagation -> verifier incompatible-attributes report.",
        "inspection_focus": "Read inferred attribute preconditions and all consumers that merge them. Verify the precise function/call-site type mismatch before treating the verifier location as causal.",
    },
    "pr49535": {
        "dependency_chain": "ValueTracking recurrence inversion -> getInvertibleOperand operand selection -> BO1 operand invariant -> ValueTracking assertion.",
        "inspection_focus": "Review newly supported recurrence shapes and the PHI/operand selection path. The known Git first-bad SHA differs from one original-apply representation, so keep SHA provenance explicit.",
    },
    "pr52635": {
        "dependency_chain": "XRay sled-v2 PC-relative handling -> AsmPrinter/InstrumentationMap symbol construction -> MCSymbolRefExpr validation -> Symbol assertion.",
        "inspection_focus": "Trace symbol variant and relocation choices from emitXRayTable through MC expression construction for the AArch64 reproducer.",
    },
    "pr200987": {
        "dependency_chain": "Clang asm-goto output codegen -> indirect-edge IR/CFG shape -> loop exit rewriting and ScalarEvolution expansion -> isKnownSentinel/SCEVExpander crash.",
        "inspection_focus": "Inspect the codegen contract for asm-goto outputs and dump IR before loop optimization. The retained history has no raw trace, so this is a source-level dependency hypothesis.",
    },
}


@dataclass(frozen=True)
class BundleResult:
    archive_path: Path
    manifest_path: Path
    review_report_path: Path


def git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def canonical_parent(repo: Path, sha: str) -> str:
    return git_output(repo, "rev-parse", f"{sha}^").strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitized_extension(path: Path) -> str:
    return path.suffix if path.suffix in {".c", ".cc", ".cpp", ".ll", ".m", ".mm"} else ".txt"


def find_reproducer(
    issue_dir: Path,
    *,
    issue: str | None = None,
    source_root: Path | None = None,
) -> Path | None:
    search_roots = [issue_dir]
    if issue and source_root is not None:
        search_roots.append(source_root / "scripts" / issue)
    candidates: list[Path] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        candidates.extend(
            path
            for path in root.rglob("repro.*")
            if path.suffix in {".c", ".cc", ".cpp", ".ll", ".m", ".mm"}
        )
        candidates.extend(
            path
            for path in root.glob("crash-*.cpp")
            if path.is_file()
        )
    return sorted(candidates)[0] if candidates else None


def find_crash_artifact(issue_dir: Path, first_bad: str) -> Path | None:
    error_files = sorted(issue_dir.rglob("*.err"))
    exact = [path for path in error_files if path.stem.startswith(first_bad[:12])]
    for path in exact + error_files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if "Assertion" in text or "fatal error:" in text or "Broken function found" in text:
            return path
    return None


def find_verdict_artifact(source_root: Path, issue: str) -> Path | None:
    raw_root = source_root / "web-presentation-data" / "scoped10" / "raw"
    candidates = sorted(raw_root.glob(f"*/*{issue}*topk3-fresh*.json"))
    return candidates[0] if candidates else None


def copy_artifact(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination.name


def make_case_metadata(
    llvm_dir: Path,
    source_root: Path,
    issue: str,
    profile: dict[str, Any],
    *,
    first_bad: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    first_bad = first_bad or CANONICAL_FIRST_BAD.get(issue, profile["bad_commit"])
    interpretation = CAUSAL_INTERPRETATIONS.get(issue)
    static_analysis = MANUAL_STATIC_ANALYSIS.get(
        issue,
        {
            "dependency_chain": "No scoped static dependency hypothesis is available for this fixture case.",
            "inspection_focus": "Compare the first-bad patch against the reproducer and retained failure evidence.",
        },
    )
    try:
        parent = canonical_parent(llvm_dir, first_bad)
        subject, changed_files, diff_text = commit_details(llvm_dir, first_bad)
        patch = git_output(llvm_dir, "show", "--no-renames", "--format=fuller", "--unified=20", first_bad)
        compact_patch = compact_diff_evidence(diff_text, limit=2400)
        missing_commit = False
    except subprocess.CalledProcessError as exc:
        parent = ""
        subject = f"<missing commit {first_bad}>"
        changed_files = []
        patch = (exc.stderr or str(exc)).strip() + "\n"
        compact_patch = "<missing first-bad commit in llvm clone>"
        missing_commit = True
    relevance = (
        RelevanceAssessment("unrelated", [], [])
        if missing_commit
        else assess_relevance(profile, subject, changed_files, diff_text)
    )
    crash_evidence = stored_trace_evidence(issue, first_bad)
    metadata = {
        "issue": issue,
        "title": profile["title"],
        "issue_url": profile.get("issue_url", ""),
        "interval": {"good": profile["good_commit"], "bad_endpoint": profile["bad_commit"]},
        "validated_first_bad": first_bad,
        "validated_first_bad_parent": parent,
        "first_bad_subject": subject,
        "changed_files": changed_files,
        "issue_summary": profile["bug_report_summary"],
        "keywords": profile.get("keywords", []),
        "relevant_paths": profile.get("relevant_paths", []),
        "high_risk_paths": profile.get("high_risk_paths", []),
        "runner": profile.get("runner", ""),
        "crash_evidence": {
            "kind": crash_evidence.source,
            "excerpt": crash_evidence.text,
            "source_artifact": crash_evidence.artifact,
        },
        "static_overlap": {
            "classification": relevance.classification,
            "path_overlap": relevance.path_overlap,
            "keyword_overlap": relevance.keyword_overlap,
        },
        "causal_role": interpretation.role if interpretation else "unclassified",
        "causal_interpretation": interpretation.explanation if interpretation else "Fixture case without a scoped causal classification.",
        "manual_static_analysis": static_analysis,
        "manual_review_limit": (
            "The first-bad SHA and patch are deliberately answer-leaking aids for manual review. "
            "They are not valid runtime inputs or benchmark results."
        ),
    }
    if missing_commit:
        metadata["missing_first_bad_commit"] = True
    return metadata, patch, compact_patch


def render_review_report(cases: list[dict[str, Any]]) -> str:
    lines = [
        "# Scoped-Ten Manual First-Bad Review Guide",
        "",
        "This guide is for a manual, answer-leaking inspection of the already validated first-bad boundaries. It is not an upper-bound benchmark method: it reveals the known boundary patch, then asks the reviewer to verify the causal chain with source and a runner-backed build.",
        "",
        "## Review Protocol",
        "",
        "1. Read `first-bad.patch` and compare it with the crash/assertion artifact and reproducer.",
        "2. Follow the stated dependency chain through the changed producer and downstream detector; do not require the assertion file itself to be modified.",
        "3. Build and run the known first-bad commit and its parent. Record any mismatch as an environment or representation issue, not a causal conclusion.",
        "4. Treat the listed first-bad SHA as an answer-leaking review anchor only. Do not use it, patch lines, or derived terms in deployable LM-bisect scoring.",
        "",
        "## Case Index",
        "",
        "| Issue | Validated first bad | Role | Crash evidence | Manual priority |",
        "|---|---|---|---|---|",
    ]
    for case in cases:
        source = case["crash_evidence"]["kind"]
        priority = "trace reconstruction" if source in {"wrapper-only", "missing"} else "patch-to-trace confirmation"
        lines.append(
            f"| `{case['issue']}` | `{case['validated_first_bad'][:12]}` | {case['causal_role']} | {source} | {priority} |"
        )
    for case in cases:
        static = case["manual_static_analysis"]
        lines.extend(
            [
                "",
                f"## {case['issue']}: {case['title']}",
                "",
                f"- Known first bad: `{case['validated_first_bad']}` ({case['first_bad_subject']})",
                f"- Immediate parent to prove good: `{case['validated_first_bad_parent']}`",
                f"- Crash/assertion: {case['crash_evidence']['excerpt']}",
                f"- Source evidence status: `{case['crash_evidence']['kind']}`",
                f"- Static role: `{case['causal_role']}`. {case['causal_interpretation']}",
                f"- Dependency chain: {static['dependency_chain']}",
                f"- What to inspect: {static['inspection_focus']}",
                f"- Changed files: {', '.join(case['changed_files'])}",
                f"- Static overlap: `{case['static_overlap']['classification']}`; paths: {', '.join(case['static_overlap']['path_overlap']) or 'none'}.",
                "- Candidate to inspect first: the known first-bad patch in this bundle. This is intentionally answer-leaking and must remain outside any fair method comparison.",
            ]
        )
    return "\n".join(lines) + "\n"


def build_bundle(
    *,
    llvm_dir: Path,
    source_root: Path,
    profiles: dict[str, dict[str, Any]],
    issues: tuple[str, ...] = SCOPED_ISSUES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    label: str = "20260807",
    first_bad_by_issue: dict[str, str] | None = None,
    bundle_slug: str = "scoped10-evidence",
    archive_slug: str = "scoped10-first-bad-evidence",
    bundle_id: str = "scoped-ten-first-bad-evidence",
) -> BundleResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_root = output_dir / f"{bundle_slug}-{label}"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir()
    cases: list[dict[str, Any]] = []
    for issue in issues:
        profile = profiles[issue]
        metadata, patch, compact_patch = make_case_metadata(
            llvm_dir,
            source_root,
            issue,
            profile,
            first_bad=(first_bad_by_issue or {}).get(issue),
        )
        case_dir = bundle_root / "cases" / issue
        case_dir.mkdir(parents=True)
        (case_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (case_dir / "first-bad.patch").write_text(patch)
        (case_dir / "first-bad-compact.diff").write_text(compact_patch + "\n")
        issue_dir = source_root / "results" / "issues" / issue
        reproducer = find_reproducer(issue_dir, issue=issue, source_root=source_root)
        if reproducer:
            metadata["packaged_reproducer"] = copy_artifact(
                reproducer, case_dir / f"reproducer{sanitized_extension(reproducer)}"
            )
        crash = find_crash_artifact(issue_dir, metadata["validated_first_bad"])
        if crash:
            metadata["packaged_crash_artifact"] = copy_artifact(crash, case_dir / "crash-assertion.err")
            observed = extract_crash_evidence(crash.read_text(errors="replace"))
            metadata["crash_evidence"] = {
                "kind": observed.source,
                "excerpt": observed.text,
                "source_artifact": str(crash.relative_to(source_root)),
            }
        elif verdict := find_verdict_artifact(source_root, issue):
            metadata["packaged_verdict_artifact"] = copy_artifact(verdict, case_dir / "runner-verdict-history.json")
            metadata["crash_evidence"] = {
                "kind": "wrapper-only",
                "excerpt": (
                    "No raw assertion trace was retained. The packaged history preserves runner-backed "
                    "good/bad verdicts and selected-candidate evidence."
                ),
                "source_artifact": str(verdict.relative_to(source_root)),
            }
        (case_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        cases.append(metadata)
    review_report_path = output_dir / f"{bundle_slug}-manual-first-bad-review-{label}.md"
    review_report_path.write_text(render_review_report(cases))
    manifest = {
        "bundle": bundle_id,
        "label": label,
        "case_count": len(cases),
        "scope": list(issues),
        "exclusions": [
            "All LM-bisect caches, full run histories, controller logs, and unrelated report documents are excluded.",
            "The standalone manual review Markdown guide is not embedded in the ZIP.",
        ],
        "cases": cases,
        "files": [
            {
                "path": path.relative_to(bundle_root).as_posix(),
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(bundle_root.rglob("*"))
            if path.is_file()
        ],
    }
    manifest_path = bundle_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    archive_path = output_dir / f"{archive_slug}-{label}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                archive.write(path, f"{bundle_root.name}/{path.relative_to(bundle_root).as_posix()}")
    return BundleResult(archive_path, manifest_path, review_report_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llvm-dir", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT_DIR)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--label", default="20260807")
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text())
    result = build_bundle(
        llvm_dir=args.llvm_dir,
        source_root=args.source_root,
        profiles=profiles,
        output_dir=args.output_dir,
        label=args.label,
    )
    print(result.archive_path)
    print(result.manifest_path)
    print(result.review_report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
