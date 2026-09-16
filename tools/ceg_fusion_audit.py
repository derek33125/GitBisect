"""Read-only diagnostics of recorded CEG frontiers, without model/runner calls.

Collection can execute over SSH stdin using only the Python standard library.
Analysis evaluates frontier mass updates, not counterfactual search trajectories.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def read_hashed_json(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def collect(root, prefixes):
    cases = []
    for path in sorted((root / "results/lm_bisect_runs").glob("*.json")):
        if not any(prefix in path.name for prefix in prefixes):
            continue
        history, history_digest = read_hashed_json(path)
        label = history.get("run_label", "")
        if not any(label.startswith(prefix) for prefix in prefixes):
            continue
        issue = history["issue"]
        namespace = history.get("model_cache_namespace", "")
        caches = [p for p in (root / "results/lm_bisect_model_cache").glob(f"{issue}-*.json")
                  if p.name.endswith(f"-ns-{namespace}.json")]
        if len(caches) > 1:
            raise ValueError(f"ambiguous cache for {path}: {caches}")
        cache, cache_digest = read_hashed_json(caches[0]) if caches else ({}, None)
        by_context = {}
        for key, value in cache.items():
            if "|context:" not in key:
                continue
            sha = key.split("|", 1)[0]
            context = key.rsplit("|context:", 1)[1]
            by_context.setdefault(context, {})[sha] = value
        steps = []
        for step in history.get("steps", []):
            frontier_shas = (step.get("model_frontier_decision") or {}).get("selected_frontier_shas", [])
            context = step.get("model_score_context")
            entries = {}
            if context and frontier_shas:
                digest = hashlib.sha256("\n".join(sorted(frontier_shas)).encode()).hexdigest()
                cache_context = hashlib.sha256(f"{context}|frontier:{digest}".encode()).hexdigest()[:16]
                entries = by_context.get(cache_context, {})
            # Some histories embed only the operational top five. Use those
            # exact-step rows as a fallback, never a judgment from another step.
            embedded = {row["sha"]: row for row in step.get("top_candidates", [])}
            embedded[step["sha"]] = step.get("selection", {})
            prior = step.get("causal_evidence_guided_prior") or {}
            masses = {row["sha"]: row["mass"] for row in prior.get("top_candidates", [])}
            frontier = {}
            for sha in frontier_shas:
                entry = entries.get(sha, embedded.get(sha, {}))
                causal = entry.get("causal_evidence") or {}
                judgment = entry.get("ordinal_judgment") or causal.get("ordinal_judgment")
                candidate = (causal.get("retrieval", {}).get("program_prior_evidence", {}).get("candidate") or {})
                mass = candidate.get("mass", masses.get(sha, entry.get("program_prior_mass")))
                if judgment is None or mass is None:
                    continue
                frontier[sha] = {
                    "mass": mass,
                    "index": candidate.get("index"),
                    "judgment": {key: judgment.get(key) for key in (
                        "rank", "confidence", "mechanism", "explains_failure", "ordinal_permutation_valid"
                    )},
                }
            steps.append({
                "step": step["step"],
                "candidate_count": step["unresolved_before"],
                "after": step["unresolved_after"],
                "verdict": step["verdict"],
                "selected_sha": step["sha"],
                "selection_mode": step.get("selection_mode"),
                "active_signal_count": prior.get("active_signal_count"),
                "old_fusion_count": step.get("selection_metadata", {}).get("fusion", {}).get("frontier_count", 0),
                "frontier_shas": frontier_shas,
                "frontier": frontier,
            })
        cases.append({
            "issue": issue, "run_label": label, "namespace": namespace,
            "prior_version": history.get("causal_evidence_guided", {}).get("program_prior_version"),
            "status": history.get("status"), "first_bad_commit": history.get("first_bad_commit"),
            "history_path": str(path), "history_sha256": history_digest,
            "cache_path": str(caches[0]) if caches else None, "cache_sha256": cache_digest,
            "steps": steps,
        })
    return {"collected_at": datetime.now(timezone.utc).isoformat(), "root": str(root), "prefixes": prefixes, "cases": cases}


def analyze(snapshot):
    try:
        from tools import ceg_fusion
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ceg_fusion

    counts = Counter({
        "posterior_directional_steps": 0, "posterior_zero_or_singleton_fusion": 0,
        "incomplete_frontiers": 0, "complete_frontiers": 0,
        "posterior_changed_fusion": 0, "posterior_no_supported_judgment": 0,
        "posterior_invalid_judgments": 0,
    })
    cases = []
    for case in snapshot["cases"]:
        steps = []
        for step in case["steps"]:
            ordinary = (step["verdict"] in {"good", "bad"} and
                        step["selection_mode"] == "causal-evidence-guided-calibrated-posterior")
            if ordinary:
                counts["posterior_directional_steps"] += 1
                counts["posterior_zero_or_singleton_fusion"] += step["old_fusion_count"] <= 1
            result = {key: value for key, value in step.items() if key != "frontier"}
            result.update({"fusion": None, "supported_candidates": []})
            expected = set(step["frontier_shas"])
            if not expected:
                result["audit_status"] = "no-model-frontier"
            elif set(step["frontier"]) != expected:
                counts["incomplete_frontiers"] += 1
                result["audit_status"] = "incomplete-frontier"
            else:
                counts["complete_frontiers"] += 1
                rows = step["frontier"]
                masses = {sha: row["mass"] for sha, row in rows.items()}
                outside_count = step["candidate_count"] - len(masses)
                remaining = 1 - sum(masses.values())
                if outside_count < 0 or remaining < -1e-6 or (outside_count == 0 and abs(remaining) > 1e-6):
                    raise ValueError(f"invalid recorded frontier mass in {case['run_label']} step {step['step']}")
                # The function uses outside entries only for count and total
                # mass. Anonymous entries suffice for an exact mass-only audit;
                # they cannot be used to replay the selector or runner.
                masses.update({f"outside:{i}": max(0.0, remaining) / outside_count for i in range(outside_count)})
                fused, audit = ceg_fusion.coverage_weighted_fusion(
                    masses, {sha: row["judgment"] for sha, row in rows.items()},
                )
                result["fusion"] = audit
                result["audit_status"] = "complete-frontier-mass-only"
                for sha in audit.get("supported_ranks", {}):
                    result["supported_candidates"].append({
                        "sha": sha, "rank": rows[sha]["judgment"]["rank"],
                        "confidence": rows[sha]["judgment"]["confidence"],
                        "old_mass": masses[sha], "new_mass": fused[sha],
                        "matches_run_boundary": sha == case.get("first_bad_commit"),
                    })
                if ordinary:
                    counts["posterior_changed_fusion"] += any(abs(fused[sha] - masses[sha]) > 1e-12 for sha in rows)
                    counts["posterior_no_supported_judgment"] += audit["reason"] == "no-supported-causal-judgment"
                    counts["posterior_invalid_judgments"] += audit["reason"] in {"invalid-confidence", "missing-or-invalid-ordinal-permutation"}
            steps.append(result)
        cases.append({**{key: value for key, value in case.items() if key != "steps"}, "steps": steps})
    return {
        "policy": ceg_fusion.POLICY,
        "is_full_search_replay": False,
        "scope": "frozen historical frontier mass updates; no future judgments or runner outcomes inferred",
        "summary": dict(counts), "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect")
    collection.add_argument("root", type=Path)
    collection.add_argument("--prefix", action="append", required=True)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        result = collect(args.root, args.prefix)
    else:
        snapshot, digest = read_hashed_json(args.snapshot)
        result = analyze(snapshot)
        result["input_sha256"] = digest
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
