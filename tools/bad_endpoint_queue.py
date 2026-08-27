from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


BAD_ASSIGN_RE = re.compile(
    r"""^\s*BAD_COMMIT=(?:
        ["'](?P<quoted>[^"']+)["']|
        \$\{BAD_COMMIT:-(?P<default>[^}]+)\}
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True)
class IssueMetadata:
    issue: str
    bad_ref: str
    runner: Path


def _extract_bad_commit(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines():
        match = BAD_ASSIGN_RE.match(line)
        if match:
            value = match.group("quoted") or match.group("default")
            default_match = re.fullmatch(r"\$\{BAD_COMMIT:-([^}]+)\}", value)
            if default_match:
                return default_match.group(1)
            return value
    return None


def load_issue_metadata(root: Path, issue: str) -> IssueMetadata:
    issue_dir = root / "scripts" / issue
    runner = issue_dir / "bisect-runner.sh"
    if not runner.exists() or not runner.is_file() or not runner.stat().st_mode & 0o111:
        raise FileNotFoundError(f"missing executable runner: {runner}")

    bad_ref = _extract_bad_commit(issue_dir / "run-bisect.sh")
    if bad_ref is None:
        bad_ref = _extract_bad_commit(issue_dir / "validate-endpoints.sh")
    if bad_ref is None:
        raise ValueError(f"could not find BAD_COMMIT for {issue}")

    return IssueMetadata(
        issue=issue,
        bad_ref=bad_ref,
        runner=Path("scripts") / issue / "bisect-runner.sh",
    )


def command_print(args: argparse.Namespace) -> int:
    metadata = load_issue_metadata(Path(args.root), args.issue)
    print(f"issue={metadata.issue}")
    print(f"bad_ref={metadata.bad_ref}")
    print(f"runner={metadata.runner}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect bad endpoint queue metadata.")
    sub = parser.add_subparsers(required=True)
    print_parser = sub.add_parser("print", help="print metadata for one issue")
    print_parser.add_argument("issue")
    print_parser.add_argument("--root", default=".")
    print_parser.set_defaults(func=command_print)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
