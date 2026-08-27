import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const pagePath = "web-presentation-data/master-status.html";
const sitePath = "web-presentation-data/data/site-data.json";

assert.ok(existsSync(pagePath), "the master-status presentation page is missing");
assert.ok(existsSync(sitePath), "generated site data is missing");

const site = JSON.parse(readFileSync(sitePath, "utf8"));
assert.ok(site.master_status, "master-status data is missing from the site payload");
assert.equal(site.master_status.source, "results/master-status-report.md");
assert.deepEqual(site.master_status.columns, [
  "Issue",
  "State",
  "Interval commits",
  "Git bisect (steps / skips)",
  "Legacy LM-bisect (steps / skips)",
  "Issue-specific heuristic (steps / skips)",
  "Weak-general heuristic (steps / skips)",
  "BCR top-k12 (steps / skips)",
  "Good Anchor",
  "Bad Anchor",
  "First Bad Commit",
  "Notes",
]);
assert.ok(site.master_status.rows.length >= 100, "the full master table must be published");
assert.ok(site.master_status.rows.some((row) => row.issue === "pr165039"));
assert.ok(site.master_status.rows.some((row) => row.issue === "pr176682"));
assert.equal(site.master_status.summary.total_issues, site.master_status.rows.length);
assert.deepEqual(
  site.master_status.summary.methods.map((method) => method.key),
  ["git", "legacy_lm", "tuned_heuristic", "weak_general_heuristic", "bcr"],
);
assert.ok(site.master_status.summary.methods.every((method) => method.valid_results > 0));

const bcr = site.master_status.rows.find((row) => row.issue === "pr165039");
assert.equal(bcr.bcr.state, "completed");
assert.equal(bcr.bcr.steps, 5);
assert.equal(bcr.bcr.skips, 0);

const intervalExample = site.master_status.rows.find((row) => row.issue === "pr204559");
assert.equal(intervalExample.interval_commits, 19791);
assert.equal(intervalExample.interval_source, "run-history");

const packagedInterval = site.master_status.rows.find((row) => row.issue === "pr156249");
assert.equal(packagedInterval.interval_commits, 53279);
assert.equal(packagedInterval.interval_source, "run-history");

const bcrAlias = site.master_status.rows.find((row) => row.issue === "pr168912");
assert.equal(bcrAlias.bcr.state, "completed", "V1 causal top-k12 rows map to BCR");
assert.equal(bcrAlias.legacy_lm.state, "not_run", "V1 causal top-k12 is not legacy LM");

const completedBcr = site.master_status.rows.find((row) => row.issue === "pr201444");
assert.equal(completedBcr.bcr.state, "completed");
assert.equal(completedBcr.bcr.steps, 15);
assert.equal(completedBcr.bcr.skips, 0);

const completedWeakGeneral = site.master_status.rows.find((row) => row.issue === "pr196244");
assert.equal(completedWeakGeneral.weak_general_heuristic.state, "completed");
assert.equal(completedWeakGeneral.weak_general_heuristic.steps, 18);
assert.equal(completedWeakGeneral.weak_general_heuristic.skips, 0);

const reconciledWeakGeneral = site.master_status.rows.find((row) => row.issue === "pr195788");
assert.equal(reconciledWeakGeneral.weak_general_heuristic.state, "completed");
assert.equal(reconciledWeakGeneral.weak_general_heuristic.steps, 14);
assert.equal(reconciledWeakGeneral.weak_general_heuristic.skips, 0);

const exactWeakGeneralOnMismatchRow = site.master_status.rows.find((row) => row.issue === "pr49535");
assert.equal(exactWeakGeneralOnMismatchRow.tuned_heuristic.state, "non_clean");
assert.equal(exactWeakGeneralOnMismatchRow.weak_general_heuristic.state, "completed");
assert.equal(exactWeakGeneralOnMismatchRow.weak_general_heuristic.steps, 11);
assert.equal(
  site.master_status.summary.methods.find((method) => method.key === "bcr").valid_results,
  18,
  "terminal BCR rows are included in valid-result totals",
);

const skipBlockedGit = site.master_status.rows.find((row) => row.issue === "pr199162");
assert.equal(skipBlockedGit.git.state, "non_clean");
assert.equal(skipBlockedGit.git.skips, 24);

const page = readFileSync(pagePath, "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
assert.match(page, /id="master-status-search"/);
assert.match(page, /id="master-status-body"/);
assert.match(page, /id="master-status-metrics"/);
assert.match(page, /Interval commits/);
assert.match(page, /automatically backfill/);
assert.match(page, /results\/master-status-report\.md/);
assert.match(app, /function renderMasterStatus/);
assert.match(app, /master-status-search/);
assert.match(app, /interval_commits/);

console.log("master status report presentation is complete");
