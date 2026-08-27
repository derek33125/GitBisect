import { existsSync, readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const comparisonPath = "web-presentation-data/scoped10/data/keyword-ablation.json";
const maintenanceControlPath = "web-presentation-data/scoped10/data/weak-maintenance-keyword-control.json";
assert.ok(existsSync(comparisonPath), "keyword-ablation comparison data is missing");
assert.ok(existsSync(maintenanceControlPath), "maintenance keyword control metadata is missing");

const comparison = JSON.parse(readFileSync(comparisonPath, "utf8"));
assert.equal(comparison.rows.length, 10, "the ablation must cover the scoped 10 cases");
assert.equal(comparison.aggregate.specific_keyword.avg_steps, 12.3);
assert.equal(comparison.aggregate.general_keyword.avg_steps, 13.2);
assert.equal(comparison.aggregate.first_bad_exact_matches, 9);

const maintenance = JSON.parse(readFileSync(maintenanceControlPath, "utf8"));
assert.equal(maintenance.status, "complete");
assert.deepEqual(maintenance.keywords, [
  "add",
  "update",
  "change",
  "test",
  "support",
  "cleanup",
  "refactor",
  "rename",
  "remove",
  "document",
]);
assert.equal(maintenance.queues.length, 3);
assert.equal(maintenance.completed_clean_issues.length, 10);
assert.ok(maintenance.completed_clean_issues.every((row) => row.skips === 0));
assert.equal(maintenance.completed_clean_issues.find((row) => row.issue === "pr204559").steps, 12);
assert.ok(maintenance.superseded_remote_runs, "superseded vocabulary state must be documented");
assert.deepEqual(
  maintenance.queues.map((queue) => queue.label),
  [
    "aws10-weak-maint-v2-a-20260714a",
    "aws10-weak-maint-v2-b-20260714a",
    "aws10-weak-maint-v2-c-20260714a",
  ]
);

for (const row of comparison.rows) {
  assert.equal(row.specific_keyword.status, "clean");
  assert.equal(row.general_keyword.status, "clean");
  assert.equal(row.specific_keyword.skips, 0);
  assert.equal(row.general_keyword.skips, 0);
}

const reapply = comparison.rows.find((row) => row.issue === "pr49535");
assert.ok(reapply, "pr49535 is missing from the ablation");
assert.equal(reapply.first_bad_exact_match, false);
assert.match(reapply.note, /reapply/i);

const sitePath = "web-presentation-data/data/site-data.json";
assert.ok(existsSync(sitePath), "generated site data is missing");
const site = JSON.parse(readFileSync(sitePath, "utf8"));
assert.equal(site.keyword_ablation.aggregate.general_keyword.avg_steps, 13.2);
assert.equal(site.weak_maintenance_keyword_control.status, "complete");
assert.equal(site.issues[0].comparison.specific_heuristic.status, "clean");
assert.equal(site.issues[0].comparison.general_heuristic.status, "clean");
assert.equal(site.aggregate.specific_heuristic.count, 10);
assert.equal(site.aggregate.specific_heuristic.avg, 12.3);
assert.equal(site.aggregate.general_heuristic.count, 10);
assert.equal(site.aggregate.general_heuristic.avg, 13.2);

const html = readFileSync("web-presentation-data/index.html", "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
assert.match(html, /Issue-specific heuristic/);
assert.match(html, /Fixed-vocabulary heuristic/);
assert.match(app, /tuned-heuristic/);
assert.match(app, /general-heuristic/);
assert.match(app, /weak_maintenance_keyword_control/);
assert.match(app, /Maintenance-keyword control/);

console.log("keyword ablation presentation data is complete");
