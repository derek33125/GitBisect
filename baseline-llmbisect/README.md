# Baseline 2: No-Patch LLMBisect-Style Static Ranker

This folder documents the adapted LLMBisect baseline for our LLVM crash benchmark.

The original LLMBisect workflow assumes a vulnerability fix patch. Our benchmark
usually starts from a validated good/bad interval plus issue evidence and a
runner, so this baseline intentionally does not require a fix patch and does not
run builds.

## Original Workflow And Adaptation

The published LLMBisect workflow begins with a known vulnerability-fixing
patch. It uses the patch to generate bug-inducing-commit candidates, applies
LLM-assisted filtering, and finalizes a ranked result. That input is not
available for a compiler crash reported before its eventual fix is known.

This baseline therefore reconstructs only the comparable static-ranker part of
that workflow. It receives a validated LLVM good/bad interval and issue
evidence instead of a fix patch. It enumerates every commit in the interval,
collects each commit's subject, body, changed files, and bounded parent diff,
then returns a deterministic top-k suspect list. It does not infer a patch,
does not use a future fixing commit, and does not test candidates.

The adaptation makes the input and target comparable to `git bisect` and the
online LM-bisect pipeline: all methods start from the same interval and are
evaluated against the same known first-bad boundary. It is not a claim that the
static ranker is a replacement for build-grounded bisection.

## Inputs And Outputs

Inputs are read from `tools/lm_bisect_profiles.json` and an LLVM checkout:

- **Issue profile**: issue ID, title, crash/bug summary, curated keywords,
  relevant paths, high-risk paths, and validated good/bad endpoint SHAs.
- **Candidate interval**: `git rev-list --reverse good..bad`, optionally
  prefix-limited only for a smoke test.
- **Commit evidence**: subject, body, changed-file list, and a bounded parent
  diff obtained with `git show`.

The command writes one JSON object with `interval_size`, requested `top_k`, and
ranked candidates. Every ranked candidate contains its SHA, original interval
index, subject, score, contributing signal generators, textual evidence, and
changed files. `prompt_payload` preserves the no-patch comparative prompt
shape, but the current implementation is deterministic and makes no model API
call. It is intentionally a static ranking artifact, not a tested search trace
or a first-bad proof.

## Scoring Signals

`baseline_llmbisect/static_ranker.py` combines four transparent signals:

- `trace_symbol`: crash/assertion identifiers and symbol-like issue terms found
  in a candidate's metadata or parent diff.
- `tool_component`: changes under profile-relevant or high-risk LLVM/Clang
  paths.
- `message_keyword`: issue-title, issue-summary, and profile-keyword matches in
  the subject, body, file names, or diff.
- `interval_background`: a small deterministic fallback/middle bias when no
  stronger signal distinguishes a candidate.

Commits whose subject mentions crash, assertion, fix, or regression receive a
small additional signal. Documentation-style changes are penalized. Scores are
sorted descending, with original interval order and SHA providing deterministic
tie breaks. These are static ranking signals, so a high rank is a hypothesis,
not a runner verdict.

## Usage

```bash
python3 -m baseline_llmbisect.static_ranker rank \
  --repo /path/to/llvm-project \
  --profiles tools/lm_bisect_profiles.json \
  --issue pr172195 \
  --top-k 10 \
  --output results/baseline-llmbisect/pr172195.json
```

Use `--limit N` only for smoke tests; benchmark runs should rank the full
validated interval.

## Relationship To Online Variants

The static ranker is **Baseline 2**, a no-patch LLMBisect-style comparison. It
does not share online state with `tools/lm_bisect.py`: it never checks out or
builds a candidate, records no `good`/`bad`/`skip` observation, and cannot
contract an interval. Its output is evaluated as a top-k candidate ranking.

The online pipeline uses the same issue profile and interval but repeatedly
chooses one candidate, runs the issue-specific build/reproducer, writes JSON
history and cache artifacts, and updates the unresolved good/bad boundary.
Its parent-diff + LLM mode uses a deterministic prefilter followed by a model
frontier. Parent-diff extraction is either a compact LLM summary or structured
causal evidence; it never requires a fix patch.

Four fixed-k12 online variants are evaluated separately from this baseline:

- **Evidence-guided diverse frontier**: a twelve-candidate frontier deliberately
  mixes the semantic leader, an information-gain probe, and an issue-relevant
  component-diverse probe.
- **Structured causal parent-diff reasoning**: retrieves issue-matched parent
  diff hunks plus local function context and extracts changed symbols,
  behavioural mechanism, issue linkage, confidence, and build risk before
  scoring.
- **Observation-conditioned posterior**: after a runner verdict, reweights
  unresolved candidates sharing extracted component/mechanism evidence.
- **Confidence-adaptive frontier**: at a fixed frontier size, agreement keeps
  semantic candidates while disagreement substitutes posterior and midpoint
  anchors for exploration.

These policies are online experimental variants, not part of the static
baseline's score. Their scoped-ten result tables are published as JSON in
`benchmark-results/scoped10/k12-variants.json`.

## Difference From Original LLMBisect

- No fix patch is required.
- Candidate generation is crash/issue-derived instead of patch-derived.
- The method outputs top-k ranked commits, not an online build/test path.
- It does not update a good/bad interval and does not handle skip verdicts.
