import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const pagePath = "web-presentation-data/k12-variants.html";
const sitePath = "web-presentation-data/data/site-data.json";

assert.ok(existsSync(pagePath), "the fixed-k12 variants presentation page is missing");
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

const page = readFileSync(pagePath, "utf8");
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
assert.match(page, /select_evidence_diverse_frontier/);
assert.match(page, /retrieve_causal_diff_evidence/);
assert.match(page, /observation_conditioned_posterior_probabilities/);
assert.match(page, /semantic_frontier_confidence/);
assert.match(page, /w\(c\) = p0\(c\) \* exp/);
assert.match(page, /pr204559 step 1/i);
assert.match(app, /renderK12VariantComparison/);
assert.match(readFileSync("web-presentation-data/index.html", "utf8"), /k12-variants\.html/);

console.log("fixed-k12 variant presentation data is complete");
