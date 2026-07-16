import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const html = readFileSync("web-presentation-data/index.html", "utf8");
const runtime = readFileSync("web-presentation-data/runtime.html", "utf8");
const explorer = readFileSync("web-presentation-data/explorer.html", "utf8");
const focusedResults = readFileSync("web-presentation-data/focused-results.html", "utf8");
const css = readFileSync("web-presentation-data/styles.css", "utf8");
const app = readFileSync("web-presentation-data/app.js", "utf8");
const site = JSON.parse(readFileSync("web-presentation-data/data/site-data.json", "utf8"));

assert.match(html, /href="\.\/runtime\.html"/, "overview must link to runtime page");
assert.match(html, /href="\.\/explorer\.html"/, "overview must link to explorer page");
assert.match(html, /href="\.\/focused-results\.html"/, "overview must link to focused comparison page");
assert.match(focusedResults, /Top-k sensitivity/, "focused page must show top-k sensitivity");
assert.match(focusedResults, /Keyword robustness/, "focused page must show keyword robustness");
assert.match(focusedResults, /id="topk-body"/, "focused page needs a top-k result table");
assert.match(focusedResults, /id="keyword-body"/, "focused page needs a keyword result table");
assert.match(focusedResults, /id="keyword-vocab"/, "focused page must show keyword vocabularies");
assert.match(focusedResults, /Convergence by issue/, "focused page must show per-issue convergence charts");
assert.match(focusedResults, /id="convergence-charts"/, "focused page needs a convergence chart container");
assert.match(runtime, /LM-bisect runtime architecture/, "runtime architecture heading is missing");
assert.match(runtime, /What enters and leaves the LLM/, "agent I\/O heading is missing");
assert.match(runtime, /semantic_score/, "model score JSON field is missing");
assert.match(runtime, /build_success_prob/, "model build-probability JSON field is missing");
assert.match(runtime, /run-history\.json/, "persisted run-history artifact is missing");
assert.match(runtime, /Per-step artifacts written by the pipeline/, "runtime artifacts heading is missing");
assert.match(runtime, /runtime-example/, "runtime page must render an actual step example");
assert.match(runtime, /paper-architecture/, "runtime page must include the paper-style architecture figure");
assert.match(explorer, /Step-by-step trace explorer/, "explorer heading is missing");
assert.match(explorer, /id="issue-select"/, "explorer issue selector is missing");
assert.match(css, /\.architecture-canvas/, "architecture graph styles are missing");
assert.match(css, /\.paper-architecture/, "paper architecture styles are missing");
assert.match(css, /\.agent-io-grid/, "agent I\/O styles are missing");
assert.match(css, /\.runtime-artifact-grid/, "runtime artifact styles are missing");
assert.match(css, /\.artifact-json/, "artifact JSON example styles are missing");
assert.match(app, /function sameCommit\(left, right\)/, "focused tables must normalize short and full SHA values");
assert.equal(site.runtime_example.issue, "pr204559", "runtime example must use a real stored model trace");
assert.equal(site.runtime_example.step.verdict, "good", "runtime example runner verdict is missing");
assert.ok(site.runtime_example.step.diff_summary, "runtime example LLM diff output is missing");
assert.ok(site.runtime_example.artifacts.length >= 4, "runtime artifact examples are missing");
assert.ok(
  site.runtime_example.artifacts.every((artifact) => {
    if (!artifact.example) return false;
    try {
      JSON.parse(artifact.example);
      return true;
    } catch {
      return false;
    }
  }),
  "runtime artifacts must show JSON examples rather than filesystem paths"
);
assert.equal(site.live_lanes.parent_llm_topk20.clean.length, 10, "latest top-k20 clean result count is stale");
assert.equal(site.focused_comparisons.topk.rows.length, 10, "top-k comparison must cover scoped 10");
assert.equal(site.focused_comparisons.topk.aggregate.topk3.avg_steps, 11);
assert.equal(site.focused_comparisons.topk.aggregate.topk10.avg_steps, 12.2);
assert.equal(site.focused_comparisons.topk.aggregate.topk20.avg_steps, 11.4);
assert.equal(site.focused_comparisons.topk.aggregate.topk20.first_bad_matches, 9);
assert.equal(site.focused_comparisons.topk.aggregate.topk10.skip_rows, 1);
assert.equal(site.focused_comparisons.convergence.rows.length, 10, "convergence charts must cover scoped 10");
for (const row of site.focused_comparisons.convergence.rows) {
  for (const key of ["topk3", "topk10", "topk20"]) {
    const curve = row.curves[key];
    assert.ok(curve.points.length >= 2, `${row.issue} ${key} must include start and runner steps`);
    assert.equal(curve.points[0].step, 0, `${row.issue} ${key} must start at step zero`);
    assert.ok(curve.points[0].remaining > 1, `${row.issue} ${key} must retain the initial interval size`);
    assert.equal(curve.points.at(-1).remaining, 1, `${row.issue} ${key} must end at one candidate`);
  }
}
assert.equal(site.focused_comparisons.keywords.rows.length, 10, "keyword comparison must cover scoped 10");
assert.equal(site.focused_comparisons.keywords.aggregate.best_parent_llm.avg_steps, 11);
assert.deepEqual(site.focused_comparisons.keywords.weak_maintenance.terms, [
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

console.log("web presentation architecture content is present");
