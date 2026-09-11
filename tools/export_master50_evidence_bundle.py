#!/usr/bin/env python3
"""Create a 50-issue first-bad evidence pack matching the scoped-10 raw bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.audit_first_bad_relevance import CANONICAL_FIRST_BAD
from tools.scoped10_evidence_bundle import build_bundle


DEFAULT_PROFILES_PATH = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
DEFAULT_SITE_PATH = ROOT_DIR / "web-presentation-data" / "data" / "site-data.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "human_analysis" / "raw"
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)

# Keep every profile that can actually be packaged as an LLVM crash case.
# skip-* is a control, pr54556 does not reproduce on the known-bad endpoint,
# and pr187875/pr193932 have no completed method and a non-clean git cell.
PROFILE_EXCLUSIONS = frozenset(
    {
        "skip-pr199918-riscv",
        "pr54556",
        "pr187875",
        "pr193932",
        # Retired output-mismatch-only cases replaced by validated crashes.
        "pr176682",
        "pr191581",
    }
)

# These boundaries are verified by the 2026-09-01 clean Git-bisect reruns.
# Keep them independent of the generated site snapshot used by older cases.
MASTER50_VALIDATED_FIRST_BAD = {
    "pr203519": "a460c8e8dafc28fef240fce44dcbed043fe56a71",
    "pr194590": "d19e954b83cb497c03cccb0e9874cb9f1a51b18d",
}


def select_issues(profiles: dict[str, Any]) -> tuple[str, ...]:
    issues = tuple(sorted(issue for issue in profiles if issue not in PROFILE_EXCLUSIONS))
    if len(issues) != 50:
        raise SystemExit(f"expected 50 profile issues after exclusions, got {len(issues)}")
    return issues


def first_bad_map(profiles: dict[str, Any], site_path: Path) -> dict[str, str]:
    rows = json.loads(site_path.read_text(encoding="utf-8"))["master_status"]["rows"]
    by_issue = {row["issue"]: row for row in rows}
    mapping: dict[str, str] = {}
    for issue, profile in profiles.items():
        known_boundary = MASTER50_VALIDATED_FIRST_BAD.get(issue) or CANONICAL_FIRST_BAD.get(issue)
        if known_boundary:
            mapping[issue] = known_boundary
            continue
        row = by_issue.get(issue) or {}
        match = SHA_RE.search(str(row.get("first_bad") or ""))
        mapping[issue] = match.group(0) if match else str(profile["bad_commit"])
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llvm-dir", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT_DIR)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES_PATH)
    parser.add_argument("--site-data", type=Path, default=DEFAULT_SITE_PATH)
    parser.add_argument("--first-bad-map", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--label", default="20260821")
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
    issues = select_issues(profiles)
    first_bads = (
        json.loads(args.first_bad_map.read_text(encoding="utf-8"))
        if args.first_bad_map
        else first_bad_map(profiles, args.site_data)
    )
    result = build_bundle(
        llvm_dir=args.llvm_dir,
        source_root=args.source_root,
        profiles=profiles,
        issues=issues,
        output_dir=args.output_dir,
        label=args.label,
        first_bad_by_issue=first_bads,
        bundle_slug="master50-evidence",
        archive_slug="master50-first-bad-evidence",
        bundle_id="master-fifty-first-bad-evidence",
    )
    print(result.archive_path)
    print(result.manifest_path)
    print(result.review_report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
