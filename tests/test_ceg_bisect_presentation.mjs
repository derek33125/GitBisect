import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const root = "human_analysis/human_analysis-20260905/human_analysis";
const index = readFileSync(`${root}/index.html`, "utf8");
const viewer = readFileSync(`${root}/viewer.js`, "utf8");
const standalone = readFileSync(`${root}/crash-to-gt-report.html`, "utf8");
const comparison = JSON.parse(readFileSync(`${root}/ceg-scoped10-comparison.json`, "utf8"));
const ablation = JSON.parse(readFileSync(`${root}/ablation-matrix.json`, "utf8"));
const master50Public = JSON.parse(
  readFileSync(`${root}/master50-ceg-evidence-results.json`, "utf8"),
);
const masterIssues = JSON.parse(readFileSync("benchmark-results/master50/issues.json", "utf8"));
const site = JSON.parse(readFileSync("web-presentation-data/data/site-data.json", "utf8"));

assert.equal(comparison.rows.length, 10);
assert.equal(comparison.aggregate.ceg.total_builds, 81);
assert.deepEqual(comparison.aggregate.deterministic_facts.ceg_wins_ties_losses, [7, 2, 1]);
assert.deepEqual(comparison.aggregate.bcr_5_parent.ceg_wins_ties_losses, [7, 0, 3]);
assert.deepEqual(comparison.aggregate.bcr_original.ceg_wins_ties_losses, [7, 2, 1]);
assert.deepEqual(comparison.aggregate.git_bisect.ceg_wins_ties_losses, [10, 0, 0]);
assert.equal(comparison.rows.reduce((sum, row) => sum + row.ceg_runner_builds, 0), 81);

for (const row of comparison.rows) {
  assert.equal(row.static_worst_rank, ablation.reference.perCase[row.issue]);
  const git = masterIssues.find((candidate) => candidate.issue === row.issue);
  assert.ok(git, `${row.issue} is missing from the master-50 issue data`);
  assert.equal(row.git_bisect_runner_builds, git.git_steps);
  assert.equal(row.git_bisect_skips, git.git_skips);

  const facts = site.deterministic_facts_bcr_v16_window.rows.find(
    (candidate) => candidate.issue === row.issue,
  );
  assert.equal(row.deterministic_facts_runner_builds, facts.v16_window.steps);

  const bcr = site.deterministic_facts_bcr.rows.find(
    (candidate) => candidate.issue === row.issue,
  );
  assert.equal(row.bcr_5_parent_runner_builds, bcr.parent_window.steps);

  const originalBcr = site.latest_bcr_comparison.rows.find(
    (candidate) => candidate.issue === row.issue,
  );
  assert.equal(row.bcr_original_runner_builds, originalBcr.bcr_single_parent.steps);
}

const comparisonByIssue = Object.fromEntries(comparison.rows.map((row) => [row.issue, row]));
assert.equal(comparisonByIssue.pr50304.ceg_runner_builds, 2);
assert.equal(comparisonByIssue.pr200987.ceg_runner_builds, 15);
assert.equal(comparison.extremes.pr50304.initial_program_prior_rank, 3);
assert.equal(comparison.extremes.pr200987.subsequent_good_builds, 4);
assert.equal(comparison.extremes.pr50304.trajectories.ceg.length, 2);
assert.equal(comparison.extremes.pr50304.trajectories.deterministic_facts_v16.length, 10);
assert.equal(comparison.extremes.pr50304.trajectories.bcr_original.length, 10);
assert.equal(comparison.extremes.pr200987.trajectories.ceg.length, 15);
assert.equal(comparison.extremes.pr200987.trajectories.deterministic_facts_v16.length, 10);
assert.equal(comparison.extremes.pr200987.trajectories.bcr_original.length, 10);

const llmStart = index.indexOf('id="strategy-llm"');
const llmEnd = index.indexOf('id="strategy-detail"');
assert.ok(llmStart >= 0 && llmEnd > llmStart, "the LLM strategy page is missing");
const llmPage = index.slice(llmStart, llmEnd);

assert.match(llmPage, /Causal Evidence-Guided Bisect（CEG-Bisect）/);
assert.doesNotMatch(llmPage, /\bv6\b/i);
assert.doesNotMatch(llmPage, /answer-free program prior/i);
assert.match(llmPage, /在任何构建之前，对未决区间内每个提交打一个程序证据分/);
assert.match(llmPage, /“人工 profile 不参与”的准确范围/);
assert.match(
  llmPage,
  /① 崩溃直接信号[\s\S]*?② \+ 推导目录[\s\S]*?③ \+ 锚点 · 消息 · 流水线 · 阶段/,
);
assert.match(llmPage, /good_commit[\s\S]*?bad_commit[\s\S]*?runner/);
assert.match(index, /这三层不是三份人工 profile/);
assert.match(llmPage, /修正后的 CEG 在线管线/);
assert.match(llmPage, /strict ordinal[\s\S]*?runner journal[\s\S]*?actual-parent validation/);
assert.match(llmPage, /完整 scoped-10 在线结果与静态排序对照/);
assert.match(llmPage, /pr204559[\s\S]*?86[\s\S]*?<strong>7<\/strong>[\s\S]*?8[\s\S]*?10[\s\S]*?17/);
assert.match(llmPage, /pr50304[\s\S]*?<strong>7<\/strong>[\s\S]*?<strong>2<\/strong>[\s\S]*?10[\s\S]*?9[\s\S]*?17/);
assert.match(llmPage, /pr200987[\s\S]*?14,344[\s\S]*?15[\s\S]*?<strong>10<\/strong>[\s\S]*?<strong>10<\/strong>[\s\S]*?20/);
assert.match(llmPage, /<strong>81<\/strong>[\s\S]*?102[\s\S]*?99[\s\S]*?108[\s\S]*?181/);
assert.match(llmPage, /Spearman[\s\S]*?0\.927[\s\S]*?Pearson[\s\S]*?0\.930/);
assert.match(llmPage, /九例保留原冻结运行[\s\S]*?pr200987[\s\S]*?15-build/);
assert.match(llmPage, /pr50304[\s\S]*?三种模型方法的选择轨迹[\s\S]*?确定性事实 v16[\s\S]*?原始 BCR · single parent/);
assert.match(llmPage, /pr200987[\s\S]*?三种模型方法的选择轨迹[\s\S]*?329ef60f3e21[\s\S]*?confidence 0\.94/);
assert.match(llmPage, /剩余 40 例的初始先验审计/);
assert.match(llmPage, /pr195788[\s\S]*?active_signal_count = 0/);
assert.match(llmPage, /pr200648[\s\S]*?pr204178[\s\S]*?pr65982/);
assert.match(llmPage, /ground truth 不进入 CEG 的在线召回、排序或探针选择/);
assert.doesNotMatch(llmPage, /其余八例仍在运行或排队/);

const resultsStart = index.indexOf('id="strategy-results"');
const resultsEnd = index.indexOf('id="strategy-llm"');
assert.ok(resultsStart >= 0 && resultsEnd > resultsStart, "the results strategy page is missing");
const resultsPage = index.slice(resultsStart, resultsEnd);
assert.match(index, /data-vs="results"/);
assert.match(resultsPage, /结果 · Master-50 对照 Git Bisect/);
assert.match(resultsPage, /不计入「搜索策略更差」/);
assert.match(resultsPage, /稍后回填/);
assert.match(resultsPage, /evidence-diverse-k12/);
assert.match(resultsPage, /区间大小/);
assert.match(resultsPage, /证据 LM first-bad/);
assert.match(resultsPage, /GT 最坏名次/);
assert.match(resultsPage, /id="result-rank"/);
assert.match(resultsPage, /Spearman 是 <strong>0\.58<\/strong>/);
assert.match(resultsPage, /pr52635[\s\S]*?<td class="mono">155<\/td>[\s\S]*?<td class="mono">3<\/td>/);
assert.match(resultsPage, /pr156249[\s\S]*?<td class="mono">2,006<\/td>[\s\S]*?<td class="mono">16<\/td>/);
assert.match(resultsPage, /pr50655[\s\S]*?<td class="mono">1<\/td>/);
assert.match(resultsPage, /pr195788[\s\S]*?无先验[\s\S]*?17/);
assert.match(resultsPage, /pr165445[\s\S]*?无先验/);
assert.match(resultsPage, /程序先验不应为空/);
assert.match(resultsPage, /active_signal_count = 0/);
assert.match(resultsPage, /pr199526[\s\S]*in_progress/);
assert.match(resultsPage, /pr199162[\s\S]*in_progress/);
assert.match(resultsPage, /pr204589[\s\S]*in_progress/);
assert.match(resultsPage, /id="result-case-pr52635"/);
assert.match(resultsPage, /Assertion `Symbol' failed/);
assert.match(resultsPage, /id="result-case-pr121365"/);
assert.match(resultsPage, /id="result-boundary"/);
assert.match(resultsPage, /id="result-case-pr167514"/);
assert.match(resultsPage, /adjacent-parent validation/);
assert.match(resultsPage, /a344db793aca/);
assert.match(resultsPage, /#83038/);
assert.match(resultsPage, /4a5ec3cec831/);
assert.match(resultsPage, /fd4f94ddbf0c/);
assert.match(resultsPage, /6003c3055a46/);
assert.match(resultsPage, /land → revert → re-commit/);
assert.match(resultsPage, /后来的 good 岛/);
assert.match(resultsPage, /Can't mangle a deduction guide name/);
assert.match(resultsPage, /Broken function found/);
assert.match(resultsPage, /EnumerateTweaks/);
assert.match(resultsPage, /1aadd4766e97/);
assert.match(resultsPage, /shufflevector[\s\S]*第 13 步才进 frontier/);
assert.match(viewer, /results: "strategy-results"/);
assert.match(viewer, /strategy-results"\)\?\.addEventListener\("click"/);
assert.match(standalone, /data-vs="results"/);
assert.match(standalone, /id="strategy-results"/);
assert.match(standalone, /id="result-case-pr52635"/);
assert.match(standalone, /id="result-case-pr167514"/);
assert.match(standalone, /4a5ec3cec831/);
assert.match(standalone, /id="result-rank"/);
assert.match(standalone, /无先验/);
assert.match(standalone, /GT 最坏名次/);
assert.match(standalone, /证据 LM first-bad/);

assert.equal(master50Public.schema, "master50-ceg-evidence-results-v1");
assert.equal(master50Public.issue_count, 50);
assert.equal(master50Public.rows.length, 50);
assert.equal(master50Public.coverage.ceg_available, 47);
assert.ok(master50Public.coverage.evidence_lm_bisect_available >= 31);
const pendingCeg = new Set(["pr199162", "pr199526", "pr204589"]);
const publicByIssue = Object.fromEntries(
  master50Public.rows.map((row) => [row.issue, row]),
);
for (const issue of masterIssues) {
  const row = publicByIssue[issue.issue];
  assert.ok(row, `${issue.issue} is missing from the public CEG result data`);
  assert.equal(typeof row.interval_commits, "number");
  assert.ok(row.interval_commits > 0);
  if (issue.interval_commits != null) {
    assert.equal(row.interval_commits, issue.interval_commits);
  }
  if (pendingCeg.has(issue.issue)) {
    assert.notEqual(row.ceg.availability, "available");
  } else {
    assert.equal(row.ceg.availability, "available");
  }
  assert.match(
    resultsPage,
    new RegExp(`${issue.issue}[\\s\\S]*?${row.interval_commits.toLocaleString("en-US")}`),
  );
}

for (const stage of [
  "evidence",
  "signals",
  "prior",
  "frontier",
  "retrieval",
  "llm",
  "fusion",
  "selector",
  "runner",
  "refresh",
]) {
  assert.match(llmPage, new RegExp(`data-llm-stage="${stage}"`), `${stage} stage is missing`);
  assert.match(viewer, new RegExp(`^\\s*${stage}: \\{`, "m"), `${stage} details are missing`);
}

assert.match(llmPage, /id="llm-stage-dialog"/);
assert.match(viewer, /immediate-parent transition/);
assert.match(viewer, /select_causal_retrieval_files\(\)/);
assert.match(viewer, /retrieve_causal_diff_evidence\(\)/);
assert.match(viewer, /mass_preserving_ordinal_fusion\(\)/);
assert.match(viewer, /build_pass_graph_path_signals\(\)/);
assert.match(viewer, /build_phase_path_signals\(\)/);
assert.match(viewer, /build_bad_tree_path_signals\(\)/);
assert.match(viewer, /ceg-bisect retrieval policy/);
assert.match(viewer, /不回退到 Master-50 或其他历史证据包/);
assert.match(viewer, /最多 5 个带距离标记的历史 transition/);
assert.match(viewer, /最终 CEG 不采用这条 fallback/);
assert.match(viewer, /CEG-Bisect 共 81 次 build/);
assert.match(viewer, /同一修正版重跑 paired scoped-10/);
assert.match(viewer, /strategyLlm\?\.addEventListener\("click"/);

assert.match(standalone, /CEG-Bisect 端到端架构/);
assert.match(standalone, /const LLM_STAGE_DETAILS = \{/);
assert.match(standalone, /十例结果完整仍不等于同一修正版已完成确认性验证/);
assert.match(standalone, /完整 scoped-10 在线结果与静态排序对照/);
assert.match(standalone, /Spearman[\s\S]*?0\.927/);
assert.match(standalone, /剩余 40 例的初始先验审计/);
assert.match(standalone, /trajectory-step bad/);
assert.doesNotMatch(standalone, /href="viewer\.css"/);
assert.doesNotMatch(standalone, /src="viewer\.js"/);

console.log("CEG-Bisect interactive presentation is complete");
