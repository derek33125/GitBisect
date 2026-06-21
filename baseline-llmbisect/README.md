# Baseline 2: No-Patch LLMBisect-Style Static Ranker

This folder documents the adapted LLMBisect baseline for our LLVM crash benchmark.

The original LLMBisect workflow assumes a vulnerability fix patch. Our benchmark
usually starts from a validated good/bad interval plus issue evidence and a
runner, so this baseline intentionally does not require a fix patch and does not
run builds.

## Goal

Rank candidate commits in the same validated interval used by `git bisect` and
LM-Bisect. The output is a static top-k suspect list, evaluated later by whether
the known first bad commit appears near the top.

## Signals

- `trace_symbol`: issue/crash/assertion terms that appear in commit text or diff.
- `tool_component`: commits touching relevant or high-risk LLVM/Clang paths.
- `message_keyword`: commit subject/body terms matching the issue mechanism.
- `interval_background`: fallback for commits with no stronger signal.

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

## Difference From Original LLMBisect

- No fix patch is required.
- Candidate generation is crash/issue-derived instead of patch-derived.
- The method outputs top-k ranked commits, not an online build/test path.
- It does not update a good/bad interval and does not handle skip verdicts.

