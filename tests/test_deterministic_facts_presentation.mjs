import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const pagePath = "web-presentation-data/human-analysis.html";
const sitePath = "web-presentation-data/data/site-data.json";

assert.ok(existsSync(pagePath), "the Human Study & Ablations page is missing");
assert.ok(existsSync(sitePath), "generated site data is missing");

const page = readFileSync(pagePath, "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
const site = JSON.parse(readFileSync(sitePath, "utf8"));
const comparison = site.deterministic_facts_bcr;
const v16 = site.deterministic_facts_bcr_v16_window;

assert.ok(comparison, "V15 deterministic-facts BCR data is missing");
assert.equal(comparison.aggregate.completed, 10);
assert.equal(comparison.aggregate.total_steps, 101);
assert.equal(comparison.aggregate.mean_steps, 10.1);
assert.equal(comparison.rows.length, 10);
assert.ok(comparison.rows.every((row) => row.deterministic_facts.state === "completed"));
assert.ok(comparison.rows.every((row) => row.deterministic_facts.steps > 0));
assert.equal(comparison.rows.find((row) => row.issue === "pr50304").deterministic_facts.steps, 10);
assert.equal(comparison.rows.find((row) => row.issue === "pr50304").deterministic_facts.skips, 0);
assert.match(comparison.integration.summary, /one ordinal LLM call/i);
assert.match(comparison.integration.summary, /not a fake build observation/i);

assert.match(page, /BCR V15/);
assert.match(page, /id="deterministic-facts-bcr-body"/);
assert.doesNotMatch(page, /\bv14\b/i);
assert.match(app, /renderDeterministicFactsBcr/);
assert.doesNotMatch(app, /renderCrashAwareBcr/);

assert.ok(v16, "V16 artifact-complete parent-window BCR data is missing");
assert.equal(v16.aggregate.completed, 10);
assert.equal(v16.aggregate.total_steps, 102);
assert.equal(v16.aggregate.mean_steps, 10.2);
assert.equal(v16.aggregate.skip_total, 0);
assert.equal(v16.rows.length, 10);
assert.ok(v16.rows.every((row) => row.v16_window.state === "completed"));
assert.ok(v16.rows.every((row) => row.v16_window.parent_context_count === 5));
assert.equal(v16.rows.find((row) => row.issue === "pr52635").v16_window.steps, 12);
assert.equal(v16.rows.find((row) => row.issue === "pr200987").v16_window.steps, 10);
assert.match(v16.configuration, /five parent contexts/i);
assert.match(v16.scope, /artifact-complete/i);

assert.match(page, /V16 artifact-complete/i);
assert.match(page, /V16 window BCR/);
assert.match(app, /renderDeterministicFactsV16Window/);

console.log("V15 deterministic-facts presentation data is complete");
