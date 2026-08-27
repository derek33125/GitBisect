import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const pagePath = "web-presentation-data/causality-study.html";
const sitePath = "web-presentation-data/data/site-data.json";

assert.ok(existsSync(pagePath), "the causality-study presentation page is missing");
assert.ok(existsSync(sitePath), "generated site data is missing");

const page = readFileSync(pagePath, "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
const site = JSON.parse(readFileSync(sitePath, "utf8"));
const study = site.first_bad_causality_study;

assert.ok(study, "causality-study data is missing from the site payload");
assert.equal(study.aggregate.total_cases, 10);
assert.equal(study.aggregate.direct, 6);
assert.equal(study.aggregate.indirect_enabling, 4);
assert.equal(study.aggregate.no_visible_match, 0);
assert.equal(study.cases.length, 10, "the study must retain all scoped cases");
assert.equal(study.cases.filter((row) => row.causal_role === "direct").length, 6);
assert.equal(study.cases.filter((row) => row.causal_role === "indirect-enabling").length, 4);
assert.ok(study.cases.every((row) => row.diff_excerpt), "each case needs first-bad diff evidence");
assert.ok(study.cases.every((row) => row.trace_evidence && row.trace_evidence.quality), "each case needs trace quality");
assert.equal(study.rules.length, 6, "the deployable conclusion must preserve six rules");
assert.match(study.rules[0].title, /Exact Diagnostic and Symbol Matches/);
assert.match(study.rules[1].title, /Downstream Detectors/);
assert.match(study.deployability_notice, /must not be supplied to a deployable run/i);

assert.match(page, /First-Bad Trace Causality Study/);
assert.match(page, /id="causality-summary"/);
assert.match(page, /id="causality-cases"/);
assert.match(page, /id="causality-rules"/);
assert.match(page, /Retrospective evidence only/);
assert.match(app, /renderCausalityStudy/);
assert.match(readFileSync("web-presentation-data/index.html", "utf8"), /causality-study\.html/);

console.log("causality study presentation data is complete");
