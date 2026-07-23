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
const ORACLE_RAW_DIRS = [
  join(SCOPED, "raw", "aws", "oracle-first-bad"),
  join(SCOPED, "raw", "edu", "oracle-first-bad"),
];
const ADAPTIVE_RAW_DIRS = [join(SCOPED, "raw", "edu", "adaptive")];
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
    pr204559: "aws10-parent-llm600k-topk3-fresh-a-20260716a",
    pr204589: "aws10-parent-llm600k-topk3-fresh-a-20260716a",
    pr201444: "aws10-parent-llm600k-topk3-fresh-a-20260716a",
    pr193164: "aws10-parent-llm600k-topk3-fresh-a-20260716a",
    pr50304: "aws10-parent-llm600k-topk3-fresh-b-20260716a",
    pr50585: "aws10-parent-llm600k-topk3-fresh-b-20260716a",
    pr48154: "aws10-parent-llm600k-topk3-fresh-b-20260716a",
    pr49535: "aws10-parent-llm600k-topk3-fresh-c-20260716a",
    pr52635: "aws10-parent-llm600k-topk3-fresh-c-20260716a",
    pr200987: "aws10-parent-llm600k-topk3-fresh-c-20260716a",
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

// The AWS queue's nine completed histories were reconciled into the canonical
// report before the final EDU correction. Only the corrected pr193164 history
// is retained in this presentation bundle, so keep this audited summary
// separate from the fixed-k raw-history loaders below.
const ADAPTIVE_TOPK_ROWS = {
  pr204559: { steps: 9, first_bad: "5a5d0fb1e471b3a1e842aee1f993e885c8d19713", source: "aws-server" },
  pr204589: { steps: 10, first_bad: "5a5d0fb1e471b3a1e842aee1f993e885c8d19713", source: "aws-server" },
  pr201444: { steps: 11, first_bad: "6bcdd843e302063c4f0d36204686155149a6bb0a", source: "aws-server" },
  pr193164: {
    steps: 10,
    first_bad: "cac7fe50e0fbedfb14028c170d83386efeb1265b",
    source: "edu-server corrected endpoint",
    run_label: "edu10-adaptive-5000-12-3-pr193164-corrected-20260718a",
  },
  pr50304: { steps: 9, first_bad: "c9c05a91c4843c243d508c39bdfbc5e26f311af2", source: "aws-server" },
  pr50585: { steps: 11, first_bad: "e38b7e894808ec2a0c976ab01e44364f167508d3", source: "aws-server" },
  pr48154: { steps: 15, first_bad: "20e989e9de6abcf9a684978a2688acc4ea01036f", source: "aws-server" },
  pr49535: { steps: 11, first_bad: "be20eae25f50f5ef648aeefa1143e1c31e4410fc", source: "aws-server" },
  pr52635: { steps: 11, first_bad: "10bc12588dac532fad044b2851dde8e7b9121e88", source: "aws-server" },
  pr200987: { steps: 12, first_bad: "329ef60f3e21fd6845e8e8b0da405cae7eb27267", source: "aws-server" },
};

const TOPK_ISSUE_TYPES = [
  {
    label: "Analysis/IR correctness and verification",
    issues: ["pr204559", "pr204589", "pr50304", "pr50585", "pr49535"],
  },
  {
    label: "Transformation and loop optimization",
    issues: ["pr193164", "pr48154", "pr200987"],
  },
  {
    label: "Target lowering and MC",
    issues: ["pr201444", "pr52635"],
  },
];

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

const ORACLE_FIRST_BAD_RUN_LABELS = {
  pr204559: "edu10-oracle-first-bad-heuristic-20260718a",
  pr204589: "edu10-oracle-first-bad-heuristic-20260718a",
  pr201444: "edu10-oracle-first-bad-tail-20260718a",
  pr193164: "edu10-oracle-first-bad-pr193164-corrected-20260719a",
  pr50304: "aws10-oracle-first-bad-compat-a-20260719a",
  pr50585: "aws10-oracle-first-bad-compat-a-20260719a",
  pr48154: "aws10-oracle-first-bad-compat-b-20260719a",
  pr49535: "aws10-oracle-first-bad-compat-b-20260719a",
  pr52635: "aws10-oracle-first-bad-compat-c-20260719a",
  pr200987: "aws10-oracle-first-bad-compat-c-20260719a",
};

// These arms intentionally remain separate. The k3 results are historical
// policy runs; the k12 values are the current clean fixed-k12 matrix snapshot.
// A cell is only included in its aggregate when it is both completed and valid.
const K12_VARIANT_METHODS = [
  {
    key: "evidence",
    label: "Evidence-guided diverse frontier",
    k3_setup: "Not run: this frontier policy was introduced for the fixed-k12 matrix.",
    k12_setup:
      "Use a 12-candidate causal frontier, then select semantically relevant, information-gain, and component-diverse probes.",
  },
  {
    key: "causal",
    label: "Structured causal parent-diff reasoning",
    k3_setup:
      "Historical k3 run: retrieve issue-matched parent-diff hunks and function context, then extract symbols, mechanisms, linkage, confidence, and build risk.",
    k12_setup: "The same causal evidence extraction with a fixed 12-candidate model frontier.",
  },
  {
    key: "posterior",
    label: "Observation-conditioned posterior",
    k3_setup:
      "Historical k3 run: runner good/bad evidence reweights unresolved candidates sharing extracted mechanism and component features.",
    k12_setup:
      "The same posterior update with generic parent-diff extraction and a fixed 12-candidate frontier.",
  },
  {
    key: "confidence",
    label: "Confidence-adaptive frontier",
    k3_setup:
      "Historical k3 run: agreement uses the semantic top three; disagreement uses a semantic leader, posterior anchor, and midpoint probe.",
    k12_setup:
      "The same confidence-conditioned selection policy with a fixed 12-candidate model frontier.",
  },
];

const K12_VARIANT_RESULTS = {
  evidence: {
    k3: {},
    k12: {
      pr204559: { state: "completed", steps: 10, source: "aws-server" },
      pr204589: { state: "completed", steps: 10, source: "aws-server" },
      pr201444: { state: "completed", steps: 9, source: "aws-server" },
      pr193164: {
        state: "running",
        steps: 3,
        source: "edu-server",
        note:
          "Corrected EDU rerun after the stale AWS 10-step result used obsolete bad endpoint 1bec68a; the stale row is excluded.",
      },
      pr50304: { state: "completed", steps: 11, source: "aws-server" },
      pr50585: { state: "completed", steps: 13, source: "aws-server" },
      pr48154: { state: "completed", steps: 16, source: "edu-server" },
      pr49535: { state: "completed", steps: 11, source: "edu-server" },
      pr52635: { state: "completed", steps: 11, source: "edu-server" },
      pr200987: { state: "completed", steps: 12, source: "edu-server" },
    },
  },
  causal: {
    k3: {
      pr204559: { state: "completed", steps: 9 },
      pr204589: { state: "completed", steps: 9 },
      pr201444: { state: "completed", steps: 12 },
      pr193164: {
        state: "invalid",
        note:
          "The historical AWS causal run used obsolete bad endpoint 1bec68a, which is validated good; excluded from the aggregate.",
      },
      pr50304: { state: "completed", steps: 9 },
      pr50585: { state: "completed", steps: 11 },
      pr48154: { state: "completed", steps: 15 },
      pr49535: { state: "completed", steps: 11 },
      pr52635: { state: "completed", steps: 11 },
      pr200987: { state: "completed", steps: 11 },
    },
    k12: {
      pr204559: { state: "completed", steps: 9, source: "aws-server" },
      pr204589: { state: "completed", steps: 13, source: "aws-server" },
      pr201444: { state: "completed", steps: 9, source: "aws-server" },
      pr193164: { state: "completed", steps: 10, source: "aws-server" },
      pr50304: { state: "completed", steps: 9, source: "aws-server" },
      pr50585: { state: "completed", steps: 12, source: "aws-server" },
      pr48154: { state: "completed", steps: 14, source: "edu-server" },
      pr49535: { state: "completed", steps: 10, source: "edu-server" },
      pr52635: { state: "completed", steps: 11, source: "edu-server" },
      pr200987: { state: "completed", steps: 10, source: "edu-server" },
    },
  },
  posterior: {
    k3: {
      pr204559: { state: "completed", steps: 8 },
      pr204589: { state: "completed", steps: 11 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 11, note: "Corrected-endpoint rerun." },
      pr50304: { state: "completed", steps: 9 },
      pr50585: { state: "completed", steps: 12 },
      pr48154: { state: "completed", steps: 16 },
      pr49535: { state: "completed", steps: 11 },
      pr52635: { state: "completed", steps: 11 },
      pr200987: { state: "completed", steps: 11 },
    },
    k12: {
      pr204559: { state: "running", steps: 1, source: "aws-server" },
      pr201444: { state: "running", steps: 2, source: "aws-server" },
      pr50304: { state: "completed", steps: 10, source: "aws-server" },
      pr50585: { state: "completed", steps: 13, source: "aws-server" },
      pr52635: { state: "completed", steps: 10, source: "edu-server" },
    },
  },
  confidence: {
    k3: {
      pr204559: { state: "completed", steps: 11 },
      pr204589: { state: "completed", steps: 11 },
      pr201444: { state: "completed", steps: 12 },
      pr193164: { state: "completed", steps: 12 },
      pr50304: { state: "completed", steps: 10 },
      pr50585: { state: "completed", steps: 14 },
      pr48154: { state: "completed", steps: 16 },
      pr49535: {
        state: "invalid",
        note: "The historical k3 replica was superseded before it reached a terminal result; excluded.",
      },
      pr52635: { state: "completed", steps: 12 },
      pr200987: { state: "completed", steps: 12 },
    },
    k12: {
      pr204559: { state: "completed", steps: 11, source: "aws-server" },
      pr50304: { state: "completed", steps: 11, source: "aws-server" },
      pr50585: { state: "completed", steps: 14, source: "aws-server" },
      pr52635: { state: "completed", steps: 11, source: "edu-server" },
    },
  },
};

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

function tailStepsAfter(run, threshold) {
  const entryStep = stepAtOrBelow(run, threshold);
  return entryStep === null ? null : run.steps - entryStep;
}

function phaseContractionRatio(run, threshold) {
  const ratios = [];
  for (const point of run.curve.points.slice(1)) {
    if (point.verdict === "skip") continue;
    const previous = run.curve.points[point.step - 1];
    if (previous.remaining <= threshold) ratios.push(point.remaining / previous.remaining);
  }
  return ratios.length ? round(geometricMean(ratios)) : null;
}

function buildTopkSensitivity(topkRows) {
  const keys = ["topk3", "topk10", "topk20"];
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
  for (const key of keys) {
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
  const controlledRuns = topkRows.map((row) => row.topk3);
  const thresholds = [1024, 256, 128, 32, 8].map((threshold) => {
    const methods = Object.fromEntries(
      keys.map((key) => {
        const runs = topkRows.map((row) => row[key]);
        return [key, {
          mean_entry_step: average(runs.map((run) => stepAtOrBelow(run, threshold))),
          mean_tail_steps: average(runs.map((run) => tailStepsAfter(run, threshold))),
          mean_tail_ratio: average(
            runs
              .map((run) => phaseContractionRatio(run, threshold))
              .filter((ratio) => ratio !== null)
          ),
        }];
      })
    );
    const compareK3 = (otherKey) => {
      const deltas = topkRows.map(
        (row) => tailStepsAfter(row.topk3, threshold) - tailStepsAfter(row[otherKey], threshold)
      );
      return {
        wins: deltas.filter((delta) => delta < 0).length,
        ties: deltas.filter((delta) => delta === 0).length,
        losses: deltas.filter((delta) => delta > 0).length,
        mean_delta: average(deltas),
      };
    };
    return {
      threshold,
      methods,
      k3_vs_k10: compareK3("topk10"),
      k3_vs_k20: compareK3("topk20"),
    };
  });
  const issueTypeSummary = TOPK_ISSUE_TYPES.map((group) => {
    const rows = topkRows.filter((row) => group.issues.includes(row.issue));
    return {
      label: group.label,
      issues: group.issues,
      count: rows.length,
      methods: Object.fromEntries(
        keys.map((key) => [key, {
          mean_steps: average(rows.map((row) => row[key].steps)),
          mean_first_ratio: average(
            rows.map((row) => row[key].curve.points[1].remaining / row[key].curve.initial_unresolved)
          ),
          mean_tail_steps_at_128: average(rows.map((row) => tailStepsAfter(row[key], 128))),
        }])
      ),
    };
  });
  return {
    evidence_scope:
      "All three rows use parent diffs, LLM extraction, trace-only observations, calibrated-posterior selection, and the 600k raw-diff cap. Fresh top-k3 runs use an empty AWS model-cache namespace; top-k10/top-k20 are selected completed earlier histories.",
    operational_default: "topk3",
    recommendation:
      "Use top-k3 as the provisional operational default. The fresh 600k run has the lowest observed mean build count, reaches the canonical boundary on all 10 scoped cases, and fits one scoring prompt. The three-way result remains exploratory until same-time replicated arms control provider/cache state.",
    limitations: [
      "The fresh top-k3 arm removes the old 12k extraction-cap mismatch, but top-k10/top-k20 are earlier selected histories rather than same-time replications.",
      "Each top-k value changes the model-scored subset before calibrated-posterior selection; it is not only a context-budget parameter.",
      "Top-k20 uses two independent scoring prompts because the scorer batches at 12 candidates. Scores from separate prompts are not guaranteed to share a calibrated scale.",
      "The cache key is per candidate and diff mode, not per frontier cohort or observation state. Recorded token usage is therefore a lower bound when prior runs populate the cache.",
      "This is a fixed 10-issue exploratory set. The observed 0.8-step top-k20 advantage is directional, not statistically conclusive.",
    ],
    controlled_topk3: {
      key: "topk3",
      label: "top-k3 (fresh 600k)",
      extraction_revision: "600k raw-diff cap",
      avg_steps: average(controlledRuns.map((run) => run.steps)),
      median_steps: median(controlledRuns.map((run) => run.steps)),
      first_bad_matches: pairRows.filter((row) => row.topk3_matches_canonical).length,
      mean_first_step_remaining_ratio: average(
        controlledRuns.map((run) => run.curve.points[1].remaining / run.curve.initial_unresolved)
      ),
      geometric_mean_per_step_remaining_ratio: perStepRatios.topk3,
      geometric_mean_last_four_step_remaining_ratio: lateStepRatios.topk3,
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
    phase_analysis: { thresholds },
    issue_type_summary: issueTypeSummary,
    rows: pairRows,
  };
}

// Index every raw file by basename so we can resolve run labels quickly.
function indexRawFiles() {
  const idx = [];
  const k12RawDirs = [join(SCOPED, "raw", "k12", "aws"), join(SCOPED, "raw", "k12", "edu")];
  for (const dir of [...RAW_DIRS, ...ORACLE_RAW_DIRS, ...ADAPTIVE_RAW_DIRS, ...k12RawDirs]) {
    if (!existsSync(dir)) continue;
    for (const name of readdirSync(dir)) {
      if (name.endsWith(".json")) idx.push({ name, path: join(dir, name) });
    }
  }
  return idx;
}

function completedVariantCell(raw, source) {
  if (!isCompletedFirstBadRun(raw)) {
    throw new Error(`expected completed variant history: ${raw?.run_label || "unknown"}`);
  }
  return {
    state: "completed",
    steps: raw.steps.length,
    first_bad: shortSha(raw.first_bad_commit),
    source,
    run_label: raw.run_label,
  };
}

function completeVariantCell(cell) {
  return {
    state: cell.state || "not_run",
    steps: typeof cell.steps === "number" ? cell.steps : null,
    first_bad: cell.first_bad ? shortSha(cell.first_bad) : null,
    source: cell.source || null,
    run_label: cell.run_label || null,
    note: cell.note || null,
  };
}

function buildVariantAggregate(rows, key, arm) {
  const completed = rows.filter((row) => row[key][arm].state === "completed");
  const steps = completed.map((row) => row[key][arm].steps);
  const referenceSteps = completed.map((row) => row.reference.steps);
  const deltas = completed.map((row) => row[key][arm].steps - row.reference.steps);
  return {
    completed: completed.length,
    total_steps: steps.reduce((sum, value) => sum + value, 0),
    mean_steps: steps.length ? average(steps) : null,
    median_steps: steps.length ? median(steps) : null,
    matched_reference_total: referenceSteps.reduce((sum, value) => sum + value, 0),
    matched_reference_mean: referenceSteps.length ? average(referenceSteps) : null,
    wins: deltas.filter((value) => value < 0).length,
    ties: deltas.filter((value) => value === 0).length,
    losses: deltas.filter((value) => value > 0).length,
  };
}

function buildK12VariantComparison(preferred, profiles, rawIdx) {
  const referenceByIssue = new Map(
    preferred.map((row) => [
      row.issue,
      {
        state: "completed",
        steps: row["parent-llm-topk3_steps"],
        first_bad: shortSha(row["parent-llm-topk3_first_bad"]),
        source: row["parent-llm-topk3_source"],
        run_label: row["parent-llm-topk3_run_label"],
      },
    ])
  );

  const rows = preferred.map((base) => {
    const row = {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      reference: referenceByIssue.get(base.issue),
    };
    for (const method of K12_VARIANT_METHODS) {
      row[method.key] = {};
      for (const arm of ["k3", "k12"]) {
        row[method.key][arm] = completeVariantCell(K12_VARIANT_RESULTS[method.key][arm][base.issue] || {});
      }
    }
    return row;
  });

  // Verify every available local raw history agrees with the audited summary.
  const expectedRaw = [
    ["causal", "k3", "aws10-causal-parent-k3"],
    ["causal", "k12", "k12matrix", "causal"],
    ["posterior", "k3", "aws10-observation-posterior"],
    ["confidence", "k3", "aws10-confidence-frontier"],
    ["evidence", "k12", "k12matrix", "evidence-diverse"],
    ["posterior", "k12", "k12matrix", "observation-posterior"],
    ["confidence", "k12", "k12matrix", "confidence"],
  ];
  for (const [key, arm, ...needles] of expectedRaw) {
    for (const row of rows) {
      const cell = row[key][arm];
      const matches = rawIdx.filter(
        (entry) =>
          entry.name.startsWith(row.issue + "-") &&
          needles.every((needle) => entry.name.includes(needle))
      );
      const rawPath = matches.at(-1)?.path;
      if (!rawPath) continue;
      const raw = readJson(rawPath);
      if (cell.state === "completed" && isCompletedFirstBadRun(raw)) {
        const checked = completedVariantCell(raw, rawPath.includes("/edu/") ? "edu-server" : "aws-server");
        if (checked.steps !== cell.steps) {
          throw new Error(`variant step mismatch for ${key} ${arm} ${row.issue}`);
        }
        cell.first_bad = checked.first_bad;
        cell.run_label = checked.run_label;
      }
    }
  }

  const configurations = K12_VARIANT_METHODS.map((method) => ({
    ...method,
    k3: { aggregate: buildVariantAggregate(rows, method.key, "k3") },
    k12: { aggregate: buildVariantAggregate(rows, method.key, "k12") },
  }));
  const referenceSteps = rows.map((row) => row.reference.steps);
  return {
    scope:
      "Four isolated policy variants evaluated on the same scoped ten LLVM crash intervals. Values are runner build steps only.",
    reference: {
      label: "Parent diff + LLM extraction (top-k3)",
      aggregate: {
        completed: referenceSteps.length,
        total_steps: referenceSteps.reduce((sum, value) => sum + value, 0),
        mean_steps: average(referenceSteps),
        median_steps: median(referenceSteps),
      },
    },
    validity_note:
      "Only completed, endpoint-valid rows contribute to a configuration's aggregate. Matched reference totals use exactly those completed rows. The stale AWS evidence-diverse pr193164 result is excluded; the corrected EDU replacement remains running.",
    configurations,
    rows,
  };
}

function loadOracleFirstBadDiagnostic(rawIdx, issue, canonicalFirstBad) {
  const runLabel = ORACLE_FIRST_BAD_RUN_LABELS[issue];
  const rawPath = findRawFile(rawIdx, issue, runLabel);
  if (!rawPath || !rawPath.includes("oracle-first-bad")) {
    throw new Error(`missing selected oracle first-bad history for ${issue}: ${runLabel}`);
  }
  const raw = readJson(rawPath);
  const derivation = raw.oracle_first_bad_derivation;
  if (!isCompletedFirstBadRun(raw) || !derivation || !Array.isArray(derivation.keywords)) {
    throw new Error(`invalid oracle first-bad history for ${issue}`);
  }
  if (!sameCommit(raw.first_bad_commit, canonicalFirstBad)) {
    throw new Error(`oracle first-bad boundary mismatch for ${issue}`);
  }

  return {
    steps: raw.steps.length,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    first_bad: raw.first_bad_commit,
    generated_keywords: derivation.keywords,
    source: rawPath.includes("/edu/") ? "edu-server" : "aws-server",
    run_label: raw.run_label,
    status: "clean",
  };
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

function loadAdaptiveTopkRow(rawIdx, issue, canonicalFirstBad) {
  const summary = ADAPTIVE_TOPK_ROWS[issue];
  if (!summary) throw new Error(`missing adaptive summary for ${issue}`);
  if (!sameCommit(summary.first_bad, canonicalFirstBad)) {
    throw new Error(`adaptive first-bad boundary mismatch for ${issue}`);
  }
  if (!summary.run_label) {
    return { ...summary, skips: 0, status: "reconciled-summary" };
  }
  const rawPath = findRawFile(rawIdx, issue, summary.run_label);
  if (!rawPath) throw new Error(`missing adaptive raw history for ${issue}`);
  const raw = readJson(rawPath);
  if (!isCompletedFirstBadRun(raw) || raw.steps.length !== summary.steps) {
    throw new Error(`invalid adaptive raw history for ${issue}`);
  }
  return {
    ...summary,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    status: "raw-history",
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
    const adaptive = loadAdaptiveTopkRow(rawIdx, base.issue, canonical);
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      git: { steps: base["git-bisect_steps"] },
      topk3,
      topk10,
      topk20,
      adaptive,
      canonical_first_bad: canonical,
      topk3_matches: shortSha(topk3.first_bad) === canonical,
      topk10_matches: shortSha(topk10.first_bad) === canonical,
      topk20_matches: shortSha(topk20.first_bad) === canonical,
    };
  });

  const topkAggregate = {};
  for (const key of ["topk3", "topk10", "topk20", "adaptive"]) {
    const rows = topkRows.map((row) => row[key]);
    const steps = rows.map((row) => row.steps);
    topkAggregate[key] = {
      count: rows.length,
      avg_steps: average(steps),
      median_steps: median(steps),
      skip_rows: rows.filter((row) => row.skips > 0).length,
      first_bad_matches: topkRows.filter((row) => sameCommit(row[key].first_bad, row.canonical_first_bad)).length,
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
    const canonical = canonicalFirstBad.get(base.issue);
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      git: { steps: base["git-bisect_steps"] },
      best_parent_llm: parent,
      issue_specific: ablation.specific_keyword,
      shared_crash: ablation.general_keyword,
      weak_maintenance: weak,
      oracle_first_bad: loadOracleFirstBadDiagnostic(rawIdx, base.issue, canonical),
      canonical_first_bad: canonical,
      note: ablation.note,
    };
  });

  const keywordAggregate = {};
  for (const key of [
    "best_parent_llm",
    "issue_specific",
    "shared_crash",
    "weak_maintenance",
    "oracle_first_bad",
  ]) {
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
      best_configuration: "top-k3 (fresh 600k reference)",
      comparison_note:
      "All rows are completed parent-diff + LLM-extraction runs using the 600k raw-diff cap. The top-k3 rows are fresh cache-isolated AWS histories; top-k10/top-k20 use selected completed histories. The top-k20 rows use the canonical completed live snapshot because it records the preferred successful retry for each issue.",
      rows: topkRows,
      aggregate: topkAggregate,
      adaptive: {
        label: "Adaptive k=12 -> 3",
        status: "completed-matched-summary",
        definition:
          "Parent-diff + LLM extraction with k=12 above 5,000 unresolved commits and k=3 at or below 5,000.",
        provenance:
          "Nine AWS rows are reconciled canonical summaries from the completed queue; the corrected pr193164 EDU row retains its full local history. The adaptive schedule is comparable by endpoints, runner, diff cap, and search policy, but it is not included in the fixed-k trajectory charts because nine per-step histories are not in this presentation bundle.",
        rows: topkRows.map((row) => ({ issue: row.issue, ...row.adaptive })),
      },
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
      oracle_first_bad: {
        definition:
          "Terms deterministically derived from the known canonical first-bad diff: subsystem tag, changed-file stems, and source-style identifiers.",
        status: "diagnostic-only",
        warning:
          "This diagnostic leaks ground truth because it derives keywords from the first-bad commit that bisection must discover. It is shown only to diagnose first-bad relevance and is not a deployable baseline.",
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
  const k12Variants = buildK12VariantComparison(preferred, profiles, rawIdx);

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
    k12_variants: k12Variants,
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
