from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IssueProfile:
    issue_id: str
    title: str
    good_commit: str
    bad_commit: str
    bug_report_summary: str
    keywords: list[str]
    relevant_paths: list[str]
    high_risk_paths: list[str]
    issue_url: str = ""
    runner: str = ""


@dataclass(frozen=True)
class Candidate:
    sha: str
    index: int
    subject: str
    body: str
    changed_files: list[str]
    diff_text: str


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    sha: str
    index: int
    subject: str
    score: float
    generators: list[str]
    evidence: list[str]
    changed_files: list[str]


@dataclass(frozen=True)
class RankResult:
    issue_id: str
    method: str
    interval_size: int
    top_k: int
    ranking: list[RankedCandidate]
    prompt_payload: dict[str, Any]


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    return completed.stdout.decode("utf-8", "replace")


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9_./:+-]+", "", value.lower())


def tokenize(value: str) -> list[str]:
    return [token for token in (normalize_token(part) for part in re.findall(r"[A-Za-z0-9_./:+-]+", value)) if token]


def compact_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def load_profiles(path: Path) -> dict[str, IssueProfile]:
    raw = json.loads(path.read_text())
    profiles: dict[str, IssueProfile] = {}
    for issue_id, item in raw.items():
        profiles[issue_id] = IssueProfile(
            issue_id=str(item.get("issue_id") or issue_id),
            issue_url=str(item.get("issue_url") or ""),
            title=str(item.get("title") or ""),
            good_commit=str(item["good_commit"]),
            bad_commit=str(item["bad_commit"]),
            runner=str(item.get("runner") or ""),
            bug_report_summary=str(item.get("bug_report_summary") or ""),
            keywords=[str(value) for value in item.get("keywords", [])],
            relevant_paths=[str(value) for value in item.get("relevant_paths", [])],
            high_risk_paths=[str(value) for value in item.get("high_risk_paths", [])],
        )
    return profiles


def list_candidate_shas(repo: Path, good_commit: str, bad_commit: str, limit: int | None = None) -> list[str]:
    output = git(repo, "rev-list", "--reverse", f"{good_commit}..{bad_commit}")
    shas = [line.strip() for line in output.splitlines() if line.strip()]
    if not shas:
        raise ValueError(f"no candidate commits in interval {good_commit}..{bad_commit}")
    if limit is not None:
        return shas[:limit]
    return shas


def commit_metadata(repo: Path, sha: str, max_diff_chars: int = 6000) -> Candidate:
    output = git(repo, "show", "--no-renames", "--format=%H%x00%s%x00%b%x00", "--name-only", sha)
    header, _, diff = output.partition("\ndiff --git ")
    fields = header.split("\x00")
    subject = fields[1].strip() if len(fields) > 1 else ""
    body = fields[2].strip() if len(fields) > 2 else ""
    changed_files = [line.strip() for line in fields[-1].splitlines() if line.strip()]
    diff_text = ("diff --git " + diff)[:max_diff_chars] if diff else ""
    return Candidate(sha=sha, index=0, subject=subject, body=body, changed_files=changed_files, diff_text=diff_text)


def load_candidates(repo: Path, shas: list[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for index, sha in enumerate(shas, start=1):
        metadata = commit_metadata(repo, sha)
        candidates.append(
            Candidate(
                sha=metadata.sha,
                index=index,
                subject=metadata.subject,
                body=metadata.body,
                changed_files=metadata.changed_files,
                diff_text=metadata.diff_text,
            )
        )
    return candidates


def issue_terms(profile: IssueProfile) -> set[str]:
    terms = set(tokenize(profile.title))
    terms.update(tokenize(profile.bug_report_summary))
    for keyword in profile.keywords:
        terms.update(tokenize(keyword))
        compact = compact_token(keyword)
        if compact:
            terms.add(compact)
    return {term for term in terms if len(term) >= 3}


def path_matches(path: str, prefixes: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized.startswith(prefix.rstrip("/") + "/") or normalized == prefix.rstrip("/") for prefix in prefixes)


def score_candidate(profile: IssueProfile, candidate: Candidate) -> tuple[float, list[str], list[str]]:
    text = "\n".join([candidate.subject, candidate.body, " ".join(candidate.changed_files), candidate.diff_text])
    text_tokens = set(tokenize(text))
    compact_text = compact_token(text)
    terms = issue_terms(profile)
    evidence: list[str] = []
    generators: set[str] = set()
    score = 0.0

    relevant_hits = [path for path in candidate.changed_files if path_matches(path, profile.relevant_paths)]
    high_risk_hits = [path for path in candidate.changed_files if path_matches(path, profile.high_risk_paths)]
    if relevant_hits:
        score += 18.0 + min(len(relevant_hits), 4) * 2.0
        generators.add("tool_component")
        evidence.append(f"touches relevant paths: {', '.join(relevant_hits[:3])}")
    if high_risk_hits:
        score += 8.0 + min(len(high_risk_hits), 4)
        generators.add("tool_component")
        evidence.append(f"touches high-risk paths: {', '.join(high_risk_hits[:3])}")

    direct_terms = sorted(term for term in terms if term in text_tokens or term in compact_text)
    if direct_terms:
        score += min(len(direct_terms), 12) * 4.0
        generators.add("message_keyword")
        evidence.append(f"matches issue terms: {', '.join(direct_terms[:8])}")

    symbol_like = [
        term
        for term in terms
        if any(char.isupper() for char in term) or "::" in term or "_" in term
    ]
    symbol_hits = sorted(term for term in symbol_like if compact_token(term) and compact_token(term) in compact_text)
    if symbol_hits:
        score += min(len(symbol_hits), 6) * 5.0
        generators.add("trace_symbol")
        evidence.append(f"matches trace/symbol terms: {', '.join(symbol_hits[:6])}")

    subject_lower = candidate.subject.lower()
    if any(word in subject_lower for word in ("crash", "assert", "assertion", "fix", "regression")):
        score += 5.0
        generators.add("message_keyword")
        evidence.append("commit subject uses crash/fix/regression wording")
    if any(path.endswith((".md", ".rst", ".txt")) or "/docs/" in path for path in candidate.changed_files):
        score -= 6.0
        evidence.append("documentation-only style change is lower risk")

    # Mild middle bias avoids ranking only endpoints when scores tie.
    score += 1.0 / (1.0 + math.log(candidate.index + 1.0))
    if not generators:
        generators.add("interval_background")
    return round(score, 6), sorted(generators), evidence


def build_prompt_payload(profile: IssueProfile, ranked: list[RankedCandidate]) -> dict[str, Any]:
    return {
        "style": "no-patch-llmbisect-comparative",
        "system_goal": "Rank candidate commits by likelihood of being the first bad LLVM crash-regression commit.",
        "issue": {
            "id": profile.issue_id,
            "title": profile.title,
            "summary": profile.bug_report_summary,
            "keywords": profile.keywords,
            "relevant_paths": profile.relevant_paths,
            "high_risk_paths": profile.high_risk_paths,
        },
        "instructions": [
            "Use issue evidence instead of a fix patch.",
            "Prefer commits touching exact crash symbols, tool components, passes, targets, or language-feature code.",
            "Penalize broad subsystem matches and documentation-only changes.",
            "Return a top-k static ranking; do not assume any build/test observation.",
        ],
        "candidates": [
            {
                "rank": item.rank,
                "sha": item.sha,
                "subject": item.subject,
                "score": item.score,
                "generators": item.generators,
                "evidence": item.evidence,
                "changed_files": item.changed_files[:20],
            }
            for item in ranked
        ],
    }


def rank_issue(repo: Path, profile: IssueProfile, top_k: int = 10, limit: int | None = None) -> RankResult:
    shas = list_candidate_shas(repo, profile.good_commit, profile.bad_commit, limit=limit)
    candidates = load_candidates(repo, shas)
    scored: list[tuple[float, Candidate, list[str], list[str]]] = []
    for candidate in candidates:
        score, generators, evidence = score_candidate(profile, candidate)
        scored.append((score, candidate, generators, evidence))
    scored.sort(key=lambda item: (-item[0], item[1].index, item[1].sha))

    ranking = [
        RankedCandidate(
            rank=index,
            sha=candidate.sha,
            index=candidate.index,
            subject=candidate.subject,
            score=score,
            generators=generators,
            evidence=evidence,
            changed_files=candidate.changed_files,
        )
        for index, (score, candidate, generators, evidence) in enumerate(scored[:top_k], start=1)
    ]
    return RankResult(
        issue_id=profile.issue_id,
        method="no-patch-llmbisect-style-static-ranker",
        interval_size=len(shas),
        top_k=top_k,
        ranking=ranking,
        prompt_payload=build_prompt_payload(profile, ranking),
    )


def result_to_json(result: RankResult) -> str:
    return json.dumps(asdict(result), indent=2) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="No-patch LLMBisect-style static ranker for LLVM intervals")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rank = subparsers.add_parser("rank", help="rank candidate commits for one issue")
    rank.add_argument("--repo", type=Path, required=True, help="LLVM git repository/worktree")
    rank.add_argument("--profiles", type=Path, default=Path("tools/lm_bisect_profiles.json"))
    rank.add_argument("--issue", required=True)
    rank.add_argument("--top-k", type=int, default=10)
    rank.add_argument("--limit", type=int, default=None, help="optional prefix limit for smoke/debug runs")
    rank.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "rank":
        profiles = load_profiles(args.profiles)
        if args.issue not in profiles:
            raise SystemExit(f"unknown issue profile: {args.issue}")
        result = rank_issue(args.repo, profiles[args.issue], top_k=args.top_k, limit=args.limit)
        payload = result_to_json(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload)
        else:
            print(payload, end="")
        return 0
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

