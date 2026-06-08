#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PathStep:
    step: int
    sha: str
    verdict: str
    subject: str = ""


@dataclass(frozen=True)
class ParsedBisectLog:
    steps: list[PathStep]
    first_bad_sha: str | None
    first_bad_subject: str


@dataclass(frozen=True)
class IntervalStats:
    total_candidates: int
    good_side_candidates: int
    bad_side_candidates: int
    good_ratio: float
    bad_ratio: float


@dataclass(frozen=True)
class CommitDetails:
    sha: str
    date: str
    subject: str
    files: list[str]


def git_lines(repo: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines()


def short_sha(sha: str | None) -> str:
    return (sha or "")[:12]


def parse_git_bisect_log(text: str) -> ParsedBisectLog:
    steps: list[PathStep] = []
    pending_subject: dict[tuple[str, str], str] = {}
    first_bad_sha: str | None = None
    first_bad_subject = ""

    comment_re = re.compile(r"^# (good|bad|skip): \[([0-9a-f]{7,40})\] ?(.*)$")
    command_re = re.compile(r"^git bisect (good|bad|skip) ([0-9a-f]{7,40})\b")
    first_bad_re = re.compile(r"^# first bad commit: \[([0-9a-f]{7,40})\] ?(.*)$")

    for line in text.splitlines():
        if match := comment_re.match(line):
            verdict, sha, subject = match.groups()
            pending_subject[(verdict, sha)] = subject.strip()
            continue
        if match := command_re.match(line):
            verdict, sha = match.groups()
            subject = pending_subject.get((verdict, sha), "")
            steps.append(PathStep(step=len(steps) + 1, sha=sha, verdict=verdict, subject=subject))
            continue
        if match := first_bad_re.match(line):
            first_bad_sha, first_bad_subject = match.groups()
            first_bad_subject = first_bad_subject.strip()

    return ParsedBisectLog(steps=steps, first_bad_sha=first_bad_sha, first_bad_subject=first_bad_subject)


def compute_interval_stats(
    llvm_dir: Path,
    *,
    good_commit: str,
    bad_commit: str,
    first_bad_commit: str,
) -> IntervalStats:
    candidates = git_lines(llvm_dir, ["rev-list", "--reverse", f"{good_commit}..{bad_commit}"])
    total = len(candidates)
    if total == 0:
        return IntervalStats(0, 0, 0, 0.0, 0.0)

    try:
        boundary_index = candidates.index(first_bad_commit)
    except ValueError:
        matches = [index for index, sha in enumerate(candidates) if sha.startswith(first_bad_commit)]
        boundary_index = matches[0] if matches else total

    good_side = boundary_index
    bad_side = max(total - boundary_index, 0)
    return IntervalStats(
        total_candidates=total,
        good_side_candidates=good_side,
        bad_side_candidates=bad_side,
        good_ratio=good_side / total,
        bad_ratio=bad_side / total,
    )


def get_commit_details(llvm_dir: Path, sha: str) -> CommitDetails:
    lines = git_lines(
        llvm_dir,
        ["show", "--no-renames", "--format=%H%n%ci%n%s", "--name-only", sha],
    )
    full_sha = lines[0] if lines else sha
    date = lines[1] if len(lines) > 1 else ""
    subject = lines[2] if len(lines) > 2 else ""
    files = [line for line in lines[4:] if line.strip()]
    return CommitDetails(sha=full_sha, date=date, subject=subject, files=files)


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def find_lm_histories(results_root: Path, issue_id: str) -> list[Path]:
    runs_dir = results_root / "lm_bisect_runs"
    if not runs_dir.exists():
        return []
    return sorted(runs_dir.glob(f"{issue_id}*.json"))


def select_lm_history(paths: list[Path]) -> tuple[Path | None, dict[str, Any] | None]:
    best_path: Path | None = None
    best_history: dict[str, Any] | None = None
    best_key: tuple[int, int, int] = (-1, -1, -1)
    for path in paths:
        try:
            history = load_json(path)
        except Exception:
            continue
        if not isinstance(history, dict):
            continue
        steps = history.get("steps") or []
        if not isinstance(steps, list):
            continue
        is_completed = 1 if history.get("status") == "completed" else 0
        is_canonical_model = 1 if history.get("model_name") == "gpt-5.4-mini" else 0
        is_model = 1 if history.get("scorer") == "model" else 0
        key = (is_completed, is_canonical_model, is_model, len(steps))
        if key > best_key:
            best_path = path
            best_history = history
            best_key = key
    return best_path, best_history


def parse_lm_steps(history: dict[str, Any] | None) -> list[PathStep]:
    if not history:
        return []
    out: list[PathStep] = []
    for index, raw in enumerate(history.get("steps") or [], start=1):
        if not isinstance(raw, dict):
            continue
        sha = str(raw.get("sha") or raw.get("commit") or "")
        verdict = str(raw.get("verdict") or "")
        subject = str(raw.get("subject") or "")
        if sha and verdict:
            out.append(PathStep(step=int(raw.get("step") or index), sha=sha, verdict=verdict, subject=subject))
    return out


def format_ratio(value: float) -> str:
    return f"{value:.1%}"


def render_path_table(steps: list[PathStep]) -> str:
    if not steps:
        return "_No path artifact found._\n"
    lines = ["| Step | Verdict | Commit | Subject |", "|---:|---|---|---|"]
    for step in steps:
        subject = step.subject.replace("|", "\\|")
        lines.append(f"| {step.step} | `{step.verdict}` | `{short_sha(step.sha)}` | {subject} |")
    return "\n".join(lines) + "\n"


def unique_step_count(steps: list[PathStep]) -> int:
    return len({step.sha for step in steps})


def non_endpoint_step_count(steps: list[PathStep], profile: dict[str, Any]) -> int:
    endpoints = {str(profile.get("good_commit") or ""), str(profile.get("bad_commit") or "")}
    return len({step.sha for step in steps if step.sha not in endpoints})


def render_summary(
    *,
    issue_id: str,
    profile: dict[str, Any],
    stats: IntervalStats | None,
    first_bad: CommitDetails | None,
    git_log_path: Path | None,
    git_path: list[PathStep],
    lm_history_path: Path | None,
    lm_history: dict[str, Any] | None,
    lm_path: list[PathStep],
) -> str:
    lines: list[str] = [
        f"# {issue_id} Summary",
        "",
        "## Issue",
        "",
        f"- Title: {profile.get('title', '')}",
        f"- Issue URL: {profile.get('issue_url', '')}",
        f"- Description: {profile.get('bug_report_summary', '')}",
        f"- Good commit: `{profile.get('good_commit', '')}` ({profile.get('good_ref', 'configured good')})",
        f"- Bad commit: `{profile.get('bad_commit', '')}`",
        "",
        "## Interval",
        "",
    ]
    if stats:
        lines.extend(
            [
                f"- Interval candidates: `{stats.total_candidates}`",
                f"- Good-side candidates before first bad: `{stats.good_side_candidates}` ({format_ratio(stats.good_ratio)})",
                f"- Bad-side candidates from first bad: `{stats.bad_side_candidates}` ({format_ratio(stats.bad_ratio)})",
            ]
        )
    else:
        lines.append("- Interval stats: unavailable")

    lines.extend(["", "## First Bad Commit", ""])
    if first_bad:
        lines.extend(
            [
                f"- Commit: `{first_bad.sha}`",
                f"- Date: {first_bad.date}",
                f"- Subject: {first_bad.subject}",
                "- Changed files:",
            ]
        )
        if first_bad.files:
            lines.extend(f"  - `{path}`" for path in first_bad.files[:30])
            if len(first_bad.files) > 30:
                lines.append(f"  - ... {len(first_bad.files) - 30} more files")
        else:
            lines.append("  - _No changed-file data available._")
        relevant = profile.get("relevant_paths") or []
        hits = [path for path in first_bad.files if any(path.startswith(prefix) for prefix in relevant)] if relevant else []
        lines.append(f"- Relation to configured crash area: {'related' if hits else 'not directly matched by configured paths'}")
    else:
        lines.append("_No first-bad commit found._")

    lines.extend(["", "## Git Bisect Path", ""])
    if git_log_path:
        lines.append(f"- Artifact: `{git_log_path}`")
        lines.append(f"- Git path entries: `{len(git_path)}`")
        lines.append(f"- Git unique commits: `{unique_step_count(git_path)}`")
        lines.append(f"- Git non-endpoint tested commits: `{non_endpoint_step_count(git_path, profile)}`")
        lines.append("")
    lines.append(render_path_table(git_path).rstrip())

    lines.extend(["", "## LM-Bisect Path", ""])
    if lm_history_path:
        lines.append(f"- Artifact: `{lm_history_path}`")
    if lm_history:
        lines.append(f"- Scorer: `{lm_history.get('scorer')}`")
        lines.append(f"- Model: `{lm_history.get('model_name')}`")
        lines.append(f"- Search policy: `{lm_history.get('search_policy')}`")
        lines.append(f"- Status: `{lm_history.get('status')}`")
        lines.append(f"- LM steps: `{len(lm_path)}`")
        lines.append(f"- LM unique tested commits: `{unique_step_count(lm_path)}`")
        lines.append("")
    lines.append(render_path_table(lm_path).rstrip())
    lines.append("")
    return "\n".join(lines)


def generate_summary(
    *,
    issue_id: str,
    profiles_path: Path,
    results_root: Path,
    llvm_dir: Path,
    output_path: Path | None = None,
) -> Path:
    profiles = load_json(profiles_path)
    profile = profiles[issue_id]
    issue_dir = results_root / "issues" / issue_id
    issue_dir.mkdir(parents=True, exist_ok=True)

    configured_log = Path(profile.get("bisect_log", ""))
    git_log_path = first_existing(
        [
            configured_log if configured_log.is_absolute() else Path(configured_log),
            issue_dir / f"{issue_id}-bisect-log.txt",
            issue_dir / f"{issue_id}-email-bisect-log.txt",
        ]
    )
    if git_log_path and not git_log_path.is_absolute():
        git_log_path = Path.cwd() / git_log_path

    parsed_git = ParsedBisectLog([], None, "")
    if git_log_path and git_log_path.exists():
        parsed_git = parse_git_bisect_log(git_log_path.read_text(errors="replace"))

    first_bad_sha = parsed_git.first_bad_sha
    lm_path, lm_history_path, lm_history = [], None, None
    lm_history_path, lm_history = select_lm_history(find_lm_histories(results_root, issue_id))
    lm_path = parse_lm_steps(lm_history)
    if not first_bad_sha and lm_path:
        first_bad_sha = lm_path[-1].sha

    stats = None
    if first_bad_sha:
        stats = compute_interval_stats(
            llvm_dir,
            good_commit=profile["good_commit"],
            bad_commit=profile["bad_commit"],
            first_bad_commit=first_bad_sha,
        )

    first_bad_details = get_commit_details(llvm_dir, first_bad_sha) if first_bad_sha else None
    out_path = output_path or issue_dir / f"{issue_id}-summary.md"
    text = render_summary(
        issue_id=issue_id,
        profile=profile,
        stats=stats,
        first_bad=first_bad_details,
        git_log_path=git_log_path,
        git_path=parsed_git.steps,
        lm_history_path=lm_history_path,
        lm_history=lm_history,
        lm_path=lm_path,
    )
    out_path.write_text(text)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a per-issue Git/LM bisect summary.")
    parser.add_argument("issues", nargs="+", help="issue ids to summarize")
    parser.add_argument("--profiles", type=Path, default=Path("tools/lm_bisect_profiles.json"))
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--llvm-dir", type=Path, default=Path("/home/derek331/research/gitbisect-work/llvm-project"))
    args = parser.parse_args()

    for issue_id in args.issues:
        out = generate_summary(
            issue_id=issue_id,
            profiles_path=args.profiles,
            results_root=args.results_root,
            llvm_dir=args.llvm_dir,
        )
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
