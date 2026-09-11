"""Program-derived commit prior for Causal Evidence-Guided Bisect.

The scorer is intentionally answer-free: it consumes only crash/reproducer
facts, bad-tree usage indexes, and metadata from commits in the unresolved
interval.  It ranks every commit and never removes one from consideration.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Mapping, Sequence


PROGRAM_PRIOR_VERSION = "ceg-program-prior-v6-source-reproducer"
IMPLEMENTED_CHANNELS = (
    "crash-file",
    "crash-stack",
    "crash-pass-token",
    "crash-derived-directory",
    "dependency-usage-tier",
    "commit-message",
    "pass-graph-self-pred-dep",
    "compiler-phase-phrase",
    "full-bad-tree-tool-module-target-path",
    "rare-checker-soft-negative",
)
DEFERRED_PARITY_CHANNELS: tuple[str, ...] = ()
DEFAULT_PRIOR_FLOOR = 0.05
DEFAULT_ANCHOR_LIMIT = 8
DEPENDENCY_TIERS = ("mention", "used", "heavy5", "heavy10", "top20")
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc", ".td")
_TEST_PARTS = frozenset({"test", "tests", "unittest", "unittests"})
_CXX_NON_VARIABLE = frozenset(
    {
        "alignas",
        "alignof",
        "and_eq",
        "bitand",
        "bitor",
        "catch",
        "class",
        "co_await",
        "co_return",
        "co_yield",
        "compl",
        "concept",
        "const",
        "const_cast",
        "consteval",
        "constexpr",
        "constinit",
        "continue",
        "decltype",
        "default",
        "delete",
        "dynamic_cast",
        "explicit",
        "export",
        "extern",
        "false",
        "friend",
        "inline",
        "mutable",
        "namespace",
        "noexcept",
        "not_eq",
        "nullptr",
        "operator",
        "private",
        "protected",
        "public",
        "register",
        "reinterpret_cast",
        "requires",
        "return",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "template",
        "thread_local",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "volatile",
        "while",
        "xor_eq",
    }
)


@dataclass(frozen=True)
class ProgramPriorCandidate:
    sha: str
    index: int
    subject: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class ProgramSignal:
    name: str
    group: str
    members: frozenset[str]
    channel: str
    polarity: int = 1
    provenance: Mapping[str, object] = field(default_factory=dict)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _source_files(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in paths:
        path = raw.replace("\\", "/")
        parts = {part.lower() for part in PurePosixPath(path).parts}
        if parts & _TEST_PARTS:
            continue
        if path.lower().endswith(_SOURCE_SUFFIXES):
            result.append(path)
    return result


def _path_component_matches(term: str, path: str) -> bool:
    compact_term = _compact(term)
    if len(compact_term) < 4:
        return False
    normalized = path.replace("\\", "/")
    stem = _compact(PurePosixPath(normalized).stem)
    parts = [_compact(part) for part in PurePosixPath(normalized).parts[:-1]]
    if compact_term == stem or compact_term in parts:
        return True
    return (
        len(compact_term) >= 8
        and len(stem) >= 8
        and (compact_term in stem or stem in compact_term)
    )


def _under(path: str, directory: str) -> bool:
    normalized = path.replace("\\", "/").rstrip("/")
    prefix = directory.replace("\\", "/").rstrip("/")
    return normalized == prefix or normalized.startswith(prefix + "/")


def _append_signal(
    target: list[ProgramSignal],
    *,
    name: str,
    group: str,
    channel: str,
    members: Iterable[str],
    polarity: int = 1,
    provenance: Mapping[str, object] | None = None,
) -> None:
    member_set = frozenset(members)
    if not member_set:
        return
    target.append(
        ProgramSignal(
            name=name,
            group=group,
            channel=channel,
            members=member_set,
            polarity=-1 if polarity < 0 else 1,
            provenance=dict(provenance or {}),
        )
    )


def _crash_query_terms(crash: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    for entry in crash.get("query_terms", []):
        value = entry.get("term") if isinstance(entry, Mapping) else entry
        value = str(value or "").strip()
        if len(value) >= 5 and value not in result:
            result.append(value)
    return result


def _message_terms(crash: Mapping[str, object]) -> list[str]:
    terms: list[str] = []

    def add(value: object) -> None:
        normalized = str(value or "").strip().lower()
        if len(normalized) >= 6 and normalized not in terms:
            terms.append(normalized)

    for value in _crash_query_terms(crash):
        add(value)
    for value in crash.get("pass_tokens", []):
        add(value)
    for value in crash.get("reproducer_terms", []):
        add(value)
    for path in crash.get("source_paths", []):
        add(PurePosixPath(str(path)).stem)
    for symbol in crash.get("symbols", []):
        for component in str(symbol).split("::"):
            add(component)
    return terms


def _message_term_matches_subject(term: str, subject: str) -> bool:
    """Match specific causal terms without expanding short prefix families."""
    compact_term = _compact(term)
    compact_subject = _compact(subject)
    if len(compact_term) < 6:
        return False
    if compact_term in compact_subject:
        return True
    for word in re.findall(r"[a-z][a-z0-9_]{7,}", subject.lower()):
        compact_word = _compact(word)
        shorter = min(len(compact_term), len(compact_word))
        longer = max(len(compact_term), len(compact_word))
        if shorter / longer >= 0.75 and (
            compact_term.startswith(compact_word)
            or compact_word.startswith(compact_term)
        ):
            return True
    return False


def _candidate_derived_directories(
    candidates: Sequence[ProgramPriorCandidate],
    crash: Mapping[str, object],
) -> dict[str, set[str]]:
    """Approximate bad-tree path derivation over paths touched in the interval.

    Paths absent from every candidate cannot affect interval ranking, so using
    the interval's union here preserves useful target/tool/symbol/pass signals
    without loading the complete tree on every step.
    """
    files = sorted(
        {
            path
            for candidate in candidates
            for path in _source_files(candidate.changed_files)
        }
    )
    by_stem: dict[str, list[str]] = {}
    directories: set[str] = set()
    for path in files:
        by_stem.setdefault(_compact(PurePosixPath(path).stem), []).append(path)
        parent = str(PurePosixPath(path).parent)
        if parent and parent != ".":
            directories.add(parent)

    derived: dict[str, set[str]] = {}

    def note(directory: str, source: str) -> None:
        if directory and directory != ".":
            derived.setdefault(directory, set()).add(source)

    for symbol in crash.get("symbols", []):
        for component in str(symbol).split("::"):
            key = _compact(component)
            if len(key) < 4:
                continue
            for path in by_stem.get(key, []):
                note(str(PurePosixPath(path).parent), "symbol")

    for token in crash.get("pass_tokens", []):
        key = _compact(str(token))
        if len(key) < 6:
            continue
        for stem, paths in by_stem.items():
            if stem == key:
                for path in paths:
                    note(str(PurePosixPath(path).parent), "pass")

    target_dirs = {
        match.group(1)
        for path in files
        if (
            match := re.match(r"^(llvm/lib/Target/[^/]+)/", path)
        )
    }
    for term in crash.get("reproducer_terms", []):
        arch = _compact(str(term))
        if not arch:
            continue
        for directory in target_dirs:
            target = _compact(PurePosixPath(directory).name)
            if arch == target or arch.startswith(target) or target.startswith(arch):
                note(directory, "target")

    tool = str(crash.get("tool") or "").strip()
    tool_candidates = {
        tool,
        re.sub(r"\+\+$", "", tool),
        re.sub(r"-(?:\d+(?:\.\d+)*|trunk|tk)$", "", tool),
    }
    for candidate_tool in tool_candidates:
        normalized_tool = candidate_tool.lower()
        if len(normalized_tool) < 3:
            continue
        matches = sorted(
            directory
            for directory in directories
            if PurePosixPath(directory).name.lower() == normalized_tool
            and not ({part.lower() for part in PurePosixPath(directory).parts} & _TEST_PARTS)
        )
        if len(matches) == 1:
            note(matches[0], "tool")
            break
    return derived


def derive_program_signals(
    candidates: Sequence[ProgramPriorCandidate],
    crash: Mapping[str, object],
    dependency_usage: Mapping[str, Mapping[str, int]],
    *,
    anchor_limit: int = DEFAULT_ANCHOR_LIMIT,
    structural_path_signals: Sequence[Mapping[str, object]] = (),
) -> list[ProgramSignal]:
    """Build fine-grained signal sets without authored issue-profile fields."""
    files_by_sha = {
        candidate.sha: _source_files(candidate.changed_files)
        for candidate in candidates
    }
    signals: list[ProgramSignal] = []

    source_paths = [
        str(path).replace("\\", "/")
        for path in crash.get("source_paths", [])
        if str(path).strip()
    ]
    rare_checker_paths = {
        str(path).replace("\\", "/")
        for path in crash.get("rare_checker_paths", [])
        if str(path).strip()
    }
    for path in source_paths:
        rare_checker = path in rare_checker_paths
        _append_signal(
            signals,
            name=f"file:{path}",
            group="rare-checker-file" if rare_checker else "crash-file",
            channel="checker-polarity" if rare_checker else "crash",
            members=(
                sha for sha, files in files_by_sha.items() if path in files
            ),
            polarity=-1 if rare_checker else 1,
            provenance={
                "source": "crash-artifact",
                "path": path,
                "rare_checker": rare_checker,
            },
        )

    symbol_components: list[str] = []
    for symbol in crash.get("symbols", []):
        for component in str(symbol).split("::"):
            if len(_compact(component)) >= 4 and component not in {"llvm", "clang"}:
                if component not in symbol_components:
                    symbol_components.append(component)
    for component in symbol_components:
        _append_signal(
            signals,
            name=f"stack:{component}",
            group="crash-stack",
            channel="crash",
            members=(
                sha
                for sha, files in files_by_sha.items()
                if any(_path_component_matches(component, path) for path in files)
            ),
            provenance={"source": "crash-stack", "component": component},
        )

    for token in dict.fromkeys(str(value) for value in crash.get("pass_tokens", [])):
        if len(_compact(token)) < 4:
            continue
        _append_signal(
            signals,
            name=f"pass:{token}",
            group="crash-pass",
            channel="crash",
            members=(
                sha
                for sha, files in files_by_sha.items()
                if any(_path_component_matches(token, path) for path in files)
            ),
            provenance={"source": "running-pass", "token": token},
        )

    derived_dir_sources = _candidate_derived_directories(candidates, crash)
    derived_dirs: list[str] = []
    for raw in [*crash.get("derived_paths", []), *source_paths]:
        value = str(raw).replace("\\", "/").rstrip("/")
        if value in rare_checker_paths:
            continue
        directory = value if "." not in PurePosixPath(value).name else str(PurePosixPath(value).parent)
        if directory and directory != "." and directory not in derived_dirs:
            derived_dirs.append(directory)
            derived_dir_sources.setdefault(directory, set()).add(
                "provided" if raw in crash.get("derived_paths", []) else "crash-file"
            )
    for directory in sorted(derived_dir_sources):
        if directory not in derived_dirs:
            derived_dirs.append(directory)
    for directory in derived_dirs:
        _append_signal(
            signals,
            name=f"dir:{directory}",
            group=f"directory:{directory}",
            channel="directory",
            members=(
                sha
                for sha, files in files_by_sha.items()
                if any(_under(path, directory) for path in files)
            ),
            provenance={
                "source": "program-derived-directory",
                "path": directory,
                "derived_from": sorted(derived_dir_sources.get(directory, set())),
            },
        )

    query_terms = _crash_query_terms(crash)
    ordered_anchors = sorted(
        (
            (term, {str(path): max(0, int(count)) for path, count in paths.items() if int(count) > 0})
            for term, paths in dependency_usage.items()
            if term in query_terms
            and term.lower() not in _CXX_NON_VARIABLE
        ),
        key=lambda item: (len(item[1]), item[0]),
    )[: max(0, anchor_limit)]
    for term, usage in ordered_anchors:
        ordered_files = sorted(usage, key=lambda path: (-usage[path], path))
        top_count = max(1, math.ceil(len(ordered_files) * 0.2))
        tiers = {
            "mention": ordered_files,
            "used": [path for path in ordered_files if usage[path] >= 2],
            "heavy5": [path for path in ordered_files if usage[path] >= 5],
            "heavy10": [path for path in ordered_files if usage[path] >= 10],
            "top20": ordered_files[:top_count],
        }
        for tier in DEPENDENCY_TIERS:
            tier_files = set(tiers[tier])
            _append_signal(
                signals,
                name=f"dependency:{term}:{tier}",
                group=f"dependency:{term}",
                channel="dependency",
                members=(
                    sha
                    for sha, files in files_by_sha.items()
                    if any(path in tier_files for path in files)
                ),
                provenance={
                    "source": "bad-tree-usage-index",
                    "term": term,
                    "tier": tier,
                    "file_count": len(tier_files),
                },
            )

    for term in _message_terms(crash):
        _append_signal(
            signals,
            name=f"message:{term}",
            # Message terms all originate from one crash artifact. Treat them
            # as correlated observations so related symbol spellings cannot
            # accumulate as independent evidence.
            group="commit-message",
            channel="message",
            members=(
                candidate.sha
                for candidate in candidates
                if _message_term_matches_subject(term, candidate.subject)
            ),
            provenance={"source": "crash-derived-message-term", "term": term},
        )
    for item in structural_path_signals:
        paths = [
            str(path).replace("\\", "/").rstrip("/")
            for path in item.get("paths", [])
            if str(path).strip()
        ]
        if not paths:
            continue
        _append_signal(
            signals,
            name=str(item.get("name") or "structural-path"),
            group=str(item.get("group") or item.get("name") or "structural-path"),
            channel=str(item.get("channel") or "structural"),
            members=(
                sha
                for sha, files in files_by_sha.items()
                if any(_under(path, target) for path in files for target in paths)
            ),
            provenance={
                "source": "bad-tree-program-structure",
                **dict(item.get("provenance", {})),
                "paths": paths,
            },
        )
    return signals


def score_program_prior(
    candidates: Sequence[ProgramPriorCandidate],
    signals: Sequence[ProgramSignal],
    *,
    floor: float = DEFAULT_PRIOR_FLOOR,
) -> dict[str, object]:
    """Score all candidates with volume-corrected surprisal and group-max."""
    if not candidates:
        raise ValueError("program prior requires at least one candidate")
    if floor <= 0:
        raise ValueError("program prior floor must be positive")
    candidate_shas = {candidate.sha for candidate in candidates}
    active = [
        signal
        for signal in signals
        if 0 < len(signal.members & candidate_shas) < len(candidates)
    ]
    changed_counts = {
        # Match the reference scorer: volume correction uses the commit's
        # complete changed-file count, while signal membership excludes tests.
        candidate.sha: max(1, len(candidate.changed_files))
        for candidate in candidates
    }
    mean_changed = max(
        1.0,
        sum(changed_counts.values()) / len(changed_counts),
    )
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        by_group: dict[str, float] = {}
        hits: list[str] = []
        group_hits: dict[str, str] = {}
        for signal in active:
            members = signal.members & candidate_shas
            if candidate.sha not in members:
                continue
            marginal = min(max(len(members) / len(candidates), 1e-9), 0.999999)
            hit_probability = 1.0 - math.pow(
                1.0 - marginal,
                changed_counts[candidate.sha] / mean_changed,
            )
            weight = -math.log(hit_probability) if hit_probability > 0 else 0.0
            contribution = weight * signal.polarity
            if (
                signal.group not in by_group
                or abs(contribution) > abs(by_group[signal.group])
            ):
                by_group[signal.group] = contribution
                group_hits[signal.group] = signal.name
            hits.append(signal.name)
        score = max(floor, floor + sum(by_group.values()))
        rows.append(
            {
                "sha": candidate.sha,
                "index": candidate.index,
                "score": score,
                "changed_file_count": changed_counts[candidate.sha],
                "hits": hits,
                "group_hits": group_hits,
                "group_weights": by_group,
            }
        )
    total = sum(float(row["score"]) for row in rows)
    for row in rows:
        row["mass"] = float(row["score"]) / total
    ranked = sorted(
        rows,
        key=lambda row: (-float(row["score"]), int(row["index"]), str(row["sha"])),
    )
    rank_by_sha = {str(row["sha"]): rank for rank, row in enumerate(ranked, 1)}
    for row in rows:
        row["rank"] = rank_by_sha[str(row["sha"])]

    signal_payload = [
        {
            "name": signal.name,
            "group": signal.group,
            "channel": signal.channel,
            "polarity": signal.polarity,
            "coverage": len(signal.members & candidate_shas),
            "provenance": dict(signal.provenance),
        }
        for signal in active
    ]
    evidence_digest = hashlib.sha256(
        json.dumps(
            {
                "version": PROGRAM_PRIOR_VERSION,
                "candidate_shas": [candidate.sha for candidate in candidates],
                "signals": signal_payload,
                "rows": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": PROGRAM_PRIOR_VERSION,
        "implementation_status": "implemented-pending-online-validation",
        "implemented_channels": list(IMPLEMENTED_CHANNELS),
        "deferred_parity_channels": list(DEFERRED_PARITY_CHANNELS),
        "hard_pruning": False,
        "floor": floor,
        "candidate_count": len(candidates),
        "active_signal_count": len(active),
        "signal_groups": len({signal.group for signal in active}),
        "signals": signal_payload,
        "candidates": rows,
        "candidate_by_sha": {str(row["sha"]): row for row in rows},
        "prior_score_by_sha": {
            str(row["sha"]): float(row["score"]) for row in rows
        },
        "prior_mass_by_sha": {
            str(row["sha"]): float(row["mass"]) for row in rows
        },
        "evidence_sha256": evidence_digest,
    }


def build_program_prior(
    candidates: Sequence[ProgramPriorCandidate],
    crash: Mapping[str, object],
    dependency_usage: Mapping[str, Mapping[str, int]],
    *,
    floor: float = DEFAULT_PRIOR_FLOOR,
    anchor_limit: int = DEFAULT_ANCHOR_LIMIT,
    structural_path_signals: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    signals = derive_program_signals(
        candidates,
        crash,
        dependency_usage,
        anchor_limit=anchor_limit,
        structural_path_signals=structural_path_signals,
    )
    result = score_program_prior(candidates, signals, floor=floor)
    result["crash_evidence"] = {
        "artifact_status": crash.get("artifact_status"),
        "artifact_origin": crash.get("artifact_origin"),
        "artifact_path": crash.get("artifact_path"),
        "artifact_sha256": crash.get("artifact_sha256"),
        "reproducer_paths": list(crash.get("reproducer_paths", [])),
        "reproducer_sha256": crash.get("reproducer_sha256"),
    }
    return result


def mass_preserving_ordinal_fusion(
    prior_mass_by_sha: Mapping[str, float],
    ordinal_rank_by_sha: Mapping[str, int],
) -> tuple[dict[str, float], dict[str, object]]:
    """Rerank the model frontier without changing its total prior mass."""
    masses = {str(sha): max(0.0, float(mass)) for sha, mass in prior_mass_by_sha.items()}
    total = sum(masses.values())
    if total <= 0:
        raise ValueError("program prior mass must be positive")
    masses = {sha: mass / total for sha, mass in masses.items()}
    ranks = {
        str(sha): max(1, int(rank))
        for sha, rank in ordinal_rank_by_sha.items()
        if str(sha) in masses
    }
    if not ranks:
        return masses, {
            "frontier_count": 0,
            "frontier_mass_before": 0.0,
            "frontier_mass_after": 0.0,
            "policy": "identity-no-ordinal-ranks",
        }

    frontier_mass = sum(masses[sha] for sha in ranks)
    weighted = {
        sha: masses[sha] * (1.0 / rank)
        for sha, rank in ranks.items()
    }
    weighted_total = sum(weighted.values())
    fused = dict(masses)
    if weighted_total > 0:
        for sha, value in weighted.items():
            fused[sha] = frontier_mass * value / weighted_total
    return fused, {
        "frontier_count": len(ranks),
        "frontier_mass_before": frontier_mass,
        "frontier_mass_after": sum(fused[sha] for sha in ranks),
        "ordinal_factor": "reciprocal-rank",
        "policy": "frontier-local-mass-preserving",
        "ranks": ranks,
    }
