#!/usr/bin/env python3
"""Measure last-tested -> candidate diff cost for a large LM-bisect frontier."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import selectors
import statistics
import subprocess
import sys
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import lm_bisect  # noqa: E402

NOISY_DIFF_PREFIXES = (
    "#include ",
    "//",
    "/*",
    "*",
)

SIGNAL_PATTERNS = (
    "assert",
    "crash",
    "fatal",
    "error",
    "nullptr",
    "null",
    "cast",
    "isa<",
    "dyn_cast",
    "getAs",
    "create",
    "emit",
    "evaluate",
    "template",
    "sema",
    "codegen",
    "lower",
    "legal",
    "vector",
    "dag",
    "token",
    "ast",
)


def git_limited_output_guarded(
    repo: Path,
    args: list[str],
    *,
    max_chars: int,
    timeout_seconds: float,
    no_lazy_fetch: bool,
) -> tuple[str, str, str]:
    """Run a Git command with bounded output and timeout.

    Returns `(text, status, detail)` where status is one of:
    `ok`, `truncated`, `timeout`, or `error`.
    """
    cmd = ["git", "-C", str(repo), *args]
    env = os.environ.copy()
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    max_bytes = max(1, max_chars * 4 + 1024)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=env,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stderr.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + timeout_seconds
    status = "ok"

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                process.kill()
                break
            events = selector.select(timeout=min(0.25, remaining))
            if not events:
                if process.poll() is not None:
                    break
                continue
            for key, _mask in events:
                try:
                    data = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    if total < max_bytes:
                        keep = data[: max_bytes - total]
                        chunks.append(keep)
                        total += len(keep)
                    if total >= max_bytes and status == "ok":
                        status = "truncated"
                        process.kill()
                else:
                    stderr_chunks.append(data[:8192])
        return_code = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        status = "timeout"
        process.kill()
        return_code = process.wait(timeout=1)
    finally:
        selector.close()

    stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace")[:1200]
    if status == "ok" and return_code:
        status = "error"
    text = b"".join(chunks).decode("utf-8", "replace")[:max_chars]
    return text, status, stderr_text


def percentile(values: list[int], percentile_value: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile_value)
    return ordered[index]


def select_frontier(
    repo: Path,
    profile: lm_bisect.IssueProfile,
    shas: list[str],
    top_k: int,
    heuristic_version: str,
) -> list[dict]:
    metadata = lm_bisect.load_commit_metadata(repo, shas, include_body=False)
    rows: list[dict] = []
    for index, sha in enumerate(shas, start=1):
        item = metadata[sha]
        semantic_score, semantic_evidence = lm_bisect.score_semantics(
            profile,
            item.subject,
            "",
            item.changed_files,
            "",
            heuristic_version=heuristic_version,
        )
        build_success_prob, build_evidence = lm_bisect.score_build_probability(
            item.subject,
            "",
            item.changed_files,
            "",
        )
        rows.append(
            {
                "sha": sha,
                "index": index,
                "subject": item.subject,
                "semantic_score": semantic_score,
                "build_success_prob": build_success_prob,
                "changed_files": item.changed_files,
                "evidence": semantic_evidence + build_evidence,
            }
        )
    rows.sort(key=lambda row: (row["semantic_score"], row["build_success_prob"], -row["index"]), reverse=True)
    return rows[: min(top_k, len(rows))]


def extract_key_diff_summary(
    files: list[str],
    diff: str,
    *,
    max_lines: int = 24,
    max_chars: int = 1200,
) -> str:
    """Extract a compact, issue-agnostic signal summary from a patch.

    This is intentionally rule-based: it lets us estimate whether a cheap
    pre-extraction layer can make top-k=2000 viable before spending another LLM
    pass on every candidate.
    """
    summary_lines: list[str] = []
    if files:
        summary_lines.append("Files:")
        for path in files[:10]:
            summary_lines.append(f"- {path}")
        if len(files) > 10:
            summary_lines.append(f"- ... {len(files) - 10} more files")

    hunk_headers: list[str] = []
    signal_changes: list[str] = []
    fallback_changes: list[str] = []
    added = 0
    removed = 0

    for raw_line in diff.splitlines():
        line = raw_line.rstrip()
        if line.startswith("@@"):
            if len(hunk_headers) < 8:
                hunk_headers.append(line)
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith("+"):
            added += 1
        else:
            removed += 1
        content = line[1:].strip()
        if not content:
            continue
        lowered = content.lower()
        if any(content.startswith(prefix) for prefix in NOISY_DIFF_PREFIXES):
            continue
        compact = line[:220]
        if any(pattern in lowered for pattern in SIGNAL_PATTERNS) or re_like_symbol_change(content):
            if len(signal_changes) < max_lines:
                signal_changes.append(compact)
        elif len(fallback_changes) < max_lines:
            fallback_changes.append(compact)

    summary_lines.append(f"Change counts: +{added} / -{removed}")
    if hunk_headers:
        summary_lines.append("Hunks:")
        summary_lines.extend(f"- {header[:220]}" for header in hunk_headers)
    selected_changes = signal_changes or fallback_changes
    if selected_changes:
        summary_lines.append("Key changed lines:")
        summary_lines.extend(f"- {line}" for line in selected_changes[:max_lines])
    text = "\n".join(summary_lines)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def re_like_symbol_change(content: str) -> bool:
    if len(content) > 180:
        return False
    return any(
        marker in content
        for marker in (
            "::",
            "->",
            " = ",
            "return ",
            "if ",
            "for ",
            "while ",
            "class ",
            "struct ",
            "enum ",
            "bool ",
            "void ",
            "auto ",
        )
    )


def stats(values: list[int]) -> dict[str, int | float]:
    if not values:
        return {
            "min": 0,
            "median": 0,
            "p95": 0,
            "max": 0,
            "mean": 0.0,
        }
    return {
        "min": min(values),
        "median": int(statistics.median(values)),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": round(statistics.mean(values), 2),
    }


def build_markdown(payload: dict) -> str:
    size_stats = payload["size_stats"]
    prompt_stats = payload["prompt_stats"]
    extracted_stats = payload.get("extracted_prompt_stats")
    return f"""# Transition Diff Top-K Simulation

Generated: {payload["generated_at"]}

## Setup

- Issue: `{payload["issue"]}`
- LLVM repo: `{payload["llvm_dir"]}`
- Interval commits: {payload["interval_commits"]:,}
- Last-tested base: `{payload["last_tested_sha"]}`
- Frontier: top {payload["frontier_count"]:,} by heuristic semantic score
- Diff command: `git diff --no-renames --unified=0 <last-tested> <candidate> -- <candidate files>`
- Diff extraction: bounded to {payload["diff_cap_chars"]:,} chars, matching the LM-bisect scorer
- Candidate file cap: first {payload["file_cap"]} candidate-touched files
- Prompt cap modelled: {payload["prompt_cap_chars"]:,} chars per candidate diff block
- Parallel extraction workers: {payload.get("jobs", 1)}
- Per-candidate diff timeout: {payload.get("diff_timeout_seconds", 0)}s
- Lazy blob fetch disabled: {payload.get("no_lazy_fetch", False)}
- Successful diff measurements: {payload.get("successful_count", payload["frontier_count"]):,} / {payload["frontier_count"]:,}
- Diff status counts: {payload.get("diff_status_counts", {})}

## Timing

- Total wall time for transition diffs: {payload["diff_wall_seconds"]:.2f}s
- Mean wall time per candidate: {payload["mean_diff_seconds"]:.4f}s
- Estimated serial time for 2,000 candidates: {payload["estimated_seconds_for_2000"]:.2f}s

## Bounded Diff Size

| metric | chars |
| --- | ---: |
| min | {size_stats["min"]:,} |
| median | {size_stats["median"]:,} |
| p95 | {size_stats["p95"]:,} |
| max | {size_stats["max"]:,} |
| mean | {size_stats["mean"]:,} |

## Prompt Size After Current 4k Crop

| metric | chars |
| --- | ---: |
| min | {prompt_stats["min"]:,} |
| median | {prompt_stats["median"]:,} |
| p95 | {prompt_stats["p95"]:,} |
| max | {prompt_stats["max"]:,} |
| mean | {prompt_stats["mean"]:,} |

## Prompt Size After Rule-Based Key-Diff Extraction

| metric | chars |
| --- | ---: |
| min | {extracted_stats["min"] if extracted_stats else 0:,} |
| median | {extracted_stats["median"] if extracted_stats else 0:,} |
| p95 | {extracted_stats["p95"] if extracted_stats else 0:,} |
| max | {extracted_stats["max"] if extracted_stats else 0:,} |
| mean | {extracted_stats["mean"] if extracted_stats else 0:,} |

## Token Estimate

- Bounded diff chars / 4 estimate: {payload["raw_token_estimate"]:,} tokens for {payload["frontier_count"]:,} candidates.
- Cropped prompt chars / 4 estimate: {payload["cropped_token_estimate"]:,} tokens for {payload["frontier_count"]:,} candidates.
- Extracted prompt chars / 4 estimate: {payload.get("extracted_token_estimate", 0):,} tokens for {payload["frontier_count"]:,} candidates.
- Candidates exceeding 4k prompt cap: {payload["over_4k_count"]:,} / {payload["frontier_count"]:,}.
- Candidates hitting {payload["diff_cap_chars"]:,} char extraction cap: {payload["at_diff_cap_count"]:,} / {payload["frontier_count"]:,}.

## Largest Diff Examples

| rank | sha | raw chars | prompt chars | files | subject |
| ---: | --- | ---: | ---: | ---: | --- |
"""


def append_examples(markdown: str, examples: list[dict]) -> str:
    rows = []
    for rank, row in enumerate(examples, start=1):
        subject = row["subject"].replace("|", "\\|")
        rows.append(
            f"| {rank} | `{row['sha'][:12]}` | {row['raw_chars']:,} | "
            f"{row['prompt_chars']:,} | {row['file_count']:,} | {subject} |"
        )
    return markdown + "\n".join(rows) + "\n"


def measure_candidate(
    repo: Path,
    row: dict,
    last_tested_sha: str,
    args: argparse.Namespace,
) -> dict:
    diff_start = time.perf_counter()
    files = row["changed_files"]
    scoped_files = [path for path in files if path][: args.file_cap]
    if scoped_files:
        diff, diff_status, diff_detail = git_limited_output_guarded(
            repo,
            ["diff", "--no-renames", "--unified=0", last_tested_sha, row["sha"], "--", *scoped_files],
            max_chars=args.diff_cap_chars,
            timeout_seconds=args.diff_timeout_seconds,
            no_lazy_fetch=args.no_lazy_fetch,
        )
    else:
        diff, diff_status, diff_detail = "", "ok", ""
    diff_seconds = time.perf_counter() - diff_start
    if diff_status in {"timeout", "error"}:
        return {
            **row,
            "raw_chars": 0,
            "prompt_chars": 0,
            "extracted_summary": "",
            "extracted_prompt_chars": 0,
            "file_count": len(files),
            "diff_seconds": diff_seconds,
            "diff_status": diff_status,
            "diff_detail": diff_detail,
        }
    prompt_text = lm_bisect.compact_diff_for_prompt(
        files,
        diff,
        max_chars=args.prompt_cap_chars,
        diff_mode="last-tested",
        base_sha=last_tested_sha,
        candidate_sha=row["sha"],
    )
    extracted_summary = ""
    extracted_prompt = ""
    if args.extract_key_diff:
        extracted_summary = extract_key_diff_summary(
            files,
            diff,
            max_chars=args.key_diff_max_chars,
        )
        extracted_prompt = lm_bisect.compact_diff_for_prompt(
            files,
            extracted_summary,
            max_chars=args.prompt_cap_chars,
            diff_mode="last-tested-key-summary",
            base_sha=last_tested_sha,
            candidate_sha=row["sha"],
        )
    return {
        **row,
        "raw_chars": len(diff),
        "prompt_chars": len(prompt_text),
        "extracted_summary": extracted_summary,
        "extracted_prompt_chars": len(extracted_prompt) if extracted_prompt else 0,
        "file_count": len(files),
        "diff_seconds": diff_seconds,
        "diff_status": diff_status,
        "diff_detail": diff_detail,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", default="pr196244")
    parser.add_argument("--llvm-dir", default="/home/derek331/research/gitbisect-work/llvm-project")
    parser.add_argument("--top-k", type=int, default=2000)
    parser.add_argument("--heuristic-version", choices=("v1", "tuned"), default="tuned")
    parser.add_argument("--last-tested-sha", default=None, help="defaults to midpoint commit in the issue interval")
    parser.add_argument("--prompt-cap-chars", type=int, default=4000)
    parser.add_argument("--diff-cap-chars", type=int, default=12000)
    parser.add_argument("--file-cap", type=int, default=lm_bisect.TRANSITION_DIFF_FILE_LIMIT)
    parser.add_argument("--extract-key-diff", action="store_true", help="also measure rule-based key-diff summaries")
    parser.add_argument("--key-diff-max-chars", type=int, default=1200)
    parser.add_argument("--jobs", type=int, default=1, help="parallel Git diff extraction workers")
    parser.add_argument("--diff-timeout-seconds", type=float, default=20.0, help="per-candidate Git diff timeout")
    parser.add_argument("--no-lazy-fetch", action="store_true", help="set GIT_NO_LAZY_FETCH=1 for diff extraction")
    parser.add_argument("--max-examples", type=int, default=10)
    parser.add_argument("--output-dir", default="results/reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    profile = lm_bisect.load_issue_profile(lm_bisect.load_profiles(), args.issue)
    interval = lm_bisect.list_candidate_commits(repo, profile.good_commit, profile.bad_commit)
    if len(interval) <= 100_000:
        print(f"warning: interval has only {len(interval):,} commits", file=sys.stderr)
    last_tested_sha = args.last_tested_sha or interval[len(interval) // 2]
    frontier = select_frontier(repo, profile, interval, args.top_k, args.heuristic_version)

    start = time.perf_counter()
    if args.jobs <= 1:
        measurements = [measure_candidate(repo, row, last_tested_sha, args) for row in frontier]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            measurements = list(executor.map(lambda row: measure_candidate(repo, row, last_tested_sha, args), frontier))
    diff_wall_seconds = time.perf_counter() - start

    successful_measurements = [row for row in measurements if row.get("diff_status") in {"ok", "truncated"}]
    raw_sizes = [row["raw_chars"] for row in successful_measurements]
    prompt_sizes = [row["prompt_chars"] for row in successful_measurements]
    extracted_prompt_sizes = [row["extracted_prompt_chars"] for row in measurements if row["extracted_prompt_chars"]]
    total_raw_chars = sum(raw_sizes)
    total_prompt_chars = sum(prompt_sizes)
    total_extracted_prompt_chars = sum(extracted_prompt_sizes)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "issue": args.issue,
        "llvm_dir": str(repo),
        "interval_commits": len(interval),
        "last_tested_sha": last_tested_sha,
        "frontier_count": len(frontier),
        "top_k": args.top_k,
        "heuristic_version": args.heuristic_version,
        "prompt_cap_chars": args.prompt_cap_chars,
        "diff_cap_chars": args.diff_cap_chars,
        "file_cap": args.file_cap,
        "diff_wall_seconds": diff_wall_seconds,
        "mean_diff_seconds": diff_wall_seconds / max(len(frontier), 1),
        "estimated_seconds_for_2000": (diff_wall_seconds / max(len(frontier), 1)) * 2000,
        "jobs": args.jobs,
        "diff_timeout_seconds": args.diff_timeout_seconds,
        "no_lazy_fetch": args.no_lazy_fetch,
        "successful_count": len(successful_measurements),
        "diff_status_counts": {
            status: sum(1 for row in measurements if row.get("diff_status") == status)
            for status in sorted({row.get("diff_status", "unknown") for row in measurements})
        },
        "size_stats": stats(raw_sizes),
        "prompt_stats": stats(prompt_sizes),
        "extracted_prompt_stats": stats(extracted_prompt_sizes),
        "raw_token_estimate": total_raw_chars // 4,
        "cropped_token_estimate": total_prompt_chars // 4,
        "extracted_token_estimate": total_extracted_prompt_chars // 4,
        "over_4k_count": sum(1 for value in prompt_sizes if value >= args.prompt_cap_chars),
        "extracted_over_4k_count": sum(1 for value in extracted_prompt_sizes if value >= args.prompt_cap_chars),
        "at_diff_cap_count": sum(1 for value in raw_sizes if value >= args.diff_cap_chars),
        "largest_examples": sorted(measurements, key=lambda row: row["raw_chars"], reverse=True)[: args.max_examples],
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.issue}-transition-diff-topk{args.top_k}-{time.strftime('%Y%m%d-%H%M%S')}"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    md_path.write_text(append_examples(build_markdown(payload), payload["largest_examples"]))
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    print(
        f"interval={len(interval):,} frontier={len(frontier):,} "
        f"diff_wall={diff_wall_seconds:.2f}s cropped_tokens~{payload['cropped_token_estimate']:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
