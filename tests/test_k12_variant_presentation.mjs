import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const pagePath = "web-presentation-data/k12-variants.html";
const humanStudyPath = "web-presentation-data/human-analysis.html";
const sitePath = "web-presentation-data/data/site-data.json";

assert.ok(existsSync(pagePath), "the fixed-k12 variants presentation page is missing");
assert.ok(existsSync(humanStudyPath), "the human-study and ablation presentation page is missing");
assert.ok(existsSync(sitePath), "generated site data is missing");

const site = JSON.parse(readFileSync(sitePath, "utf8"));
const variants = site.k12_variants;
assert.ok(variants, "k12 variant comparison data is missing from the site payload");
assert.equal(variants.rows.length, 10, "the fixed-k12 matrix must retain the scoped ten cases");
assert.equal(variants.reference.label, "Parent diff + LLM extraction (top-k3)");
assert.equal(variants.reference.aggregate.total_steps, 111);
assert.equal(variants.reference.aggregate.mean_steps, 11.1);

const causal = variants.configurations.find((config) => config.key === "causal");
assert.ok(causal, "the causal parent-diff variant is missing");
assert.equal(causal.k3.aggregate.completed, 9);
assert.equal(causal.k3.aggregate.total_steps, 98);
assert.equal(causal.k12.aggregate.completed, 10);
assert.equal(causal.k12.aggregate.total_steps, 107);

const expansion = site.expansion_comparison;
assert.ok(expansion, "three-method expansion comparison data is missing");
assert.equal(expansion.aggregate.heuristic.total_steps, 123);
assert.equal(expansion.aggregate.original.total_steps, 111);
assert.equal(expansion.aggregate.causal.total_steps, 107);
assert.deepEqual(expansion.aggregate.causal.vs_original, {
  wins: 5,
  ties: 2,
  losses: 3,
  step_delta: -4,
});
assert.equal(expansion.by_type.length, 3);
assert.deepEqual(
  expansion.by_type.map((group) => group.causal_step_delta_vs_original),
  [4, -6, -2]
);

const terra = site.terra_bcr_comparison;
assert.ok(terra, "the GPT-5.6 Terra BCR comparison is missing");
assert.equal(terra.model.name, "gpt-5.6-terra");
assert.equal(terra.model.reasoning_effort, "high");
assert.equal(terra.rows.length, 10);
assert.equal(terra.aggregate.terra.total_steps, 108);
assert.equal(terra.aggregate.terra.mean_steps, 10.8);
assert.equal(terra.aggregate.terra.canonical_boundary_matches, 9);
assert.equal(terra.aggregate.terra.noncanonical_terminal_cases, 1);
assert.deepEqual(terra.aggregate.terra.vs_mini_bcr, {
  wins: 3,
  ties: 3,
  losses: 4,
  step_delta: 1,
});
assert.equal(terra.aggregate.parent_window.total_steps, 99);
assert.equal(terra.aggregate.parent_window.mean_steps, 9.9);
assert.equal(terra.aggregate.parent_window.canonical_boundary_matches, 9);
assert.equal(terra.aggregate.parent_window.skip_total, 0);
assert.deepEqual(terra.aggregate.parent_window.vs_parent_llm_topk3, {
  wins: 7,
  ties: 2,
  losses: 1,
  step_delta: -12,
});
assert.deepEqual(terra.aggregate.parent_window.vs_terra_single_parent, {
  wins: 7,
  ties: 3,
  losses: 0,
  step_delta: -9,
});
const terraAmbiguity = terra.rows.find((row) => row.issue === "pr49535");
assert.equal(terraAmbiguity.canonical_boundary, false);
assert.match(terraAmbiguity.note, /apply\/reapply/i);
assert.equal(terraAmbiguity.parent_window_steps, 9);
assert.equal(terraAmbiguity.parent_window_canonical_boundary, false);

const deterministicFacts = site.deterministic_facts_bcr;
assert.ok(deterministicFacts, "V15 deterministic-facts BCR comparison data is missing");
assert.equal(deterministicFacts.rows.length, 10, "V15 BCR must retain the scoped ten cases");
assert.equal(deterministicFacts.aggregate.completed, 10);
assert.equal(deterministicFacts.aggregate.total_steps, 101);
assert.equal(deterministicFacts.aggregate.mean_steps, 10.1);
assert.equal(deterministicFacts.aggregate.interrupted, 0);
assert.equal(deterministicFacts.aggregate.queued, 0);
assert.deepEqual(deterministicFacts.aggregate.vs_terra_single_parent, {
  compared: 10,
  wins: 4,
  ties: 4,
  losses: 2,
  step_delta: -7,
});
assert.deepEqual(deterministicFacts.aggregate.vs_human_guided, {
  compared: 10,
  wins: 4,
  ties: 1,
  losses: 5,
  step_delta: -2,
});
assert.deepEqual(deterministicFacts.aggregate.vs_parent_window, {
  compared: 10,
  wins: 2,
  ties: 2,
  losses: 6,
  step_delta: 2,
});
assert.match(deterministicFacts.integration.summary, /crash artifact/i);
assert.equal(deterministicFacts.integration.rules.length, 6);
assert.equal(
  deterministicFacts.rows.find((row) => row.issue === "pr52635").deterministic_facts.state,
  "completed"
);

const heuristicFactors = site.heuristic_factor_ablation;
assert.ok(heuristicFactors, "heuristic-factor ablation data is missing");
assert.equal(heuristicFactors.baseline.label, "Tuned heuristic");
assert.equal(heuristicFactors.factors.length, 6);
assert.equal(
  heuristicFactors.factors.find((factor) => factor.key === "keywords").aggregate.completed_clean,
  4
);
assert.equal(
  heuristicFactors.factors.find((factor) => factor.key === "feedback").aggregate.completed_clean,
  4
);
for (const [key, expectedSteps] of Object.entries({
  "risky-words": 10,
  buildability: 13,
  feedback: 10,
})) {
  const factor = heuristicFactors.factors.find((item) => item.key === key);
  assert.equal(factor.aggregate.completed_clean, 4, `${key} must include the completed pr193164 run`);
  assert.equal(
    factor.rows.find((row) => row.issue === "pr193164").result.steps,
    expectedSteps,
    `${key} pr193164 step count must match the terminal EDU history`
  );
}
assert.ok(
  heuristicFactors.factors.every((factor) => factor.disabled_component && factor.definition),
  "each ablation must explain exactly what was disabled"
);

const repeatability = terra.repeatability;
assert.equal(repeatability.parent_window_independent_repeats, 0);
assert.deepEqual(repeatability.cached_same_host_replay, {
  issue_groups: 9,
  exact_step_and_verdict_paths: 9,
  boundary_agreements: 9,
});
assert.equal(repeatability.cross_host_parent_topk3.issue_groups, 9);
assert.equal(repeatability.cross_host_parent_topk3.boundary_agreements, 9);
assert.equal(repeatability.cross_host_parent_topk3.mean_absolute_step_delta, 1);
assert.equal(repeatability.cross_host_parent_topk3.max_absolute_step_delta, 3);
assert.equal(repeatability.cross_host_parent_topk3.mean_selected_sha_jaccard, 0.78);
assert.equal(repeatability.cross_host_parent_topk3.mean_same_position_rate, 0.51);
assert.equal(repeatability.cross_host_parent_topk20.issue_groups, 3);
assert.equal(repeatability.cross_host_parent_topk20.boundary_agreements, 3);
assert.equal(repeatability.cross_host_parent_topk20.mean_selected_sha_jaccard, 0.33);
assert.equal(repeatability.cross_host_parent_topk20.max_absolute_step_delta, 2);

const evidence = variants.configurations.find((config) => config.key === "evidence");
assert.ok(evidence, "the evidence-diverse variant is missing");
assert.equal(evidence.k3.aggregate.completed, 0);
assert.equal(evidence.k12.aggregate.completed, 9);
assert.equal(evidence.k12.aggregate.matched_reference_total, 98);

const staleEvidence = variants.rows.find((row) => row.issue === "pr193164").evidence.k12;
assert.equal(staleEvidence.state, "running");
assert.equal(staleEvidence.steps, 3);
assert.match(staleEvidence.note, /corrected EDU rerun/i);

const staleCausal = variants.rows.find((row) => row.issue === "pr193164").causal.k3;
assert.equal(staleCausal.state, "invalid");
assert.match(staleCausal.note, /obsolete bad endpoint/i);

const retrieval = variants.causal_retrieval_example;
assert.equal(retrieval.issue, "pr204559");
assert.equal(retrieval.candidate_sha, "d518f8ff6740");
assert.ok(retrieval.retrieval.selected_hunks.length > 0);
assert.ok(retrieval.retrieval.selected_hunks[0].match_reasons.length > 0);
assert.ok(retrieval.retrieval.function_contexts.length > 0);
assert.match(retrieval.causal_evidence.summary, /MemorySSA/i);
assert.ok(retrieval.shared_scorer_handoff.semantic_score > 0);
assert.equal(retrieval.shared_scorer_handoff.runner_verdict, "good");

const page = readFileSync(pagePath, "utf8");
const humanStudyPage = readFileSync(humanStudyPath, "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
for (const name of [
  "Evidence-guided diverse frontier",
  "Structured causal parent-diff reasoning",
  "Observation-conditioned posterior",
  "Confidence-adaptive frontier",
]) {
  assert.match(page, new RegExp(name));
}
assert.match(page, /id="k12-variant-cards"/);
assert.match(page, /id="k12-variant-body"/);
assert.match(page, /id="variant-base-contract"/);
assert.match(page, /id="variant-policy-map"/);
assert.match(page, /id="causal-retrieval-example"/);
assert.match(page, /How BCR retrieves evidence/);
assert.match(page, /shared scoring handoff/i);
assert.match(page, /BCR replaces the generic raw-diff summary/);
assert.match(page, /no second generic raw-diff summary runs in parallel/i);
assert.match(page, /file ranking &rarr; hunk ranking &rarr; context lookup/);
assert.match(page, /id="expansion-guidance"/);
assert.match(page, /id="terra-bcr-comparison"/);
assert.match(page, /GPT-5\.6 Terra BCR/);
assert.match(page, /id="parent-window-comparison"/);
assert.match(page, /five first-parent predecessors/i);
assert.match(page, /id="lm-repeatability"/);
assert.match(page, /human-analysis\.html/);
assert.match(humanStudyPage, /Human Study &amp; Ablations/);
assert.match(humanStudyPage, /id="human-analysis-bcr"/);
assert.match(humanStudyPage, /id="deterministic-facts-bcr-body"/);
assert.match(humanStudyPage, /id="human-guided-method-comparison"/);
assert.match(humanStudyPage, /Original BCR.*causal-llm/i);
assert.match(humanStudyPage, /historical Human-guided BCR.*causal-llm-human/i);
assert.match(humanStudyPage, /not direct answer leakage/i);
assert.match(humanStudyPage, /Human-study BCR V15/i);
assert.match(humanStudyPage, /general runtime parser/i);
assert.match(humanStudyPage, /first-bad SHA|first-bad commit/i);
assert.match(humanStudyPage, /answer-informed.*pool|frontier.*answer-informed/i);
assert.match(humanStudyPage, /id="heuristic-factor-ablation"/);
assert.match(humanStudyPage, /id="heuristic-factor-ablation-body"/);
assert.match(humanStudyPage, /what the study found/i);
assert.match(humanStudyPage, /what changed in BCR/i);
assert.match(humanStudyPage, /runner-backed result/i);
assert.match(humanStudyPage, /completes the scoped ten/i);
assert.match(page, /Boundary stable, path variable/i);
assert.match(page, /id="expansion-method-cards"/);
assert.match(page, /id="expansion-type-body"/);
assert.match(page, /select_evidence_diverse_frontier/);
assert.match(page, /retrieve_causal_diff_evidence/);
assert.match(page, /observation_conditioned_posterior_probabilities/);
assert.match(page, /semantic_frontier_confidence/);
assert.match(page, /w\(c\) = p0\(c\) \* exp/);
assert.match(page, /pr204559 step 1/i);
assert.match(app, /renderK12VariantComparison/);
assert.match(app, /renderCausalRetrievalExample/);
assert.match(app, /renderExpansionComparison/);
assert.match(app, /renderLmRepeatability/);
assert.match(app, /renderDeterministicFactsBcr/);
assert.match(app, /renderHeuristicFactorAblation/);
assert.match(readFileSync("web-presentation-data/index.html", "utf8"), /k12-variants\.html/);
assert.match(readFileSync("web-presentation-data/index.html", "utf8"), /human-analysis\.html/);

console.log("fixed-k12 variant presentation data is complete");
