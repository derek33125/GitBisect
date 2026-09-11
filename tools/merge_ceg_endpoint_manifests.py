#!/usr/bin/env python3
"""Merge disjoint CEG bad-endpoint capture manifests without weakening provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes capture root: {relative}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--issues", nargs="+", required=True)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()

    if len(args.issues) != len(set(args.issues)):
        raise SystemExit("expected issue list contains duplicates")
    if (args.output / "manifest.json").exists():
        raise SystemExit(f"output manifest already exists: {args.output}")

    profiles = json.loads(args.profiles.read_text())
    cases_by_issue: dict[str, tuple[Path, dict[str, object]]] = {}
    source_manifests = []
    for input_root in args.inputs:
        input_root = input_root.resolve()
        manifest_path = input_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("protocol") != "ceg-bad-endpoint-v1":
            raise SystemExit(f"protocol mismatch: {manifest_path}")
        source_manifests.append(
            {
                "path": str(manifest_path),
                "sha256": sha256(manifest_path),
                "label": manifest.get("label"),
            }
        )
        for case in manifest.get("cases", []):
            issue = str(case.get("issue", ""))
            if not issue or issue in cases_by_issue:
                raise SystemExit(f"missing or duplicate captured issue: {issue!r}")
            cases_by_issue[issue] = (input_root, case)

    if set(cases_by_issue) != set(args.issues):
        missing = sorted(set(args.issues) - set(cases_by_issue))
        extra = sorted(set(cases_by_issue) - set(args.issues))
        raise SystemExit(f"capture partition mismatch: missing={missing} extra={extra}")

    output_cases = args.output / "cases"
    output_cases.mkdir(parents=True, exist_ok=True)
    merged_cases = []
    for issue in args.issues:
        input_root, source = cases_by_issue[issue]
        if source.get("capture_role") != "bad-endpoint":
            raise SystemExit(f"{issue}: capture role is not bad-endpoint")
        if source.get("capture_commit") != profiles[issue]["bad_commit"]:
            raise SystemExit(f"{issue}: capture commit does not match configured bad endpoint")

        artifact = safe_path(input_root, str(source["crash_artifact"]))
        if sha256(artifact) != source["crash_artifact_sha256"]:
            raise SystemExit(f"{issue}: crash artifact hash mismatch")
        reproducer_sources = []
        for relative, expected_hash in source.get("reproducer_sha256", {}).items():
            path = safe_path(input_root, str(relative))
            if sha256(path) != expected_hash:
                raise SystemExit(f"{issue}: reproducer hash mismatch: {relative}")
            reproducer_sources.append((path, str(expected_hash)))
        if not reproducer_sources:
            raise SystemExit(f"{issue}: no reproducer sources")

        destination = output_cases / issue
        destination.mkdir()
        artifact_target = destination / "crash-assertion.err"
        shutil.copy2(artifact, artifact_target)
        reproducer_hashes = {}
        reproducer_paths = []
        for source_path, expected_hash in reproducer_sources:
            if any(
                token in source_path.name.lower()
                for token in ("first-bad", "metadata", "patch", "diff")
            ):
                raise SystemExit(f"{issue}: forbidden answer-derived filename")
            target = destination / source_path.name
            shutil.copy2(source_path, target)
            relative = target.relative_to(args.output).as_posix()
            reproducer_paths.append(relative)
            reproducer_hashes[relative] = expected_hash

        relative_artifact = artifact_target.relative_to(args.output).as_posix()
        merged_cases.append(
            {
                "issue": issue,
                "capture_role": "bad-endpoint",
                "capture_commit": source["capture_commit"],
                "crash_artifact": relative_artifact,
                "crash_artifact_sha256": sha256(artifact_target),
                "source_artifact": source.get("source_artifact"),
                "source_partition": source.get("capture_partition"),
                "reproducer_paths": reproducer_paths,
                "reproducer_sha256": reproducer_hashes,
            }
        )

    manifest = {
        "protocol": "ceg-bad-endpoint-v1",
        "label": args.label,
        "purpose": "Merged leakage-controlled bad-endpoint inputs for CEG-Bisect.",
        "source_manifests": source_manifests,
        "forbidden_inputs": [
            "validated first-bad SHA",
            "first-bad patch",
            "first-bad compact diff",
            "researcher-authored issue summary",
            "researcher-authored relevant/high-risk paths",
        ],
        "cases": merged_cases,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"merged {len(merged_cases)} CEG endpoint cases into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
