#!/usr/bin/env python3
"""Collect one canonical CEG and evidence-diverse history per Master-50 issue."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import zipfile


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def classify_history(payload: dict[str, object]) -> str | None:
    if payload.get("search_policy") == "causal-evidence-guided":
        return "ceg"
    if payload.get("model_frontier") == "evidence-diverse":
        return "evidence-diverse"
    return None


def summarize_history(
    path: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    steps = [
        step for step in payload.get("steps", []) if isinstance(step, dict)
    ]
    verdicts = Counter(str(step.get("verdict", "unknown")) for step in steps)
    first_bad = payload.get("first_bad_commit")
    valid = (
        payload.get("status") == "completed"
        and payload.get("remaining_unresolved") == 1
        and isinstance(first_bad, str)
        and len(first_bad) == 40
    )
    return {
        "path": path,
        "filename": path.name,
        "issue": payload.get("issue"),
        "kind": classify_history(payload),
        "valid": valid,
        "status": payload.get("status"),
        "run_label": payload.get("run_label"),
        "interval_commits": payload.get("initial_unresolved"),
        "runner_builds": len(steps),
        "skips": verdicts["skip"],
        "first_bad_commit": first_bad,
        "remaining_unresolved": payload.get("remaining_unresolved"),
        "mtime": path.stat().st_mtime,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def select_history(
    rows: list[dict[str, object]],
    issue: str,
    kind: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    candidates = [
        row for row in rows if row["issue"] == issue and row["kind"] == kind
    ]
    if not candidates:
        return None, None
    valid = [row for row in candidates if row["valid"]]
    selected = max(valid, key=lambda row: float(row["mtime"])) if valid else None
    latest = max(candidates, key=lambda row: float(row["mtime"]))
    return selected, latest


def resolve_interval(
    ledger_row: dict[str, object],
    *histories: dict[str, object] | None,
) -> tuple[int, str]:
    ledger_interval = ledger_row.get("interval_commits")
    if isinstance(ledger_interval, int) and ledger_interval > 0:
        source = str(ledger_row.get("interval_source") or "ledger")
        return ledger_interval, source
    for history in histories:
        if history is None:
            continue
        interval = history.get("interval_commits")
        if isinstance(interval, int) and interval > 0:
            return interval, "run-history"
    raise RuntimeError(
        f"no interval_commits available for {ledger_row.get('issue')}"
    )


def public_method_row(
    selected: dict[str, object] | None,
    latest: dict[str, object] | None,
    destination: str | None,
) -> dict[str, object]:
    if selected is None:
        if latest is None:
            return {
                "availability": "not_run",
                "status": "not_run",
                "runner_builds": None,
                "skips": None,
                "first_bad_commit": None,
                "run_label": None,
                "history_path": None,
            }
        return {
            "availability": "no_valid_result",
            "status": latest["status"],
            "runner_builds": latest["runner_builds"],
            "skips": latest["skips"],
            "first_bad_commit": latest["first_bad_commit"],
            "run_label": latest["run_label"],
            "history_path": None,
            "latest_invalid_history": latest["filename"],
        }
    return {
        "availability": "available",
        "status": selected["status"],
        "runner_builds": selected["runner_builds"],
        "skips": selected["skips"],
        "first_bad_commit": selected["first_bad_commit"],
        "run_label": selected["run_label"],
        "history_path": destination,
        "history_sha256": selected["sha256"],
        "history_bytes": selected["bytes"],
    }


def package(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    histories = root / "results" / "lm_bisect_runs"
    ledger_path = root / "benchmark-results" / "master50" / "issues.json"
    ledger = load_json(ledger_path)
    issue_rows = {str(row["issue"]): row for row in ledger}
    if len(issue_rows) != 50:
        raise ValueError(f"expected 50 Master-50 issues, found {len(issue_rows)}")

    all_rows: list[dict[str, object]] = []
    for path in histories.glob("*.json"):
        try:
            payload = load_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if payload.get("issue") not in issue_rows or classify_history(payload) is None:
            continue
        all_rows.append(summarize_history(path, payload))

    bundle_name = f"master50-ceg-evidence-results-{args.snapshot_date}"
    output_root = args.output_root.resolve()
    bundle_root = output_root / bundle_name
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    (bundle_root / "ceg").mkdir(parents=True)
    (bundle_root / "evidence-diverse").mkdir()

    rows = []
    selected_files: list[Path] = []
    for issue, ledger_row in issue_rows.items():
        ceg, latest_ceg = select_history(all_rows, issue, "ceg")
        evidence, latest_evidence = select_history(
            all_rows,
            issue,
            "evidence-diverse",
        )
        if args.require_complete_ceg and ceg is None:
            raise RuntimeError(f"no valid CEG result for {issue}")

        ceg_destination = None
        if ceg is not None:
            destination = bundle_root / "ceg" / f"{issue}.json"
            shutil.copy2(ceg["path"], destination)
            selected_files.append(destination)
            ceg_destination = str(destination.relative_to(bundle_root))

        evidence_destination = None
        if evidence is not None:
            destination = bundle_root / "evidence-diverse" / f"{issue}.json"
            shutil.copy2(evidence["path"], destination)
            selected_files.append(destination)
            evidence_destination = str(destination.relative_to(bundle_root))

        interval, interval_source = resolve_interval(
            ledger_row,
            ceg,
            evidence,
            latest_ceg,
            latest_evidence,
        )
        rows.append(
            {
                "issue": issue,
                "interval_commits": interval,
                "interval_source": interval_source,
                "binary_search_upper_bound": math.ceil(math.log2(interval)),
                "git_bisect": {
                    "status": ledger_row.get("git_status"),
                    "runner_builds": ledger_row.get("git_steps"),
                    "skips": ledger_row.get("git_skips"),
                    "first_bad_commit": ledger_row.get("first_bad"),
                },
                "ceg": public_method_row(ceg, latest_ceg, ceg_destination),
                "evidence_lm_bisect": public_method_row(
                    evidence,
                    latest_evidence,
                    evidence_destination,
                ),
            }
        )

    inventory = {
        "schema": "master50-ceg-evidence-results-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": args.snapshot_date,
        "scope": "Master-50 designated CEG and endpoint-matched evidence-diverse LM-Bisect histories",
        "issue_count": 50,
        "units": {
            "interval_commits": "Commits in the configured good..bad candidate interval.",
            "runner_builds": "Fresh issue-runner invocations in the selected history.",
        },
        "selection": {
            "ceg": "Newest valid completed causal-evidence-guided history per issue.",
            "evidence_lm_bisect": "Newest valid completed evidence-diverse history per issue, when one exists.",
            "valid": "status=completed, remaining_unresolved=1, and a 40-hex first_bad_commit.",
        },
        "coverage": {
            "ceg_available": sum(
                row["ceg"]["availability"] == "available" for row in rows
            ),
            "evidence_lm_bisect_available": sum(
                row["evidence_lm_bisect"]["availability"] == "available"
                for row in rows
            ),
        },
        "rows": rows,
    }
    inventory_path = bundle_root / "inventory.json"
    write_json(inventory_path, inventory)
    selected_files.append(inventory_path)

    files = []
    for path in sorted(selected_files):
        files.append(
            {
                "path": str(path.relative_to(bundle_root)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    checksums_path = bundle_root / "sha256sums.json"
    write_json(
        checksums_path,
        {
            "schema": "master50-result-checksums-v1",
            "files": files,
        },
    )

    zip_path = output_root / f"{bundle_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    arcname=f"{bundle_name}/{path.relative_to(bundle_root)}",
                )

    print(
        json.dumps(
            {
                "bundle": str(bundle_root),
                "zip": str(zip_path),
                "zip_bytes": zip_path.stat().st_size,
                "zip_sha256": sha256_file(zip_path),
                "coverage": inventory["coverage"],
            },
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--snapshot-date", default="20260921")
    parser.add_argument("--require-complete-ceg", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    package(parse_args())
