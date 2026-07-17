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
        "# Scoped-10 First-Bad Relevance Audit",
        "",
        "This audit checks whether the validated first-bad commit visibly matches the issue crash vocabulary and relevant paths. It is static evidence, not proof of causality. `wrapper-only` means the selected JSON saved the runner verdict but not the compiler's raw assertion text.",
        "",
        "| Issue | First bad | Trace evidence | File/path overlap | Keyword/symbol overlap | Classification |",
        "|---|---|---|---|---|---|",
    ]
    details: list[str] = []
    for issue in SCOPED_ISSUES:
        profile = profiles[issue]
        sha = CANONICAL_FIRST_BAD[issue]
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
            f"| `{issue}` | `{sha[:12]}` | {evidence.source} | {paths} | {keywords} | **{assessment.classification}** |"
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
                f"- Classification: **{assessment.classification}**. Manual review is still required because a direct file/symbol match demonstrates plausibility, not a causal proof.",
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
