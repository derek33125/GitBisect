#!/usr/bin/env python3
"""Refresh and archive an already captured master-50 evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

# Direct execution sets sys.path to tools/, not the repository root.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.master50_crash_capture import backfill_running_passes
from tools.master50_evidence_audit import audit_bundle


DEFAULT_BUNDLE = ROOT_DIR / "human_analysis" / "raw" / "master50-evidence-20260903-r4"
DEFAULT_OUTPUT = ROOT_DIR / "human_analysis" / "raw"
EXPECTED_CASE_COUNT = 50


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cases(bundle_root: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for metadata_path in sorted((bundle_root / "cases").glob("*/metadata.json")):
        cases.append(json.loads(metadata_path.read_text(encoding="utf-8")))
    return cases


def provenance(case: dict[str, Any]) -> str:
    return "fresh first-bad replay" if case.get("crash_capture") else "curated first-bad artifact"


def runtime_signal_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Count factual runtime signals after metadata has been backfilled."""

    signal_names = ("failure_marker", "stack_trace", "running_pass")
    summary = {
        signal: sum(
            bool(case.get("crash_evidence", {}).get("runtime_signals", {}).get(signal))
            for case in cases
        )
        for signal in signal_names
    }
    summary["complete"] = sum(
        case.get("crash_evidence", {}).get("runtime_signal_status") == "complete"
        for case in cases
    )
    summary["incomplete"] = len(cases) - summary["complete"]
    return summary


def runtime_signal_label(evidence: dict[str, Any]) -> str:
    """Render only observed signal presence; never invent a pass assignment."""

    signals = evidence.get("runtime_signals", {})
    present = [
        label
        for key, label in (
            ("failure_marker", "marker"),
            ("stack_trace", "stack"),
            ("running_pass", "pass"),
        )
        if signals.get(key)
    ]
    missing = [
        label
        for key, label in (
            ("failure_marker", "marker"),
            ("stack_trace", "stack"),
            ("running_pass", "pass"),
        )
        if not signals.get(key)
    ]
    if not missing:
        return "complete (marker, stack, pass)"
    return f"present: {', '.join(present) or 'none'}; missing: {', '.join(missing)}"


def render_review_report(cases: list[dict[str, Any]]) -> str:
    runtime = runtime_signal_summary(cases)
    lines = [
        "# Master-50 Manual First-Bad Review Guide",
        "",
        "This guide accompanies the marker-focused first-bad evidence bundle. It is for manual, answer-leaking inspection of known first-bad boundaries, not a fair LM-bisect benchmark.",
        "",
        "## Review Protocol",
        "",
        "1. Read `first-bad.patch` and compare it with `crash-assertion.err` and the packaged reproducer.",
        "2. Follow the producer-to-detector dependency chain; the assertion file need not be modified by the first-bad commit.",
        "3. Build and run the known first-bad commit and its parent, recording environment mismatches separately from causal conclusions.",
        "4. Do not provide the first-bad SHA, patch, compact diff, or derived terms to deployable LM-bisect scoring.",
        "",
        "## Audit Summary",
        "",
        f"- Cases: **{len(cases)}/{EXPECTED_CASE_COUNT}**",
        f"- Concrete assertion artifacts: **{sum(case['crash_evidence']['kind'] == 'assertion' for case in cases)}/{len(cases)}**",
        f"- Concrete signal-only crash artifacts: **{sum(case['crash_evidence']['kind'] == 'crash' for case in cases)}/{len(cases)}**",
        f"- Fresh validated-first-bad replays: **{sum(bool(case.get('crash_capture')) for case in cases)}/{len(cases)}**",
        f"- Curated first-bad artifacts without a new replay record: **{sum(not bool(case.get('crash_capture')) for case in cases)}/{len(cases)}**",
        f"- Concrete failure markers: **{runtime['failure_marker']}/{len(cases)}**",
        f"- Stack traces: **{runtime['stack_trace']}/{len(cases)}**",
        f"- Explicit LLVM running-pass records: **{runtime['running_pass']}/{len(cases)}**",
        f"- Complete marker/stack/pass triplets: **{runtime['complete']}/{len(cases)}**",
        "",
        "Every case contains `metadata.json`, `first-bad.patch`, `first-bad-compact.diff`, `crash-assertion.err`, and a packaged reproducer. Runtime fields record only emitted diagnostics: a missing pass is not inferred from stack frames or source paths.",
        "",
        "## Case Index",
        "",
        "| Issue | First bad | Evidence | Runtime signals | Provenance | Manual priority |",
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        kind = case["crash_evidence"]["kind"]
        priority = "patch-to-trace confirmation" if kind == "assertion" else "crash-signal confirmation"
        lines.append(
            f"| `{case['issue']}` | `{case['validated_first_bad'][:12]}` | {kind} | {runtime_signal_label(case['crash_evidence'])} | {provenance(case)} | {priority} |"
        )

    for case in cases:
        evidence = case["crash_evidence"]
        static = case.get("manual_static_analysis", {})
        overlap = case.get("static_overlap", {})
        lines.extend(
            [
                "",
                f"## {case['issue']}: {case['title']}",
                "",
                f"- Known first bad: `{case['validated_first_bad']}` ({case['first_bad_subject']})",
                f"- Immediate parent to prove good: `{case.get('validated_first_bad_parent', '<not recorded>')}`",
                f"- Crash/assertion: {evidence['excerpt']}",
                f"- Source evidence status: `{evidence['kind']}`; provenance: {provenance(case)}.",
                f"- Runtime signals: {runtime_signal_label(evidence)}.",
                f"- Dependency chain: {static.get('dependency_chain', 'Not recorded.')}",
                f"- What to inspect: {static.get('inspection_focus', 'Compare the first-bad patch with the reproducer and failure evidence.')}",
                f"- Changed files: {', '.join(case.get('changed_files', []))}",
                f"- Static overlap: `{overlap.get('classification', 'unknown')}`; paths: {', '.join(overlap.get('path_overlap', [])) or 'none'}.",
                "- Candidate to inspect first: the known first-bad patch in this bundle. This is intentionally answer-leaking and must remain outside any fair method comparison.",
            ]
        )
    return "\n".join(lines) + "\n"


def refresh_bundle(
    bundle_root: Path,
    *,
    output_dir: Path,
    label: str,
    supersedes: str,
) -> Path:
    backfill_running_passes(bundle_root)
    report = audit_bundle(bundle_root, expected_case_count=EXPECTED_CASE_COUNT)
    if not report.ok:
        raise ValueError("refusing to package an invalid bundle: " + "; ".join(report.failures))

    cases = load_cases(bundle_root)
    runtime = runtime_signal_summary(cases)
    review_path = bundle_root / "manual-first-bad-review.md"
    review_path.write_text(render_review_report(cases), encoding="utf-8")
    (bundle_root / "README.md").write_text(
        "\n".join(
            [
                f"# Master-50 First-Bad Evidence Bundle {label}",
                "",
                "This bundle contains the validated first-bad patch, compact diff, marker-focused failure output, and reproducer for all 50 benchmark cases.",
                "",
                "The failure artifact is captured output beginning at the first concrete assertion or crash marker, not a build log prefix. It is therefore bounded and excludes the unrelated ninja/CMake preamble that made the previous package difficult to audit.",
                "",
                f"- Cases: **50/50**",
                f"- Assertion markers: **{sum(case['crash_evidence']['kind'] == 'assertion' for case in cases)}**",
                f"- Signal-only crash markers: **{sum(case['crash_evidence']['kind'] == 'crash' for case in cases)}**",
                f"- Fresh validated-first-bad replays: **{sum(bool(case.get('crash_capture')) for case in cases)}**",
                f"- Curated first-bad artifacts without a new replay record: **{sum(not bool(case.get('crash_capture')) for case in cases)}**",
                f"- Concrete failure markers: **{runtime['failure_marker']}/{EXPECTED_CASE_COUNT}**",
                f"- Stack traces: **{runtime['stack_trace']}/{EXPECTED_CASE_COUNT}**",
                f"- Explicit LLVM running-pass records: **{runtime['running_pass']}/{EXPECTED_CASE_COUNT}**",
                f"- Complete marker/stack/pass triplets: **{runtime['complete']}/{EXPECTED_CASE_COUNT}**",
                "",
                "The first-bad SHA, patch, and compact diff are answer-leaking material for manual review. They must not be supplied to a fair LM-bisect run. `crash-assertion.err` remains the compatibility filename for signal-only crash cases, which are labeled `crash` rather than being overstated as assertions. Missing running-pass records are retained as incomplete diagnostics, not guessed components.",
                "",
                "See `manual-first-bad-review.md` for the case-by-case review guide and provenance distinction.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_root / "AUDIT.md").write_text(
        "\n".join(
            [
                f"# Master-50 Evidence Audit {label}",
                "",
                "The structural fail-closed audit passed after the first-bad evidence refresh. The runtime-signal counts below distinguish complete captures from artifacts where a diagnostic was not emitted.",
                "",
                f"- Cases: **{report.case_count}/{EXPECTED_CASE_COUNT}**",
                f"- Concrete failure artifacts: **{report.evidence_count}/{EXPECTED_CASE_COUNT}**",
                f"- Fresh validated-first-bad replays: **{sum(bool(case.get('crash_capture')) for case in cases)}/{EXPECTED_CASE_COUNT}**",
                f"- Curated first-bad artifacts without a new replay record: **{sum(not bool(case.get('crash_capture')) for case in cases)}/{EXPECTED_CASE_COUNT}**",
                "- Packaged reproducers: **50/50**",
                f"- Complete marker/stack/pass triplets: **{runtime['complete']}/{EXPECTED_CASE_COUNT}**",
                f"- Incomplete runtime triplets: **{runtime['incomplete']}/{EXPECTED_CASE_COUNT}**",
                "",
                "No endpoint-derived artifact is used for the refreshed cases. Signal-only cases are labeled `crash`; no assertion text or running-pass component is synthesized.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    files = [
        {
            "path": path.relative_to(bundle_root).as_posix(),
            "sha256": file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(bundle_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    manifest = {
        "bundle": "master-fifty-first-bad-evidence",
        "label": label,
        "supersedes": supersedes,
        "case_count": len(cases),
        "crash_evidence": {
            "assertion": sum(case["crash_evidence"]["kind"] == "assertion" for case in cases),
            "crash": sum(case["crash_evidence"]["kind"] == "crash" for case in cases),
        },
        "provenance": {
            "fresh_validated_first_bad_replays": sum(bool(case.get("crash_capture")) for case in cases),
            "curated_first_bad_artifacts": sum(not bool(case.get("crash_capture")) for case in cases),
        },
        "running_pass": {
            "cases_with_explicit_pass": runtime["running_pass"],
            "component_source": "last explicit Running pass record in crash-assertion.err",
            "not_inferred": True,
        },
        "runtime_signals": {
            **runtime,
            "required_for_complete": ["failure_marker", "stack_trace", "running_pass"],
            "not_inferred": True,
        },
        "scope": [case["issue"] for case in cases],
        "exclusions": [
            "LM-bisect caches, full run histories, controller logs, and unrelated report documents are excluded.",
            "The first-bad patch and compact diff are answer-leaking manual-review aids, not fair-method inputs.",
        ],
        "cases": cases,
        "files": files,
        "review_guide": "manual-first-bad-review.md",
    }
    (bundle_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    final_report = audit_bundle(bundle_root, expected_case_count=EXPECTED_CASE_COUNT)
    if not final_report.ok:
        raise ValueError("refreshed bundle failed audit: " + "; ".join(final_report.failures))

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"master50-first-bad-evidence-{label}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                archive.write(path, f"{bundle_root.name}/{path.relative_to(bundle_root).as_posix()}")
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--label", default="20260903-r4")
    parser.add_argument("--supersedes", default="master50-first-bad-evidence-20260902-r2.zip")
    args = parser.parse_args()
    print(refresh_bundle(args.bundle, output_dir=args.output_dir, label=args.label, supersedes=args.supersedes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
