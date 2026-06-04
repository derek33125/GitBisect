#!/usr/bin/env python3
"""Coarse version screening through the public Compiler Explorer API.

This is intentionally weaker than the local LLVM runners: it only checks hosted
Compiler Explorer compilers and cannot validate arbitrary LLVM commits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


GODBOLT_BASE = "https://godbolt.org"


def request_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "User-Agent": "lm-bisect-godbolt-coarse-probe/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def available_compilers() -> list[dict[str, Any]]:
    url = f"{GODBOLT_BASE}/api/compilers/c++?fields=id,name,semver,releaseTrack"
    data = request_json(url)
    if not isinstance(data, list):
        raise RuntimeError("unexpected Compiler Explorer compiler-list response")
    return data


def pick_clang_for_major(compilers: list[dict[str, Any]], major: int, assertions: bool) -> dict[str, Any]:
    suffix = "assert" if assertions else ""
    candidates: list[dict[str, Any]] = []
    pattern = re.compile(rf"^clang{major}(\d+){suffix}$")
    for compiler in compilers:
        cid = str(compiler.get("id", ""))
        if not pattern.match(cid):
            continue
        if str(compiler.get("releaseTrack", "")) != "stable":
            continue
        candidates.append(compiler)
    if not candidates:
        raise RuntimeError(f"no hosted x86-64 clang {major}.x compiler found")

    def sort_key(compiler: dict[str, Any]) -> tuple[int, ...]:
        semver = str(compiler.get("semver", ""))
        nums = [int(part) for part in re.findall(r"\d+", semver)]
        return tuple(nums)

    return sorted(candidates, key=sort_key)[-1]


def compile_with_ce(compiler_id: str, source: str, user_args: str) -> tuple[dict[str, Any], float]:
    payload = {
        "source": source,
        "lang": "c++",
        "options": {
            "userArguments": user_args,
            "compilerOptions": {"executorRequest": False},
        },
    }
    started = time.time()
    data = request_json(f"{GODBOLT_BASE}/api/compiler/{compiler_id}/compile", payload)
    return data, time.time() - started


def collect_text(lines: list[dict[str, Any]]) -> str:
    return "\n".join(str(line.get("text", "")) for line in lines)


def classify(result: dict[str, Any], bad_patterns: list[str], inconclusive_patterns: list[str]) -> str:
    code = result.get("code")
    stderr = result.get("stderr") or []
    stdout = result.get("stdout") or []
    text = collect_text(stderr) + "\n" + collect_text(stdout)
    lower_text = text.lower()
    for pattern in bad_patterns:
        if pattern.lower() in lower_text:
            return "bad"
    for pattern in inconclusive_patterns:
        if pattern.lower() in lower_text:
            return "inconclusive"
    if code == 0:
        return "good"
    return "inconclusive"


def write_note(path: Path, result: dict[str, Any]) -> None:
    verdict = result["verdict"]
    compiler = result["compiler"]
    lines = [
        f"# {result['issue'].upper()} Godbolt {result['major']}.x Coarse Probe",
        "",
        f"- Issue: `{result['issue']}`",
        "- Tool: public Compiler Explorer API at `godbolt.org`",
        "- Mode: compile-only hosted compiler screening",
        f"- Compiler: `{compiler['id']}` (`{compiler.get('name', '')}`)",
        f"- Arguments: `{result['arguments']}`",
        f"- Verdict: `{verdict}`",
        f"- Raw result: `{result['raw_result_path']}`",
        "",
        "## Interpretation",
        "",
    ]
    if verdict == "good":
        lines.append(
            "The hosted compiler accepted the source with exit code 0, so this version bucket is a promising older-good candidate for local validation."
        )
    elif verdict == "bad":
        lines.append(
            "The hosted compiler output matched a configured bad-pattern, so this version bucket should not be used as a good anchor."
        )
    else:
        lines.append(
            "The hosted compiler did not produce a clean pass and did not match a configured bad-pattern. Treat this as a screening failure, not as a validated good anchor."
        )
    lines.extend(
        [
            "",
            "This does not replace the local LLVM runner for exact commit validation or tool-specific reproducers such as `clangd` LSP tests.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--major", required=True, type=int, help="LLVM/Clang major version bucket, e.g. 13")
    parser.add_argument("--source-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--arguments", default="-std=c++20")
    parser.add_argument("--assertions", action="store_true", help="Prefer assertion-enabled hosted compiler if available")
    parser.add_argument(
        "--bad-pattern",
        action="append",
        default=[],
        help="Substring that means the hosted compiler reproduced the bug.",
    )
    parser.add_argument(
        "--inconclusive-pattern",
        action="append",
        default=[],
        help="Substring that means the hosted compiler hit an unrelated limit or setup issue.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source_file.read_text(encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        compiler = pick_clang_for_major(available_compilers(), args.major, args.assertions)
        ce_result, elapsed = compile_with_ce(str(compiler["id"]), source, args.arguments)
    except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    verdict = classify(ce_result, args.bad_pattern, args.inconclusive_pattern)
    suffix = "assert" if args.assertions else "release"
    stem = f"{args.issue}-godbolt-{args.major}x-{suffix}-coarse"
    raw_path = args.output_dir / f"{stem}.json"
    note_path = args.output_dir / f"{stem}-note.md"
    raw_result = {
        "issue": args.issue,
        "major": args.major,
        "mode": "public-godbolt-compile-only",
        "compiler": compiler,
        "arguments": args.arguments,
        "elapsed_sec": round(elapsed, 3),
        "verdict": verdict,
        "bad_patterns": args.bad_pattern,
        "inconclusive_patterns": args.inconclusive_pattern,
        "compile_result": ce_result,
    }
    raw_path.write_text(json.dumps(raw_result, indent=2), encoding="utf-8")
    raw_result["raw_result_path"] = str(raw_path)
    write_note(note_path, raw_result)
    print(json.dumps({"verdict": verdict, "compiler": compiler, "raw": str(raw_path), "note": str(note_path)}, indent=2))
    return 0 if verdict == "good" else 1


if __name__ == "__main__":
    raise SystemExit(main())
