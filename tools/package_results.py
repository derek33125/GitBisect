from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path


EXCLUDED_SUFFIXES = {
    ".md",
    ".tex",
    ".pdf",
    ".png",
    ".svg",
    ".jpg",
    ".jpeg",
    ".zip",
}

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    "docs",
    "doc",
    "reports",
    "packages",
    "package-staging",
    "plots",
    "figures",
}

EXCLUDED_FILE_SUFFIXES = {
    ".todo",
    ".done",
    ".lock",
}


@dataclass(frozen=True)
class RemoteSource:
    name: str
    host: str
    remote_results: str
    ssh_options: tuple[str, ...] = ()


def should_include(path: Path, source_root: Path) -> bool:
    if not path.is_file():
        return False
    rel = path.relative_to(source_root)
    parts = set(rel.parts[:-1])
    if parts & EXCLUDED_DIR_NAMES:
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if path.suffix in EXCLUDED_FILE_SUFFIXES:
        return False
    return True


def iter_packaged_files(source_root: Path) -> list[Path]:
    return sorted(
        path
        for path in source_root.rglob("*")
        if should_include(path, source_root)
    )


def build_package(
    *,
    source_root: Path,
    source_name: str,
    output_dir: Path,
    timestamp: str,
) -> tuple[Path, list[str]]:
    source_root = source_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / f"gitbisect-results-{source_name}-{timestamp}.zip"
    files = iter_packaged_files(source_root)
    manifest = {
        "source": source_name,
        "created_at_utc": timestamp,
        "source_root": str(source_root),
        "file_count": len(files),
        "filters": {
            "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
            "excluded_dirs": sorted(EXCLUDED_DIR_NAMES),
            "excluded_state_suffixes": sorted(EXCLUDED_FILE_SUFFIXES),
        },
        "files": [path.relative_to(source_root).as_posix() for path in files],
    }

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{source_name}/package-manifest.json", json.dumps(manifest, indent=2) + "\n")
        for path in files:
            rel = path.relative_to(source_root).as_posix()
            archive.write(path, f"{source_name}/{rel}")

    return package_path, manifest["files"]


def run_command(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def sync_remote(remote: RemoteSource, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    ssh_command = ["ssh", *remote.ssh_options]
    rsync_command = [
        "rsync",
        "-az",
        "-e",
        " ".join(ssh_command),
        f"{remote.host}:{remote.remote_results.rstrip('/')}/",
        f"{destination}/",
    ]
    run_command(rsync_command)


def default_remote_sources() -> list[RemoteSource]:
    aws_key = Path("/tmp/gitbisect_aws_key")
    return [
        RemoteSource(
            name="edu-server",
            host="derek@143.215.129.209",
            remote_results="/home/derek/gitbisect-work/k12-runner-20260724/results",
            ssh_options=("-o", "BatchMode=yes", "-o", "ConnectTimeout=20"),
        ),
        RemoteSource(
            name="aws-server",
            host="ubuntu@13.220.255.235",
            remote_results="/home/ubuntu/gitbisect-work/k12-runner-20260725-causal-impl-v2/results",
            ssh_options=(
                "-i",
                str(aws_key),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
            ),
        ),
    ]


def install_aws_key(repo_root: Path) -> None:
    source = repo_root / ".server_key" / "GitBisect.pem"
    target = Path("/tmp/gitbisect_aws_key")
    if not source.exists():
        return
    shutil.copy2(source, target)
    target.chmod(0o600)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package GitBisect result artifacts into per-source zip files."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--timestamp", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    parser.add_argument("--output-dir", type=Path, default=Path("results/packages"))
    parser.add_argument("--stage-dir", type=Path, default=Path("results/package-staging"))
    parser.add_argument("--skip-remote-sync", action="store_true")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=("local", "edu-server", "aws-server"),
        default=["local", "edu-server", "aws-server"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = (repo_root / args.output_dir).resolve()
    stage_dir = (repo_root / args.stage_dir).resolve()
    packages: list[dict[str, object]] = []

    if any(source != "local" for source in args.sources):
        install_aws_key(repo_root)

    source_roots: dict[str, Path] = {}
    if "local" in args.sources:
        source_roots["local"] = repo_root / "results"

    remote_by_name = {remote.name: remote for remote in default_remote_sources()}
    for name in args.sources:
        if name == "local":
            continue
        destination = stage_dir / name / "results"
        if not args.skip_remote_sync:
            sync_remote(remote_by_name[name], destination)
        source_roots[name] = destination

    for name in args.sources:
        package_path, files = build_package(
            source_root=source_roots[name],
            source_name=name,
            output_dir=output_dir,
            timestamp=args.timestamp,
        )
        packages.append(
            {
                "source": name,
                "zip": str(package_path),
                "file_count": len(files),
            }
        )

    print(json.dumps({"packages": packages}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
