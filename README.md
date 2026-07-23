# LLVM Crash Bisect Benchmark

## Overview

This repository studies build-grounded identification of the first bad LLVM
commit for compiler crash regressions. Each issue has a validated good/bad
endpoint pair, a reproducer, and an issue-specific runner. A runner checkout
builds the selected revision and returns `good`, `bad`, or `skip`; the search
therefore reports the tested path as well as its final boundary.

The code supports conventional `git bisect`, deterministic heuristic ranking,
and online model-guided search. The model-guided methods use the same endpoint
and runner contract as the non-model methods, so build counts and final
boundaries remain directly comparable. The goal is comparative bisection, not
patch generation or automated repair.

## Repository Layout

- `tools/lm_bisect.py`: online LM-bisect implementation, candidate scoring,
  diff evidence extraction, runner observations, and JSON artifacts.
- `tools/lm_bisect_profiles.json`: issue profiles with validated endpoints,
  crash evidence, paths, and runner metadata.
- `scripts/benchmark/`: reproducible queue wrappers and the fixed-k12 variant
  matrix runner.
- `scripts/pr*/`: per-issue reproducers and runner wrappers used to classify a
  checkout as good, bad, or skipped.
- `baseline_llmbisect/`: the executable no-patch LLMBisect-style static-ranker
  baseline.
- `baseline-llmbisect/`: documentation for that baseline and its relationship
  to the online methods.
- `web-presentation-data/`: the presentation site, its data builder, and the
  source tables for the scoped-ten evaluation.
- `benchmark-results/scoped10/`: JSON-only public copy of the scoped-ten
  presentation results. It intentionally excludes reports, raw build trees,
  caches, and other documents.

## Method Families

### Git Bisect

The runner supplies good/bad/skip verdicts to standard Git bisection. This is
the boundary-search baseline. A skipped revision is excluded rather than
silently labelled, which can leave Git unable to prove a unique first-bad
commit.

### Heuristic-Only Search

The deterministic selector scores all unresolved candidates from issue terms,
crash/assertion symbols, changed paths, high-risk paths, and commit metadata.
It has no model call. Keyword ablations in the scoped-ten bundle separate
issue-specific profiles from fixed general vocabularies.

### Parent-Diff + LLM Extraction

The online model-guided path first uses deterministic evidence to prefilter an
unresolved frontier. For a parent diff, it compares each candidate with its
immediate Git parent. The raw diff is compressed by a separate extraction call
before the scorer sees it. The scorer combines issue evidence, changed files,
the extracted diff evidence, and prior runner observations to choose one
revision for the next build. Each build updates the good/bad interval and
writes a JSON history and observation cache.

`--model-top-k` is the number of heuristic-prefiltered candidates rescored by
the model on a step, not the total number of commits in the interval. It should
not be confused with a heuristic-only search setting.

## Fixed-k12 Variants

The four isolated variants use the same scoped intervals, runners, search
policy, and a fixed model frontier of twelve candidates. They are implemented
in `tools/lm_bisect.py` and scheduled by
`scripts/benchmark/run-k12-variant-matrix-20260722.sh`.

- **Evidence-guided diverse frontier**: selects semantic, information-gain,
  and issue-relevant component-diverse probes from a causal evidence frontier.
- **Structured causal parent-diff reasoning**: retrieves profile-matched diff
  hunks and local function context, then asks the extractor for changed
  symbols, mechanism, issue linkage, confidence, and build risk.
- **Observation-conditioned posterior**: reweights unresolved candidates that
  share component or mechanism evidence after each runner good/bad result.
- **Confidence-adaptive frontier**: uses the semantic top candidates when
  pre-model evidence agrees, and mixes semantic, posterior, and midpoint
  anchors when that evidence is diffuse or disagrees.

The variants are experimental policies, not claims that every completed row is
valid. The result bundle records row status and excludes stale-endpoint or
incomplete rows from aggregates.

## Scoped-10 Results

`benchmark-results/scoped10/` is the repository-facing copy of the ten issues
used by the web presentation. It contains JSON only:

- `manifest.json` defines the scope and provenance.
- `issues.json`, `preferred-comparison.json`, and `method-summary.json` hold
  the principal comparison tables.
- `keyword-ablation.json` and
  `weak-maintenance-keyword-control.json` hold the heuristic controls.
- `runs.json` retains per-run result metadata for the scoped cases.
- `k12-variants.json` contains the fixed-k12 variant matrix extracted from the
  presentation data contract.

The bundle is a frozen result snapshot, not a runnable benchmark input. For
the live site and data-generation command, use `web-presentation-data/`:

```bash
npm run build-data --prefix web-presentation-data
```

For the data schema and current presentation implementation, see
`web-presentation-data/README.md` and the `web-presentation-data/scoped10/`
directory.

## Running A Search

Use a prepared LLVM checkout and an issue profile with already-validated
endpoints. The benchmark queue wrappers provide the complete runner setup;
the lower-level command is useful for inspection and controlled experiments:

```bash
python3 tools/lm_bisect.py run-online \
  --issue pr204559 \
  --llvm-dir /path/to/llvm-project \
  --scorer model \
  --search-policy calibrated-posterior \
  --model-top-k 3 \
  --model-diff-mode parent \
  --model-diff-extraction llm \
  --observations /path/to/observations.json \
  --run-label experiment-name \
  --max-steps 30
```

This command can build revisions and call a configured model provider. Do not
run it against an unvalidated issue profile or publish generated build/cache
artifacts as benchmark results.

## Reproducibility Notes

The public results are limited to the scoped-ten presentation snapshot. They
do not include compiler worktrees, build directories, model credentials,
private server state, or Markdown/Word progress reports. Re-running a method
requires the matching LLVM history, compatible host toolchain, issue runner,
and model configuration; LLM outputs may vary across providers or models.
