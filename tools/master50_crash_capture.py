#!/usr/bin/env python3
"""Record validated master-50 bad-endpoint crash evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
CRASH_MARKER_RE = re.compile(
    r"(?:"
    r"Assertion [`'].*?(?:failed\.?|$)"
    r"|PLEASE submit a bug report"
    r"|Stack dump:"
    r"|UNREACHABLE executed"
    r"|fatal error: error in backend"
    r"|(?:AddressSanitizer|UndefinedBehaviorSanitizer):"
    r"|\b(?:Segmentation fault|SIGSEGV|SIGABRT)\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CaptureMetadata:
    issue: str
    bad_commit: str
    runner: Path


@dataclass(frozen=True)
class CaptureResult:
    issue: str
    marker_detected: bool
    artifact: Path | None
    source_log: str


def has_crash_marker(output: str) -> bool:
    """Return true only for output containing a concrete crash signal."""

    return CRASH_MARKER_RE.search(output) is not None


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
    del evidence_root  # Kept in the API to make the case destination explicit.
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
    bad_commit = str(profile["bad_commit"])
    if not re.fullmatch(r"[0-9a-fA-F]{40}", bad_commit):
        raise ValueError(f"profile bad_commit is not a full SHA for {issue}: {bad_commit}")
    return CaptureMetadata(issue=issue, bad_commit=bad_commit, runner=runner)


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
) -> CaptureResult:
    """Persist one capture and update its case metadata without fabricating evidence."""

    if not source_log.is_file():
        raise FileNotFoundError(f"capture log does not exist: {source_log}")
    case_dir = evidence_root / "cases" / issue
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing evidence metadata: {metadata_path}")

    output = source_log.read_text(errors="replace")
    marker_detected = has_crash_marker(output)
    artifact = case_dir / "crash-assertion.err"
    if marker_detected:
        shutil.copyfile(source_log, artifact)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source = _relative_path(source_log, root)
    metadata["crash_capture"] = {
        "tested_sha": tested_sha,
        "exit_code": exit_code,
        "marker_detected": marker_detected,
        "started_at": started_at,
        "finished_at": finished_at,
        "source_log": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if marker_detected:
        metadata["crash_evidence"] = {
            "kind": "assertion",
            "excerpt": "A crash/assertion marker was captured at the profile bad endpoint.",
            "source_artifact": source,
        }
    elif not artifact.exists():
        metadata["crash_evidence"] = {
            "kind": "missing",
            "excerpt": "The profile bad endpoint completed without a concrete crash/assertion marker.",
            "source_artifact": "",
        }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return CaptureResult(
        issue=issue,
        marker_detected=marker_detected,
        artifact=artifact if marker_detected else None,
        source_log=source,
    )


def command_print(args: argparse.Namespace) -> int:
    metadata = load_capture_metadata(
        Path(args.root),
        Path(args.evidence_root),
        args.issue,
        Path(args.profiles) if args.profiles else None,
    )
    print(f"issue={metadata.issue}")
    print(f"bad_commit={metadata.bad_commit}")
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
