# No-Patch LLMBisect-Style Static Ranker Design

## Objective

Build Baseline 2 from `results/reports/llmbisect-related-work-and-baseline-plan-20260620.md`.

The baseline uses the same validated LLVM good/bad interval as `git bisect` and
LM-Bisect, but it does not run builds. It statically ranks candidate commits
using issue evidence and commit metadata. This makes it a static localization
baseline, not a build-step baseline.

## Architecture

- `baseline_llmbisect.static_ranker` is the importable implementation.
- `baseline-llmbisect/run_static_ranker.py` is a thin folder-local entrypoint.
- Existing `tools/lm_bisect_profiles.json` remains the profile source.
- Output is JSON with method metadata, top-k candidates, generator provenance,
  and a prompt payload that can later be sent to a model.

## Adaptation From LLMBisect

Original LLMBisect uses patch-derived function and critical-line histories.
This no-patch baseline replaces those with crash-derived generators:

- `trace_symbol`: issue/assertion/stack terms found in commit text or diff.
- `tool_component`: relevant and high-risk paths from the issue profile.
- `message_keyword`: commit message/body terms matching issue mechanisms.
- `interval_background`: fallback for candidates without stronger signals.

The baseline intentionally avoids fix-patch assumptions, vulnerability wording,
kernel-specific history logic, and online build/test decisions.

## Evaluation To Add Later

When benchmark runs are ready, evaluate:

- top-1, top-3, top-5 hit rate;
- rank of the known first bad commit;
- static candidate recall before optional LLM filtering;
- distance from predicted commit to true first bad in the interval.

