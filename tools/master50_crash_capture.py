#!/usr/bin/env python3
"""Record marker-focused master-50 crash evidence at validated first-bad SHAs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
CRASH_MARKER_RE = re.compile(
    r"(?:"
    r"Assertion [`'].*?(?:failed\.?|$)"
    r"|PLEASE submit a bug report"
    r"|Stack dump:"
    r"|UNREACHABLE executed"
    r"|fatal error: error in backend"
    r"|Signalled during .+ action:"
    r"|frontend command failed with exit code [1-9]"
    r"|(?:AddressSanitizer|UndefinedBehaviorSanitizer):"
    r"|\b(?:Segmentation fault|SIGSEGV|SIGABRT)\b"
    r")",
    re.IGNORECASE,
)
STACK_RUNNING_PASS_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:\*+\s*)?Running pass\s+"
    r"(?P<quote>['\"])(?P<stack_pass>.+?)(?P=quote)\s+on\s+"
    r"(?P<stack_scope>function|module)\s+"
    r"(?P<target_quote>['\"])(?P<stack_target>.+?)(?P=target_quote)"
    r"\.?\s*(?:\*+\s*)?$",
    re.IGNORECASE,
)
DEBUG_RUNNING_PASS_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:\*+\s*)?Running pass:\s+"
    r"(?P<debug_pass>.+?)\s+on\s+(?P<debug_target>.+?)\s*$",
    re.IGNORECASE,
)
LEGACY_EXECUTING_PASS_RE = re.compile(
    r"^\s*Executing Pass ['\"](?P<pass>.+?)['\"] on "
    r"(?P<scope>Function|Module) ['\"](?P<target>.+?)['\"]\.\.\.\s*$",
    re.IGNORECASE,
)
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_CONTEXT_LINES = 160
MAX_PASS_EVIDENCE_BYTES = 8 * 1024
PASS_EVIDENCE_HEADER = "--- explicit LLVM Running pass records from complete replay log ---\n"


def extract_running_passes(output: str) -> list[dict[str, str]]:
    """Extract explicit LLVM runtime-pass records, preserving their order."""

    passes: list[dict[str, str]] = []
    for line in output.splitlines():
        match = STACK_RUNNING_PASS_RE.match(line)
        if match:
            stack_pass = match.group("stack_pass")
            pass_name = stack_pass
            scope = match.group("stack_scope").lower()
            target = match.group("stack_target")
        else:
            debug_match = DEBUG_RUNNING_PASS_RE.match(line)
            if debug_match:
                pass_name = debug_match.group("debug_pass")
                scope = "unknown"
                target = debug_match.group("debug_target")
            else:
                legacy_match = LEGACY_EXECUTING_PASS_RE.match(line)
                if not legacy_match:
                    continue
                pass_name = legacy_match.group("pass")
                scope = legacy_match.group("scope").lower()
                target = legacy_match.group("target")
        passes.append(
            {
                "pass": pass_name.strip(),
                "scope": scope,
                "target": target.strip().strip("'\"").rstrip("."),
            }
        )
    return passes


def running_pass_component(pass_name: str) -> str:
    """Return a stable pass-family label without guessing a subsystem."""

    match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)(?:<|\(|$)", pass_name.strip())
    if match:
        return match.group(1)
    return pass_name.strip()


def running_pass_evidence(output: str) -> dict[str, object] | None:
    """Return the most specific explicit pass record, or no inferred evidence."""

    passes = extract_running_passes(output)
    if not passes:
        return None
    selected = passes[-1]
    return {
        "running_pass": selected["pass"],
        "component": running_pass_component(selected["pass"]),
        "scope": selected["scope"],
        "target": selected["target"],
        "pass_record_count": len(passes),
    }


@dataclass(frozen=True)
class CaptureMetadata:
    issue: str
    first_bad_commit: str
    runner: Path


@dataclass(frozen=True)
class CaptureResult:
    issue: str
    marker_detected: bool
    artifact: Path | None
    source_log: str
    runtime_signal_gaps: tuple[str, ...]


def find_crash_marker(output: str) -> str | None:
    """Return the strongest concrete failing line, excluding build configuration text."""

    concrete: list[str] = []
    for line in output.splitlines():
        if "assertions:" in line.lower() and "failed" not in line.lower():
            continue
        if CRASH_MARKER_RE.search(line):
            candidate = line.strip()
            if re.search(
                r"Assertion|UNREACHABLE|fatal error: error in backend|"
                r"Broken function found|AddressSanitizer|UndefinedBehaviorSanitizer|"
                r"Signalled during .+ action:|Segmentation fault|SIGSEGV|SIGABRT|"
                r"frontend command failed with exit code [1-9]",
                candidate,
                re.IGNORECASE,
            ):
                concrete.append(candidate)
    return concrete[0] if concrete else None


def has_crash_marker(output: str) -> bool:
    """Return true only for output containing a concrete crash signal."""

    return find_crash_marker(output) is not None


def has_stack_trace(output: str) -> bool:
    """Recognize an emitted LLVM stack trace without inventing stack frames."""

    return bool(re.search(r"(?m)^Stack dump:|^#\d+\s", output))


def runtime_signal_gaps(output: str) -> tuple[str, ...]:
    """Report required bisection signals that are absent from a capture artifact."""

    return tuple(
        signal
        for signal, present in runtime_signal_presence(output).items()
        if not present
    )


def runtime_signal_presence(output: str) -> dict[str, bool]:
    """Return factual presence flags for the three non-inferable runtime signals."""

    return {
        "failure_marker": has_crash_marker(output),
        "stack_trace": has_stack_trace(output),
        "running_pass": running_pass_evidence(output) is not None,
    }


def marker_kind(marker: str) -> str:
    """Classify the concrete marker without overstating a signal as an assertion."""

    if re.search(r"Assertion|UNREACHABLE|fatal error: error in backend|Broken function found", marker, re.IGNORECASE):
        return "assertion"
    return "crash"


def marker_context(output: str, marker: str, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    """Keep the marker and its immediate diagnostic context, never build preamble."""

    lines = output.splitlines(keepends=True)
    marker_index = next(
        (index for index, line in enumerate(lines) if line.strip() == marker),
        None,
    )
    if marker_index is None:
        raise ValueError("crash marker disappeared while extracting context")
    # A process-level signal or compiler driver failure can follow LLVM's stack
    # dump. Preserve the nearest preceding dump so the artifact retains the
    # executing pass and frames, while metadata still names the concrete crash
    # marker. Do not do this for an assertion marker: an earlier stack header
    # may belong to unrelated output.
    start = marker_index
    if re.search(
        r"Segmentation fault|SIGSEGV|SIGABRT|frontend command failed with exit code",
        marker,
        re.IGNORECASE,
    ):
        previous_dump = next(
            (
                index
                for index in range(marker_index - 1, -1, -1)
                if lines[index].strip() == "Stack dump:"
            ),
            None,
        )
        if previous_dump is not None:
            start = previous_dump

    kept: list[str] = []
    size = 0
    for line in lines[start : start + MAX_CONTEXT_LINES]:
        encoded = line.encode("utf-8", errors="replace")
        if size + len(encoded) > max_bytes:
            remaining = max_bytes - size
            if remaining > 0:
                kept.append(encoded[:remaining].decode("utf-8", errors="ignore"))
            break
        kept.append(line)
        size += len(encoded)
    text = "".join(kept)
    return text if text.endswith("\n") else text + "\n"


def explicit_running_pass_lines(output: str, *, max_bytes: int = MAX_PASS_EVIDENCE_BYTES) -> str:
    """Return exact explicit pass lines, retaining the final records within the cap."""

    lines = [
        line.rstrip("\n")
        for line in output.splitlines(keepends=True)
        if (
            STACK_RUNNING_PASS_RE.match(line)
            or DEBUG_RUNNING_PASS_RE.match(line)
            or LEGACY_EXECUTING_PASS_RE.match(line)
        )
    ]
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        encoded = (line + "\n").encode("utf-8", errors="replace")
        if size + len(encoded) > max_bytes:
            break
        kept.append(line)
        size += len(encoded)
    kept.reverse()
    return "\n".join(kept) + ("\n" if kept else "")


def artifact_with_pass_evidence(
    output: str,
    marker: str,
    pass_output: str | None,
) -> str:
    """Keep marker context and append only explicit pass evidence from a full log."""

    pass_lines = explicit_running_pass_lines(pass_output or "")
    if not pass_lines:
        return marker_context(output, marker)
    # Reserve space for the final diagnostic records before taking crash context.
    # This prevents a long stack trace from silently discarding the pass evidence.
    artifact = marker_context(
        output,
        marker,
        max_bytes=MAX_ARTIFACT_BYTES
        - len(PASS_EVIDENCE_HEADER.encode("utf-8"))
        - len(pass_lines.encode("utf-8", errors="replace")),
    )
    return artifact + PASS_EVIDENCE_HEADER + pass_lines


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_capture_metadata(
    root: Path,
    evidence_root: Path,
    issue: str,
    profiles_path: Path | None = None,
) -> CaptureMetadata:
    profiles = json.loads(
        (profiles_path or root / "tools" / "lm_bisect_profiles.json").read_text(
            encoding="utf-8"
        )
    )
    try:
        profile = profiles[issue]
    except KeyError as exc:
        raise KeyError(f"unknown master-50 issue: {issue}") from exc
    runner = Path(profile["runner"])
    runner_path = root / runner
    if not runner_path.is_file() or not runner_path.stat().st_mode & 0o111:
        raise FileNotFoundError(f"missing executable runner: {runner_path}")
    metadata_path = evidence_root / "cases" / issue / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing evidence metadata: {metadata_path}")
    evidence_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    first_bad_commit = str(evidence_metadata.get("validated_first_bad", ""))
    if not re.fullmatch(r"[0-9a-fA-F]{40}", first_bad_commit):
        raise ValueError(
            f"validated_first_bad is not a full SHA for {issue}: {first_bad_commit}"
        )
    return CaptureMetadata(
        issue=issue,
        first_bad_commit=first_bad_commit,
        runner=runner,
    )


def _normalize_reproducers(
    reproducer: Path | Iterable[Path] | None,
) -> tuple[Path, ...]:
    if reproducer is None:
        return ()
    if isinstance(reproducer, Path):
        return (reproducer,)
    return tuple(path for path in reproducer if path.is_file())


def package_reproducers(case_dir: Path, reproducer: Path | Iterable[Path] | None) -> list[str]:
    """Copy current-run reproducer inputs without contaminating crash output."""

    for stale in case_dir.glob("reproducer*"):
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()

    sources = _normalize_reproducers(reproducer)
    if not sources:
        return []

    packaged: list[str] = []
    if len(sources) == 1:
        source = sources[0]
        suffix = source.suffix or ".txt"
        destination = case_dir / f"reproducer{suffix}"
        shutil.copy2(source, destination)
        return [destination.name]

    destination_root = case_dir / "reproducer"
    destination_root.mkdir()
    used_names: set[str] = set()
    for source in sources:
        name = source.name
        if name in used_names:
            name = f"{len(used_names)}-{name}"
        used_names.add(name)
        destination = destination_root / name
        shutil.copy2(source, destination)
        packaged.append(destination.relative_to(case_dir).as_posix())
    return packaged


def record_capture(
    *,
    root: Path,
    evidence_root: Path,
    issue: str,
    tested_sha: str,
    exit_code: int,
    started_at: str,
    finished_at: str,
    source_log: Path,
    pass_source_log: Path | None = None,
    reproducer: Path | Iterable[Path] | None = None,
    require_runtime_signals: bool = False,
) -> CaptureResult:
    """Persist one capture and update its case metadata without fabricating evidence."""

    if not source_log.is_file():
        raise FileNotFoundError(f"capture log does not exist: {source_log}")
    if pass_source_log is not None and not pass_source_log.is_file():
        raise FileNotFoundError(f"pass source log does not exist: {pass_source_log}")
    case_dir = evidence_root / "cases" / issue
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing evidence metadata: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    validated_first_bad = str(metadata.get("validated_first_bad", ""))
    if tested_sha.lower() != validated_first_bad.lower():
        raise ValueError(
            f"tested SHA for {issue} is not the validated first-bad: "
            f"tested={tested_sha}, validated={validated_first_bad}"
        )
    if not re.fullmatch(r"[0-9a-fA-F]{40}", validated_first_bad):
        raise ValueError(f"invalid validated first-bad SHA for {issue}: {validated_first_bad}")

    output = source_log.read_text(errors="replace")
    pass_output = (
        pass_source_log.read_text(errors="replace") if pass_source_log is not None else None
    )
    marker = find_crash_marker(output)
    marker_detected = marker is not None
    artifact = case_dir / "crash-assertion.err"
    source = _relative_path(source_log, root)
    attempt = {
        "tested_sha": tested_sha,
        "validated_first_bad": validated_first_bad,
        "capture_target": "validated-first-bad",
        "exit_code": exit_code,
        "marker_detected": marker_detected,
        "started_at": started_at,
        "finished_at": finished_at,
        "source_log": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if pass_source_log is not None:
        attempt["pass_source_log"] = _relative_path(pass_source_log, root)

    # A failed retry must not erase a curated or previously successful capture.
    # Keep unsuccessful attempts separately; only a concrete marker can replace
    # the canonical evidence and reproducer set.
    existing_artifact = artifact.is_file() and has_crash_marker(
        artifact.read_text(errors="replace")
    )
    attempts = metadata.setdefault("crash_capture_attempts", [])
    candidate_artifact = (
        artifact_with_pass_evidence(output, marker, pass_output) if marker_detected else ""
    )
    signal_presence = runtime_signal_presence(candidate_artifact)
    attempt["runtime_signals"] = signal_presence
    attempt["runtime_signal_gaps"] = [
        signal for signal, present in signal_presence.items() if not present
    ]
    attempt["runtime_signal_status"] = (
        "complete" if not attempt["runtime_signal_gaps"] else "incomplete"
    )
    attempts.append(attempt)
    if marker_detected and (
        not require_runtime_signals or not attempt["runtime_signal_gaps"]
    ):
        artifact.write_text(candidate_artifact, encoding="utf-8")
        packaged_reproducers = package_reproducers(case_dir, reproducer)
        metadata.pop("packaged_reproducer", None)
        metadata["crash_capture"] = attempt
        metadata["crash_evidence"] = {
            "kind": marker_kind(marker),
            "excerpt": marker,
            "source_artifact": source,
            "packaged_artifact": artifact.name,
        }
        pass_evidence = running_pass_evidence(artifact.read_text(errors="replace"))
        if pass_evidence:
            metadata["crash_evidence"].update(pass_evidence)
            metadata["crash_evidence"]["source"] = artifact.name
    elif not existing_artifact:
        metadata.pop("crash_capture", None)
        metadata.pop("packaged_reproducer", None)
        for stale in case_dir.glob("reproducer*"):
            if stale.is_dir():
                shutil.rmtree(stale)
            else:
                stale.unlink()
        metadata["crash_evidence"] = {
            "kind": "missing",
            "excerpt": "No concrete crash/assertion marker was produced at the validated first-bad commit.",
            "source_artifact": "",
        }
    # Otherwise the existing artifact, metadata evidence, and reproducers stay
    # untouched; the attempt record is the only new state.
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return CaptureResult(
        issue=issue,
        marker_detected=marker_detected,
        artifact=(
            artifact
            if marker_detected
            and (not require_runtime_signals or not attempt["runtime_signal_gaps"])
            else None
        ),
        source_log=source,
        runtime_signal_gaps=tuple(attempt["runtime_signal_gaps"]),
    )


def backfill_running_passes(evidence_root: Path) -> dict[str, int]:
    """Promote factual runtime-signal and explicit pass facts from artifacts."""

    cases = 0
    with_running_pass = 0
    updated = 0
    pass_keys = ("running_pass", "component", "scope", "target", "pass_record_count", "source")
    for artifact in sorted(evidence_root.glob("cases/*/crash-assertion.err")):
        cases += 1
        metadata_path = artifact.parent / "metadata.json"
        if not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        evidence = metadata.setdefault("crash_evidence", {})
        text = artifact.read_text(errors="replace")
        signal_presence = runtime_signal_presence(text)
        signal_status = "complete" if all(signal_presence.values()) else "incomplete"
        metadata_changed = (
            evidence.get("runtime_signals") != signal_presence
            or evidence.get("runtime_signal_status") != signal_status
        )
        evidence["runtime_signals"] = signal_presence
        evidence["runtime_signal_status"] = signal_status

        parsed = running_pass_evidence(text)
        if parsed is None:
            if any(key in evidence for key in pass_keys):
                for key in pass_keys:
                    evidence.pop(key, None)
                metadata_changed = True
            if metadata_changed:
                metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
                updated += 1
            continue

        with_running_pass += 1
        expected = {**parsed, "source": artifact.name}
        if any(evidence.get(key) != value for key, value in expected.items()):
            evidence.update(expected)
            metadata_changed = True
        if metadata_changed:
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            updated += 1
    return {
        "cases": cases,
        "with_running_pass": with_running_pass,
        "updated": updated,
    }


def command_print(args: argparse.Namespace) -> int:
    metadata = load_capture_metadata(
        Path(args.root),
        Path(args.evidence_root),
        args.issue,
        Path(args.profiles) if args.profiles else None,
    )
    print(f"issue={metadata.issue}")
    print(f"first_bad_commit={metadata.first_bad_commit}")
    print(f"runner={metadata.runner}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    print_parser = sub.add_parser("print", help="print profile capture metadata")
    print_parser.add_argument("issue")
    print_parser.add_argument("--root", default=str(ROOT_DIR))
    print_parser.add_argument(
        "--evidence-root", default=str(ROOT_DIR / "human_analysis/raw/master50-evidence-20260821")
    )
    print_parser.add_argument("--profiles")
    print_parser.set_defaults(func=command_print)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
