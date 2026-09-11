"""Program-derived LLVM pass/phase evidence for CEG-Bisect.

This is a Python port of the answer-free portions of the human-analysis
``pass-graph.js`` pipeline. It reads only the crash payload and the repository
at the configured bad endpoint; no ground-truth commit or fix patch is used.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc", ".td")
PROJECT_ROOTS = ("llvm", "clang", "clang-tools-extra", "polly", "mlir", "lldb", "flang", "lld")
ROLE_SUFFIX_RE = re.compile(
    r"(?:InfoWrapperPass|WrapperPass|LegacyPass|Analysis|Printer|Pass)$"
)
ANALYSIS_REQUEST_RES = (
    re.compile(r"\bget(?:Cached)?Result<\s*([A-Za-z_][\w:]*)"),
    re.compile(r"\bgetAnalysis(?:IfAvailable)?<\s*([A-Za-z_][\w:]*)"),
    re.compile(r"\bINITIALIZE_PASS_DEPENDENCY\(\s*([A-Za-z_]\w*)"),
    re.compile(r"\bAU\.addRequired(?:Transitive|ID)?<\s*([A-Za-z_][\w:]*)"),
)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=check,
    )
    return completed.stdout


def flatten_pipeline(pipeline: str) -> list[str]:
    """Flatten nested new-PM syntax without splitting angle-bracket options."""
    result: list[str] = []
    token: list[str] = []
    angle_depth = 0

    def flush() -> None:
        value = "".join(token).strip()
        if value:
            result.append(value)
        token.clear()

    for char in str(pipeline or ""):
        if char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1
        if angle_depth == 0 and char in "(),":
            flush()
        else:
            token.append(char)
    flush()
    return result


def parse_crash_pipeline(crash: Mapping[str, object]) -> dict[str, object]:
    runs = [
        str(item.get("pass", ""))
        if isinstance(item, Mapping)
        else str(item)
        for item in (crash.get("pass_records") or crash.get("passes", []))
        if str(item.get("pass", "") if isinstance(item, Mapping) else item).strip()
    ]
    declared = str(crash.get("declared_pass") or "").strip() or None
    last_run = runs[-1] if runs else None
    last_record = declared or last_run
    if not last_record:
        return {
            "pipeline": [],
            "crashing": None,
            "predecessors": [],
            "legacy": bool(crash.get("legacy_pass_format")),
            "pass_source": None,
            "pass_agrees": None,
        }
    pipeline: list[str] = []
    for value in [*runs, last_record]:
        flattened = flatten_pipeline(value)
        if len(flattened) > len(pipeline):
            pipeline = flattened
    last_flattened = flatten_pipeline(last_record)
    crashing = last_flattened[-1] if last_flattened else None
    predecessors: list[str] = []
    if crashing and crashing in pipeline:
        at = pipeline.index(crashing)
        if at > 0:
            predecessors = pipeline[:at]
    return {
        "pipeline": pipeline,
        "crashing": crashing,
        "predecessors": predecessors,
        "legacy": bool(crash.get("legacy_pass_format")),
        "pass_source": "manifest" if declared else "artifact",
        "pass_agrees": declared == last_run if declared and last_run else None,
        "declared_component": crash.get("declared_component"),
    }


def parse_pass_registry(text: str) -> dict[str, dict[str, object]]:
    by_name: dict[str, dict[str, object]] = {}
    analyses: dict[str, str] = {}
    flat = re.sub(r"\s+", " ", str(text or ""))
    pattern = re.compile(
        r"(MODULE|FUNCTION|LOOP|LOOPNEST|CGSCC|MACHINE_FUNCTION)_"
        r"(ANALYSIS|PASS)(?:_WITH_PARAMS)?\s*\(\s*\"([^\"]+)\"\s*,\s*"
        r"(?:\"([^\"]+)\"|([A-Za-z_][\w:]*))"
    )
    for match in pattern.finditer(flat):
        scope, kind, name, quoted_class, bare_class = match.groups()
        class_name = str(quoted_class or bare_class or "").split("::")[-1]
        if not class_name:
            continue
        record = {
            "class_name": class_name,
            "kind": kind.lower(),
            "scope": scope.lower(),
            "project": "llvm",
        }
        by_name.setdefault(name, record)
        base = re.sub(r"<.*$", "", name)
        by_name.setdefault(base, record)
        if kind == "ANALYSIS":
            analyses.setdefault(class_name, name)
    return {"by_name": by_name, "analyses": analyses}


def parse_legacy_registry(text: str, *, path: str = "") -> dict[str, dict[str, object]]:
    source = str(text or "")
    definitions: dict[str, str] = {}
    for match in re.finditer(
        r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+\"((?:[^\"\\]|\\.)*)\"",
        source,
        re.MULTILINE,
    ):
        definitions[match.group(1)] = match.group(2)
    for match in re.finditer(
        r"\b(?:static\s+)?const\s+char\s*\*?\s*([A-Za-z_]\w*)\s*"
        r"(?:\[\s*\])?\s*=\s*\"((?:[^\"\\]|\\.)*)\"",
        source,
    ):
        definitions.setdefault(match.group(1), match.group(2))

    def literal(token: str) -> str | None:
        value = str(token or "").strip()
        quoted = re.fullmatch(r"\"((?:[^\"\\]|\\.)*)\"", value)
        return quoted.group(1) if quoted else definitions.get(value)

    by_display: dict[str, dict[str, object]] = {}
    by_arg: dict[str, dict[str, object]] = {}
    flat = re.sub(r"\s+", " ", source)
    for match in re.finditer(
        r"INITIALIZE_(?:PASS|PASS_BEGIN)\s*\(\s*([A-Za-z_][\w:]*)\s*,"
        r"\s*([^,]+?)\s*,\s*([^,]+?)\s*,",
        flat,
    ):
        class_name = match.group(1).split("::")[-1]
        record = {
            "class_name": class_name,
            "file": path,
            "project": path.split("/", 1)[0] if "/" in path else None,
        }
        argument = literal(match.group(2))
        display = literal(match.group(3))
        if display:
            by_display.setdefault(display, record)
        if argument:
            by_arg.setdefault(argument, record)
    for match in re.finditer(
        r"getPassName\s*\([^)]*\)[^{;]*\{\s*return\s+\"((?:[^\"\\]|\\.)*)\"",
        flat,
    ):
        by_display.setdefault(
            match.group(1),
            {
                "class_name": None,
                "file": path,
                "project": path.split("/", 1)[0] if "/" in path else None,
            },
        )
    return {"by_display": by_display, "by_arg": by_arg}


def stem_candidates(class_name: str) -> list[str]:
    base = ROLE_SUFFIX_RE.sub("", str(class_name or ""))
    return list(
        dict.fromkeys(
            value
            for value in (str(class_name or ""), base, f"{base}Info" if base else "")
            if len(value) >= 4
        )
    )


def mirror_paths(path: str) -> list[str]:
    values = [str(PurePosixPath(path).parent)]
    match = re.match(
        r"^(llvm|clang|mlir|lldb|flang|polly)/include/\1/(.+)/[^/]+\.[^/]+$",
        path,
    )
    if match:
        values.append(f"{match.group(1)}/lib/{match.group(2)}")
    return list(dict.fromkeys(value for value in values if value and value != "."))


def analysis_dependencies(source: str) -> list[str]:
    dependencies: list[str] = []
    for pattern in ANALYSIS_REQUEST_RES:
        for match in pattern.finditer(str(source or "")):
            class_name = match.group(1).split("::")[-1]
            if len(class_name) >= 4 and class_name not in dependencies:
                dependencies.append(class_name)
    return dependencies


def _tree_index(repo: Path, bad_sha: str) -> tuple[list[str], dict[str, list[str]]]:
    try:
        paths = [
            line.strip()
            for line in _git(repo, "ls-tree", "-r", "--name-only", bad_sha).splitlines()
            if line.strip().lower().endswith(SOURCE_SUFFIXES)
            and "/test/" not in line.lower()
            and "/unittests/" not in line.lower()
        ]
    except subprocess.CalledProcessError:
        return [], {}
    by_stem: dict[str, list[str]] = {}
    for path in paths:
        by_stem.setdefault(PurePosixPath(path).stem, []).append(path)
    return paths, by_stem


def _read_at(repo: Path, sha: str, path: str) -> str:
    try:
        return _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return ""


def _files_for_class(
    class_name: str | None,
    by_stem: Mapping[str, Sequence[str]],
    *,
    project: str | None = None,
    implementations_only: bool = False,
) -> list[str]:
    if not class_name:
        return []
    for stem in stem_candidates(class_name):
        hits = [
            path
            for path in by_stem.get(stem, [])
            if (not project or path.startswith(project + "/"))
            and (
                not implementations_only
                or path.lower().endswith((".cpp", ".cc", ".cxx"))
            )
        ]
        if hits:
            return hits
    return []


def _legacy_for_names(
    repo: Path,
    bad_sha: str,
    names: Sequence[str],
) -> dict[str, dict[str, object]]:
    by_display: dict[str, dict[str, object]] = {}
    by_arg: dict[str, dict[str, object]] = {}
    candidate_paths: set[str] = set()
    for name in dict.fromkeys(value for value in names if value):
        try:
            output = _git(
                repo,
                "grep",
                "-l",
                "-F",
                name,
                bad_sha,
                "--",
                *[f"{project}/lib" for project in PROJECT_ROOTS],
            )
        except subprocess.CalledProcessError:
            continue
        for line in output.splitlines():
            path = line.split(":", 1)[-1].strip()
            if path.lower().endswith((".cpp", ".cc", ".cxx")):
                candidate_paths.add(path)
    for path in sorted(candidate_paths)[:200]:
        parsed = parse_legacy_registry(_read_at(repo, bad_sha, path), path=path)
        for name, record in parsed["by_display"].items():
            by_display.setdefault(name, record)
        for name, record in parsed["by_arg"].items():
            by_arg.setdefault(name, record)
    return {"by_display": by_display, "by_arg": by_arg}


def build_pass_graph_path_signals(
    repo: Path,
    bad_sha: str,
    crash: Mapping[str, object],
) -> dict[str, object]:
    """Resolve self/pred/dep pass evidence into source paths at ``bad_sha``."""
    pipeline = parse_crash_pipeline(crash)
    crashing = str(pipeline.get("crashing") or "")
    predecessors = [str(value) for value in pipeline.get("predecessors", [])]
    if not crashing:
        return {"signals": [], **pipeline, "status": "no-crashing-pass"}

    _paths, by_stem = _tree_index(repo, bad_sha)
    registry_text = _read_at(repo, bad_sha, "llvm/lib/Passes/PassRegistry.def")
    registry = parse_pass_registry(registry_text)
    legacy = _legacy_for_names(repo, bad_sha, [crashing, *predecessors])

    def resolve(name: str) -> dict[str, object] | None:
        bare = re.sub(r"<.*$", "", name)
        record = registry["by_name"].get(name) or registry["by_name"].get(bare)
        if record:
            return dict(record)
        record = (
            legacy["by_display"].get(name)
            or legacy["by_display"].get(bare)
            or legacy["by_arg"].get(bare)
        )
        if record:
            return dict(record)
        if re.fullmatch(r"[A-Za-z_]\w*", bare) and re.search(r"[A-Z]", bare):
            if _files_for_class(bare, by_stem):
                return {"class_name": bare, "file": None, "project": None}
        return None

    crash_record = resolve(crashing)
    predecessor_records = [
        (name, record)
        for name in predecessors
        if (record := resolve(name)) is not None
    ]

    def files_for(record: Mapping[str, object] | None) -> list[str]:
        if not record:
            return []
        registered = str(record.get("file") or "")
        if registered:
            return [registered]
        return _files_for_class(
            str(record.get("class_name") or ""),
            by_stem,
            project=str(record.get("project") or "") or None,
            implementations_only=True,
        )

    def directories_for(record: Mapping[str, object] | None) -> list[str]:
        return list(
            dict.fromkeys(
                directory
                for path in files_for(record)
                for directory in mirror_paths(path)
            )
        )

    signals: list[dict[str, object]] = []

    def append(
        *,
        name: str,
        group: str,
        paths: Sequence[str],
        provenance: Mapping[str, object],
    ) -> None:
        unique = list(dict.fromkeys(path for path in paths if path))
        if unique:
            signals.append(
                {
                    "name": name,
                    "group": group,
                    "channel": "pipeline",
                    "paths": unique,
                    "provenance": dict(provenance),
                }
            )

    crash_files = files_for(crash_record)
    append(
        name=f"pipe:self:{crashing}:file",
        group="pipe:self",
        paths=crash_files,
        provenance={"kind": "self", "pass": crashing, "granularity": "file"},
    )
    append(
        name=f"pipe:self:{crashing}:dir",
        group="pipe:self",
        paths=directories_for(crash_record),
        provenance={"kind": "self", "pass": crashing, "granularity": "directory"},
    )

    nearest_first = list(reversed(predecessor_records))
    if nearest_first:
        cuts = [value for value in (1, 2, 4, 8) if value < len(nearest_first)]
        cuts.append(len(nearest_first))
        for depth in cuts:
            selected = nearest_first[:depth]
            label = "all" if depth == len(nearest_first) else str(depth)
            append(
                name=f"pipe:pred:f{label}",
                group="pipe:pred",
                paths=[
                    path
                    for _name, record in selected
                    for path in files_for(record)
                ],
                provenance={
                    "kind": "pred",
                    "depth": depth,
                    "nearest": nearest_first[0][0],
                    "granularity": "file",
                },
            )
            append(
                name=f"pipe:pred:d{label}",
                group="pipe:pred",
                paths=[
                    directory
                    for _name, record in selected
                    for directory in directories_for(record)
                ],
                provenance={
                    "kind": "pred",
                    "depth": depth,
                    "nearest": nearest_first[0][0],
                    "granularity": "directory",
                },
            )

    dependency_classes: set[str] = set()
    dependency_seeds = [record for record in (crash_record,) if record]
    if predecessor_records:
        dependency_seeds.append(predecessor_records[-1][1])
    for record in dependency_seeds:
        for path in files_for(record):
            for class_name in analysis_dependencies(_read_at(repo, bad_sha, path)):
                if class_name in registry["analyses"]:
                    dependency_classes.add(class_name)
    for class_name in sorted(dependency_classes):
        dependency_files = _files_for_class(
            class_name,
            by_stem,
            project="llvm",
            implementations_only=True,
        )
        display = str(registry["analyses"].get(class_name, class_name))
        append(
            name=f"pipe:dep:{display}:file",
            group="pipe:dep",
            paths=dependency_files,
            provenance={
                "kind": "dep",
                "analysis": class_name,
                "granularity": "file",
            },
        )
        append(
            name=f"pipe:dep:{display}:dir",
            group="pipe:dep",
            paths=[
                directory
                for path in dependency_files
                for directory in mirror_paths(path)
            ],
            provenance={
                "kind": "dep",
                "analysis": class_name,
                "granularity": "directory",
            },
        )
    return {
        **pipeline,
        "status": "resolved" if signals else "unresolved-pass",
        "crash_class": crash_record.get("class_name") if crash_record else None,
        "dependency_classes": sorted(dependency_classes),
        "signals": signals,
    }


def build_phase_path_signals(
    repo: Path,
    bad_sha: str,
    crash: Mapping[str, object],
) -> dict[str, object]:
    """Locate non-outer PrettyStackTrace phase phrases in repository source."""
    phases = [
        item
        for item in crash.get("phases", [])
        if isinstance(item, Mapping)
        and str(item.get("text", "")).strip()
        and item.get("index") is not None
    ]
    if not phases:
        return {"status": "no-phases", "signals": []}
    outermost = min(int(item["index"]) for item in phases)
    signals: list[dict[str, object]] = []
    for item in phases:
        if int(item["index"]) == outermost:
            continue
        phrase = str(item["text"]).strip()
        try:
            output = _git(
                repo,
                "grep",
                "-l",
                "-F",
                phrase,
                bad_sha,
                "--",
                *[f"{project}/lib" for project in PROJECT_ROOTS],
            )
        except subprocess.CalledProcessError:
            continue
        files = [
            line.split(":", 1)[-1].strip()
            for line in output.splitlines()
            if line.split(":", 1)[-1].strip().lower().endswith(SOURCE_SUFFIXES)
            and "/test/" not in line.lower()
            and "/unittests/" not in line.lower()
        ]
        directories = list(
            dict.fromkeys(str(PurePosixPath(path).parent) for path in files)
        )
        for granularity, paths in (("file", files), ("directory", directories)):
            if paths:
                signals.append(
                    {
                        "name": f"phase:inner:{phrase}:{granularity}",
                        "group": "phase",
                        "channel": "phase",
                        "paths": list(dict.fromkeys(paths)),
                        "provenance": {
                            "phrase": phrase,
                            "index": int(item["index"]),
                            "outermost_index": outermost,
                            "granularity": granularity,
                        },
                    }
                )
    return {
        "status": "resolved" if signals else "unresolved-phases",
        "outermost_index": outermost,
        "signals": signals,
    }


def build_bad_tree_path_signals(
    repo: Path,
    bad_sha: str,
    crash: Mapping[str, object],
) -> dict[str, object]:
    """Derive file-layout priors from the real bad-endpoint source tree."""
    try:
        all_paths = [
            line.strip()
            for line in _git(repo, "ls-tree", "-r", "--name-only", bad_sha).splitlines()
            if line.strip()
        ]
    except subprocess.CalledProcessError:
        return {"status": "tree-unavailable", "signals": [], "provenance": {}}
    source_paths = [
        path
        for path in all_paths
        if path.lower().endswith(SOURCE_SUFFIXES)
        and "/test/" not in path.lower()
        and "/unittests/" not in path.lower()
    ]
    all_path_set = set(all_paths)
    by_stem: dict[str, list[str]] = {}
    by_flat_stem: dict[str, list[str]] = {}
    all_directories: set[str] = set()
    directories_by_name: dict[str, list[str]] = {}
    for path in all_paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            directory = "/".join(parts[:index])
            if directory in all_directories:
                continue
            all_directories.add(directory)
            directories_by_name.setdefault(parts[index - 1].lower(), []).append(directory)
    for path in source_paths:
        stem = PurePosixPath(path).stem
        by_stem.setdefault(stem, []).append(path)
        by_flat_stem.setdefault(re.sub(r"[^a-z0-9]+", "", stem.lower()), []).append(path)

    seeds: dict[str, list[str]] = {
        "crash-file": [],
        "symbol": [],
        "pass": [],
        "target": [],
        "tool": [],
        "module": [],
    }
    rare_checker_paths = {
        str(path).replace("\\", "/")
        for path in crash.get("rare_checker_paths", [])
        if str(path).strip()
    }
    for raw in crash.get("source_paths", []):
        path = str(raw).replace("\\", "/")
        if path in all_path_set and path not in rare_checker_paths:
            seeds["crash-file"].append(path)
    for symbol in crash.get("symbols", []):
        for component in str(symbol).split("::"):
            if len(component) >= 4 and component not in {"llvm", "clang"}:
                seeds["symbol"].extend(by_stem.get(component, []))
    for token in crash.get("pass_tokens", []):
        flattened = re.sub(r"[^a-z0-9]+", "", str(token).lower())
        if len(flattened) >= 6:
            seeds["pass"].extend(by_flat_stem.get(flattened, []))

    target_directories = sorted(
        {
            match.group(1)
            for path in all_paths
            if (match := re.match(r"^(llvm/lib/Target/[^/]+)/", path))
        }
    )
    for term in crash.get("reproducer_terms", []):
        arch = re.sub(r"[^a-z0-9]+", "", str(term).lower())
        for directory in target_directories:
            target = PurePosixPath(directory).name.lower()
            if arch == target or arch.startswith(target) or target.startswith(arch):
                seeds["target"].append(directory)

    non_source_directory = re.compile(
        r"(^|/)(?:test|tests|unittests|docs|examples|bindings|Inputs)(?:/|$)"
        r"|utils/(?:gn|lit|bazel)/",
        re.IGNORECASE,
    )
    tool = str(crash.get("tool") or "").strip()
    for candidate in dict.fromkeys(
        (
            tool,
            re.sub(r"\+\+$", "", tool),
            re.sub(r"-(?:\d+(?:\.\d+)*|trunk|tk)$", "", tool),
        )
    ):
        normalized = candidate.lower()
        if len(normalized) < 3:
            continue
        matches = [
            directory
            for directory in directories_by_name.get(normalized, [])
            if not non_source_directory.search(directory)
        ]
        if len(matches) == 1:
            seeds["tool"].extend(matches)
            break

    tool_roots = list(dict.fromkeys(seeds["tool"]))
    option_names: set[str] = set()
    for flag in crash.get("flags", []):
        match = re.match(
            r"-{1,2}(?:checks?|passes|analyze-checker|clang-tidy-checks)=([^\s\"']+)",
            str(flag),
            re.IGNORECASE,
        )
        if not match:
            continue
        for raw_name in match.group(1).split(","):
            name = re.sub(r"^[-+*]", "", raw_name)
            name = re.sub(r"<.*$", "", name).strip()
            if re.fullmatch(r"[a-z][a-z0-9-]*-[a-z0-9-]+", name):
                option_names.add(name)
    for name in sorted(option_names):
        tokens = [token for token in name.split("-") if token]
        for take in range(1, len(tokens)):
            head = "-".join(tokens[:take])
            matches = [
                directory
                for directory in directories_by_name.get(head, [])
                if not non_source_directory.search(directory)
                and (
                    not tool_roots
                    or any(
                        directory == root or directory.startswith(root + "/")
                        for root in tool_roots
                    )
                )
            ]
            if not matches:
                continue
            seeds["module"].extend(matches)
            camel = "".join(token[:1].upper() + token[1:] for token in tokens[take:])
            if len(camel) >= 4:
                for path in source_paths:
                    if not any(path.startswith(directory + "/") for directory in matches):
                        continue
                    stem = PurePosixPath(path).stem
                    if stem in {camel, f"{camel}Check"}:
                        seeds["module"].append(path)
            break

    directory_sources: dict[str, set[str]] = {}

    def note(directory: str, source: str) -> None:
        if directory and directory != ".":
            directory_sources.setdefault(directory, set()).add(source)

    for source in ("crash-file", "symbol", "pass", "module"):
        for path in dict.fromkeys(seeds[source]):
            if path in all_directories:
                note(path, source)
            else:
                note(str(PurePosixPath(path).parent), source)
    for source in ("target", "tool"):
        for directory in dict.fromkeys(seeds[source]):
            note(directory, source)

    signals = [
        {
            "name": f"dir:{directory}",
            "group": f"directory:{directory}",
            "channel": "directory",
            "paths": [directory],
            "provenance": {
                "derived_from": sorted(sources),
                "bad_sha": bad_sha,
            },
        }
        for directory, sources in sorted(directory_sources.items())
    ]
    return {
        "status": "resolved" if signals else "no-derived-paths",
        "signals": signals,
        "provenance": {
            directory: sorted(sources)
            for directory, sources in sorted(directory_sources.items())
        },
        "seed_counts": {
            source: len(set(paths))
            for source, paths in seeds.items()
        },
    }
