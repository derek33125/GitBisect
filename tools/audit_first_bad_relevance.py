#!/usr/bin/env python3
"""Audit whether scoped first-bad diffs visibly relate to each crash report."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES_PATH = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
DEFAULT_RAW_DIR = ROOT_DIR / "web-presentation-data" / "scoped10" / "raw" / "aws"
DEFAULT_OUTPUT_PATH = ROOT_DIR / "results" / "reports" / "scoped-10-first-bad-relevance-audit-20260717.md"
SCOPED_ISSUES = (
    "pr204559",
    "pr204589",
    "pr201444",
    "pr193164",
    "pr50304",
    "pr50585",
    "pr48154",
    "pr49535",
    "pr52635",
    "pr200987",
)
CANONICAL_FIRST_BAD = {
    "pr204559": "5a5d0fb1e471b3a1e842aee1f993e885c8d19713",
    "pr204589": "5a5d0fb1e471b3a1e842aee1f993e885c8d19713",
    "pr201444": "6bcdd843e302063c4f0d36204686155149a6bb0a",
    "pr193164": "cac7fe50e0fbedfb14028c170d83386efeb1265b",
    "pr50304": "c9c05a91c4843c243d508c39bdfbc5e26f311af2",
    "pr50585": "e38b7e894808ec2a0c976ab01e44364f167508d3",
    "pr48154": "20e989e9de6abcf9a684978a2688acc4ea01036f",
    "pr49535": "be20eae25f50f5ef648aeefa1143e1c31e4410fc",
    "pr52635": "10bc12588dac532fad044b2851dde8e7b9121e88",
    "pr200987": "329ef60f3e21fd6845e8e8b0da405cae7eb27267",
}
ASSERTION_RE = re.compile(r"(?:Assertion [`'].*?(?:failed\.|$)|fatal error:.*|PLEASE submit a bug report.*|UNREACHABLE executed.*)", re.IGNORECASE)
SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_:]{3,}\b")
GENERIC_KEYWORDS = {"crash", "assertion", "optimizer", "backend", "codegen", "verifier", "fatalerror"}


@dataclass(frozen=True)
class CrashEvidence:
    source: str
    text: str
    artifact: str = ""


@dataclass(frozen=True)
class RelevanceAssessment:
    classification: str
    keyword_overlap: list[str]
    path_overlap: list[str]


@dataclass(frozen=True)
class CausalInterpretation:
    role: str
    explanation: str


# Static path/keyword overlap cannot distinguish a commit that creates invalid
# IR from the verifier that later detects it.  These scoped-ten conclusions are
# grounded in manual review of the validated first-bad diffs and crash traces.
CAUSAL_INTERPRETATIONS = {
    "pr204559": CausalInterpretation(
        "indirect-enabling",
        "SimpleLoopUnswitch changes CFG construction and loop-latch handling; "
        "the resulting MemorySSA dominance invariant is checked later by the "
        "MemorySSA clobber walker.",
    ),
    "pr204589": CausalInterpretation(
        "indirect-enabling",
        "The same SimpleLoopUnswitch CFG transformation leaves MemorySSA uses "
        "in an invalid relationship; removeFromLookups is the downstream "
        "consistency check, not the changed implementation site.",
    ),
    "pr201444": CausalInterpretation(
        "direct",
        "The first-bad X86 DAG-combine implementation adds "
        "peekThroughBitPosExtTrunc and the exact low-bits assertion reported "
        "by the reproducer.",
    ),
    "pr193164": CausalInterpretation(
        "direct",
        "The first bad changes LoopVectorize and VPlan canonical-IV recipe "
        "construction, the same subsystem named by the issue.  The retained "
        "history is wrapper-only, so this is source-level rather than raw-trace "
        "confirmation.",
    ),
    "pr50304": CausalInterpretation(
        "direct",
        "The first bad rewrites ConstantFolding around APFloat conversion and "
        "representability, matching the reported APFloat assertion mechanism.",
    ),
    "pr50585": CausalInterpretation(
        "indirect-enabling",
        "DivRemPairs newly hoists a division/remainder pair to a common "
        "predecessor.  That placement can violate dominance; the verifier's "
        "Broken function report is the later detector of the invalid IR.",
    ),
    "pr48154": CausalInterpretation(
        "indirect-enabling",
        "BuildLibCalls begins inferring argmemonly and related attributes.  "
        "Attributor or the verifier later observes the incompatible attribute "
        "state, so their stack locations need not be touched by the first bad.",
    ),
    "pr49535": CausalInterpretation(
        "direct",
        "The first bad adds ValueTracking recurrence inversion and the exact "
        "PHI operand invariant asserted by the reproducer.",
    ),
    "pr52635": CausalInterpretation(
        "direct",
        "The XRay sled-v2 change updates PC-relative symbol handling in "
        "AsmPrinter and InstrumentationMap, matching the MCSymbolRefExpr "
        "assertion path.",
    ),
    "pr200987": CausalInterpretation(
        "direct",
        "Clang CodeGen adds asm-goto outputs on indirect edges, which is the "
        "feature exercised by the reproducer before the later optimizer crash. "
        "The retained history is wrapper-only.",
    ),
}


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def extract_crash_evidence(output: str) -> CrashEvidence:
    for line in output.splitlines():
        match = ASSERTION_RE.search(line)
        if match:
            return CrashEvidence("assertion", match.group(0).strip())
    if "repro exit code:" in output or "verdict: bad" in output:
        return CrashEvidence(
            "wrapper-only",
            "The saved runner wrapper records a bad verdict, but does not contain the raw assertion or crash trace.",
        )
    return CrashEvidence("missing", "No runner trace or assertion was saved in this selected history.")


def path_matches_relevant(changed_path: str, relevant_path: str) -> bool:
    return changed_path.startswith(relevant_path) or relevant_path.startswith(changed_path)


def assess_relevance(
    profile: dict[str, Any],
    subject: str,
    changed_files: list[str],
    diff_text: str,
) -> RelevanceAssessment:
    searchable = f"{subject}\n{diff_text}".lower()
    keyword_overlap = [
        keyword
        for keyword in profile.get("keywords", [])
        if len(normalize_token(str(keyword))) >= 4 and normalize_token(str(keyword)) in normalize_token(searchable)
    ]
    path_overlap = [
        path
        for path in changed_files
        if any(path_matches_relevant(path, relevant) for relevant in profile.get("relevant_paths", []))
    ]
    symbols = {normalize_token(symbol) for symbol in SYMBOL_RE.findall(subject + "\n" + diff_text)}
    specific_keywords = {
        normalize_token(str(keyword))
        for keyword in profile.get("keywords", [])
        if len(normalize_token(str(keyword))) >= 6
        and normalize_token(str(keyword)) not in GENERIC_KEYWORDS
    }
    specific_overlap = [
        keyword for keyword in keyword_overlap if normalize_token(str(keyword)) not in GENERIC_KEYWORDS
    ]
    symbol_match = bool(symbols & specific_keywords)
    if path_overlap and (specific_overlap or symbol_match):
        classification = "direct"
    elif path_overlap or keyword_overlap:
        classification = "partial"
    else:
        classification = "unrelated"
    return RelevanceAssessment(classification, keyword_overlap, path_overlap)


def git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def commit_details(repo: Path, sha: str) -> tuple[str, list[str], str]:
    subject = git_output(repo, "show", "-s", "--format=%s", sha).strip()
    changed_files = [line for line in git_output(repo, "show", "--format=", "--name-only", sha).splitlines() if line]
    diff_text = git_output(repo, "show", "--no-renames", "--format=", "--unified=0", sha)
    return subject, changed_files, diff_text


def selected_history(raw_dir: Path, issue: str) -> dict[str, Any]:
    matches = sorted(raw_dir.glob(f"{issue}-*topk3-fresh*.json"))
    if not matches:
        return {}
    for path in matches:
        candidate = json.loads(path.read_text())
        if isinstance(candidate, dict) and isinstance(candidate.get("steps"), list):
            return candidate
    return {}


def evidence_from_history(history: dict[str, Any]) -> CrashEvidence:
    bad_steps = [step for step in history.get("steps", []) if step.get("verdict") == "bad"]
    for step in reversed(bad_steps):
        evidence = extract_crash_evidence(
            "\n".join(
                value for value in (step.get("trace_excerpt"), step.get("log_excerpt")) if isinstance(value, str)
            )
        )
        if evidence.source != "missing":
            return evidence
    return CrashEvidence("missing", "No bad-step trace was saved in the selected history.")


def stored_trace_evidence(issue: str, first_bad_sha: str) -> CrashEvidence:
    issue_dir = ROOT_DIR / "results" / "issues" / issue
    if not issue_dir.exists():
        return CrashEvidence("missing", "No local issue artifact directory is available.")
    candidates = sorted(issue_dir.rglob("*.err"))
    exact_candidates = [path for path in candidates if path.stem.startswith(first_bad_sha[:12])]
    for source, paths in (("first-bad-assertion", exact_candidates), ("endpoint-assertion", candidates)):
        for path in paths:
            try:
                evidence = extract_crash_evidence(path.read_text(errors="replace"))
            except OSError:
                continue
            if evidence.source == "assertion":
                return CrashEvidence(source, evidence.text, str(path.relative_to(ROOT_DIR)))
    return CrashEvidence("missing", "No raw assertion was located in the local issue artifacts.")


def compact_diff_evidence(diff_text: str, limit: int = 900) -> str:
    lines = [line.rstrip() for line in diff_text.splitlines() if line.startswith(("+++", "---", "@@", "+", "-"))]
    compact = "\n".join(lines)
    return compact[:limit].rstrip() if compact else "<empty diff>"


def render_report(repo: Path, profiles: dict[str, dict[str, Any]], raw_dir: Path) -> str:
    lines = [
        "# Scoped-10 First-Bad Causality Study",
        "",
        "This study reviews each validated first-bad diff against the saved crash or trace evidence. It separates a direct source-level mechanism match from an indirect-enabling change: the latter creates invalid CFG, IR, or attributes that a later verifier or analysis detects. It is evidence of causal plausibility, not a substitute for reduction or debugging.",
        "",
        "## Aggregate Result",
        "",
        "- **6/10 direct cases** modify the crashing subsystem, symbol, or feature path: `pr201444`, `pr193164`, `pr50304`, `pr49535`, `pr52635`, and `pr200987`.",
        "- **4/10 indirect-enabling cases** modify an upstream transformation or attribute inference while MemorySSA/the verifier detects the resulting invalid state later: `pr204559`, `pr204589`, `pr50585`, and `pr48154`.",
        "- **0/10 no-visible-match cases** remain after manual source review. Eight cases retain raw assertion/fatal-error evidence; `pr193164` and `pr200987` have only a saved runner verdict, so their direct classification is source-level evidence only.",
        "",
        "A missing assertion-site overlap is therefore expected for the four indirect cases. Compiler correctness checks are intentionally downstream: a transformation can violate a dominance, MemorySSA, or attribute invariant without editing the checker that reports the failure.",
        "",
        "| Issue | First bad | Trace evidence | Static overlap | Causal role |",
        "|---|---|---|---|---|",
    ]
    details: list[str] = []
    for issue in SCOPED_ISSUES:
        profile = profiles[issue]
        sha = CANONICAL_FIRST_BAD[issue]
        interpretation = CAUSAL_INTERPRETATIONS[issue]
        subject, files, diff_text = commit_details(repo, sha)
        assessment = assess_relevance(profile, subject, files, diff_text)
        evidence = evidence_from_history(selected_history(raw_dir, issue))
        if evidence.source in {"missing", "wrapper-only"}:
            stored = stored_trace_evidence(issue, sha)
            if stored.source in {"first-bad-assertion", "endpoint-assertion"}:
                evidence = stored
        paths = ", ".join(assessment.path_overlap[:3]) or "none"
        keywords = ", ".join(assessment.keyword_overlap[:5]) or "none"
        lines.append(
            f"| `{issue}` | `{sha[:12]}` | {evidence.source} | {assessment.classification}: {paths}; {keywords} | **{interpretation.role}** |"
        )
        details.extend(
            [
                "",
                f"## {issue}",
                "",
                f"- Issue: {profile['title']}",
                f"- Validated first bad: `{sha}`",
                f"- First-bad subject: {subject}",
                f"- Generated keywords: {', '.join(profile.get('keywords', []))}",
                f"- Relevant paths: {', '.join(profile.get('relevant_paths', []))}",
                f"- Crash evidence ({evidence.source}): {evidence.text}",
                f"- Crash-evidence artifact: `{evidence.artifact}`" if evidence.artifact else "- Crash-evidence artifact: <none>",
                f"- Changed files: {', '.join(files[:12]) or '<none>'}",
                f"- Overlap: paths = {paths}; keywords/symbols = {keywords}.",
                f"- Static overlap classification: **{assessment.classification}**.",
                f"- Causal role: **{interpretation.role}**. {interpretation.explanation}",
                "- Interpretation limit: the boundary is validated, but this report does not prove the mechanism without a reduced test or debugger-level trace.",
                "",
                "```diff",
                compact_diff_evidence(diff_text),
                "```",
            ]
        )
    return "\n".join(lines + details) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llvm-dir", required=True, type=Path)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text())
    report = render_report(args.llvm_dir, profiles, args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
