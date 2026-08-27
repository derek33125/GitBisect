from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


RELEASE_RE = re.compile(r"^llvmorg-(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class IssueEndpoint:
    issue: str
    bad_version: str
    bad_ref: str
    status: str = "pending"


@dataclass(frozen=True)
class ReleasePlan:
    release: str
    eligible_issues: list[IssueEndpoint]


@dataclass(frozen=True)
class ValidationRow:
    issue: str
    release: str
    verdict: str
    note: str


@dataclass(frozen=True)
class PoolUpdate:
    remaining: list[str]
    validated_good: list[str]
    still_bad: list[str]
    skipped: list[str]
    all_skipped: bool


def release_key(tag: str) -> tuple[int, int, int]:
    match = RELEASE_RE.match(tag)
    if not match:
        raise ValueError(f"not an llvm release tag: {tag}")
    return tuple(int(part) for part in match.groups())


def is_release_older_than_bad(release: str, bad_version: str) -> bool:
    return release_key(release) < release_key(bad_version)


def select_common_release(
    issues: Iterable[IssueEndpoint],
    releases: Iterable[str],
    *,
    min_eligible: int = 1,
    strategy: str = "newest",
) -> ReleasePlan | None:
    if strategy not in {"newest", "coverage"}:
        raise ValueError(f"unknown selection strategy: {strategy}")
    issue_list = [issue for issue in issues if issue.status not in {"good", "done", "dropped"}]
    if strategy == "coverage":
        best: ReleasePlan | None = None
        for release in sorted(releases, key=release_key, reverse=True):
            eligible = [
                issue
                for issue in issue_list
                if is_release_older_than_bad(release, issue.bad_version)
            ]
            if len(eligible) < min_eligible:
                continue
            if best is None or len(eligible) > len(best.eligible_issues):
                best = ReleasePlan(release=release, eligible_issues=eligible)
        return best

    for release in sorted(releases, key=release_key, reverse=True):
        eligible = [
            issue
            for issue in issue_list
            if is_release_older_than_bad(release, issue.bad_version)
        ]
        if len(eligible) >= min_eligible:
            return ReleasePlan(release=release, eligible_issues=eligible)
    return None


def update_pool(pool: Iterable[str], rows: Iterable[ValidationRow]) -> PoolUpdate:
    pool_list = list(pool)
    row_by_issue = {row.issue: row for row in rows}
    validated_good: list[str] = []
    still_bad: list[str] = []
    skipped: list[str] = []
    remaining: list[str] = []

    for issue in pool_list:
        row = row_by_issue.get(issue)
        if row is None:
            remaining.append(issue)
            continue
        if row.verdict == "good":
            validated_good.append(issue)
        elif row.verdict == "bad":
            still_bad.append(issue)
            remaining.append(issue)
        else:
            skipped.append(issue)
            remaining.append(issue)

    tested_count = len(validated_good) + len(still_bad) + len(skipped)
    all_skipped = tested_count > 0 and len(skipped) == tested_count
    return PoolUpdate(
        remaining=remaining,
        validated_good=validated_good,
        still_bad=still_bad,
        skipped=skipped,
        all_skipped=all_skipped,
    )


def parse_validation_tsv(text: str) -> list[ValidationRow]:
    rows: list[ValidationRow] = []
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    for raw in reader:
        issue = (raw.get("issue") or "").strip()
        release = (raw.get("release") or "").strip()
        verdict = (raw.get("verdict") or "").strip()
        note = (raw.get("note") or "").strip()
        if not issue:
            continue
        rows.append(ValidationRow(issue=issue, release=release, verdict=verdict, note=note))
    return rows


def load_issue_endpoints(path: Path) -> list[IssueEndpoint]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError(f"expected JSON list in {path}")
    issues: list[IssueEndpoint] = []
    for raw in payload:
        issues.append(
            IssueEndpoint(
                issue=str(raw["issue"]),
                bad_version=str(raw["bad_version"]),
                bad_ref=str(raw["bad_ref"]),
                status=str(raw.get("status", "pending")),
            )
        )
    return issues


def write_pool_update(path: Path, update: PoolUpdate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(update), indent=2, sort_keys=True) + "\n")
