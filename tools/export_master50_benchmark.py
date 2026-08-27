"""Export a 50-issue JSON snapshot from the web master-status ledger.

Writes benchmark-results/master50/ in the same JSON-only layout as
benchmark-results/scoped10/, using the master-report method columns
(git, legacy LM, issue-specific heuristic, weak-general heuristic, BCR).
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.export_master50_evidence_bundle import PROFILE_EXCLUSIONS, select_issues

SITE = REPO / "web-presentation-data" / "data" / "site-data.json"
OUT = REPO / "benchmark-results" / "master50"

METHODS = [
    ("git", "git-bisect", "Git bisect"),
    ("legacy_lm", "old-model-guided", "Legacy LM-bisect"),
    ("tuned_heuristic", "issue-specific-heuristic", "Issue-specific heuristic"),
    ("weak_general_heuristic", "weak-general-heuristic", "Weak-general heuristic"),
    ("bcr", "bcr-topk12", "BCR top-k12"),
]


def sha_cell(value: object) -> str:
    text = str(value or "")
    for token in text.replace("(", " ").replace(")", " ").split():
        if len(token) >= 12 and all(c in "0123456789abcdefABCDEF" for c in token[:12]):
            return token
    return ""


def completed(row: dict, key: str) -> bool:
    return (row.get(key) or {}).get("state") == "completed"


def valid_count(row: dict) -> int:
    return sum(1 for key, _, _ in METHODS if completed(row, key))


def select_cohort(rows: list[dict], profiles: dict) -> list[dict]:
    """50 profiled GitHub issues, matching the human_analysis/raw evidence pack.

    Drops skip-* controls, the non-reproducing pr54556 endpoint, and the two
    profiled PRs with no completed method and a non-clean git cell.
    """
    wanted = select_issues(profiles)
    by_issue = {row["issue"]: row for row in rows}
    missing = [issue for issue in wanted if issue not in by_issue]
    if missing:
        raise SystemExit("master ledger is missing profiled issues: " + ", ".join(missing))
    return [by_issue[issue] for issue in wanted]


def status_for(method: dict, *, git: bool) -> str:
    state = (method or {}).get("state") or "not_run"
    if state == "completed":
        return "baseline" if git else "clean"
    if state == "not_run":
        return "missing"
    if state == "running":
        return "in_progress"
    if state == "stopped":
        return "stopped"
    return state


def mean_median(values: list[int]) -> tuple[float | None, int | None]:
    if not values:
        return None, None
    avg = round(sum(values) / len(values), 2)
    med = int(statistics.median(values)) if len(values) % 2 else statistics.median(values)
    if isinstance(med, float) and med.is_integer():
        med = int(med)
    return avg, med


def main() -> None:
    site = json.loads(SITE.read_text(encoding="utf-8"))
    master = site["master_status"]
    profiles = json.loads((REPO / "tools" / "lm_bisect_profiles.json").read_text(encoding="utf-8"))
    cohort = select_cohort(master["rows"], profiles)
    if len(cohort) != 50:
        raise SystemExit(f"expected 50 issues, got {len(cohort)}")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    issues_order = [row["issue"] for row in cohort]

    issues_json = []
    preferred = []
    master_rows = []
    for row in cohort:
        git = row.get("git") or {}
        legacy = row.get("legacy_lm") or {}
        issues_json.append(
            {
                "issue": row["issue"],
                "interval_commits": row.get("interval_commits"),
                "interval_source": row.get("interval_source"),
                "git_steps": git.get("steps"),
                "git_skips": git.get("skips"),
                "git_status": git.get("state"),
                "old_model_steps": legacy.get("steps"),
                "old_model_status": legacy.get("state"),
                "tuned_heuristic_steps": (row.get("tuned_heuristic") or {}).get("steps"),
                "tuned_heuristic_status": (row.get("tuned_heuristic") or {}).get("state"),
                "weak_general_steps": (row.get("weak_general_heuristic") or {}).get("steps"),
                "weak_general_status": (row.get("weak_general_heuristic") or {}).get("state"),
                "bcr_steps": (row.get("bcr") or {}).get("steps"),
                "bcr_status": (row.get("bcr") or {}).get("state"),
                "first_bad": sha_cell(row.get("first_bad")),
            }
        )
        pref: dict[str, object] = {"issue": row["issue"]}
        for key, export_key, _label in METHODS:
            method = row.get(key) or {}
            pref[f"{export_key}_steps"] = method.get("steps")
            pref[f"{export_key}_skips"] = method.get("skips")
            pref[f"{export_key}_status"] = status_for(method, git=(key == "git"))
            pref[f"{export_key}_first_bad"] = sha_cell(row.get("first_bad")) if method.get("state") == "completed" else ""
            pref[f"{export_key}_source"] = "master-status-report"
            pref[f"{export_key}_run_label"] = ""
        pref["good_anchor"] = sha_cell(row.get("good_anchor"))
        pref["bad_anchor"] = sha_cell(row.get("bad_anchor"))
        pref["interval_commits"] = row.get("interval_commits")
        pref["notes"] = row.get("notes") or ""
        preferred.append(pref)
        master_rows.append(
            {
                "issue": row["issue"],
                "state": row.get("state"),
                "interval_commits": row.get("interval_commits"),
                "interval_source": row.get("interval_source"),
                "good_anchor": sha_cell(row.get("good_anchor")),
                "bad_anchor": sha_cell(row.get("bad_anchor")),
                "first_bad": sha_cell(row.get("first_bad")),
                "local_git": row.get("local_git"),
                "local_lm": row.get("local_lm"),
                "remote_git": row.get("remote_git"),
                "remote_lm": row.get("remote_lm"),
                "git": row.get("git"),
                "legacy_lm": row.get("legacy_lm"),
                "tuned_heuristic": row.get("tuned_heuristic"),
                "weak_general_heuristic": row.get("weak_general_heuristic"),
                "bcr": row.get("bcr"),
                "notes": row.get("notes"),
            }
        )

    method_summary = []
    for key, export_key, label in METHODS:
        completed_rows = [row for row in cohort if completed(row, key)]
        steps = [int((row.get(key) or {}).get("steps")) for row in completed_rows if (row.get(key) or {}).get("steps") is not None]
        skipcap = sum(1 for row in completed_rows if ((row.get(key) or {}).get("skips") or 0) > 0)
        avg, med = mean_median(steps)
        method_summary.append(
            {
                "method": export_key,
                "label": label,
                "rows": 50,
                "clean_or_baseline_rows": len(completed_rows),
                "avg_steps": avg,
                "median_steps": med,
                "skipcap_rows": skipcap,
                "partial_rows": sum(1 for row in cohort if (row.get(key) or {}).get("state") in {"running", "stopped"}),
                "missing_rows": sum(1 for row in cohort if (row.get(key) or {}).get("state") == "not_run"),
                "non_clean_rows": sum(1 for row in cohort if (row.get(key) or {}).get("state") == "non_clean"),
                "valid_for_comparison": len(completed_rows) > 0,
            }
        )

    bcr_clean = [
        {
            "issue": row["issue"],
            "steps": (row.get("bcr") or {}).get("steps"),
            "skips": (row.get("bcr") or {}).get("skips"),
            "first_bad": sha_cell(row.get("first_bad")),
            "source": row.get("remote_lm") or "master-status-report",
        }
        for row in cohort
        if completed(row, "bcr")
    ]
    weak_clean = [
        {
            "issue": row["issue"],
            "steps": (row.get("weak_general_heuristic") or {}).get("steps"),
            "skips": (row.get("weak_general_heuristic") or {}).get("skips"),
            "first_bad": sha_cell(row.get("first_bad")),
        }
        for row in cohort
        if completed(row, "weak_general_heuristic")
    ]

    current_lanes = {
        "generated_at": generated_at,
        "scope": "master-50 from tools/lm_bisect_profiles.json, method cells from the master-status ledger",
        "bcr_topk12": {
            "label": "BCR top-k12 as recorded on the canonical master ledger",
            "result_policy": "Only completed rows are comparison data. missing/non_clean/in_progress rows stay visible in preferred-comparison.json but are excluded from aggregates.",
            "clean": bcr_clean,
            "partial": [
                {
                    "issue": row["issue"],
                    "state": (row.get("bcr") or {}).get("state"),
                    "steps": (row.get("bcr") or {}).get("steps"),
                }
                for row in cohort
                if (row.get("bcr") or {}).get("state") in {"running", "stopped"}
            ],
        },
        "weak_general_heuristic": {
            "label": "Weak-general heuristic as recorded on the canonical master ledger",
            "clean_completed": weak_clean,
            "note": f"{len(weak_clean)} completed weak-general rows in this 50-issue cohort.",
        },
        "active": [],
    }

    git_n = sum(1 for row in cohort if completed(row, "git"))
    fill = [row["issue"] for row in cohort if not completed(row, "git")]
    manifest = {
        "scope": "master-50",
        "issue_count": 50,
        "issues": issues_order,
        "source": "tools/lm_bisect_profiles.json plus web-presentation-data/data/site-data.json master_status",
        "site_generated_at": site.get("generated_at"),
        "bundle_generated_at": generated_at,
        "selection": {
            "ledger_rows": master["summary"]["total_issues"],
            "profile_rows": len(profiles),
            "policy": (
                "Take every issue in tools/lm_bisect_profiles.json except skip-* controls, "
                "the non-reproducing pr54556 endpoint, and pr187875/pr193932 (no completed method). "
                "This matches the human_analysis/raw 50-issue first-bad evidence pack."
            ),
            "git_completed_in_cohort": git_n,
            "fill_issues_without_completed_git": fill,
            "excluded": sorted(PROFILE_EXCLUSIONS),
        },
        "contents": [
            "issues.json",
            "preferred-comparison.json",
            "method-summary.json",
            "current-lanes.json",
            "master-rows.json",
        ],
        "exclusions": [
            "reports and progress documents",
            "raw build trees and compiler artifacts",
            "observation caches and model caches",
            "private server state and credentials",
            "scoped-10-only k12/keyword-ablation matrices (not defined on this 50)",
        ],
        "notes": [
            "This JSON-only bundle is a frozen presentation snapshot, not a runnable benchmark input.",
            "Method columns match the web master-status table, not the scoped-10 parent-llm/lastdiff matrix.",
            "Status and validity fields determine whether a row contributes to an aggregate.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    writers = {
        "manifest.json": manifest,
        "issues.json": issues_json,
        "preferred-comparison.json": preferred,
        "method-summary.json": method_summary,
        "current-lanes.json": current_lanes,
        "master-rows.json": {
            "source": master["source"],
            "columns": master["columns"],
            "issue_count": 50,
            "rows": master_rows,
        },
    }
    for name, payload in writers.items():
        (OUT / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "issues": 50, "git_completed": git_n, "bcr_completed": len(bcr_clean)}, indent=2))


if __name__ == "__main__":
    main()
