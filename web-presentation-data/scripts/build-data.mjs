// Build a single self-contained site-data.json for the presentation web page.
//
// It reads:
//   - scoped10/data/preferred-comparison.json  (which run represents each existing method)
//   - scoped10/data/keyword-ablation.json     (completed tuned vs crash-general rows)
//   - scoped10/data/weak-maintenance-keyword-control.json (pending weak control)
//   - scoped10/raw/{aws,edu}/*.json            (full per-step run histories)
//   - ../tools/lm_bisect_profiles.json         (issue crash context) [optional]
//
// It emits:
//   - data/site-data.json
//
// The output is fully standalone: the deployed site never touches the rest of
// the repo. Run this whenever the underlying runs change:
//   node scripts/build-data.mjs

import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(ROOT, "..");

const SCOPED = join(ROOT, "scoped10");
const RAW_DIRS = [join(SCOPED, "raw", "aws"), join(SCOPED, "raw", "edu")];
const OUT_DIR = join(ROOT, "data");
const OUT_FILE = join(OUT_DIR, "site-data.json");
const LIVE_LANES_FILE = join(SCOPED, "data", "current-lanes.json");

// Methods we surface a full step-by-step trace for. Git bisect has no per-step
// run history here (it is the external baseline), so it is handled separately.
const TRACE_METHODS = [
  {
    key: "tuned-heuristic",
    label: "Issue-specific heuristic",
    short: "Issue-specific heuristic",
    family: "heuristic",
  },
  {
    key: "general-heuristic",
    label: "Fixed-vocabulary heuristic",
    short: "Fixed-vocabulary heuristic",
    family: "heuristic",
  },
  {
    key: "parent-llm-topk3",
    label: "Parent-diff + LLM extraction (top-k3)",
    short: "Parent-diff + LLM",
    family: "model",
  },
];

const TOPK_RUN_LABELS = {
  topk3: {
    pr204559: "edu-scoped10-parent-extract-topk3-rerun-a-20260707a",
    pr204589: "aws-scoped10-parent-extract-topk3-rerun-a-20260707a",
    pr201444: "aws-scoped10-parent-extract-topk3-rerun-a-20260707a",
    pr193164: "edu-scoped10-parent-extract-topk3-rerun-a-20260707a",
    pr50304: "aws-scoped10-parent-extract-topk3-rerun-a-20260707a",
    pr50585: "aws-scoped10-parent-extract-topk3-rerun-b-20260707a",
    pr48154: "aws-scoped10-parent-extract-topk3-rerun-b-20260707a",
    pr49535: "aws-scoped10-parent-extract-topk3-rerun-b-20260707a",
    pr52635: "edu-scoped10-parent-extract-topk3-rerun-b-20260707a",
    pr200987: "aws-scoped10-parent-extract-topk3-rerun-b-20260707a",
  },
  topk10: {
    pr204559: "aws10-parent-llm600k-topk10-a-20260715a",
    pr204589: "aws10-parent-llm600k-topk10-retry-pr204589-20260715b",
    pr201444: "aws10-parent-llm600k-topk10-final-a-20260715c",
    pr193164: "aws10-parent-llm600k-topk10-final-b-20260715c",
    pr50304: "aws10-parent-llm600k-topk10-b-20260715a",
    pr50585: "aws10-parent-llm600k-topk10-b-20260715a",
    pr48154: "aws10-parent-llm600k-topk10-b-20260715a",
    pr49535: "aws10-parent-llm600k-topk10-c-20260715a",
    pr52635: "aws10-parent-llm600k-topk10-c-20260715a",
    pr200987: "aws10-parent-llm600k-topk10-final-c-20260715c",
  },
  topk20: {
    pr204559: "edu10-parent-llm600k-topk20-ratio4-20260711a",
    pr204589: "edu10-parent-llm600k-topk20-hardened-a-20260715d",
    pr201444: "edu10-parent-llm600k-topk20-hardened-b-20260715d",
    pr193164: "edu10-parent-llm600k-topk20-retry-c-20260713a",
    pr50304: "aws10-parent-llm600k-topk20-dirfix2-c-20260710b",
    pr50585: "aws10-parent-llm600k-topk20-dirfix2-c-20260710b",
    pr48154: "edu10-parent-llm600k-topk20-dirfix2-d-20260710b",
    pr49535: "edu10-parent-llm600k-topk20-dirfix2-d-20260710b",
    pr52635: "edu10-parent-llm600k-topk20-dirfix2-e-20260710b",
    pr200987: "aws10-parent-llm600k-topk20-hardened-b-20260715d",
  },
};

const GENERAL_CRASH_TERMS = [
  "crash",
  "assertion",
  "abort",
  "segfault",
  "ice",
  "internal compiler error",
  "diagnostic",
  "parser",
  "sema",
  "ast",
  "ir",
  "optimizer",
  "analysis",
  "codegen",
  "lowering",
  "vectorizer",
  "loop",
  "target",
];

function readJson(p) {
  return JSON.parse(readFileSync(p, "utf8"));
}

function shortSha(sha) {
  return typeof sha === "string" ? sha.slice(0, 12) : sha;
}

function sameCommit(left, right) {
  return shortSha(left) === shortSha(right);
}

function isCompletedFirstBadRun(raw) {
  return (
    raw &&
    raw.status === "completed" &&
    typeof raw.first_bad_commit === "string" &&
    raw.first_bad_commit.length > 0 &&
    Array.isArray(raw.final_unresolved_window) &&
    raw.final_unresolved_window.length === 1
  );
}

function average(values) {
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 100) / 100;
}

function median(values) {
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

function geometricMean(values) {
  if (!values.length) return null;
  return Math.exp(values.reduce((sum, value) => sum + Math.log(value), 0) / values.length);
}

function stepAtOrBelow(run, threshold) {
  const point = run.curve.points.find((entry) => entry.step > 0 && entry.remaining <= threshold);
  return point ? point.step : null;
}

function buildTopkSensitivity(topkRows) {
  const referenceKey = "topk3";
  const comparableKeys = ["topk10", "topk20"];
  const pairRows = topkRows.map((row) => {
    const topk3 = row.topk3;
    const topk10 = row.topk10;
    const topk20 = row.topk20;
    const initial = topk10.curve.initial_unresolved;
    const first3 = topk3.curve.points[1].remaining / initial;
    const first10 = topk10.curve.points[1].remaining / initial;
    const first20 = topk20.curve.points[1].remaining / initial;
    const toTenPercent3 = stepAtOrBelow(topk3, initial * 0.1);
    const toTenPercent10 = stepAtOrBelow(topk10, initial * 0.1);
    const toTenPercent20 = stepAtOrBelow(topk20, initial * 0.1);
    const to32_3 = stepAtOrBelow(topk3, 32);
    const to32_10 = stepAtOrBelow(topk10, 32);
    const to32_20 = stepAtOrBelow(topk20, 32);
    return {
      issue: row.issue,
      topk3_steps: topk3.steps,
      topk10_steps: topk10.steps,
      topk20_steps: topk20.steps,
      step_delta_topk20_minus_topk10: topk20.steps - topk10.steps,
      topk3_first_remaining_ratio: round(first3),
      topk10_first_remaining_ratio: round(first10),
      topk20_first_remaining_ratio: round(first20),
      first_step_ratio_delta_topk20_minus_topk10: round(first20 - first10),
      topk3_to_ten_percent: toTenPercent3,
      topk10_to_ten_percent: toTenPercent10,
      topk20_to_ten_percent: toTenPercent20,
      topk3_to_32: to32_3,
      topk10_to_32: to32_10,
      topk20_to_32: to32_20,
      topk3_matches_canonical: sameCommit(topk3.first_bad, row.canonical_first_bad),
      first_bad_agrees: sameCommit(topk10.first_bad, topk20.first_bad),
    };
  });
  const pairStepDeltas = pairRows.map((row) => row.step_delta_topk20_minus_topk10);
  const firstRatios = {};
  const perStepRatios = {};
  const lateStepRatios = {};
  for (const key of comparableKeys) {
    const runs = topkRows.map((row) => row[key]);
    firstRatios[key] = average(
      runs.map((run) => run.curve.points[1].remaining / run.curve.initial_unresolved)
    );
    const allRatios = [];
    const lateRatios = [];
    for (const run of runs) {
      const runnerPoints = run.curve.points.slice(1);
      for (const point of runnerPoints) {
        const previous = run.curve.points[point.step - 1];
        if (point.verdict !== "skip") allRatios.push(point.remaining / previous.remaining);
      }
      for (const point of runnerPoints.slice(-4)) {
        const previous = run.curve.points[point.step - 1];
        if (point.verdict !== "skip") lateRatios.push(point.remaining / previous.remaining);
      }
    }
    perStepRatios[key] = round(geometricMean(allRatios));
    lateStepRatios[key] = round(geometricMean(lateRatios));
  }
  const topk20StepWins = pairStepDeltas.filter((value) => value < 0).length;
  const topk10StepWins = pairStepDeltas.filter((value) => value > 0).length;
  const stepTies = pairStepDeltas.filter((value) => value === 0).length;
  const referenceRuns = topkRows.map((row) => row[referenceKey]);
  const referenceRatios = [];
  const referenceLateRatios = [];
  for (const run of referenceRuns) {
    const runnerPoints = run.curve.points.slice(1);
    for (const point of runnerPoints) {
      const previous = run.curve.points[point.step - 1];
      if (point.verdict !== "skip") referenceRatios.push(point.remaining / previous.remaining);
    }
    for (const point of runnerPoints.slice(-4)) {
      const previous = run.curve.points[point.step - 1];
      if (point.verdict !== "skip") referenceLateRatios.push(point.remaining / previous.remaining);
    }
  }
  return {
    evidence_scope:
      "The controlled frontier-size comparison is top-k10 versus top-k20: both use parent diffs, LLM extraction, trace-only observations, calibrated-posterior selection, and the 600k raw-diff cap. Top-k3 is shown separately because it used the earlier extraction revision.",
    operational_default: "topk10",
    recommendation:
      "Use top-k10 as the present operational default. It fits one 12-candidate scoring prompt, preserves canonical first-bad agreement on all 10 scoped cases, and is materially cheaper. Top-k20 is the fastest observed 600k setting, but it crosses the 12-item scoring-batch boundary and has one alternate apply/reapply boundary; treat it as a promising experimental setting, not a settled default.",
    limitations: [
      "Top-k3 is included as an archived pre-600k reference, not as a controlled frontier-size comparison with top-k10/top-k20.",
      "Each top-k value changes the model-scored subset before calibrated-posterior selection; it is not only a context-budget parameter.",
      "Top-k20 uses two independent scoring prompts because the scorer batches at 12 candidates. Scores from separate prompts are not guaranteed to share a calibrated scale.",
      "The cache key is per candidate and diff mode, not per frontier cohort or observation state. Recorded token usage is therefore a lower bound when prior runs populate the cache.",
      "This is a fixed 10-issue exploratory set. The observed 0.8-step top-k20 advantage is directional, not statistically conclusive.",
    ],
    pre600k_reference: {
      key: referenceKey,
      label: "top-k3 (pre-600k reference)",
      extraction_revision: "12k raw-diff cap",
      avg_steps: average(referenceRuns.map((run) => run.steps)),
      median_steps: median(referenceRuns.map((run) => run.steps)),
      first_bad_matches: pairRows.filter((row) => row.topk3_matches_canonical).length,
      mean_first_step_remaining_ratio: average(
        referenceRuns.map((run) => run.curve.points[1].remaining / run.curve.initial_unresolved)
      ),
      geometric_mean_per_step_remaining_ratio: round(geometricMean(referenceRatios)),
      geometric_mean_last_four_step_remaining_ratio: round(geometricMean(referenceLateRatios)),
    },
    comparable_pair: {
      left: "topk10",
      right: "topk20",
      same_600k_extraction: true,
      same_first_bad: pairRows.filter((row) => row.first_bad_agrees).length,
      topk20_step_wins: topk20StepWins,
      topk10_step_wins: topk10StepWins,
      step_ties: stepTies,
      average_step_delta_topk20_minus_topk10: average(pairStepDeltas),
      mean_first_step_remaining_ratio: firstRatios,
      geometric_mean_per_step_remaining_ratio: perStepRatios,
      geometric_mean_last_four_step_remaining_ratio: lateStepRatios,
      scoring_batch_size: 12,
      topk10_scoring_batches_per_step: 1,
      topk20_scoring_batches_per_step: 2,
    },
    rows: pairRows,
  };
}

// Index every raw file by basename so we can resolve run labels quickly.
function indexRawFiles() {
  const idx = [];
  for (const dir of RAW_DIRS) {
    if (!existsSync(dir)) continue;
    for (const name of readdirSync(dir)) {
      if (name.endsWith(".json")) idx.push({ name, path: join(dir, name) });
    }
  }
  return idx;
}

function findRawFile(rawIdx, issue, runLabel) {
  if (!runLabel) return null;
  const hit = rawIdx.find(
    (f) => f.name.startsWith(issue + "-") && f.name.endsWith(runLabel + ".json")
  );
  return hit ? hit.path : null;
}

// Keep the LLM diff-reasoning readable but bounded so the bundle stays small.
function clip(text, max) {
  if (typeof text !== "string") return text;
  if (text.length <= max) return text;
  return text.slice(0, max).trimEnd() + " ...";
}

function compactCandidate(c, isModel) {
  const out = {
    rank: c.rank,
    sha: shortSha(c.sha),
    subject: c.subject,
    semantic_score: round(c.semantic_score),
    build_success_prob: round(c.build_success_prob),
    posterior_bad_mass: round(c.calibrated_posterior_bad_mass ?? c.posterior_bad_mass),
    info_gain: round(c.calibrated_posterior_info_gain),
  };
  if (isModel && c.diff_summary) out.diff_summary = clip(c.diff_summary, 900);
  return out;
}

function round(n) {
  if (typeof n !== "number" || Number.isNaN(n)) return n;
  return Math.round(n * 1000) / 1000;
}

function compactStep(step, isModel) {
  const sel = step.selection || {};
  const out = {
    step: step.step,
    sha: shortSha(step.sha),
    subject: step.subject,
    verdict: step.verdict,
    source: step.source,
    unresolved_before: step.unresolved_before,
    unresolved_after: step.unresolved_after,
    semantic_score: round(sel.semantic_score),
    build_success_prob: round(sel.build_success_prob),
    posterior_bad_mass: round(sel.calibrated_posterior_bad_mass ?? sel.posterior_bad_mass),
    info_gain: round(sel.calibrated_posterior_info_gain),
    evidence: Array.isArray(sel.evidence) ? sel.evidence : [],
  };
  if (isModel && sel.diff_summary) out.diff_summary = clip(sel.diff_summary, 1600);
  if (Array.isArray(step.top_candidates)) {
    out.top_candidates = step.top_candidates.slice(0, 3).map((c) => compactCandidate(c, isModel));
  }
  const repro = (step.evidence || []).find((l) => /repro exit code/.test(l));
  if (repro) out.repro_exit = repro.replace(/.*repro exit code:\s*/, "").trim();
  return out;
}

function buildTrace(rawPath, isModel) {
  const raw = readJson(rawPath);
  const steps = (raw.steps || []).map((s) => compactStep(s, isModel));
  let firstBad = null;
  for (const s of raw.steps || []) {
    if (s.verdict === "bad") firstBad = shortSha(s.sha);
  }
  return {
    scorer: raw.scorer,
    model_name: raw.model_name,
    model_top_k: raw.model_top_k,
    diff_mode: raw.model_diff_mode,
    diff_extraction: raw.model_diff_extraction,
    good_commit: shortSha(raw.good_commit),
    bad_commit: shortSha(raw.bad_commit),
    initial_unresolved: raw.initial_unresolved,
    status: raw.status,
    total_steps: (raw.steps || []).length,
    last_first_bad: firstBad,
    steps,
  };
}

function buildRuntimeExample(rawIdx, profiles) {
  const issue = "pr204559";
  const runLabel = "aws-scoped10-parent-extract-topk3-rerun-a-20260707a";
  const rawPath = findRawFile(rawIdx, issue, runLabel);
  if (!rawPath) {
    return null;
  }

  const raw = readJson(rawPath);
  const step = raw.steps?.[0];
  if (!step) {
    return null;
  }

  const profile = profiles[issue] || {};
  const selection = step.selection || {};
  const runnerSignals = (step.evidence || []).filter((line) =>
    /repro exit code|verdict:|build type|assertions:/i.test(line)
  );
  const selectedCacheEntry = {
    sha: shortSha(step.sha),
    diff_mode: raw.model_diff_mode,
    diff_extraction: raw.model_diff_extraction,
    semantic_score: round(selection.semantic_score),
    build_success_prob: round(selection.build_success_prob),
    diff_summary: clip(selection.diff_summary || "", 320),
  };
  return {
    issue,
    title: profile.title || issue,
    crash_summary: profile.bug_report_summary || null,
    keywords: profile.keywords || [],
    relevant_paths: profile.relevant_paths || [],
    interval: {
      good_commit: shortSha(raw.good_commit),
      bad_commit: shortSha(raw.bad_commit),
      initial_unresolved: raw.initial_unresolved,
    },
    method: {
      run_label: raw.run_label,
      model_name: raw.model_name,
      model_top_k: raw.model_top_k,
      diff_mode: raw.model_diff_mode,
      diff_extraction: raw.model_diff_extraction,
    },
    step: {
      number: step.step,
      selected_sha: shortSha(step.sha),
      subject: step.subject,
      verdict: step.verdict,
      unresolved_before: step.unresolved_before,
      unresolved_after: step.unresolved_after,
      diff_summary: selection.diff_summary || null,
      semantic_score: round(selection.semantic_score),
      build_success_prob: round(selection.build_success_prob),
      posterior_bad_mass: round(
        selection.calibrated_posterior_bad_mass ?? selection.posterior_bad_mass
      ),
      info_gain: round(selection.calibrated_posterior_info_gain),
      evidence: Array.isArray(selection.evidence) ? selection.evidence.slice(0, 3) : [],
      runner_evidence: Array.isArray(step.evidence) ? step.evidence.slice(0, 12) : [],
      runner_log_excerpt: clip(step.log_excerpt || step.trace_excerpt || "", 1500),
    },
    artifacts: [
      {
        name: "observations.json",
        role: "runner verdict and captured reproducer evidence for the tested SHA",
        example: JSON.stringify(
          {
            sha: shortSha(step.sha),
            verdict: step.verdict,
            source: step.source,
            evidence: runnerSignals,
          },
          null,
          2
        ),
      },
      {
        name: "run-history.json",
        role: "the selected SHA, its interval effect, and the policy inputs that selected it",
        example: JSON.stringify(
          {
            step: step.step,
            sha: shortSha(step.sha),
            verdict: step.verdict,
            unresolved_before: step.unresolved_before,
            unresolved_after: step.unresolved_after,
            selection: {
              semantic_score: round(selection.semantic_score),
              build_success_prob: round(selection.build_success_prob),
              posterior_bad_mass: round(
                selection.calibrated_posterior_bad_mass ?? selection.posterior_bad_mass
              ),
              info_gain: round(selection.calibrated_posterior_info_gain),
            },
          },
          null,
          2
        ),
      },
      {
        name: "unresolved-window.json",
        role: "the ordered final unresolved window used for a safe resume",
        example: JSON.stringify(raw.final_unresolved_window || [], null, 2),
      },
      {
        name: "model-cache.json entry",
        role: "cached LLM extraction and scores for this SHA and diff mode",
        example: JSON.stringify(selectedCacheEntry, null, 2),
      },
    ],
  };
}

function loadProfiles() {
  const p = join(REPO_ROOT, "tools", "lm_bisect_profiles.json");
  if (!existsSync(p)) return {};
  try {
    return readJson(p);
  } catch {
    return {};
  }
}

function loadFocusedRun(rawIdx, issue, runLabel) {
  const rawPath = findRawFile(rawIdx, issue, runLabel);
  if (!rawPath) throw new Error(`missing focused comparison history for ${issue}: ${runLabel}`);
  const raw = readJson(rawPath);
  if (!isCompletedFirstBadRun(raw)) {
    throw new Error(`focused comparison history did not resolve one first-bad commit: ${issue}: ${runLabel}`);
  }
  return {
    steps: raw.steps.length,
    first_bad: raw.first_bad_commit,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    source: rawPath.includes("/edu/") ? "edu-server" : "aws-server",
    run_label: raw.run_label,
    curve: {
      initial_unresolved: raw.initial_unresolved,
      points: [
        { step: 0, remaining: raw.initial_unresolved, verdict: "start" },
        ...raw.steps.map((step) => ({
          step: step.step,
          remaining: step.unresolved_after,
          verdict: step.verdict,
        })),
      ],
    },
  };
}

function buildFocusedComparisons(preferred, keywordAblation, weakControl, liveLanes, profiles, rawIdx) {
  const canonicalFirstBad = new Map();
  for (const row of preferred) {
    canonicalFirstBad.set(row.issue, shortSha(row["parent-llm-topk3_first_bad"]));
  }
  const topk20ByIssue = new Map(
    (liveLanes?.parent_llm_topk20?.clean || []).map((row) => [row.issue, row])
  );

  const topkRows = preferred.map((base) => {
    const topk3 = loadFocusedRun(rawIdx, base.issue, base["parent-llm-topk3_run_label"]);
    if (topk3.steps !== base["parent-llm-topk3_steps"]) {
      throw new Error(`top-k3 step mismatch for ${base.issue}`);
    }
    topk3.source = base["parent-llm-topk3_source"];
    const topk10 = loadFocusedRun(rawIdx, base.issue, TOPK_RUN_LABELS.topk10[base.issue]);
    const topk20Snapshot = topk20ByIssue.get(base.issue);
    if (!topk20Snapshot) throw new Error(`missing top-k20 snapshot row for ${base.issue}`);
    const topk20 = loadFocusedRun(rawIdx, base.issue, TOPK_RUN_LABELS.topk20[base.issue]);
    if (topk20.steps !== topk20Snapshot.steps) {
      throw new Error(`top-k20 step mismatch for ${base.issue}`);
    }
    topk20.source = topk20Snapshot.source;
    const canonical = canonicalFirstBad.get(base.issue);
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      git: { steps: base["git-bisect_steps"] },
      topk3,
      topk10,
      topk20,
      canonical_first_bad: canonical,
      topk3_matches: shortSha(topk3.first_bad) === canonical,
      topk10_matches: shortSha(topk10.first_bad) === canonical,
      topk20_matches: shortSha(topk20.first_bad) === canonical,
    };
  });

  const topkAggregate = {};
  for (const key of ["topk3", "topk10", "topk20"]) {
    const rows = topkRows.map((row) => row[key]);
    const steps = rows.map((row) => row.steps);
    topkAggregate[key] = {
      count: rows.length,
      avg_steps: average(steps),
      median_steps: median(steps),
      skip_rows: rows.filter((row) => row.skips > 0).length,
      first_bad_matches: topkRows.filter((row) => row[`${key}_matches`]).length,
    };
  }

  const ablationByIssue = new Map(keywordAblation.rows.map((row) => [row.issue, row]));
  const weakByIssue = new Map(weakControl.completed_clean_issues.map((row) => [row.issue, row]));
  const bestParent = "topk3";
  const keywordRows = preferred.map((base) => {
    const ablation = ablationByIssue.get(base.issue);
    const weak = weakByIssue.get(base.issue);
    if (!ablation || !weak) throw new Error(`missing keyword control row for ${base.issue}`);
    const parent = {
      steps: base["parent-llm-topk3_steps"],
      first_bad: shortSha(base["parent-llm-topk3_first_bad"]),
      skips: 0,
      source: base["parent-llm-topk3_source"],
      run_label: base["parent-llm-topk3_run_label"],
    };
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      git: { steps: base["git-bisect_steps"] },
      best_parent_llm: parent,
      issue_specific: ablation.specific_keyword,
      shared_crash: ablation.general_keyword,
      weak_maintenance: weak,
      canonical_first_bad: canonicalFirstBad.get(base.issue),
      note: ablation.note,
    };
  });

  const keywordAggregate = {};
  for (const key of ["best_parent_llm", "issue_specific", "shared_crash", "weak_maintenance"]) {
    const rows = keywordRows.map((row) => row[key]);
    const steps = rows.map((row) => row.steps);
    keywordAggregate[key] = {
      count: rows.length,
      avg_steps: average(steps),
      median_steps: median(steps),
      first_bad_matches: rows.filter(
        (row, index) => shortSha(row.first_bad) === keywordRows[index].canonical_first_bad
      ).length,
    };
  }

  return {
    topk: {
      best_configuration: "top-k3 (pre-600k reference)",
      comparison_note:
      "All rows are completed parent-diff + LLM-extraction runs. Top-k3 is a pre-600k reference; top-k10 and top-k20 use the 600k raw-diff cap, so frontier size and extraction revision both differ. The top-k20 rows use the canonical completed live snapshot because it records the preferred successful retry for each issue.",
      rows: topkRows,
      aggregate: topkAggregate,
      sensitivity: buildTopkSensitivity(topkRows),
    },
    convergence: {
      label: "Parent-diff + LLM extraction remaining candidate window",
      y_axis: "unresolved commits remaining (log scale)",
      note: "The line charts use the exact completed run history selected for each top-k row. Git bisect is omitted because the scoped archive retains only its final build-step total, not a comparable per-step unresolved-window history.",
      rows: topkRows.map((row) => ({
        issue: row.issue,
        title: row.title,
        curves: {
          topk3: row.topk3.curve,
          topk10: row.topk10.curve,
          topk20: row.topk20.curve,
        },
      })),
    },
    keywords: {
      best_parent_configuration: "parent-diff + LLM extraction top-k3",
      comparison_note:
        "The keyword variants share the same endpoints, runner, calibrated-posterior policy, and build settings. Only the keyword vocabulary changes. The model row is the completed top-k3 parent-diff reference, not a per-case oracle selection.",
      issue_specific: {
        definition: "Profile-authored crash keywords, shown per issue in the table.",
        terms_by_issue: Object.fromEntries(preferred.map((row) => [row.issue, profiles[row.issue]?.keywords || []])),
      },
      shared_crash: {
        definition: keywordAblation.general_keyword_definition,
        terms: GENERAL_CRASH_TERMS,
      },
      weak_maintenance: {
        definition: weakControl.definition,
        terms: weakControl.keywords,
      },
      rows: keywordRows,
      aggregate: keywordAggregate,
    },
  };
}

function main() {
  const preferred = readJson(join(SCOPED, "data", "preferred-comparison.json"));
  const keywordAblation = readJson(join(SCOPED, "data", "keyword-ablation.json"));
  const weakMaintenanceKeywordControl = readJson(
    join(SCOPED, "data", "weak-maintenance-keyword-control.json")
  );
  const liveLanes = existsSync(LIVE_LANES_FILE) ? readJson(LIVE_LANES_FILE) : null;
  const ablationByIssue = new Map(keywordAblation.rows.map((row) => [row.issue, row]));
  const rawIdx = indexRawFiles();
  const profiles = loadProfiles();
  const focusedComparisons = buildFocusedComparisons(
    preferred,
    keywordAblation,
    weakMaintenanceKeywordControl,
    liveLanes,
    profiles,
    rawIdx
  );

  const issues = [];
  for (const baseRow of preferred) {
    const ablationRow = ablationByIssue.get(baseRow.issue);
    if (!ablationRow) throw new Error(`missing keyword-ablation row for ${baseRow.issue}`);
    const row = {
      ...baseRow,
      "tuned-heuristic_steps": ablationRow.specific_keyword.steps,
      "tuned-heuristic_status": ablationRow.specific_keyword.status,
      "tuned-heuristic_first_bad": ablationRow.specific_keyword.first_bad,
      "tuned-heuristic_run_label": ablationRow.specific_keyword.run_label,
      "tuned-heuristic_source": ablationRow.specific_keyword.source,
      "general-heuristic_steps": ablationRow.general_keyword.steps,
      "general-heuristic_status": ablationRow.general_keyword.status,
      "general-heuristic_first_bad": ablationRow.general_keyword.first_bad,
      "general-heuristic_run_label": ablationRow.general_keyword.run_label,
      "general-heuristic_source": ablationRow.general_keyword.source,
    };
    const issue = row.issue;
    const prof = profiles[issue] || {};

    const methods = {};
    for (const m of TRACE_METHODS) {
      const runLabel = row[`${m.key}_run_label`];
      const rawPath = findRawFile(rawIdx, issue, runLabel);
      const isModel = m.family === "model";
      const entry = {
        key: m.key,
        label: m.label,
        short: m.short,
        family: m.family,
        steps_count: row[`${m.key}_steps`],
        status: row[`${m.key}_status`],
        first_bad: row[`${m.key}_first_bad`],
        run_label: runLabel || null,
        source: row[`${m.key}_source`] || null,
      };
      if (rawPath) {
        try {
          entry.trace = buildTrace(rawPath, isModel);
        } catch (e) {
          entry.trace_error = String(e && e.message ? e.message : e);
        }
      }
      methods[m.key] = entry;
    }

    issues.push({
      issue,
      title: prof.title || issue,
      issue_url: prof.issue_url || null,
      crash_summary: prof.bug_report_summary || null,
      keywords: prof.keywords || [],
      relevant_paths: prof.relevant_paths || [],
      good_commit: shortSha(prof.good_commit) || null,
      bad_commit: shortSha(prof.bad_commit) || null,
      comparison: {
        git: { steps: row["git-bisect_steps"], status: row["git-bisect_status"] },
        old_model: { steps: row["old-model-guided_steps"], status: row["old-model-guided_status"] },
        specific_heuristic: { steps: row["tuned-heuristic_steps"], status: row["tuned-heuristic_status"] },
        general_heuristic: { steps: row["general-heuristic_steps"], status: row["general-heuristic_status"] },
        parent_llm_topk3: { steps: row["parent-llm-topk3_steps"], status: row["parent-llm-topk3_status"] },
      },
      methods,
    });
  }

  // Aggregate clean metrics (final window 1, first-bad present, no skip cap).
  const agg = {};
  const aggMethods = [
    ["git", "git"],
    ["old_model", "old_model"],
    ["specific_heuristic", "specific_heuristic"],
    ["general_heuristic", "general_heuristic"],
    ["parent_llm_topk3", "parent_llm_topk3"],
  ];
  for (const [name, comparisonKey] of aggMethods) {
    const vals = [];
    for (const row of issues) {
      const result = row.comparison[comparisonKey] || {};
      const steps = result.steps;
      const status = String(result.status || "");
      const isClean =
        typeof steps === "number" &&
        steps > 0 &&
        !/skip|partial|missing|in_progress/i.test(status);
      if (isClean) vals.push(steps);
    }
    vals.sort((a, b) => a - b);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const med = vals.length
      ? vals.length % 2
        ? vals[(vals.length - 1) / 2]
        : (vals[vals.length / 2 - 1] + vals[vals.length / 2]) / 2
      : null;
    agg[name] = {
      count: vals.length,
      avg: avg === null ? null : Math.round(avg * 100) / 100,
      median: med,
    };
  }

  const out = {
    generated_at: new Date().toISOString(),
    scope:
      "Shared 10-case comparison set. Numbers are for apples-to-apples method comparison on a fixed set, not a benchmark-wide claim.",
    aggregate: agg,
    keyword_ablation: keywordAblation,
    weak_maintenance_keyword_control: weakMaintenanceKeywordControl,
    focused_comparisons: focusedComparisons,
    live_lanes: liveLanes,
    runtime_example: buildRuntimeExample(rawIdx, profiles),
    issues,
  };

  if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, JSON.stringify(out, null, 2));
  const kb = (Buffer.byteLength(JSON.stringify(out)) / 1024).toFixed(1);
  console.log(`Wrote ${OUT_FILE} (${kb} KB) with ${issues.length} issues.`);
}

main();
