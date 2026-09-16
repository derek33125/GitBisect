"""Experimental fusion of program evidence and ordinal causal judgments."""

from __future__ import annotations

import math
from typing import Mapping


POLICY = "coverage-mixture-v1"
MAX_LLM_SHARE = 0.5
CAUSAL_MECHANISMS = frozenset({"invariant-break", "precondition-violation"})


def coverage_weighted_fusion(
    prior_mass_by_sha: Mapping[str, float],
    judgments_by_sha: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, float], dict[str, object]]:
    """Mix within the inspected frontier, retaining at least half its prior.

    Coverage and reported confidence are bounded trust weights, not calibrated
    probabilities. Only affirmative causal judgments receive the LLM share;
    an unknown or negative judgment alone cannot change the prior.
    """
    masses = {sha: float(value) for sha, value in prior_mass_by_sha.items()}
    total = sum(masses.values())
    if (
        not math.isfinite(total)
        or total <= 0
        or any(not math.isfinite(p) or p < 0 for p in masses.values())
    ):
        raise ValueError("program prior must contain finite nonnegative mass with positive total")
    masses = {sha: p / total for sha, p in masses.items()}
    frontier = {
        sha: row
        for sha, row in judgments_by_sha.items()
        if sha in masses and isinstance(row, Mapping)
    }
    malformed_frontier = {
        sha for sha, row in judgments_by_sha.items()
        if sha in masses and not isinstance(row, Mapping)
    }
    frontier_mass = sum(masses[sha] for sha in frontier)
    coverage = len(frontier) / len(masses)
    audit: dict[str, object] = {
        "policy": POLICY,
        "frontier_count": len(frontier),
        "frontier_mass_before": frontier_mass,
        "frontier_mass_after": frontier_mass,
        "candidate_coverage": coverage,
        "max_llm_share": MAX_LLM_SHARE,
        "mixture_weight": 0.0,
        "supported_count": 0,
        "ordinal_permutation_valid": False,
    }
    ranks = [row.get("rank") for row in frontier.values()]
    if (
        not frontier
        or malformed_frontier
        or any(type(rank) is not int for rank in ranks)
        or set(ranks) != set(range(1, len(frontier) + 1))
        or any(row.get("ordinal_permutation_valid") is not True for row in frontier.values())
    ):
        audit["reason"] = "missing-or-invalid-ordinal-permutation"
        return masses, audit
    audit["ordinal_permutation_valid"] = True
    try:
        confidence = {sha: float(row.get("confidence", 0.0)) for sha, row in frontier.items()}
    except (TypeError, ValueError):
        audit["reason"] = "invalid-confidence"
        return masses, audit
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in confidence.values()):
        audit["reason"] = "invalid-confidence"
        return masses, audit
    supported = {
        sha: confidence[sha] / row["rank"]
        for sha, row in frontier.items()
        if row.get("explains_failure") is True
        and row.get("mechanism") in CAUSAL_MECHANISMS
        and confidence[sha] > 0
    }
    audit["supported_count"] = len(supported)
    if not supported:
        audit["reason"] = "no-supported-causal-judgment"
        return masses, audit
    alpha = MAX_LLM_SHARE * coverage * max(confidence[sha] for sha in supported)
    support_total = sum(supported.values())
    fused = dict(masses)
    for sha in frontier:
        llm_mass = frontier_mass * supported.get(sha, 0.0) / support_total
        fused[sha] = (1 - alpha) * masses[sha] + alpha * llm_mass
    audit.update({
        "mixture_weight": alpha,
        "frontier_mass_after": sum(fused[sha] for sha in frontier),
        "reason": "bounded-supported-ordinal-mixture",
        "supported_ranks": {sha: frontier[sha]["rank"] for sha in supported},
    })
    return fused, audit


def isolated_namespace(namespace: str | None, policy: str) -> str | None:
    if policy == "legacy":
        return namespace
    if policy != POLICY:
        raise ValueError(f"unsupported CEG fusion policy: {policy}")
    if not namespace:
        raise ValueError("experimental CEG fusion requires a model cache namespace")
    suffix = f"--ceg-fusion-{policy}"
    return namespace if namespace.endswith(suffix) else namespace + suffix
