#!/usr/bin/env python3
"""Offline paired analysis for the scoped-10 heuristic follow-up studies."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCOPED10_ISSUES = (
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


def _step_map(history: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(step["step"]): step
        for step in history.get("steps", [])
        if isinstance(step, dict) and step.get("step") is not None
    }


def _selected_rank(step: dict[str, Any]) -> int | None:
    selected_sha = step.get("sha")
    return _candidate_rank(step, selected_sha)


def _candidate_rank(step: dict[str, Any], sha: object) -> int | None:
    if not isinstance(sha, str):
        return None
    for candidate in step.get("top_candidates", []):
        if candidate.get("sha") == sha:
            rank = candidate.get("rank")
            return int(rank) if isinstance(rank, int) else None
    return None


def compare_history_pair(
    baseline: dict[str, Any],
    variant: dict[str, Any],
    factor: str,
) -> dict[str, Any]:
    """Compare two histories only while their unresolved states remain paired."""

    baseline_steps = _step_map(baseline)
    variant_steps = _step_map(variant)
    paired_steps = 0
    rank_deltas: list[int] = []
    selection_changes: list[dict[str, Any]] = []
    first_divergence_step: int | None = None
    first_divergence_decision: dict[str, Any] | None = None
    max_common_step = min(max(baseline_steps, default=0), max(variant_steps, default=0))

    for number in range(1, max_common_step + 1):
        left = baseline_steps[number]
        right = variant_steps[number]
        same_state = (
            left.get("unresolved_before") == right.get("unresolved_before")
            and left.get("unresolved_after") == right.get("unresolved_after")
        )
        same_selection = left.get("sha") == right.get("sha")
        if not same_state or not same_selection:
            first_divergence_step = number
            first_divergence_decision = {
                "step": number,
                "same_unresolved_before": left.get("unresolved_before") == right.get("unresolved_before"),
                "same_unresolved_after": left.get("unresolved_after") == right.get("unresolved_after"),
                "baseline_selected_sha": left.get("sha"),
                "variant_selected_sha": right.get("sha"),
                "baseline_selected_rank_in_variant": _candidate_rank(right, left.get("sha")),
                "variant_selected_rank_in_baseline": _candidate_rank(left, right.get("sha")),
            }
            break
        paired_steps += 1
        left_rank = _selected_rank(left)
        right_rank = _selected_rank(right)
        if left_rank is not None and right_rank is not None:
            rank_deltas.append(right_rank - left_rank)

    divergence = first_divergence_step or (max_common_step + 1)
    for number in range(divergence, max_common_step + 1):
        left = baseline_steps[number]
        right = variant_steps[number]
        if left.get("sha") != right.get("sha"):
            selection_changes.append(
                {
                    "step": number,
                    "baseline_sha": left.get("sha"),
                    "variant_sha": right.get("sha"),
                    "baseline_rank": _selected_rank(left),
                    "variant_rank": _selected_rank(right),
                }
            )

    return {
        "issue": baseline.get("issue") or variant.get("issue"),
        "factor": factor,
        "baseline_run_label": baseline.get("run_label"),
        "variant_run_label": variant.get("run_label"),
        "baseline_status": baseline.get("status"),
        "variant_status": variant.get("status"),
        "baseline_steps": baseline.get("steps_executed", len(baseline_steps)),
        "variant_steps": variant.get("steps_executed", len(variant_steps)),
        "paired_steps": paired_steps,
        "first_divergence_step": first_divergence_step,
        "first_divergence_decision": first_divergence_decision,
        "post_divergence_steps_not_paired": max(0, max_common_step - paired_steps),
        "selection_changes": selection_changes,
        "paired_rank_delta_mean": (
            sum(rank_deltas) / len(rank_deltas) if rank_deltas else None
        ),
    }


def summarize_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    paired = [row for row in rows if row["first_divergence_step"] is None]
    changed = [row for row in rows if row["first_divergence_step"] is not None]
    return {
        "issues": len(rows),
        "fully_paired_issues": len(paired),
        "diverged_issues": len(changed),
        "mean_baseline_steps": (
            sum(float(row["baseline_steps"]) for row in rows) / len(rows) if rows else None
        ),
        "mean_variant_steps": (
            sum(float(row["variant_steps"]) for row in rows) / len(rows) if rows else None
        ),
        "mean_paired_steps": (
            sum(int(row["paired_steps"]) for row in rows) / len(rows) if rows else None
        ),
        "first_divergence_step_counts": dict(
            sorted(Counter(row["first_divergence_step"] for row in changed).items())
        ),
        "warning": (
            "Only the common prefix before the first changed selection/state is paired; "
            "later step counts are descriptive, not counterfactual effects."
        ),
    }


def load_history(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"history is not an object: {path}")
    return payload


def load_pair_set(
    manifest_path: Path,
    factor: str,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Load explicit history pairs so mixed historical runs stay auditable."""

    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError(f"pair manifest is not an object: {manifest_path}")
    pair_sets = manifest.get("pair_sets")
    if not isinstance(pair_sets, dict) or not isinstance(pair_sets.get(factor), list):
        raise ValueError(f"pair set not found for factor {factor!r}: {manifest_path}")

    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for entry in pair_sets[factor]:
        if not isinstance(entry, dict):
            raise ValueError(f"pair entry is not an object: {entry!r}")
        issue = entry.get("issue")
        baseline_rel = entry.get("baseline_path")
        variant_rel = entry.get("variant_path")
        if not all(isinstance(value, str) and value for value in (issue, baseline_rel, variant_rel)):
            raise ValueError(f"invalid pair entry: {entry!r}")
        baseline = load_history(manifest_path.parent / baseline_rel)
        variant = load_history(manifest_path.parent / variant_rel)
        if baseline.get("issue") != issue or variant.get("issue") != issue:
            raise ValueError(f"pair issue mismatch for {issue}: {entry!r}")
        pairs.append((issue, baseline, variant))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--pair-manifest", type=Path)
    input_group.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--variant-dir", type=Path)
    parser.add_argument("--factor", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.baseline_dir is not None and args.variant_dir is None:
        parser.error("--variant-dir is required with --baseline-dir")
    if args.pair_manifest is not None and args.variant_dir is not None:
        parser.error("--variant-dir cannot be used with --pair-manifest")

    rows = []
    if args.pair_manifest is not None:
        for _issue, baseline, variant in load_pair_set(args.pair_manifest, args.factor):
            rows.append(compare_history_pair(baseline, variant, args.factor))
    else:
        for issue in SCOPED10_ISSUES:
            baseline = load_history(args.baseline_dir / f"{issue}.json")
            variant = load_history(args.variant_dir / f"{issue}.json")
            rows.append(compare_history_pair(baseline, variant, args.factor))
    payload = {
        "scope": "web scoped-10 heuristic follow-up",
        "study": "offline counterfactual matched-prefix analysis",
        "factor": args.factor,
        "rows": rows,
        "aggregate": summarize_pairs(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
