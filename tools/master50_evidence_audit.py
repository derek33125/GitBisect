"""Fail-closed validation for a complete master-50 evidence bundle."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Direct execution sets sys.path to tools/, not the repository root.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.master50_crash_capture import (
    MAX_ARTIFACT_BYTES,
    has_crash_marker,
    runtime_signal_presence,
    runtime_signal_gaps,
    running_pass_evidence,
)


REQUIRED_FILES = (
    "metadata.json",
    "first-bad.patch",
    "first-bad-compact.diff",
    "crash-assertion.err",
)
GENERIC_EXCERPTS = frozenset(
    {
        "A crash/assertion marker was captured at the profile bad endpoint.",
        "A crash/assertion marker was captured at the validated first-bad commit.",
    }
)
@dataclass(frozen=True)
class AuditReport:
    case_count: int
    evidence_count: int
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def audit_bundle(
    bundle_root: Path,
    *,
    expected_case_count: int,
    require_runtime_signals: bool = False,
) -> AuditReport:
    cases_root = bundle_root / "cases"
    case_dirs = sorted(path for path in cases_root.iterdir() if path.is_dir())
    failures: list[str] = []
    evidence_count = 0

    for case_dir in case_dirs:
        issue = case_dir.name
        missing = [
            name
            for name in REQUIRED_FILES
            if not (case_dir / name).is_file() or (case_dir / name).stat().st_size == 0
        ]
        if missing:
            failures.append(f"{issue}: missing or empty files: {', '.join(missing)}")
            continue

        try:
            metadata = json.loads((case_dir / "metadata.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{issue}: invalid metadata.json: {exc}")
            continue
        if metadata.get("crash_evidence", {}).get("kind") not in {"assertion", "crash"}:
            failures.append(f"{issue}: metadata does not declare concrete crash evidence")

        capture = metadata.get("crash_capture") or {}
        validated_first_bad = str(metadata.get("validated_first_bad", ""))
        # Older curated cases have no replay record. When a replay record is
        # present, validate it strictly rather than silently trusting it.
        if capture:
            if capture.get("tested_sha") != validated_first_bad:
                failures.append(f"{issue}: capture was not run at the validated first-bad SHA")
            if capture.get("validated_first_bad") != validated_first_bad:
                failures.append(f"{issue}: capture does not record the validated first-bad SHA")
            if capture.get("capture_target") != "validated-first-bad":
                failures.append(f"{issue}: capture target is not validated-first-bad")
            if capture.get("marker_detected") is not True:
                failures.append(f"{issue}: capture did not record a concrete crash marker")

        reproducers = [
            path
            for path in case_dir.rglob("*")
            if path.is_file()
            and path.stat().st_size > 0
            and (
                path.name.startswith("reproducer")
                or "reproducer" in path.relative_to(case_dir).parts
            )
        ]
        if not reproducers:
            failures.append(f"{issue}: missing packaged reproducer")

        artifact = case_dir / "crash-assertion.err"
        if artifact.stat().st_size > MAX_ARTIFACT_BYTES:
            failures.append(
                f"{issue}: crash-assertion.err is too large ({artifact.stat().st_size} bytes)"
            )
        text = artifact.read_text(errors="replace")
        expected_signals = runtime_signal_presence(text)
        metadata_signals = metadata.get("crash_evidence", {}).get("runtime_signals")
        if metadata_signals is not None and metadata_signals != expected_signals:
            failures.append(f"{issue}: metadata runtime signals do not match crash artifact")
        expected_status = "complete" if all(expected_signals.values()) else "incomplete"
        metadata_status = metadata.get("crash_evidence", {}).get("runtime_signal_status")
        if metadata_status is not None and metadata_status != expected_status:
            failures.append(f"{issue}: metadata runtime signal status does not match crash artifact")
        if not has_crash_marker(text):
            failures.append(f"{issue}: crash-assertion.err has no concrete crash marker")
        else:
            evidence_count += 1
        if require_runtime_signals:
            for signal in runtime_signal_gaps(text):
                failures.append(f"{issue}: crash-assertion.err lacks required {signal}")

        pass_evidence = running_pass_evidence(text)
        metadata_pass = metadata.get("crash_evidence", {})
        for key in ("running_pass", "component", "scope", "target"):
            if pass_evidence is None:
                if key in metadata_pass:
                    failures.append(f"{issue}: metadata claims a Running pass absent from artifact")
                break
            if metadata_pass.get(key) != pass_evidence[key]:
                failures.append(f"{issue}: metadata Running pass facts do not match crash artifact")
                break

        excerpt = metadata.get("crash_evidence", {}).get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            failures.append(f"{issue}: metadata has no crash evidence excerpt")
        elif excerpt in GENERIC_EXCERPTS:
            failures.append(f"{issue}: metadata has a generic crash evidence excerpt")
        elif excerpt not in text:
            failures.append(f"{issue}: metadata excerpt is absent from crash-assertion.err")

    if len(case_dirs) != expected_case_count:
        failures.append(
            f"case count {len(case_dirs)} does not match expected {expected_case_count}"
        )
    return AuditReport(len(case_dirs), evidence_count, tuple(failures))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed audit of a master-50 evidence bundle."
    )
    parser.add_argument("bundle_root", type=Path)
    parser.add_argument("--expected-case-count", type=int, required=True)
    parser.add_argument(
        "--require-runtime-signals",
        action="store_true",
        help="require a crash marker, stack trace, and explicit runtime-pass record",
    )
    args = parser.parse_args()

    report = audit_bundle(
        args.bundle_root,
        expected_case_count=args.expected_case_count,
        require_runtime_signals=args.require_runtime_signals,
    )
    print(f"case_count={report.case_count}")
    print(f"evidence_count={report.evidence_count}")
    for failure in report.failures:
        print(f"failure: {failure}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
