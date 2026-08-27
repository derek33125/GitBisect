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
const PARENT_WINDOW_RAW_DIRS = [
  join(SCOPED, "raw", "parent-window", "aws"),
  join(SCOPED, "raw", "parent-window", "edu"),
];
const TERRA_SINGLE_PARENT_RAW_DIRS = [join(SCOPED, "raw", "terra-single-parent", "edu")];
const ORACLE_PATCH_PROOF_RAW_DIRS = [
  join(SCOPED, "raw", "oracle-patch-proof", "aws"),
  join(SCOPED, "raw", "oracle-patch-proof", "edu"),
];
const DETERMINISTIC_FACTS_BCR_RAW_DIRS = [
  join(SCOPED, "raw", "deterministic-facts", "edu"),
];
const DETERMINISTIC_FACTS_V16_WINDOW_RAW_DIRS = [
  join(SCOPED, "raw", "deterministic-facts-v16", "aws"),
  join(SCOPED, "raw", "deterministic-facts-v16", "edu"),
];
const OUT_DIR = join(ROOT, "data");
const OUT_FILE = join(OUT_DIR, "site-data.json");
const LIVE_LANES_FILE = join(SCOPED, "data", "current-lanes.json");
const MASTER_STATUS_FILE = join(REPO_ROOT, "results", "master-status-report.md");
const CAUSALITY_STUDY_FILE = join(
  REPO_ROOT,
  "results",
  "reports",
  "scoped10-first-bad-trace-causality-study-20260731.md"
);
const MASTER_RESULT_ROOTS = [
  join(REPO_ROOT, "results", "lm_bisect_runs"),
  join(REPO_ROOT, "results", "issues"),
];
const PACKAGED_SERVER_RESULT_ROOTS = [
  join(REPO_ROOT, "results", "package-staging", "edu-server", "results", "lm_bisect_runs"),
  join(REPO_ROOT, "results", "package-staging", "aws-server", "results", "lm_bisect_runs"),
];

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

// The answer-term posterior control is intentionally kept outside benchmark
// aggregates. It tests whether leaked lexical terms improve the normal search
// policy, not a deployable method.
const ORACLE_POSTERIOR_CONTROL = {
  pr204559: { state: "completed", steps: 11, skips: 0, first_bad: "5a5d0fb1e471", source: "aws-server" },
  pr204589: { state: "completed", steps: 11, skips: 0, first_bad: "5a5d0fb1e471", source: "edu-server" },
  pr201444: { state: "completed", steps: 13, skips: 0, first_bad: "6bcdd843e302", source: "edu-server" },
  pr193164: {
    state: "stopped",
    steps: 4,
    skips: 0,
    source: "edu-server",
    note: "Stopped deliberately to release the EDU lane; partial history retained.",
  },
  pr50304: { state: "completed", steps: 11, skips: 0, first_bad: "c9c05a91c484", source: "aws-server" },
  pr50585: { state: "completed", steps: 12, skips: 0, first_bad: "e38b7e894808", source: "edu-server" },
  pr48154: { state: "completed", steps: 12, skips: 0, first_bad: "20e989e9de6a", source: "edu-server" },
  pr49535: { state: "completed", steps: 13, skips: 0, first_bad: "6792e26c0d0f", source: "aws-server" },
  pr52635: { state: "completed", steps: 11, skips: 0, first_bad: "10bc12588dac", source: "edu-server" },
  pr200987: { state: "completed", steps: 15, skips: 0, first_bad: "329ef60f3e21", source: "edu-server" },
};

const ORACLE_PATCH_PROOF = {
  definition:
    "A known first-bad patch leaks exact changed files and normalized changed source lines. A result is accepted only after runner-backed bad(candidate) plus good(parent) proof.",
  warning:
    "This is an answer-aware upper-bound diagnostic, not a benchmark method. It is displayed for analysis but excluded from all benchmark aggregates.",
  failed_preflight: ["pr204559", "pr204589", "pr201444"],
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
      pr204559: { state: "completed", steps: 9, first_bad: "5a5d0fb1e471", source: "aws-server" },
      pr204589: { state: "completed", steps: 13, first_bad: "5a5d0fb1e471", source: "aws-server" },
      pr201444: { state: "completed", steps: 9, first_bad: "6bcdd843e302", source: "aws-server" },
      pr193164: { state: "completed", steps: 10, first_bad: "cac7fe50e0fb", source: "aws-server" },
      pr50304: { state: "completed", steps: 9, first_bad: "c9c05a91c484", source: "aws-server" },
      pr50585: { state: "completed", steps: 12, first_bad: "e38b7e894808", source: "aws-server" },
      pr48154: { state: "completed", steps: 14, first_bad: "20e989e9de6a", source: "edu-server" },
      pr49535: { state: "completed", steps: 10, first_bad: "be20eae25f50", source: "edu-server" },
      pr52635: { state: "completed", steps: 11, first_bad: "10bc12588dac", source: "edu-server" },
      pr200987: { state: "completed", steps: 10, first_bad: "329ef60f3e21", source: "edu-server" },
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

const DETERMINISTIC_FACTS_BCR_ROWS = {
  pr204559: { source: "EDU", run_label: "edu-v15-deterministic-facts-a-20260821" },
  pr204589: { source: "EDU", run_label: "edu-v15-deterministic-facts-a-20260821" },
  pr201444: { source: "EDU", run_label: "edu-v15-deterministic-facts-a-20260821" },
  pr193164: { source: "EDU", run_label: "edu-v15-deterministic-facts-a-20260821" },
  pr50304: { source: "EDU", run_label: "edu-v15-deterministic-facts-compat-pr50304-20260822" },
  pr50585: { source: "EDU", run_label: "edu-v15-deterministic-facts-b-20260821" },
  pr48154: { source: "EDU", run_label: "edu-v15-deterministic-facts-b-20260821" },
  pr49535: { source: "EDU", run_label: "edu-v15-deterministic-facts-b-20260821" },
  pr52635: { source: "EDU", run_label: "edu-v15-deterministic-facts-b-20260821" },
  pr200987: { source: "EDU", run_label: "edu-v15-deterministic-facts-b-20260821" },
};

const DETERMINISTIC_FACTS_V16_WINDOW_ROWS = {
  pr204559: { source: "EDU", run_label: "edu-v16-window-pr204559-20260822" },
  pr204589: { source: "AWS", run_label: "aws-v16-window-range5-a-20260824" },
  pr201444: { source: "AWS", run_label: "aws-v16-window-range5-a-20260824" },
  pr193164: { source: "AWS", run_label: "aws-v16-window-range5-b-20260824" },
  pr50304: { source: "EDU", run_label: "edu-v16-window-compat-pr50304-20260822" },
  pr50585: { source: "AWS", run_label: "aws-v16-window-range5-compat-c-20260824" },
  pr48154: { source: "AWS", run_label: "aws-v16-window-range5-compat-c-20260824" },
  pr49535: { source: "AWS", run_label: "aws-v16-window-range5-compat-c-20260824" },
  pr52635: {
    source: "EDU",
    run_label: "edu-v16-window-pr52635-compat-rerun-20260825",
  },
  pr200987: {
    source: "AWS",
    run_label: "aws-v16-window-range5-pr200987-gcc13-rerun-20260825",
  },
};

const HEURISTIC_FACTOR_ABLATIONS = [
  {
    key: "keywords",
    label: "Minus issue keywords",
    disabled_component: "Issue-specific keyword matching",
    definition: "Removes the profile's assertion, stack, reproducer, and subsystem terms from the tuned semantic score. Path, risky-word, buildability, and observation-feedback terms remain enabled.",
    rows: {
      pr204559: { state: "completed", steps: 11 },
      pr204589: { state: "completed", steps: 10 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 12 },
      pr50304: { state: "non-clean", steps: 30, skips: 30 },
      pr50585: { state: "non-clean", steps: 30, skips: 30 },
      pr48154: { state: "non-clean", steps: 30, skips: 30 },
      pr49535: { state: "non-clean", steps: 30, skips: 30 },
      pr52635: { state: "non-clean", steps: 30, skips: 30 },
      pr200987: { state: "non-clean", steps: 30, skips: 29 },
    },
  },
  {
    key: "relevant-paths",
    label: "Minus relevant paths",
    disabled_component: "Relevant-path matching",
    definition: "Removes profile-provided implementation and test path priors from semantic scoring. Issue keywords, high-risk paths, risky words, buildability, and feedback remain enabled.",
    rows: {
      pr204559: { state: "completed", steps: 12 },
      pr204589: { state: "completed", steps: 12 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 12 },
      pr50304: { state: "non-clean", steps: 30, skips: 30 },
      pr50585: { state: "non-clean", steps: 30, skips: 30 },
      pr48154: { state: "non-clean", steps: 30, skips: 30 },
      pr49535: { state: "non-clean", steps: 30, skips: 30 },
      pr52635: { state: "non-clean", steps: 30, skips: 30 },
      pr200987: { state: "non-clean", steps: 30, skips: 29 },
    },
  },
  {
    key: "high-risk-paths",
    label: "Minus high-risk paths",
    disabled_component: "High-risk path bonus",
    definition: "Removes the smaller high-risk path bonus while keeping the broader relevant-path prior, issue terms, risky words, buildability, and feedback.",
    rows: {
      pr204559: { state: "completed", steps: 11 },
      pr204589: { state: "completed", steps: 12 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 11 },
      pr50304: { state: "non-clean", steps: 30, skips: 30 },
      pr50585: { state: "non-clean", steps: 30, skips: 30 },
      pr48154: { state: "non-clean", steps: 30, skips: 30 },
      pr49535: { state: "non-clean", steps: 30, skips: 30 },
      pr52635: { state: "non-clean", steps: 30, skips: 30 },
      pr200987: { state: "non-clean", steps: 30, skips: 29 },
    },
  },
  {
    key: "risky-words",
    label: "Minus risky words",
    disabled_component: "Generic risky-word bonus",
    definition: "Removes generic change-language terms such as fix, revert, crash, and regression from semantic scoring. Issue-specific terms and path priors remain enabled.",
    rows: {
      pr204559: { state: "completed", steps: 12 },
      pr204589: { state: "completed", steps: 11 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 10, source: "EDU", run_label: "edu-heuristic-ablation-risky-words-20260819" },
    },
  },
  {
    key: "buildability",
    label: "Minus buildability",
    disabled_component: "Build-success probability",
    definition: "Sets every candidate's build-success probability to 1.0, so the calibrated selector no longer de-prioritizes build, generator, cross-subsystem, or revert-style changes.",
    rows: {
      pr204559: { state: "completed", steps: 12 },
      pr204589: { state: "completed", steps: 12 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 13, source: "EDU", run_label: "edu-heuristic-ablation-buildability-20260819" },
    },
  },
  {
    key: "feedback",
    label: "Minus observation feedback",
    disabled_component: "Runner-observation feedback bias",
    definition: "Prevents prior good, bad, and skip runner observations from reweighting semantic score or buildability. The interval still contracts only from the actual runner verdict.",
    rows: {
      pr204559: { state: "completed", steps: 12 },
      pr204589: { state: "completed", steps: 13 },
      pr201444: { state: "completed", steps: 11 },
      pr193164: { state: "completed", steps: 10, source: "EDU", run_label: "edu-heuristic-ablation-feedback-20260819" },
    },
  },
];

// These groups are descriptive strata for the ten-case pilot, not a taxonomy
// learned from the results. They keep the expansion advice tied to the
// benchmark's observed crash families.
const EXPANSION_ISSUE_TYPES = [
  {
    key: "analysis-ir",
    label: "Analysis, IR, and verifier correctness",
    issues: ["pr204559", "pr204589", "pr50304", "pr50585", "pr49535"],
  },
  {
    key: "transform-loop",
    label: "Transformation and loop optimization",
    issues: ["pr193164", "pr48154", "pr200987"],
  },
  {
    key: "target-lowering",
    label: "Target lowering and MC",
    issues: ["pr201444", "pr52635"],
  },
];

function readJson(p) {
  return JSON.parse(readFileSync(p, "utf8"));
}

function stripMarkdown(value) {
  return String(value || "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .trim();
}

function markdownBullet(section, label) {
  const match = section.match(new RegExp(`^- ${label}:\\s*(.+)$`, "m"));
  return match ? stripMarkdown(match[1]) : "";
}

function splitCommaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function paragraphsAndBullets(section) {
  const paragraphs = [];
  const bullets = [];
  const paragraphLines = [];
  const flushParagraph = () => {
    const text = stripMarkdown(paragraphLines.join(" "));
    if (text) paragraphs.push(text);
    paragraphLines.length = 0;
  };
  for (const line of section.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
    } else if (trimmed.startsWith("- ")) {
      flushParagraph();
      bullets.push(stripMarkdown(trimmed.slice(2)));
    } else if (!trimmed.startsWith("```")) {
      paragraphLines.push(trimmed);
    }
  }
  flushParagraph();
  return { paragraphs, bullets };
}

// The report is the source of truth. This parser deliberately exports only
// reviewed evidence and conclusion text into the self-contained site bundle.
function loadFirstBadCausalityStudy(profiles) {
  if (!existsSync(CAUSALITY_STUDY_FILE)) {
    throw new Error(`missing first-bad causality study: ${CAUSALITY_STUDY_FILE}`);
  }
  const report = readFileSync(CAUSALITY_STUDY_FILE, "utf8");
  const issueSections = [...report.matchAll(/^## (pr\d+)\n([\s\S]*?)(?=^## |$(?![\s\S]))/gm)];
  const cases = issueSections.map((match) => {
    const issue = match[1];
    const section = match[2];
    const traceMatch = section.match(/^- Crash evidence \(([^)]+)\):\s*(.+)$/m);
    const staticOverlap = markdownBullet(section, "Static overlap classification").replace(/^\*\*|\*\*$/g, "");
    const causalLine = markdownBullet(section, "Causal role");
    const causalMatch = causalLine.match(/^(direct|indirect-enabling)\.\s*(.*)$/i);
    const diffMatch = section.match(/```diff\n([\s\S]*?)\n```/);
    return {
      issue,
      title: profiles[issue]?.title || markdownBullet(section, "Issue") || issue,
      first_bad: markdownBullet(section, "Validated first bad"),
      subject: markdownBullet(section, "First-bad subject"),
      keywords: splitCommaList(markdownBullet(section, "Generated keywords")),
      relevant_paths: splitCommaList(markdownBullet(section, "Relevant paths")),
      trace_evidence: {
        quality: traceMatch ? traceMatch[1] : "not retained",
        text: traceMatch ? stripMarkdown(traceMatch[2]) : "No saved trace evidence.",
        artifact: markdownBullet(section, "Crash-evidence artifact") || "not retained",
      },
      changed_files: splitCommaList(markdownBullet(section, "Changed files")),
      overlap: markdownBullet(section, "Overlap"),
      static_overlap: staticOverlap,
      causal_role: causalMatch ? causalMatch[1].toLowerCase() : "unclassified",
      causal_explanation: causalMatch ? causalMatch[2] : causalLine,
      interpretation_limit: markdownBullet(section, "Interpretation limit"),
      diff_excerpt: diffMatch ? diffMatch[1].trim() : "No diff excerpt retained.",
    };
  });

  const conclusionMatch = report.match(/^## Conclusion: Evidence-Derived Rules for LM Bisect\n([\s\S]*)$/m);
  if (!conclusionMatch) throw new Error("missing causality-study conclusion");
  const conclusion = conclusionMatch[1];
  const ruleMatches = [...conclusion.matchAll(/^### Rule \d+: ([^\n]+)\n([\s\S]*?)(?=^### |$(?![\s\S]))/gm)];
  const rules = ruleMatches.map((match, index) => {
    const parsed = paragraphsAndBullets(match[2]);
    return {
      number: index + 1,
      title: stripMarkdown(match[1]),
      summary: parsed.paragraphs[0] || "",
      rationale: parsed.bullets,
    };
  });
  const exclusionsMatch = conclusion.match(/^### What Not To Learn From This Study\n([\s\S]*?)(?=^### |$(?![\s\S]))/m);
  const exclusions = exclusionsMatch ? paragraphsAndBullets(exclusionsMatch[1]).bullets : [];
  const conclusionParagraphs = paragraphsAndBullets(conclusion).paragraphs;
  const deployabilityNotice =
    conclusionParagraphs.find((paragraph) => /must not be supplied to a deployable run/i.test(paragraph)) ||
    "Known first-bad commits are retrospective evidence only and must not be supplied to a deployable run.";

  const aggregate = {
    total_cases: cases.length,
    direct: cases.filter((row) => row.causal_role === "direct").length,
    indirect_enabling: cases.filter((row) => row.causal_role === "indirect-enabling").length,
    no_visible_match: cases.filter((row) => row.causal_role === "unclassified").length,
  };
  if (aggregate.total_cases !== 10 || aggregate.direct !== 6 || aggregate.indirect_enabling !== 4) {
    throw new Error("causality-study case classifications do not match the audited scoped-ten totals");
  }
  if (rules.length !== 6) throw new Error("causality-study conclusion must contain six deployable rules");

  return {
    report_title: "Scoped-10 First-Bad Causality Study",
    report_date: "2026-07-31",
    aggregate,
    conclusion:
      "Across the scoped ten, a first-bad diff is causally plausible either as a direct match to the reported mechanism or as an upstream producer of invalid compiler state later caught by a verifier or analysis.",
    deployability_notice: deployabilityNotice,
    cases,
    rules,
    exclusions,
  };
}

function cleanMarkdownCell(value) {
  return value
    .trim()
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<br\s*\/?\s*>/gi, " ");
}

function parseMarkdownTableRow(line) {
  if (!line.startsWith("|")) return null;
  const cells = line.slice(1).split("|");
  if (cells.at(-1)?.trim() === "") cells.pop();
  return cells.map(cleanMarkdownCell);
}

function isMarkdownSeparator(cells) {
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, "")));
}

const MASTER_METHODS = [
  { key: "git", label: "Git bisect" },
  { key: "legacy_lm", label: "Legacy LM-bisect" },
  { key: "tuned_heuristic", label: "Issue-specific heuristic" },
  { key: "weak_general_heuristic", label: "Weak-general heuristic" },
  { key: "bcr", label: "BCR top-k12" },
];

function methodKeyForLedgerLabel(value) {
  const label = value.toLowerCase();
  if (/\bbcr\b|\bv1\b/.test(label)) return "bcr";
  if (/weak-general/.test(label)) return "weak_general_heuristic";
  if (/tuned|issue-specific heuristic/.test(label)) return "tuned_heuristic";
  if (/legacy\s*lm|\bmodel\b/.test(label)) return "legacy_lm";
  if (/\bgit\b/.test(label)) return "git";
  return null;
}

function integerFromLedgerCell(value) {
  const match = value.match(/\b(\d+)\b/);
  return match ? Number(match[1]) : null;
}

function sourceColumnHasCompletedRun(value) {
  const text = value.toLowerCase();
  return (
    /\bdone\b/.test(text) &&
    !/(validation|bad-endpoint|shared-good|recheck|probe|queued|stopped|blocked)/.test(text)
  );
}

function completedMethodsFromState(row) {
  const text = [row.state, row.local_git, row.local_lm, row.remote_git, row.remote_lm]
    .join(" ")
    .toLowerCase();
  const methods = [];
  const isBcr = /\bbcr\b|\bv1\b/.test(text);
  if (
    /\bgit\b/.test(text) ||
    sourceColumnHasCompletedRun(row.local_git) ||
    sourceColumnHasCompletedRun(row.remote_git)
  ) {
    methods.push("git");
  }
  if (
    /legacy\s*lm/.test(text) ||
    (/\bmodel\b/.test(text) && !isBcr) ||
    sourceColumnHasCompletedRun(row.local_lm) ||
    sourceColumnHasCompletedRun(row.remote_lm)
  ) {
    methods.push("legacy_lm");
  }
  if (/tuned|issue-specific heuristic/.test(text)) methods.push("tuned_heuristic");
  if (/weak-general/.test(text)) methods.push("weak_general_heuristic");
  if (isBcr) methods.push("bcr");
  return [...new Set(methods)];
}

function methodResultState(row, key) {
  const state = row.state.toLowerCase();
  const remoteGit = row.remote_git.toLowerCase();
  const explicitExactByMethod = {
    git: /\bgit(?:\s+bisect)?\b[^;|]*\bexact\b/,
    legacy_lm: /legacy\s*lm\b[^;|]*\bexact\b/,
    tuned_heuristic: /(?:tuned|issue-specific heuristic)\b[^;|]*\bexact\b/,
    weak_general_heuristic: /weak-general(?: heuristic)?\b[^;|]*\bexact\b/,
    bcr: /(?:\bbcr\b|\bv1\b)[^;|]*\bexact\b/,
  };
  if (explicitExactByMethod[key]?.test(state)) return "completed";
  if (/^(invalid|blocked)/.test(state)) return "non_clean";
  if (/non-monotonic/.test(state)) return "non_clean";
  if (/mismatch/.test(state) && key !== "git") return "non_clean";
  if (key === "git" && /non-unique/.test(remoteGit)) return "non_clean";
  return "completed";
}

function runningMethodKeys(row) {
  const text = [row.state, row.steps, row.remote_lm].join(" ").toLowerCase();
  const keys = new Set();
  if (/\bbcr running\b/.test(text)) keys.add("bcr");
  if (/tuned(?: heuristic)? running/.test(text)) keys.add("tuned_heuristic");
  if (/weak-general(?: heuristic)? running/.test(text)) keys.add("weak_general_heuristic");
  return keys;
}

function explicitSkipCounts(row) {
  const text = `${row.skips} ${row.notes}`.toLowerCase();
  const counts = {};
  const add = (key, pattern) => {
    const match = text.match(pattern);
    if (match) counts[key] = Number(match[1]);
  };
  add("git", /git(?:-bisect)?[^.]{0,80}?\b(\d+)\s+skip/);
  add("legacy_lm", /(?:local |aws )?model[^.]{0,80}?\b(\d+)\s+skip/);
  return counts;
}

function fullShaFromLedgerCell(value) {
  const match = String(value || "").match(/\b[0-9a-f]{40}\b/i);
  return match ? match[0].toLowerCase() : null;
}

function explicitIntervalFromLedgerNote(value) {
  const text = String(value || "");
  const match =
    text.match(/\binterval size(?: is|:)?\s*([\d,]+)\s+commits?\b/i) ||
    text.match(/\b([\d,]+)-commit interval\b/i);
  return match ? Number(match[1].replaceAll(",", "")) : null;
}

function collectJsonFiles(directory, files = []) {
  if (!existsSync(directory)) return files;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const filePath = join(directory, entry.name);
    if (entry.isDirectory()) {
      collectJsonFiles(filePath, files);
    } else if (entry.isFile() && entry.name.endsWith(".json")) {
      files.push(filePath);
    }
  }
  return files;
}

function loadMasterIntervalIndex() {
  const index = new Map();
  for (const [source, roots] of [
    ["run-history", MASTER_RESULT_ROOTS],
    ["packaged-server-run-history", PACKAGED_SERVER_RESULT_ROOTS],
  ]) {
    for (const root of roots) {
      for (const filePath of collectJsonFiles(root)) {
        let raw;
        try {
          raw = readJson(filePath);
        } catch {
          continue;
        }
        if (
          !raw?.issue ||
          !fullShaFromLedgerCell(raw.good_commit) ||
          !fullShaFromLedgerCell(raw.bad_commit) ||
          !Number.isInteger(raw.initial_unresolved)
        ) {
          continue;
        }
        const key = `${raw.issue}:${fullShaFromLedgerCell(raw.good_commit)}:${fullShaFromLedgerCell(raw.bad_commit)}`;
        const previous = index.get(key);
        if (
          !previous ||
          (previous.source !== "run-history" && source === "run-history") ||
          (previous.source === source && raw.steps?.length > previous.step_count)
        ) {
          index.set(key, {
            interval_commits: raw.initial_unresolved,
            source,
            step_count: Array.isArray(raw.steps) ? raw.steps.length : 0,
          });
        }
      }
    }
  }
  return index;
}

function intervalForMasterRow(row, intervalIndex) {
  const good = fullShaFromLedgerCell(row.good_anchor);
  const bad = fullShaFromLedgerCell(row.bad_anchor);
  const history = good && bad ? intervalIndex.get(`${row.issue}:${good}:${bad}`) : null;
  if (history) {
    return { interval_commits: history.interval_commits, interval_source: history.source };
  }
  const reported = explicitIntervalFromLedgerNote(row.notes);
  if (reported !== null) {
    return { interval_commits: reported, interval_source: "ledger-note" };
  }
  return { interval_commits: null, interval_source: null };
}

function buildMasterMethodMatrix(row) {
  const methods = Object.fromEntries(MASTER_METHODS.map(({ key }) => [key, { state: "not_run", steps: null, skips: null }]));
  const stepParts = row.steps.split("/").map((part) => part.trim());
  const skipParts = row.skips.split("/").map((part) => part.trim());
  const assigned = new Set();
  const methodForPart = [];

  for (const [index, stepPart] of stepParts.entries()) {
    const key = methodKeyForLedgerLabel(stepPart);
    const steps = integerFromLedgerCell(stepPart);
    if (!key || steps === null) continue;
    methods[key] = { state: methodResultState(row, key), steps, skips: 0 };
    assigned.add(index);
    methodForPart[index] = key;
  }

  const unlabeledSteps = stepParts
    .map((stepPart, index) => ({ index, steps: integerFromLedgerCell(stepPart) }))
    .filter(({ index, steps }) => !assigned.has(index) && steps !== null);
  const inferredMethods = completedMethodsFromState(row).filter((key) => methods[key].state === "not_run");
  for (const [index, entry] of unlabeledSteps.entries()) {
    const key = inferredMethods[index];
    if (!key) continue;
    methods[key] = { state: methodResultState(row, key), steps: entry.steps, skips: 0 };
    methodForPart[entry.index] = key;
  }

  for (const [index, skipPart] of skipParts.entries()) {
    const skips = integerFromLedgerCell(skipPart);
    if (skips === null) continue;
    const labeledKey = methodKeyForLedgerLabel(skipPart);
    const key = labeledKey || methodForPart[index];
    if (key && methods[key].steps !== null) methods[key].skips = skips;
  }

  for (const [key, skips] of Object.entries(explicitSkipCounts(row))) {
    if (methods[key].steps !== null) {
      methods[key].skips = skips;
    } else {
      methods[key] = { state: "non_clean", steps: null, skips };
    }
  }

  for (const key of runningMethodKeys(row)) {
    if (methods[key].state === "not_run") methods[key] = { state: "running", steps: null, skips: null };
  }
  return methods;
}

function loadMasterStatus() {
  if (!existsSync(MASTER_STATUS_FILE)) return null;
  const lines = readFileSync(MASTER_STATUS_FILE, "utf8").split("\n");
  const start = lines.findIndex((line) => line.trim() === "## Master Table");
  if (start < 0) throw new Error("master status report has no Master Table heading");

  let header = null;
  const rows = [];
  const intervalIndex = loadMasterIntervalIndex();
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("## ")) break;
    const cells = parseMarkdownTableRow(line);
    if (!cells) continue;
    if (!header) {
      header = cells;
      continue;
    }
    if (isMarkdownSeparator(cells)) continue;
    if (cells.length !== header.length) {
      throw new Error(`master status row has ${cells.length} cells; expected ${header.length}: ${line}`);
    }
    const values = Object.fromEntries(header.map((column, index) => [column, cells[index]]));
    const row = {
      issue: values.Issue,
      state: values.State,
      local_git: values["Local Git"],
      local_lm: values["Local LM"],
      remote_git: values["Edu/AWS Git"],
      remote_lm: values["Edu/AWS LM"],
      good_anchor: values["Good Anchor"],
      bad_anchor: values["Bad Anchor"],
      first_bad: values["First Bad Commit"],
      steps: values.Steps,
      skips: values.Skips,
      notes: values.Notes,
    };
    Object.assign(row, buildMasterMethodMatrix(row));
    Object.assign(row, intervalForMasterRow(row, intervalIndex));
    rows.push(row);
  }
  if (!header || rows.length === 0) throw new Error("master status report has no parseable table rows");
  return {
    source: "results/master-status-report.md",
    columns: [
      "Issue",
      "State",
      "Interval commits",
      ...MASTER_METHODS.map(({ label }) => `${label} (steps / skips)`),
      "Good Anchor",
      "Bad Anchor",
      "First Bad Commit",
      "Notes",
    ],
    summary: {
      total_issues: rows.length,
      methods: MASTER_METHODS.map(({ key, label }) => ({
        key,
        label,
        valid_results: rows.filter((row) => row[key].state === "completed").length,
      })),
    },
    rows,
  };
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
  for (const dir of [
    ...RAW_DIRS,
    ...ORACLE_RAW_DIRS,
    ...ORACLE_PATCH_PROOF_RAW_DIRS,
    ...ADAPTIVE_RAW_DIRS,
    ...PARENT_WINDOW_RAW_DIRS,
    ...k12RawDirs,
  ]) {
    if (!existsSync(dir)) continue;
    for (const name of readdirSync(dir)) {
      if (name.endsWith(".json")) idx.push({ name, path: join(dir, name) });
    }
  }
  return idx;
}

function indexJsonFiles(directories) {
  const idx = [];
  for (const dir of directories) {
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

function buildExpansionMethodCell(raw) {
  return {
    steps: raw.steps,
    first_bad: shortSha(raw.first_bad),
    source: raw.source || null,
    run_label: raw.run_label || null,
  };
}

function buildMethodExpansionComparison(preferred, keywordAblation, profiles) {
  const heuristicByIssue = new Map(
    keywordAblation.rows.map((row) => [row.issue, row.specific_keyword])
  );
  const causalByIssue = new Map(
    Object.entries(K12_VARIANT_RESULTS.causal.k12).map(([issue, row]) => [issue, row])
  );
  const rows = preferred.map((base) => {
    const heuristic = heuristicByIssue.get(base.issue);
    const causal = causalByIssue.get(base.issue);
    if (!heuristic || !causal || causal.state !== "completed") {
      throw new Error(`missing clean expansion-comparison row for ${base.issue}`);
    }
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      type: EXPANSION_ISSUE_TYPES.find((group) => group.issues.includes(base.issue))?.key || "other",
      heuristic: buildExpansionMethodCell(heuristic),
      original: {
        steps: base["parent-llm-topk3_steps"],
        first_bad: shortSha(base["parent-llm-topk3_first_bad"]),
        source: base["parent-llm-topk3_source"],
        run_label: base["parent-llm-topk3_run_label"],
      },
      causal: {
        steps: causal.steps,
        first_bad: shortSha(causal.first_bad),
        source: causal.source,
        run_label: causal.run_label || null,
      },
    };
  });

  const methodKeys = ["heuristic", "original", "causal"];
  const aggregate = Object.fromEntries(
    methodKeys.map((key) => {
      const steps = rows.map((row) => row[key].steps);
      return [key, {
        count: rows.length,
        total_steps: steps.reduce((sum, value) => sum + value, 0),
        avg_steps: average(steps),
        median_steps: median(steps),
        first_bad_matches_reference: rows.filter(
          (row) => sameCommit(row[key].first_bad, row.original.first_bad)
        ).length,
      }];
    })
  );
  for (const key of ["heuristic", "causal"]) {
    const deltas = rows.map((row) => row[key].steps - row.original.steps);
    aggregate[key].vs_original = {
      wins: deltas.filter((delta) => delta < 0).length,
      ties: deltas.filter((delta) => delta === 0).length,
      losses: deltas.filter((delta) => delta > 0).length,
      step_delta: deltas.reduce((sum, delta) => sum + delta, 0),
    };
  }

  const by_type = EXPANSION_ISSUE_TYPES.map((group) => {
    const groupRows = rows.filter((row) => row.type === group.key);
    return {
      key: group.key,
      label: group.label,
      count: groupRows.length,
      heuristic_avg_steps: average(groupRows.map((row) => row.heuristic.steps)),
      original_avg_steps: average(groupRows.map((row) => row.original.steps)),
      causal_avg_steps: average(groupRows.map((row) => row.causal.steps)),
      causal_step_delta_vs_original: groupRows.reduce(
        (sum, row) => sum + row.causal.steps - row.original.steps,
        0
      ),
      causal_wins: groupRows.filter((row) => row.causal.steps < row.original.steps).length,
      causal_ties: groupRows.filter((row) => row.causal.steps === row.original.steps).length,
      causal_losses: groupRows.filter((row) => row.causal.steps > row.original.steps).length,
    };
  });

  return {
    scope:
      "Endpoint-valid scoped-ten comparison. The causal arm is fixed-k12; the original model arm is the fresh parent-diff + LLM top-k3 reference; the heuristic arm uses issue-specific profile terms. All three arms resolve the recorded boundary for the ten cases.",
    methods: [
      {
        key: "heuristic",
        label: "Issue-specific heuristic",
        observed_advantage:
          "No LLM calls or diff extraction; it provides a cheap deterministic control and a practical screening method when high-quality issue terms are available.",
        observed_limitation:
          "Its ranking relies on authored issue vocabulary. The fixed-vocabulary ablation is slower (13.2 versus 12.3 mean steps), so it should not be the sole method for a broader benchmark.",
        expansion_role:
          "Run for every new validated issue as the cost-efficient baseline and keyword-sensitivity control.",
      },
      {
        key: "original",
        label: "Original parent-diff + LLM extraction (top-k3)",
        observed_advantage:
          "A complete model-guided reference with generic LLM extraction, 11.1 mean build steps, and no dependence on one selected first-bad mechanism.",
        observed_limitation:
          "The extracted evidence is broad rather than explicitly linked to a symbol, behavioral change, and issue signal, which weakens inspection of why a candidate was preferred.",
        expansion_role:
          "Keep as the primary model baseline for every new issue so policy changes remain comparable.",
      },
      {
        key: "causal",
        label: "Structured causal parent-diff reasoning (fixed-k12)",
        observed_advantage:
          "The only refined variant with all ten valid rows; it reduces the total from 111 to 107 builds versus the original reference and produces inspectable symbol, mechanism, linkage, confidence, and build-risk evidence.",
        observed_limitation:
          "The four-build gain is modest and the frontier size differs from the top-k3 reference, so this pilot does not isolate causal extraction as the sole cause of the gain.",
        expansion_role:
          "Run alongside the original model method on new issues, prioritizing failures with assertion text, stack/pass clues, or subsystem-specific reproducers where causal retrieval can be evaluated.",
      },
    ],
    aggregate,
    by_type,
    rows,
    recommendation:
      "For benchmark expansion, retain all three arms: heuristic as a low-cost keyword-sensitive control, original parent-diff + LLM top-k3 as the stable model reference, and structured causal reasoning as the leading inspectable refinement. Add issue families outside the current optimization-heavy pilot before selecting a default method.",
    caveat:
      "The scoped ten cases are not sufficient to claim universal superiority. The comparison measures runner-backed build steps and matching terminal boundaries; it does not measure API cost, wall time, or generalization to unrepresented crash families.",
  };
}

const TERRA_SINGLE_PARENT_ROWS = {
  pr204559: { source: "EDU", run_label: "edu-terra-single-parent-k12-rerun-a-20260815" },
  pr204589: { source: "EDU", run_label: "edu-terra-single-parent-k12-rerun-a-20260815" },
  pr201444: { source: "EDU", run_label: "edu-terra-single-parent-k12-rerun-c-20260816" },
  pr193164: { source: "EDU", run_label: "edu-terra-single-parent-k12-rerun-d-20260816" },
  pr50304: { source: "EDU", run_label: "edu-terra-single-parent-k12-b-20260814" },
  pr50585: { source: "EDU", run_label: "edu-terra-single-parent-k12-b-20260814" },
  pr48154: { source: "EDU", run_label: "edu-terra-single-parent-k12-b-20260814" },
  pr49535: { source: "EDU", run_label: "edu-terra-single-parent-k12-c-20260814" },
  pr52635: { source: "EDU", run_label: "edu-terra-single-parent-k12-c-20260814" },
  pr200987: { source: "EDU", run_label: "edu-terra-single-parent-k12-rerun-b-20260815" },
};

const SCOPED_TEN_GIT_BOUNDARIES = {
  pr204559: "5a5d0fb1e471",
  pr204589: "5a5d0fb1e471",
  pr201444: "6bcdd843e302",
  pr193164: "cac7fe50e0fb",
  pr50304: "c9c05a91c484",
  pr50585: "e38b7e894808",
  pr48154: "20e989e9de6a",
  pr49535: "6792e26c0d0f",
  pr52635: "10bc12588dac",
  pr200987: "329ef60f3e21",
};

const SCOPED_TEN_ACCEPTED_ALTERNATE_BOUNDARIES = {
  // Git records the reapply; runner-backed searches consistently isolate the
  // original apply that first introduces the behavior.
  pr49535: "be20eae25f50",
};

const TERRA_PARENT_WINDOW_ROWS = {
  pr204559: { source: "EDU", run_label: "edu-terra-bcr-range5-k12-a-20260807" },
  pr204589: { source: "EDU", run_label: "edu-terra-bcr-range5-k12-a-20260807" },
  pr201444: { source: "AWS", run_label: "aws-terra-bcr-range5-k12-rerun-a-20260811T011517Z" },
  pr193164: {
    source: "EDU",
    run_label: "edu-terra-window-pr193164-clean-rerun-20260817-r1",
  },
  pr50304: { source: "EDU", run_label: "edu-terra-bcr-range5-k12-compat-a-20260808" },
  pr50585: { source: "AWS", run_label: "aws-terra-bcr-range5-k12-rerun-compat-20260811T011517Z" },
  pr48154: { source: "AWS", run_label: "aws-terra-bcr-range5-k12-rerun-cstdint-20260811T092549Z" },
  pr49535: { source: "EDU", run_label: "edu-terra-bcr-range5-k12-compat-b-20260808" },
  pr52635: { source: "AWS", run_label: "aws-terra-bcr-range5-k12-rerun-cmake35-20260811T092549Z" },
  pr200987: { source: "EDU", run_label: "edu-terra-bcr-range5-k12-c-20260807" },
};

function loadTerraParentWindowRun(rawIdx, issue) {
  const expected = TERRA_PARENT_WINDOW_ROWS[issue];
  const rawPath = findRawFile(rawIdx, issue, expected?.run_label);
  if (!expected || !rawPath) throw new Error(`missing Terra parent-window history for ${issue}`);
  const raw = readJson(rawPath);
  const validConfiguration =
    isCompletedFirstBadRun(raw) &&
    raw.model_name === "gpt-5.6-terra" &&
    raw.model_reasoning_effort === "high" &&
    raw.model_top_k === 12 &&
    raw.model_diff_mode === "parent" &&
    raw.model_diff_extraction === "causal-llm" &&
    raw.causal_context_parent_count === 5;
  if (!validConfiguration) {
    throw new Error(`invalid Terra parent-window configuration for ${issue}: ${rawPath}`);
  }
  return {
    steps: raw.steps.length,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    first_bad: shortSha(raw.first_bad_commit),
    source: expected.source,
    run_label: raw.run_label,
  };
}

function loadTerraSingleParentRun(rawIdx, issue) {
  const expected = TERRA_SINGLE_PARENT_ROWS[issue];
  const rawPath = findRawFile(rawIdx, issue, expected?.run_label);
  if (!expected || !rawPath) throw new Error(`missing Terra single-parent history for ${issue}`);
  const raw = readJson(rawPath);
  const validConfiguration =
    isCompletedFirstBadRun(raw) &&
    raw.model_name === "gpt-5.6-terra" &&
    raw.model_reasoning_effort === "high" &&
    raw.model_top_k === 12 &&
    raw.model_diff_mode === "parent" &&
    raw.model_diff_extraction === "causal-llm" &&
    raw.causal_context_parent_count === 0;
  if (!validConfiguration) {
    throw new Error(`invalid Terra single-parent configuration for ${issue}: ${rawPath}`);
  }
  return {
    steps: raw.steps.length,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    first_bad: shortSha(raw.first_bad_commit),
    source: expected.source,
    run_label: raw.run_label,
  };
}

function selectedShaJaccard(left, right) {
  const leftSet = new Set(left.steps.map((step) => step.sha));
  const rightSet = new Set(right.steps.map((step) => step.sha));
  const union = new Set([...leftSet, ...rightSet]);
  if (union.size === 0) return 1;
  return [...leftSet].filter((sha) => rightSet.has(sha)).length / union.size;
}

function samePositionRate(left, right) {
  const compared = Math.min(left.steps.length, right.steps.length);
  if (compared === 0) return 1;
  let matches = 0;
  for (let index = 0; index < compared; index += 1) {
    if (left.steps[index].sha === right.steps[index].sha) matches += 1;
  }
  return matches / compared;
}

function repeatRunMap(rawIdx, matcher) {
  const runs = new Map();
  for (const entry of rawIdx) {
    const raw = readJson(entry.path);
    if (!isCompletedFirstBadRun(raw) || !matcher(raw.run_label || "")) continue;
    runs.set(`${raw.issue}:${raw.run_label}`, raw);
  }
  return runs;
}

function repeatPairRows(leftRuns, rightRuns, leftMatcher, rightMatcher) {
  const issues = new Set([...leftRuns.values()].map((run) => run.issue));
  const rows = [];
  for (const issue of [...issues].sort()) {
    const left = [...leftRuns.values()].find(
      (run) => run.issue === issue && leftMatcher(run.run_label || "")
    );
    const right = [...rightRuns.values()].find(
      (run) => run.issue === issue && rightMatcher(run.run_label || "")
    );
    if (!left || !right) continue;
    rows.push({
      issue,
      left_steps: left.steps.length,
      right_steps: right.steps.length,
      boundary_agrees: sameCommit(left.first_bad_commit, right.first_bad_commit),
      exact_step_and_verdict_path:
        JSON.stringify(left.steps.map((step) => [step.sha, step.verdict])) ===
        JSON.stringify(right.steps.map((step) => [step.sha, step.verdict])),
      selected_sha_jaccard: selectedShaJaccard(left, right),
      same_position_rate: samePositionRate(left, right),
    });
  }
  return rows;
}

function summarizeCrossHostRepeatRows(rows) {
  const absoluteStepDeltas = rows.map((row) => Math.abs(row.right_steps - row.left_steps));
  return {
    issue_groups: rows.length,
    boundary_agreements: rows.filter((row) => row.boundary_agrees).length,
    mean_absolute_step_delta: average(absoluteStepDeltas),
    max_absolute_step_delta: Math.max(...absoluteStepDeltas),
    mean_selected_sha_jaccard: average(rows.map((row) => row.selected_sha_jaccard)),
    mean_same_position_rate: average(rows.map((row) => row.same_position_rate)),
    rows: rows.map((row) => ({
      ...row,
      selected_sha_jaccard: Math.round(row.selected_sha_jaccard * 100) / 100,
      same_position_rate: Math.round(row.same_position_rate * 100) / 100,
    })),
  };
}

function buildLmBisectRepeatability(rawIdx) {
  const pre600k = repeatRunMap(rawIdx, (label) =>
    /^(aws|edu)-scoped10-parent-extract-topk3-(?:rerun-)?[ab]-2026070[67][a-z]$/.test(label)
  );
  const initialAws = (label) => /^aws-scoped10-parent-extract-topk3-[ab]-20260706b$/.test(label);
  const replayAws = (label) => /^aws-scoped10-parent-extract-topk3-rerun-[ab]-20260707a$/.test(label);
  const independentEdu = (label) => /^edu-scoped10-parent-extract-topk3-rerun-[ab]-20260707a$/.test(label);
  const cacheReplayRows = repeatPairRows(pre600k, pre600k, initialAws, replayAws);
  const crossHostTopk3Rows = repeatPairRows(pre600k, pre600k, initialAws, independentEdu);

  const topk20 = repeatRunMap(rawIdx, (label) => /parent-llm600k-topk20-hardened-/.test(label));
  const crossHostTopk20Rows = repeatPairRows(
    topk20,
    topk20,
    (label) => /^aws10-parent-llm600k-topk20-hardened-/.test(label),
    (label) => /^edu10-parent-llm600k-topk20-hardened-/.test(label)
  );

  return {
    parent_window_independent_repeats: 0,
    cached_same_host_replay: {
      issue_groups: cacheReplayRows.length,
      exact_step_and_verdict_paths: cacheReplayRows.filter(
        (row) => row.exact_step_and_verdict_path
      ).length,
      boundary_agreements: cacheReplayRows.filter((row) => row.boundary_agrees).length,
    },
    cross_host_parent_topk3: summarizeCrossHostRepeatRows(crossHostTopk3Rows),
    cross_host_parent_topk20: summarizeCrossHostRepeatRows(crossHostTopk20Rows),
    conclusion:
      "The retained repeats show stable terminal boundaries but variable search paths. Step counts changed modestly (at most three builds in these paired samples), while selected-commit overlap fell sharply in independent cross-host top-k20 runs.",
    cache_caveat:
      "The nine same-host replays reused the default issue/model scoring cache and reproduced every SHA/verdict path exactly. They measure cache replay, not LLM stochasticity. Older histories did not persist a cache namespace or cache digest, so cross-host rows are supporting evidence rather than controlled fresh-cache trials.",
    parent_window_caveat:
      "The completed five-parent window cohort has one valid run per issue. Its repeatability cannot be estimated directly until fresh-cache reruns are performed with the same model, reasoning effort, top-k, extraction mode, parent count, policy, endpoints, and runner environment.",
  };
}

function buildTerraBcrComparison(preferred, profiles, rawIdx, terraSingleParentRawIdx) {
  const miniBcr = new Map(
    Object.entries(K12_VARIANT_RESULTS.causal.k12).map(([issue, row]) => [issue, row])
  );
  const rows = preferred.map((base) => {
    const terra = loadTerraSingleParentRun(terraSingleParentRawIdx, base.issue);
    const parentWindow = loadTerraParentWindowRun(rawIdx, base.issue);
    const mini = miniBcr.get(base.issue);
    if (!mini || mini.state !== "completed") {
      throw new Error(`missing completed Terra or mini BCR row for ${base.issue}`);
    }
    const canonicalBoundary = sameCommit(
      terra.first_bad,
      SCOPED_TEN_GIT_BOUNDARIES[base.issue]
    );
    const parentWindowCanonicalBoundary = sameCommit(
      parentWindow.first_bad,
      SCOPED_TEN_GIT_BOUNDARIES[base.issue]
    );
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      legacy_lm_steps: base["old-model-guided_steps"],
      parent_llm_topk3_steps: base["parent-llm-topk3_steps"],
      mini_bcr_steps: mini.steps,
      terra_bcr_steps: terra.steps,
      parent_window_steps: parentWindow.steps,
      parent_window_skips: parentWindow.skips,
      parent_window_source: parentWindow.source,
      parent_window_run_label: parentWindow.run_label,
      parent_window_first_bad: parentWindow.first_bad,
      parent_window_canonical_boundary: parentWindowCanonicalBoundary,
      source: terra.source,
      run_label: terra.run_label,
      first_bad: terra.first_bad,
      canonical_boundary: canonicalBoundary,
      note: canonicalBoundary
        ? "Endpoint-matched terminal result."
        : "Known apply/reapply representation mismatch: Terra identifies the original apply commit, while Git records a later reapply.",
    };
  });

  const terraSteps = rows.map((row) => row.terra_bcr_steps);
  const parentWindowSteps = rows.map((row) => row.parent_window_steps);
  const miniDeltas = rows.map((row) => row.terra_bcr_steps - row.mini_bcr_steps);
  const originalDeltas = rows.map(
    (row) => row.terra_bcr_steps - row.parent_llm_topk3_steps
  );
  const legacyDeltas = rows.map((row) => row.terra_bcr_steps - row.legacy_lm_steps);
  const parentWindowMiniDeltas = rows.map(
    (row) => row.parent_window_steps - row.mini_bcr_steps
  );
  const parentWindowOriginalDeltas = rows.map(
    (row) => row.parent_window_steps - row.parent_llm_topk3_steps
  );
  const parentWindowLegacyDeltas = rows.map(
    (row) => row.parent_window_steps - row.legacy_lm_steps
  );
  const parentWindowTerraDeltas = rows.map(
    (row) => row.parent_window_steps - row.terra_bcr_steps
  );
  const comparison = (deltas) => ({
    wins: deltas.filter((delta) => delta < 0).length,
    ties: deltas.filter((delta) => delta === 0).length,
    losses: deltas.filter((delta) => delta > 0).length,
    step_delta: deltas.reduce((sum, delta) => sum + delta, 0),
  });

  return {
    model: {
      name: "gpt-5.6-terra",
      reasoning_effort: "high",
      note: "No gpt-5.5-terra run exists in the retained histories; this is the verified stronger-model cohort.",
    },
    configuration:
      "BCR causal retrieval with parent diffs, causal-llm extraction, calibrated-posterior selection, trace-only observations, and top-k12.",
    parent_window_configuration:
      "The same Terra/high BCR contract, with each candidate represented by its own parent diff plus five first-parent predecessor diffs before causal extraction.",
    scope:
      "The endpoint-valid scoped ten-case cohort. Each Terra history is terminal with zero skip verdicts; pr49535 remains a documented non-canonical apply/reapply boundary representation.",
    aggregate: {
      terra: {
        completed: rows.length,
        total_steps: terraSteps.reduce((sum, steps) => sum + steps, 0),
        mean_steps: average(terraSteps),
        median_steps: median(terraSteps),
        skip_total: 0,
        canonical_boundary_matches: rows.filter((row) => row.canonical_boundary).length,
        noncanonical_terminal_cases: rows.filter((row) => !row.canonical_boundary).length,
        vs_mini_bcr: comparison(miniDeltas),
        vs_parent_llm_topk3: comparison(originalDeltas),
        vs_legacy_lm: comparison(legacyDeltas),
      },
      parent_window: {
        completed: rows.length,
        total_steps: parentWindowSteps.reduce((sum, steps) => sum + steps, 0),
        mean_steps: average(parentWindowSteps),
        median_steps: median(parentWindowSteps),
        skip_total: rows.reduce((sum, row) => sum + row.parent_window_skips, 0),
        canonical_boundary_matches: rows.filter(
          (row) => row.parent_window_canonical_boundary
        ).length,
        noncanonical_terminal_cases: rows.filter(
          (row) => !row.parent_window_canonical_boundary
        ).length,
        vs_mini_bcr: comparison(parentWindowMiniDeltas),
        vs_parent_llm_topk3: comparison(parentWindowOriginalDeltas),
        vs_legacy_lm: comparison(parentWindowLegacyDeltas),
        vs_terra_single_parent: comparison(parentWindowTerraDeltas),
      },
    },
    rows,
    repeatability: buildLmBisectRepeatability(rawIdx),
    caveat:
      "The mini, Terra single-parent, and Terra parent-window cohorts were executed at different times and on different hosts. The 10-case results are descriptive paired comparisons, not isolated causal estimates of model strength or parent-window context.",
  };
}

function compareCompletedSteps(rows, key, referenceKey) {
  const compared = rows.filter(
    (row) => row[key]?.state === "completed" && row[referenceKey]?.state === "completed"
  );
  const deltas = compared.map((row) => row[key].steps - row[referenceKey].steps);
  return {
    compared: compared.length,
    wins: deltas.filter((value) => value < 0).length,
    ties: deltas.filter((value) => value === 0).length,
    losses: deltas.filter((value) => value > 0).length,
    step_delta: deltas.reduce((sum, value) => sum + value, 0),
  };
}

function loadDeterministicFactsBcrRun(rawIdx, issue) {
  const expected = DETERMINISTIC_FACTS_BCR_ROWS[issue];
  const rawPath = findRawFile(rawIdx, issue, expected?.run_label);
  if (!expected || !rawPath) {
    throw new Error(`missing deterministic-facts BCR v15 history for ${issue}`);
  }
  const raw = readJson(rawPath);
  const skips = raw.steps?.filter((step) => step.verdict === "skip").length;
  const canonicalBoundary = sameCommit(raw.first_bad_commit, SCOPED_TEN_GIT_BOUNDARIES[issue]);
  const acceptedAlternateBoundary = sameCommit(
    raw.first_bad_commit,
    SCOPED_TEN_ACCEPTED_ALTERNATE_BOUNDARIES[issue]
  );
  const validConfiguration =
    isCompletedFirstBadRun(raw) &&
    raw.model_name === "gpt-5.6-terra" &&
    raw.model_reasoning_effort === "high" &&
    raw.model_top_k === 12 &&
    raw.model_diff_mode === "parent" &&
    raw.model_diff_extraction === "causal-llm-deterministic-facts" &&
    raw.causal_context_parent_count === 0 &&
    skips === 0 &&
    (canonicalBoundary || acceptedAlternateBoundary);
  if (!validConfiguration) {
    throw new Error(`invalid deterministic-facts BCR v15 history for ${issue}: ${rawPath}`);
  }
  return {
    state: "completed",
    steps: raw.steps.length,
    skips,
    first_bad: shortSha(raw.first_bad_commit),
    canonical_boundary: canonicalBoundary,
    source: expected.source,
    run_label: raw.run_label,
    note: canonicalBoundary
      ? "Endpoint-matched terminal result."
      : "Known apply/reapply representation mismatch: V15 identifies the original apply commit, while Git records a later reapply.",
  };
}

function buildDeterministicFactsBcrComparison(
  preferred,
  profiles,
  terraBcrComparison,
  causalityStudy,
  rawIdx
) {
  const terraByIssue = new Map(
    terraBcrComparison.rows.map((row) => [
      row.issue,
      { state: "completed", steps: row.terra_bcr_steps, first_bad: row.first_bad },
    ])
  );
  const windowByIssue = new Map(
    terraBcrComparison.rows.map((row) => [
      row.issue,
      { state: "completed", steps: row.parent_window_steps, first_bad: row.parent_window_first_bad },
    ])
  );
  const humanGuided = {
    pr204559: 9,
    pr204589: 8,
    pr201444: 11,
    pr193164: 9,
    pr50304: 9,
    pr50585: 12,
    pr48154: 16,
    pr49535: 8,
    pr52635: 11,
    pr200987: 10,
  };
  const rows = preferred.map((base) => {
    const deterministicFacts = loadDeterministicFactsBcrRun(rawIdx, base.issue);
    return {
      issue: base.issue,
      title: profiles[base.issue]?.title || base.issue,
      deterministic_facts: deterministicFacts,
      terra_single_parent: terraByIssue.get(base.issue),
      human_guided: { state: "completed", steps: humanGuided[base.issue] },
      parent_window: windowByIssue.get(base.issue),
    };
  });
  const completed = rows.filter((row) => row.deterministic_facts.state === "completed");
  const steps = completed.map((row) => row.deterministic_facts.steps);

  return {
    configuration:
      "GPT-5.6 Terra, high reasoning, parent diff, top-k12, one ordinal deterministic-facts LLM call, calibrated-posterior selection, and trace-only observations.",
    scope:
      "The same endpoint-valid scoped ten. Every row is a terminal one-commit, zero-skip V15 history; nine match the Git/reference boundary and pr49535 retains the accepted original-apply versus reapply representation. The earlier pr50304 skip-capped attempt is preserved as raw provenance but excluded in favor of its clean compatibility rerun.",
    integration: {
      summary:
        "V15 implements the human-study LLM loop without a leaked first-bad locator: code parses the bad-endpoint crash artifact, ranks real hunks, and computes checker polarity, contract, destruction-surface, and contact-path facts; one ordinal LLM call judges mechanism from those facts and hunks; code maps that ordinal result into the existing calibrated posterior. Crash evidence is not a fake build observation and candidate pruning remains off.",
      rules: [
        {
          number: 1,
          title: "Crash Artifact Is Step-Zero Evidence",
          applied_as:
            "The known bad-endpoint artifact supplies structured assertion, stack business symbol, pass, and flag facts before the first probe.",
        },
        {
          number: 2,
          title: "Checker Files Are Signed Evidence",
          applied_as:
            "A rarely touched crash file is penalized as a likely detector; a hot file is boosted. Neither outcome removes candidates.",
        },
        {
          number: 3,
          title: "Code Selects Hunks and Repository Facts",
          applied_as:
            "Deterministic ranking combines crash-anchor contact, dependency/API-use, assertion contract, destruction surface, and no-contact facts with normal BCR paths and terms.",
        },
        {
          number: 4,
          title: "One Model Call Judges Mechanism",
          applied_as:
            "Selected real hunks and structured facts go directly to an ordinal rank/mechanism/confidence prompt; V15 does not run causal compression followed by an absolute-score prompt.",
        },
        {
          number: 5,
          title: "Calibration and Eligibility Stay Deterministic",
          applied_as:
            "Code maps the ordinal judgment to the existing calibrated posterior. The runner alone contracts the interval; no crash fact is treated as an observation.",
        },
        {
          number: 6,
          title: "No Artifact Does Not Invent Evidence",
          applied_as:
            "Missing or verifier-only crash anchors are recorded as empty evidence and retain the normal BCR path rather than a hard filter or answer-informed map.",
        },
      ],
    },
    aggregate: {
      completed: completed.length,
      canonical_boundary_matches: completed.filter((row) => row.deterministic_facts.canonical_boundary).length,
      accepted_alternate_boundaries: completed.filter((row) => !row.deterministic_facts.canonical_boundary).length,
      interrupted: rows.filter((row) => row.deterministic_facts.state === "interrupted").length,
      queued: rows.filter((row) => row.deterministic_facts.state === "queued").length,
      total_steps: steps.reduce((sum, value) => sum + value, 0),
      mean_steps: steps.length ? average(steps) : null,
      vs_terra_single_parent: compareCompletedSteps(rows, "deterministic_facts", "terra_single_parent"),
      vs_human_guided: compareCompletedSteps(rows, "deterministic_facts", "human_guided"),
      vs_parent_window: compareCompletedSteps(rows, "deterministic_facts", "parent_window"),
    },
    rows,
  };
}

function loadDeterministicFactsV16WindowRun(rawIdx, issue) {
  const expected = DETERMINISTIC_FACTS_V16_WINDOW_ROWS[issue];
  const rawPath = findRawFile(rawIdx, issue, expected?.run_label);
  if (!expected || !rawPath) {
    throw new Error(`missing deterministic-facts BCR V16 window history for ${issue}`);
  }
  const raw = readJson(rawPath);
  const skips = raw.steps?.filter((step) => step.verdict === "skip").length;
  const canonicalBoundary = sameCommit(raw.first_bad_commit, SCOPED_TEN_GIT_BOUNDARIES[issue]);
  const acceptedAlternateBoundary = sameCommit(
    raw.first_bad_commit,
    SCOPED_TEN_ACCEPTED_ALTERNATE_BOUNDARIES[issue]
  );
  const validConfiguration =
    isCompletedFirstBadRun(raw) &&
    raw.model_name === "gpt-5.6-terra" &&
    raw.model_reasoning_effort === "high" &&
    raw.model_top_k === 12 &&
    raw.model_diff_mode === "parent" &&
    raw.model_diff_extraction === "causal-llm-deterministic-facts-artifact" &&
    raw.causal_context_parent_count === 5 &&
    skips === 0 &&
    (canonicalBoundary || acceptedAlternateBoundary);
  if (!validConfiguration) {
    throw new Error(`invalid deterministic-facts BCR V16 window history for ${issue}: ${rawPath}`);
  }
  return {
    state: "completed",
    steps: raw.steps.length,
    skips,
    first_bad: shortSha(raw.first_bad_commit),
    canonical_boundary: canonicalBoundary,
    parent_context_count: raw.causal_context_parent_count,
    source: expected.source,
    run_label: raw.run_label,
    note: canonicalBoundary
      ? "Endpoint-matched terminal result."
      : "Known apply/reapply representation mismatch: V16 identifies the original apply commit, while Git records a later reapply.",
  };
}

function buildDeterministicFactsV16WindowComparison(
  preferred,
  profiles,
  deterministicFactsBcr,
  rawIdx
) {
  const v15ByIssue = new Map(
    deterministicFactsBcr.rows.map((row) => [row.issue, row.deterministic_facts])
  );
  const rows = preferred.map((base) => ({
    issue: base.issue,
    title: profiles[base.issue]?.title || base.issue,
    v15: v15ByIssue.get(base.issue),
    v16_window: loadDeterministicFactsV16WindowRun(rawIdx, base.issue),
  }));
  const completed = rows.filter((row) => row.v16_window.state === "completed");
  const steps = completed.map((row) => row.v16_window.steps);

  return {
    configuration:
      "GPT-5.6 Terra, high reasoning, top-k12, artifact-complete deterministic facts, five parent contexts per candidate, one ordinal mechanism call, and calibrated-posterior selection.",
    scope:
      "Artifact-complete V16 was completed on the endpoint-valid scoped ten. Every accepted history is terminal with one unresolved commit and zero skips; nine match the Git/reference boundary and pr49535 retains the accepted original-apply versus reapply representation. The earlier pr200987 and pr52635 environment-failed histories remain preserved as provenance and are excluded in favor of their clean corrective reruns.",
    aggregate: {
      completed: completed.length,
      canonical_boundary_matches: completed.filter((row) => row.v16_window.canonical_boundary).length,
      accepted_alternate_boundaries: completed.filter((row) => !row.v16_window.canonical_boundary)
        .length,
      interrupted: rows.filter((row) => row.v16_window.state === "interrupted").length,
      queued: rows.filter((row) => row.v16_window.state === "queued").length,
      skip_total: completed.reduce((sum, row) => sum + row.v16_window.skips, 0),
      total_steps: steps.reduce((sum, value) => sum + value, 0),
      mean_steps: steps.length ? average(steps) : null,
      vs_v15: compareCompletedSteps(rows, "v16_window", "v15"),
    },
    rows,
  };
}

function buildHeuristicFactorAblation(preferred, profiles, keywordAblation) {
  const baselineByIssue = new Map(
    keywordAblation.rows.map((row) => [
      row.issue,
      { state: "completed", steps: row.specific_keyword.steps, first_bad: row.specific_keyword.first_bad },
    ])
  );
  const rows = preferred.map((base) => ({
    issue: base.issue,
    title: profiles[base.issue]?.title || base.issue,
    baseline: baselineByIssue.get(base.issue),
  }));
  const factors = HEURISTIC_FACTOR_ABLATIONS.map((factor) => {
    const factorRows = rows.map((base) => ({
      ...base,
      result: completeVariantCell(factor.rows[base.issue] || {}),
    }));
    const clean = factorRows.filter((row) => row.result.state === "completed");
    const deltas = clean.map((row) => row.result.steps - row.baseline.steps);
    return {
      key: factor.key,
      label: factor.label,
      disabled_component: factor.disabled_component,
      definition: factor.definition,
      rows: factorRows,
      aggregate: {
        completed_clean: clean.length,
        interrupted_or_running: factorRows.filter((row) => row.result.state === "running").length,
        non_clean: factorRows.filter((row) => row.result.state === "non-clean").length,
        total_steps: clean.reduce((sum, row) => sum + row.result.steps, 0),
        mean_steps: clean.length ? average(clean.map((row) => row.result.steps)) : null,
        baseline_total_steps: clean.reduce((sum, row) => sum + row.baseline.steps, 0),
        baseline_mean_steps: clean.length ? average(clean.map((row) => row.baseline.steps)) : null,
        wins: deltas.filter((value) => value < 0).length,
        ties: deltas.filter((value) => value === 0).length,
        losses: deltas.filter((value) => value > 0).length,
        step_delta: deltas.reduce((sum, value) => sum + value, 0),
      },
    };
  });
  return {
    baseline: {
      label: "Tuned heuristic",
      formula:
        "semantic = 0.05 + min(4.5, 0.60 * keyword_hits) + min(4.0, 1.2 * relevant_path_hits) + min(2.0, 0.5 * high_risk_path_hits) + min(1.5, 0.2 * risky_word_hits); selector = calibrated posterior * build-success probability, then runner feedback reweights future candidates.",
      scope: "Each variant disables exactly one factor. The runner, endpoints, calibrated-posterior selector, max-30 cap, and all other factors remain fixed.",
    },
    factors,
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
    bcr_extraction_contract:
      "BCR replaces the generic raw-diff summary. It performs deterministic file ranking, hunk ranking, and context lookup, then one causal-extraction LLM call. Its normalized causal evidence is the diff_summary passed to the unchanged shared scorer; no second generic raw-diff summary runs in parallel.",
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
      "Only completed, endpoint-valid rows contribute to a configuration's aggregate. Matched reference totals use exactly those completed rows. The stale AWS evidence-diverse pr193164 result is excluded pending its corrected EDU replacement.",
    configurations,
    rows,
  };
}

function buildCausalRetrievalExample(rawIdx) {
  const issue = "pr204559";
  const runLabel = "aws12-k12matrix-a-causal-20260722a";
  const candidateSha = "d518f8ff67407fc49d2c37cba575297d5dc7e713";
  const rawPath = findRawFile(rawIdx, issue, runLabel);
  if (!rawPath) throw new Error(`missing BCR retrieval example history: ${issue}: ${runLabel}`);

  const raw = readJson(rawPath);
  const step = raw.steps?.[0];
  const candidate = step?.top_candidates?.find((item) => item.sha === candidateSha);
  const causalEvidence = candidate?.causal_evidence;
  const retrieval = causalEvidence?.retrieval;
  if (!step || !candidate || !causalEvidence || !retrieval) {
    throw new Error(`incomplete BCR retrieval payload: ${issue}: ${runLabel}`);
  }

  return {
    issue,
    run_label: raw.run_label,
    step: step.step,
    candidate_sha: shortSha(candidate.sha),
    candidate_subject: candidate.subject,
    candidate_rank: candidate.rank,
    selected_for_build: shortSha(step.sha) === shortSha(candidate.sha),
    retrieval: {
      selected_files: retrieval.selected_files || [],
      selected_hunks: (retrieval.selected_hunks || []).map((hunk) => ({
        path: hunk.path,
        header: hunk.header,
        match_reasons: hunk.match_reasons || [],
        symbols: (hunk.symbols || []).slice(0, 5),
        source_kind: hunk.source_kind || "implementation",
        patch: clip(hunk.patch || "", 600),
      })),
      function_contexts: (retrieval.function_contexts || []).map((context) => ({
        path: context.path,
        symbol: context.symbol,
        context: clip(context.context || "", 600),
      })),
      omitted_hunk_count: retrieval.omitted_hunk_count || 0,
      raw_diff_chars: retrieval.raw_diff_chars || 0,
      raw_diff_truncated: Boolean(retrieval.raw_diff_truncated),
      retrieval_policy: retrieval.retrieval_policy || "balanced (not persisted in this historical run)",
      test_fallback_used: Boolean(retrieval.test_fallback_used),
    },
    causal_evidence: {
      summary: causalEvidence.summary || "",
      changed_symbols: (causalEvidence.changed_symbols || []).slice(0, 8),
      behavioral_change: (causalEvidence.behavioral_change || []).slice(0, 3),
      issue_link: causalEvidence.issue_link || {},
      confidence: causalEvidence.confidence,
      build_risk: (causalEvidence.build_risk || []).slice(0, 3),
    },
    shared_scorer_handoff: {
      diff_extraction: candidate.diff_extraction,
      diff_summary_injected: Boolean(candidate.diff_summary),
      semantic_score: candidate.semantic_score,
      build_success_prob: candidate.build_success_prob,
      selection_score: candidate.selection_score,
      generic_score_evidence_persisted: false,
      runner_selected_sha: shortSha(step.sha),
      runner_verdict: step.verdict,
    },
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
    state: "completed",
    steps: raw.steps.length,
    skips: raw.steps.filter((step) => step.verdict === "skip").length,
    first_bad: raw.first_bad_commit,
    generated_keywords: derivation.keywords,
    source: rawPath.includes("/edu/") ? "edu-server" : "aws-server",
    run_label: raw.run_label,
    status: "clean",
  };
}

function oracleControlCell(summary, canonicalFirstBad) {
  const firstBadMatches = summary.first_bad && sameCommit(summary.first_bad, canonicalFirstBad);
  return {
    ...summary,
    first_bad_matches: Boolean(firstBadMatches),
    status: summary.state === "completed" ? "clean" : summary.state,
  };
}

function loadOraclePatchProofDiagnostic(rawIdx, issue, canonicalFirstBad) {
  const rawPath = rawIdx.find(
    (file) =>
      file.name.startsWith(issue + "-") &&
      file.name.includes("oracle-first-bad-major-tuned-patch")
  )?.path;

  if (!rawPath) {
    return {
      state: "not_run",
      steps: null,
      skips: null,
      first_bad: null,
      proof_passed: false,
      source: null,
      run_label: null,
      note: "No copied patch-proof history.",
    };
  }

  const raw = readJson(rawPath);
  const steps = Array.isArray(raw.steps) ? raw.steps : [];
  const skips = steps.filter((step) => step.verdict === "skip").length;
  const proof = raw.oracle_diagnostic?.parent_proof;
  const resolved =
    isCompletedFirstBadRun(raw) &&
    proof?.passed === true &&
    sameCommit(raw.first_bad_commit, canonicalFirstBad);
  const source = rawPath.includes("/edu/") ? "edu-server" : "aws-server";

  if (resolved) {
    return {
      state: "completed",
      steps: steps.length,
      skips,
      first_bad: raw.first_bad_commit,
      proof_passed: true,
      source,
      run_label: raw.run_label,
      note: "Runner proved bad(candidate) and good(parent).",
    };
  }

  if (raw.status === "in_progress") {
    return {
      state: "running",
      steps: steps.length,
      skips,
      first_bad: null,
      proof_passed: false,
      source,
      run_label: raw.run_label,
      note: "Proof run is still active; no accepted boundary yet.",
    };
  }

  return {
    state: "unresolved",
    steps: steps.length,
    skips,
    first_bad: null,
    proof_passed: false,
    source,
    run_label: raw.run_label,
    note:
      skips > 0
        ? "Source-build skips exhausted the step budget before an accepted parent proof."
        : "The run ended without a runner-backed first-bad parent proof.",
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
      state: "completed",
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
      issue_specific: { ...ablation.specific_keyword, state: "completed" },
      shared_crash: { ...ablation.general_keyword, state: "completed" },
      weak_maintenance: { ...weak, state: "completed" },
      oracle_first_bad: loadOracleFirstBadDiagnostic(rawIdx, base.issue, canonical),
      oracle_posterior_control: oracleControlCell(ORACLE_POSTERIOR_CONTROL[base.issue], canonical),
      oracle_patch_proof: loadOraclePatchProofDiagnostic(rawIdx, base.issue, canonical),
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
    "oracle_posterior_control",
    "oracle_patch_proof",
  ]) {
    const rows = keywordRows
      .map((row) => ({ result: row[key], canonical: row.canonical_first_bad }))
      .filter(({ result }) => result.state === "completed");
    const steps = rows.map(({ result }) => result.steps);
    keywordAggregate[key] = {
      count: rows.length,
      avg_steps: average(steps),
      median_steps: median(steps),
      first_bad_matches: rows.filter(
        ({ result, canonical }) => sameCommit(result.first_bad, canonical)
      ).length,
    };
  }

  const patchProofRows = keywordRows.map((row) => row.oracle_patch_proof);
  const patchProofSummary = {
    ...ORACLE_PATCH_PROOF,
    status: "diagnostic-only, partial",
    completed_count: patchProofRows.filter((row) => row.state === "completed").length,
    running_count: patchProofRows.filter((row) => row.state === "running").length,
    unresolved_count: patchProofRows.filter((row) => row.state === "unresolved").length,
    completed_issues: keywordRows
      .filter((row) => row.oracle_patch_proof.state === "completed")
      .map((row) => row.issue),
    running_issues: keywordRows
      .filter((row) => row.oracle_patch_proof.state === "running")
      .map((row) => row.issue),
    unresolved_issues: keywordRows
      .filter((row) => row.oracle_patch_proof.state === "unresolved")
      .map((row) => row.issue),
  };

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
      oracle_posterior_control: {
        definition:
          "The same first-bad-derived terms are retained with the normal calibrated-posterior selector, rather than semantic-only selection.",
        status: "diagnostic-only, 9/10 completed; 1 stopped",
        warning:
          "This answer-leaking control is excluded from all benchmark aggregates. Its 9 completed rows average 12.1 builds, so lexical terms alone did not improve the normal posterior policy.",
      },
      oracle_patch_proof: {
        ...patchProofSummary,
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
  const terraSingleParentRawIdx = indexJsonFiles(TERRA_SINGLE_PARENT_RAW_DIRS);
  const deterministicFactsBcrRawIdx = indexJsonFiles(DETERMINISTIC_FACTS_BCR_RAW_DIRS);
  const deterministicFactsV16WindowRawIdx = indexJsonFiles(
    DETERMINISTIC_FACTS_V16_WINDOW_RAW_DIRS
  );
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
  k12Variants.causal_retrieval_example = buildCausalRetrievalExample(rawIdx);
  const expansionComparison = buildMethodExpansionComparison(preferred, keywordAblation, profiles);
  const terraBcrComparison = buildTerraBcrComparison(
    preferred,
    profiles,
    rawIdx,
    terraSingleParentRawIdx
  );
  const masterStatus = loadMasterStatus();
  const firstBadCausalityStudy = loadFirstBadCausalityStudy(profiles);
  const deterministicFactsBcr = buildDeterministicFactsBcrComparison(
    preferred,
    profiles,
    terraBcrComparison,
    firstBadCausalityStudy,
    deterministicFactsBcrRawIdx
  );
  const deterministicFactsBcrV16Window = buildDeterministicFactsV16WindowComparison(
    preferred,
    profiles,
    deterministicFactsBcr,
    deterministicFactsV16WindowRawIdx
  );
  const heuristicFactorAblation = buildHeuristicFactorAblation(
    preferred,
    profiles,
    keywordAblation
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
    k12_variants: k12Variants,
    expansion_comparison: expansionComparison,
    terra_bcr_comparison: terraBcrComparison,
    deterministic_facts_bcr: deterministicFactsBcr,
    deterministic_facts_bcr_v16_window: deterministicFactsBcrV16Window,
    heuristic_factor_ablation: heuristicFactorAblation,
    live_lanes: liveLanes,
    master_status: masterStatus,
    first_bad_causality_study: firstBadCausalityStudy,
    runtime_example: buildRuntimeExample(rawIdx, profiles),
    issues,
  };

  if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, JSON.stringify(out, null, 2));
  const kb = (Buffer.byteLength(JSON.stringify(out)) / 1024).toFixed(1);
  console.log(`Wrote ${OUT_FILE} (${kb} KB) with ${issues.length} issues.`);
}

main();
