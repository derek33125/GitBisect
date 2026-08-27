import { existsSync, readdirSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";
import { join } from "node:path";

const rootReadme = "README.md";
const baselineReadme = "baseline-llmbisect/README.md";
const resultsDir = "benchmark-results/scoped10";

assert.ok(existsSync(rootReadme), "the repository root README is missing");
assert.ok(existsSync(baselineReadme), "the LLMBisect baseline README is missing");
assert.ok(existsSync(resultsDir), "the scoped-10 public result bundle is missing");

const root = readFileSync(rootReadme, "utf8");
for (const heading of [
  "## Overview",
  "## Repository Layout",
  "## Method Families",
  "## Fixed-k12 Variants",
  "## Scoped-10 Results",
  "## Master-50 Results",
]) {
  assert.match(root, new RegExp(heading));
}
assert.match(root, /benchmark-results\/scoped10/);
assert.match(root, /benchmark-results\/master50/);
assert.match(root, /web-presentation-data/);

const baseline = readFileSync(baselineReadme, "utf8");
for (const heading of [
  "## Original Workflow And Adaptation",
  "## Inputs And Outputs",
  "## Scoring Signals",
  "## Relationship To Online Variants",
]) {
  assert.match(baseline, new RegExp(heading));
}
assert.match(baseline, /does not require a fix patch/i);

const files = readdirSync(resultsDir).sort();
assert.ok(files.length >= 6, "the scoped result bundle is incomplete");
assert.ok(files.every((name) => name.endsWith(".json")), "the result bundle must contain JSON only");
assert.ok(!files.some((name) => /\.(md|docx?)$/i.test(name)), "documents must not enter the result bundle");

const manifest = JSON.parse(readFileSync(join(resultsDir, "manifest.json"), "utf8"));
assert.equal(manifest.scope, "scoped-10");
assert.equal(manifest.issue_count, 10);
assert.deepEqual(manifest.issues, [
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
]);

const primary = JSON.parse(readFileSync(join(resultsDir, "preferred-comparison.json"), "utf8"));
assert.equal(primary.length, 10);
const variants = JSON.parse(readFileSync(join(resultsDir, "k12-variants.json"), "utf8"));
assert.equal(variants.rows.length, 10);
assert.equal(variants.reference.aggregate.total_steps, 111);
assert.equal(variants.configurations.find((config) => config.key === "causal").k12.aggregate.total_steps, 107);

const master50Dir = "benchmark-results/master50";
assert.ok(existsSync(master50Dir), "the master-50 public result bundle is missing");
const master50Files = readdirSync(master50Dir).sort();
assert.ok(master50Files.every((name) => name.endsWith(".json")), "the master-50 bundle must contain JSON only");
const master50 = JSON.parse(readFileSync(join(master50Dir, "manifest.json"), "utf8"));
assert.equal(master50.scope, "master-50");
assert.equal(master50.issue_count, 50);
assert.equal(master50.issues.length, 50);
const master50cmp = JSON.parse(readFileSync(join(master50Dir, "preferred-comparison.json"), "utf8"));
assert.equal(master50cmp.length, 50);

console.log("repository entry points and scoped result bundle are complete");
