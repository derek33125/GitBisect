#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
PROFILES_PATH = ROOT_DIR / "tools" / "lm_bisect_profiles.json"
DEFAULT_OBSERVATIONS_DIR = ROOT_DIR / "results" / "lm_bisect_observations"
DEFAULT_RUN_HISTORY_DIR = ROOT_DIR / "results" / "lm_bisect_runs"
DEFAULT_ISSUE_RESULTS_DIR = ROOT_DIR / "results" / "issues"
DEFAULT_ENV_PATH = ROOT_DIR / ".env"
DEFAULT_MODEL_CACHE_DIR = ROOT_DIR / "results" / "lm_bisect_model_cache"

BUILD_FILES = {
    "cmakelists.txt",
    "makefile",
    "build.ninja",
    "meson.build",
    "bazel",
    "bazel.build",
    "workspace",
    "workspace.bazel",
}

RISKY_WORDS = (
    "fix",
    "revert",
    "vector",
    "dag",
    "selectiondag",
    "licm",
    "loop",
    "pgo",
    "profile",
    "alias",
    "hoist",
    "masked",
    "x86",
    "codegen",
    "target",
    "opt",
    "poison",
    "build_vector",
)

BUILD_RISK_WORDS = (
    "cmake",
    "make",
    "bazel",
    "gn",
    "ninja",
    "depend",
    "toolchain",
    "workflow",
    "ci",
    "script",
    "build",
    "configure",
    "link",
    "cross",
    "platform",
)

DEFAULT_CALIBRATED_PRIOR_POWER = 1.35
DEFAULT_CALIBRATED_PRIOR_BONUS = 1.5
DEFAULT_WEAK_RELEVANCE_PENALTY = 0.05
DEFAULT_WEAK_RELEVANCE_THRESHOLD = 0.8
DEFAULT_BUILD_SUCCESS_POWER = 1.0
MODEL_SCORING_VERSION = "v5-distinct-crash-evidence"
TRACE_PROMPT_SIGNATURE_SUFFIX = " [same crash signature repeated "
DEFAULT_MODEL_SCORING_BATCH_SIZE = 12
DEFAULT_MODEL_RANK_BONUS = 0.35
DEFAULT_MODEL_PRIOR_SOFTMAX_TEMPERATURE = 4.0
DEFAULT_MODEL_DIRECT_HIT_BONUS = 2.5
DEFAULT_MODEL_MECHANISM_BONUS = 0.25
DEFAULT_MODEL_MECHANISM_OVERRIDE_SCALE = 2.0
TRACE_PROMPT_MAX_CHARS = 2400

CANDIDATE_PRUNING_GROUPS = {
    "clang-pgo": (
        "clang",
        "llvm/include",
        "llvm/lib/Analysis",
        "llvm/lib/CodeGen",
        "llvm/lib/Frontend",
        "llvm/lib/IR",
        "llvm/lib/MC",
        "llvm/lib/Passes",
        "llvm/lib/ProfileData",
        "llvm/lib/Support",
        "llvm/lib/Target/X86",
        "llvm/lib/Transforms",
    ),
    "opt-lli": (
        "llvm/include",
        "llvm/lib/Analysis",
        "llvm/lib/CodeGen",
        "llvm/lib/ExecutionEngine",
        "llvm/lib/IR",
        "llvm/lib/MC",
        "llvm/lib/Passes",
        "llvm/lib/Support",
        "llvm/lib/Target/X86",
        "llvm/lib/Transforms",
        "llvm/tools/lli",
        "llvm/tools/opt",
    ),
}

ISSUE_CANDIDATE_PRUNING_GROUP = {
    "pr172195": "clang-pgo",
    "pr176682": "clang-pgo",
    "pr191581": "clang-pgo",
    "pr187875": "opt-lli",
}

AMBIGUOUS_CORE_PREFIXES = (
    "llvm/lib/AsmParser",
    "llvm/lib/Bitcode",
    "llvm/lib/Frontend",
    "llvm/lib/MC",
    "llvm/lib/Object",
    "llvm/lib/Passes",
    "llvm/lib/TargetParser",
)

BUILD_KEEP_PREFIXES = (
    ".github/workflows",
    "cmake",
    "llvm/cmake",
    "llvm/utils/gn",
    "llvm/utils/TableGen",
)

GENERATOR_FILE_SUFFIXES = (".td", ".def")
LIT_CONFIG_NAMES = ("lit.cfg", "lit.cfg.py", "lit.site.cfg", "lit.site.cfg.py")


def git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    completed = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def log_progress(message: str) -> None:
    print(f"[run-online {time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9_./+-]+", "", token.lower())


def tokenize(text: str) -> list[str]:
    return [t for t in (normalize_token(p) for p in re.findall(r"[A-Za-z0-9_./+-]+", text.lower())) if t]


def compact_alnum(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def tokenize_v1(text: str) -> list[str]:
    return [t for t in (normalize_token(p) for p in re.split(r"\s+", text.lower())) if t]


def stem_path(path: str) -> str:
    parts = Path(path).parts
    if len(parts) >= 4:
        return "/".join(parts[:4])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return path


@dataclass(frozen=True)
class IssueProfile:
    issue_id: str
    issue_url: str
    title: str
    good_commit: str
    good_ref: str
    bad_commit: str
    bisect_log: str
    runner: str
    bug_report_summary: str
    keywords: list[str]
    relevant_paths: list[str]
    high_risk_paths: list[str]


@dataclass
class CommitRecord:
    index: int
    sha: str
    subject: str
    body: str
    changed_files: list[str]
    diff_text: str
    semantic_score: float
    build_success_prob: float
    suspicion_weight: float
    cumulative_weight: float = 0.0
    balance_score: float = 0.0
    utility: float = 0.0
    selection_score: float = 0.0
    posterior_bad_mass: float = 0.0
    posterior_info_gain: float = 0.0
    calibrated_suspicion_weight: float = 0.0
    calibrated_posterior_bad_mass: float = 0.0
    calibrated_posterior_info_gain: float = 0.0
    weak_relevance_penalty: float = 0.0
    evidence: list[str] | None = None
    feedback_bias: float = 0.0
    features: list[str] | None = None


@dataclass
class SuggestionResult:
    selected: CommitRecord
    candidates: list[CommitRecord]
    lambda_weight: float
    build_success_power: float


@dataclass(frozen=True)
class SelectionDecision:
    selected: CommitRecord
    ranked_candidates: list[CommitRecord]
    search_policy: str
    selection_mode: str
    hybrid_switch_window: int | None = None


@dataclass(frozen=True)
class CommitObservation:
    sha: str
    verdict: str
    summary: str
    features: list[str]
    source: str = "manual"
    evidence: list[str] | None = None
    log_excerpt: str = ""
    trace_excerpt: str = ""


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str
    model_name: str
    observation_prompt_mode: str = "legacy"


@dataclass
class CommitMetadata:
    sha: str
    subject: str
    body: str
    changed_files: list[str]


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, IssueProfile]:
    raw = json.loads(path.read_text())
    profiles: dict[str, IssueProfile] = {}
    for issue_id, data in raw.items():
        profiles[issue_id] = IssueProfile(**data)
    return profiles


def load_env_file(env_path: Path = DEFAULT_ENV_PATH) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def load_model_config(
    model_name: str | None = None,
    observation_prompt_mode: str = "legacy",
    env_path: Path = DEFAULT_ENV_PATH,
) -> ModelConfig:
    load_env_file(env_path)
    api_key = os.getenv("CHATANYWHERE_KEY")
    if not api_key:
        raise RuntimeError("CHATANYWHERE_KEY is not set; check .env")
    base_url = os.getenv("CHATANYWHERE_BASE_URL", "https://api.chatanywhere.tech/v1")
    resolved_model = model_name or os.getenv("CHATANYWHERE_MODEL", "gpt-5.4-mini")
    return ModelConfig(
        api_key=api_key,
        base_url=base_url,
        model_name=resolved_model,
        observation_prompt_mode=observation_prompt_mode,
    )


def model_cache_path(issue_id: str, model_name: str, scoring_version: str = MODEL_SCORING_VERSION) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name)
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", scoring_version)
    return DEFAULT_MODEL_CACHE_DIR / f"{issue_id}-{safe_model}-{safe_version}.json"


def resolved_model_scoring_version(observation_prompt_mode: str = "legacy") -> str:
    if observation_prompt_mode == "legacy":
        return MODEL_SCORING_VERSION
    safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", observation_prompt_mode)
    return f"{MODEL_SCORING_VERSION}-obs-{safe_mode}"


def load_model_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    content = path.read_text().strip()
    if not content:
        return {}
    return json.loads(content)


def save_model_cache(path: Path, payload: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(path)


def list_candidate_commits(repo: Path, good: str, bad: str) -> list[str]:
    output = git(repo, "rev-list", "--reverse", f"{good}..{bad}")
    commits = [line.strip() for line in output.splitlines() if line.strip()]
    if not commits:
        raise ValueError(f"no commits found in range {good}..{bad}")
    return commits


def load_candidate_commits_from_file(path: Path) -> list[str]:
    raw = json.loads(path.read_text())
    commits: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            sha = item.strip()
        else:
            sha = item["sha"].strip()
        if sha in seen:
            continue
        commits.append(sha)
        seen.add(sha)
    if not commits:
        raise ValueError(f"no commits found in {path}")
    return commits


def commit_subject(repo: Path, sha: str) -> str:
    return git(repo, "show", "-s", "--format=%s", sha).strip()


def commit_body(repo: Path, sha: str) -> str:
    return git(repo, "show", "-s", "--format=%b", sha).strip()


def commit_changed_files(repo: Path, sha: str) -> list[str]:
    output = git(repo, "show", "--no-renames", "--format=", "--name-only", sha)
    return [line.strip() for line in output.splitlines() if line.strip()]


def commit_diff_text(repo: Path, sha: str, max_chars: int = 12000) -> str:
    text = git(repo, "show", "--no-renames", "--format=", "--unified=0", sha)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def commit_subject_and_files(repo: Path, sha: str) -> tuple[str, list[str]]:
    cmd = [
        "git",
        "-C",
        str(repo),
        "show",
        "-z",
        "--no-renames",
        "--format=%x1e%H%x00%s%x00",
        "--name-only",
        sha,
    ]
    completed = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = completed.stdout
    parts = output.split(b"\x1e")
    record = next((part for part in parts if part), b"")
    if not record:
        raise ValueError(f"failed to parse subject/files for {sha}")
    items = record.split(b"\x00")
    parsed_sha = items[0].decode("utf-8", "replace").strip()
    if parsed_sha != sha:
        raise ValueError(f"unexpected sha while parsing {sha}: got {parsed_sha}")
    subject = items[1].decode("utf-8", "replace").strip() if len(items) > 1 else ""
    files: list[str] = []
    for raw in items[2:]:
        entry = raw.decode("utf-8", "replace").strip()
        if entry:
            files.append(entry)
    return subject, files


def load_commit_metadata(
    repo: Path,
    shas: list[str],
    include_body: bool,
) -> dict[str, CommitMetadata]:
    metadata: dict[str, CommitMetadata] = {}
    if not shas:
        return metadata

    format_string = "%x1e%H%x00%s%x00%b%x00" if include_body else "%x1e%H%x00%s%x00"
    cmd = [
        "git",
        "-C",
        str(repo),
        "show",
        "-z",
        f"--format={format_string}",
        "--name-only",
        "--no-renames",
        "--stdin",
    ]
    stdin = "\n".join(shas) + "\n"
    completed = subprocess.run(
        cmd,
        check=True,
        input=stdin.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    for raw_record in completed.stdout.split(b"\x1e"):
        if not raw_record:
            continue
        items = raw_record.split(b"\x00")
        sha = items[0].decode("utf-8", "replace").strip()
        if not sha:
            continue
        subject = items[1].decode("utf-8", "replace").strip() if len(items) > 1 else ""
        body = items[2].decode("utf-8", "replace").strip() if include_body and len(items) > 2 else ""
        file_start = 3 if include_body else 2
        files: list[str] = []
        for raw in items[file_start:]:
            entry = raw.decode("utf-8", "replace").strip()
            if entry:
                files.append(entry)
        metadata[sha] = CommitMetadata(
            sha=sha,
            subject=subject,
            body=body,
            changed_files=files,
        )
    return metadata


def path_matches(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def candidate_pruning_keep_prefixes(profile: IssueProfile) -> list[str]:
    keep_prefixes: list[str] = []
    group_name = ISSUE_CANDIDATE_PRUNING_GROUP.get(profile.issue_id)
    for prefix in CANDIDATE_PRUNING_GROUPS.get(group_name, ()):
        append_unique(keep_prefixes, prefix)
    for prefix in profile.relevant_paths:
        append_unique(keep_prefixes, prefix)
    for prefix in profile.high_risk_paths:
        append_unique(keep_prefixes, prefix)
    return keep_prefixes


def is_build_or_generator_path(path: str) -> bool:
    lowered = path.lower()
    filename = Path(path).name.lower()
    suffix = Path(path).suffix.lower()
    parts = {part.lower() for part in Path(path).parts}

    if filename in BUILD_FILES or filename in LIT_CONFIG_NAMES:
        return True
    if suffix in GENERATOR_FILE_SUFFIXES:
        return True
    if path_matches(path, BUILD_KEEP_PREFIXES):
        return True
    return any(part in {"build", "ci", "cmake", "workflow"} for part in parts) or lowered.endswith(".cmake")


def is_ambiguous_core_llvm_path(path: str) -> bool:
    if path_matches(path, AMBIGUOUS_CORE_PREFIXES):
        return True
    parts = Path(path).parts
    lowered_parts = tuple(part.lower() for part in parts)
    if len(lowered_parts) >= 4 and lowered_parts[:3] == ("llvm", "lib", "target"):
        if len(lowered_parts) == 4:
            return True
        if lowered_parts[3] in {"globalisel", "targetparser"}:
            return True
    return False


def candidate_pruning_decision(changed_files: list[str], keep_prefixes: list[str]) -> tuple[bool, str]:
    if not changed_files:
        return True, "missing-metadata"
    if any(is_build_or_generator_path(path) for path in changed_files):
        return True, "build-or-generator"
    if any(path_matches(path, keep_prefixes) for path in changed_files):
        return True, "target-closure"
    if any(is_ambiguous_core_llvm_path(path) for path in changed_files):
        return True, "ambiguous-core"
    return False, "outside-target-closure"


def apply_candidate_pruning(
    profile: IssueProfile,
    shas: list[str],
    metadata_by_sha: dict[str, CommitMetadata],
    mode: str,
) -> tuple[list[str], dict]:
    if mode == "off":
        return shas[:], {
            "mode": mode,
            "before_count": len(shas),
            "after_count": len(shas),
            "pruned_count": 0,
            "keep_prefixes": [],
            "kept_reason_counts": {},
            "prune_reason_counts": {},
            "pruned_examples": [],
        }
    if mode != "conservative":
        raise ValueError(f"unsupported candidate pruning mode: {mode}")

    keep_prefixes = candidate_pruning_keep_prefixes(profile)
    kept: list[str] = []
    kept_reason_counts: dict[str, int] = {}
    prune_reason_counts: dict[str, int] = {}
    pruned_examples: list[dict] = []

    for sha in shas:
        metadata = metadata_by_sha.get(sha)
        if metadata is None:
            kept.append(sha)
            kept_reason_counts["missing-metadata"] = kept_reason_counts.get("missing-metadata", 0) + 1
            continue
        keep, reason = candidate_pruning_decision(metadata.changed_files, keep_prefixes)
        if keep:
            kept.append(sha)
            kept_reason_counts[reason] = kept_reason_counts.get(reason, 0) + 1
            continue
        prune_reason_counts[reason] = prune_reason_counts.get(reason, 0) + 1
        if len(pruned_examples) < 10:
            pruned_examples.append(
                {
                    "sha": sha,
                    "subject": metadata.subject,
                    "reason": reason,
                    "changed_files": metadata.changed_files[:6],
                }
            )

    fallback = None
    if not kept:
        kept = shas[:]
        kept_reason_counts = {"fallback-empty-window": len(kept)}
        prune_reason_counts = {}
        pruned_examples = []
        fallback = "empty-window"

    summary = {
        "mode": mode,
        "before_count": len(shas),
        "after_count": len(kept),
        "pruned_count": len(shas) - len(kept),
        "keep_prefixes": keep_prefixes,
        "kept_reason_counts": kept_reason_counts,
        "prune_reason_counts": prune_reason_counts,
        "pruned_examples": pruned_examples,
    }
    if fallback is not None:
        summary["fallback"] = fallback
    return kept, summary


def score_semantics_v1(profile: IssueProfile, subject: str, body: str, files: list[str], diff: str) -> tuple[float, list[str]]:
    evidence: list[str] = []
    text = " ".join([subject, body, diff]).lower()
    tokens = set(tokenize_v1(text))
    score = 1.0

    keyword_hits = 0
    for keyword in profile.keywords:
        normalized_keyword = normalize_token(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword in tokens:
            keyword_hits += 1
            continue
        if len(normalized_keyword) >= 6 and any(ch in keyword for ch in (" ", ".", "-", "/")):
            if keyword.lower() in text:
                keyword_hits += 1
    if keyword_hits:
        score += min(2.5, 0.35 * keyword_hits)
        evidence.append(f"{keyword_hits} keyword hits")

    relevant_file_hits = [path for path in files if path_matches(path, profile.relevant_paths)]
    if relevant_file_hits:
        score += min(2.5, 0.8 * len(relevant_file_hits))
        evidence.append(f"relevant paths: {', '.join(relevant_file_hits[:3])}")

    high_risk_hits = [path for path in files if path_matches(path, profile.high_risk_paths)]
    if high_risk_hits:
        score += min(1.5, 0.3 * len(high_risk_hits))
        evidence.append(f"high-risk paths touched: {len(high_risk_hits)}")

    risky_hits = 0
    for word in RISKY_WORDS:
        if word in text:
            risky_hits += 1
    if risky_hits:
        score += min(1.0, 0.15 * risky_hits)
        evidence.append(f"risky words: {risky_hits}")

    if not evidence:
        evidence.append("no strong semantic matches")

    return score, evidence


def score_semantics_tuned(profile: IssueProfile, subject: str, body: str, files: list[str], diff: str) -> tuple[float, list[str]]:
    evidence: list[str] = []
    text = " ".join([subject, body, diff]).lower()
    tokens = set(tokenize(text))
    compact_text = compact_alnum(text)
    score = 0.05

    keyword_hits = 0
    for keyword in profile.keywords:
        normalized_keyword = normalize_token(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword in tokens:
            keyword_hits += 1
            continue
        # Phrase-style keywords are allowed to match in raw text.
        if len(normalized_keyword) >= 6 and any(ch in keyword for ch in (" ", ".", "-", "/", "_")):
            if keyword.lower() in text:
                keyword_hits += 1
                continue
        # Long structured identifiers often appear with punctuation removed or attached to suffixes.
        if len(normalized_keyword) >= 8 and normalized_keyword in compact_text:
            keyword_hits += 1
    if keyword_hits:
        score += min(4.5, 0.60 * keyword_hits)
        evidence.append(f"{keyword_hits} keyword hits")

    relevant_file_hits = [path for path in files if path_matches(path, profile.relevant_paths)]
    if relevant_file_hits:
        score += min(4.0, 1.2 * len(relevant_file_hits))
        evidence.append(f"relevant paths: {', '.join(relevant_file_hits[:3])}")

    high_risk_hits = [path for path in files if path_matches(path, profile.high_risk_paths)]
    if high_risk_hits:
        score += min(2.0, 0.5 * len(high_risk_hits))
        evidence.append(f"high-risk paths touched: {len(high_risk_hits)}")

    risky_hits = 0
    for word in RISKY_WORDS:
        if word in text:
            risky_hits += 1
    if risky_hits:
        score += min(1.5, 0.2 * risky_hits)
        evidence.append(f"risky words: {risky_hits}")

    if not evidence:
        evidence.append("no strong semantic matches")

    return score, evidence


def score_semantics(
    profile: IssueProfile,
    subject: str,
    body: str,
    files: list[str],
    diff: str,
    heuristic_version: str = "tuned",
) -> tuple[float, list[str]]:
    if heuristic_version == "v1":
        return score_semantics_v1(profile, subject, body, files, diff)
    if heuristic_version == "tuned":
        return score_semantics_tuned(profile, subject, body, files, diff)
    raise ValueError(f"unsupported heuristic version: {heuristic_version}")


def score_build_probability(subject: str, body: str, files: list[str], diff: str) -> tuple[float, list[str]]:
    evidence: list[str] = []
    score = 0.92
    lowered_text = " ".join([subject, body, diff]).lower()

    build_file_hits = []
    for path in files:
        filename = Path(path).name.lower()
        if filename in BUILD_FILES:
            build_file_hits.append(path)
        elif any(part in {"cmake", "build", "ci", "workflow"} for part in Path(path).parts):
            build_file_hits.append(path)
    if build_file_hits:
        score -= min(0.40, 0.12 * len(build_file_hits))
        evidence.append(f"build files touched: {', '.join(build_file_hits[:3])}")

    build_word_hits = sum(1 for word in BUILD_RISK_WORDS if word in lowered_text)
    if build_word_hits:
        score -= min(0.30, 0.04 * build_word_hits)
        evidence.append(f"build-risk words: {build_word_hits}")

    cross_subsystem_count = len({Path(path).parts[:2] for path in files if len(Path(path).parts) >= 2})
    if cross_subsystem_count >= 4:
        score -= min(0.20, 0.03 * (cross_subsystem_count - 3))
        evidence.append(f"cross-subsystem change: {cross_subsystem_count} groups")

    if any("revert" in line.lower() for line in [subject, body]):
        score -= 0.05
        evidence.append("revert-style commit")

    score = max(0.05, min(0.99, score))
    if not evidence:
        evidence.append("no obvious build-risk indicators")
    return score, evidence


def compact_diff_for_prompt(files: list[str], diff: str, max_chars: int = 4000) -> str:
    file_block = "\n".join(files[:20])
    text = f"Changed files:\n{file_block}\n\nDiff:\n{diff}"
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def summarize_files_for_prompt(files: list[str], max_files: int = 30) -> str:
    if not files:
        return "Changed files:\n<none>"
    return "Changed files:\n" + "\n".join(files[:max_files])


def normalize_trace_signature(text: str) -> str:
    stripped = " ".join(text.strip().split())
    if not stripped:
        return ""
    assertion_match = re.search(r"(Assertion .*? failed\.)", stripped)
    if assertion_match:
        return assertion_match.group(1)
    fatal_match = re.search(r"(fatal error:.*)", stripped, re.IGNORECASE)
    if fatal_match:
        return fatal_match.group(1)
    frontend_match = re.search(r"(clang frontend command failed with exit code \d+)", stripped, re.IGNORECASE)
    if frontend_match:
        return frontend_match.group(1)
    running_pass_match = re.search(r"(Running pass .*? on (?:function|module) .*?)$", stripped)
    if running_pass_match:
        return running_pass_match.group(1)
    if "Stack dump:" in stripped:
        return "Stack dump:"
    return stripped[:240]


def observation_crash_signature(observation: CommitObservation, *, trace_only: bool = False) -> str:
    raw_text = observation.trace_excerpt.strip()
    if not trace_only and not raw_text:
        raw_text = observation.log_excerpt.strip() or "\n".join((observation.evidence or [])[:8]).strip()
    if not raw_text:
        return ""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        signature = normalize_trace_signature(line)
        if signature.startswith("Assertion "):
            return signature
    for line in lines:
        lowered = line.lower()
        if "fatal error:" in lowered or "clang frontend command failed" in lowered:
            return normalize_trace_signature(line)
    for line in lines:
        if "Running pass" in line:
            return normalize_trace_signature(line)
    for line in lines:
        if "Stack dump:" in line:
            return "Stack dump:"
    return normalize_trace_signature(lines[0])


def recent_crash_observations_for_prompt(
    observations: list[CommitObservation],
    *,
    limit: int = 2,
    trace_only: bool = False,
) -> list[tuple[CommitObservation, int]]:
    bad_runner_observations = [
        obs
        for obs in observations
        if obs.verdict in {"bad", "skip"}
        and obs.source == "runner"
        and (
            obs.trace_excerpt.strip() if trace_only else (obs.trace_excerpt or obs.log_excerpt or obs.evidence)
        )
    ]
    if limit <= 0:
        return []
    if not trace_only:
        return [(obs, 1) for obs in bad_runner_observations[-limit:]]

    latest_by_signature: dict[str, tuple[CommitObservation, int]] = {}
    for obs in reversed(bad_runner_observations):
        signature = observation_crash_signature(obs, trace_only=True)
        if not signature:
            continue
        existing = latest_by_signature.get(signature)
        if existing is None:
            latest_by_signature[signature] = (obs, 1)
        else:
            latest_by_signature[signature] = (existing[0], existing[1] + 1)
    return list(latest_by_signature.values())[:limit]


def format_observation_for_prompt(
    observation: CommitObservation,
    *,
    trace_only: bool = False,
    repeat_count: int = 1,
) -> str:
    trace_excerpt = (observation.trace_excerpt or "").strip()
    if trace_only:
        if not trace_excerpt:
            return ""
        if len(trace_excerpt) > TRACE_PROMPT_MAX_CHARS:
            trace_excerpt = trace_excerpt[:TRACE_PROMPT_MAX_CHARS]
        repeat_note = ""
        if repeat_count > 1:
            repeat_note = f"{TRACE_PROMPT_SIGNATURE_SUFFIX}{repeat_count} times]"
        return (
            f"Observed bad commit: {observation.sha}\n"
            f"Primary crash/assertion evidence{repeat_note}:\n{trace_excerpt}\n"
        )
    if not trace_excerpt and not trace_only:
        trace_excerpt = "\n".join((observation.evidence or [])[:8]).strip()
    if not trace_excerpt and not trace_only and observation.log_excerpt:
        trace_excerpt = observation.log_excerpt.strip()[:1200]
    if len(trace_excerpt) > 1200:
        trace_excerpt = trace_excerpt[:1200]
    return (
        f"Observed bad commit: {observation.sha}\n"
        f"Summary: {observation.summary}\n"
        f"Trace excerpt:\n{trace_excerpt or '<none>'}\n"
    )


def build_model_scoring_prompt(
    profile: IssueProfile,
    commits: list[dict],
    observations: list[CommitObservation] | None = None,
    observation_prompt_mode: str = "legacy",
) -> str:
    commit_blocks = []
    for item in commits:
        commit_blocks.append(
            f"""Commit SHA: {item['sha']}
Subject: {item['subject']}
Body: {item['body']}
{compact_diff_for_prompt(item['files'], item.get('diff', '')) if item.get('diff') else summarize_files_for_prompt(item['files'])}
"""
        )
    trace_only = observation_prompt_mode == "trace-only"
    recent_bad_observations = recent_crash_observations_for_prompt(
        observations or [],
        limit=2,
        trace_only=trace_only,
    )
    observed_crash_context = ""
    if recent_bad_observations:
        formatted_observations = [
            format_observation_for_prompt(obs, trace_only=trace_only, repeat_count=count)
            for obs, count in recent_bad_observations
        ]
        formatted_observations = [item for item in formatted_observations if item.strip()]
    else:
        formatted_observations = []
    if formatted_observations:
        observed_crash_context = (
            "Observed crash evidence from the latest bad builds:\n\n"
            + "\n".join(formatted_observations)
        )
    return f"""
You are scoring LLVM commits for an LLM-assisted bug bisect system.

Goal:
- Estimate which candidate is most likely to be near the true first bad boundary for this issue.
- Use the full score range. Do not compress all plausible commits into nearly the same score band.
- Be contrastive: if several commits touch the same subsystem, give clearly higher scores to the ones whose mechanism best matches the issue, and clearly lower scores to nearby but weaker candidates.

Issue:
- id: {profile.issue_id}
- title: {profile.title}
- summary: {profile.bug_report_summary}
- relevant paths: {', '.join(profile.relevant_paths)}
- high-risk paths: {', '.join(profile.high_risk_paths)}
- keywords: {', '.join(profile.keywords)}

{observed_crash_context if observed_crash_context else ''}

Candidate commits:

{chr(10).join(commit_blocks)}

Return strict JSON as an array. One object per commit, with this schema:
[
  {{
    "sha": "<commit sha>",
    "semantic_score": <float between 0.1 and 8.0>,
    "build_success_prob": <float between 0.05 and 0.99>,
    "evidence": ["short reason 1", "short reason 2"],
    "features": ["feature1", "feature2", "feature3"]
  }}
]

Scoring guidance:
- If observed crash evidence is provided, treat assertion message or fatal error text as the strongest signal. Stack trace and pass/function names are supporting context. Use the issue summary only as backup context.
- semantic_score should be highest for commits that most directly match the reported bug mechanism, stack trace terms, relevant paths, or likely faulty optimization logic.
- Lower the score when a commit only touches the same subsystem but does not strongly match the actual failure mechanism.
- build_success_prob should be lower when the commit looks likely to fail build or be unstable to test.
- features should be normalized reusable hints for later similarity matching.
- Prefer substantive mechanisms over superficial keyword overlap.
Only output JSON.
""".strip()


def parse_model_json_payload(content: str) -> list[dict]:
    def parse_with_control_char_escape(candidate: str):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            if "Invalid control character" not in str(exc):
                raise
            escaped = re.sub(
                r"(?<!\\)[\x00-\x08\x0b-\x0c\x0e-\x1f]",
                lambda match: f"\\u{ord(match.group(0)):04x}",
                candidate,
            )
            return json.loads(escaped)

    stripped = content.strip()
    if not stripped:
        raise ValueError("model returned empty content")
    try:
        payload = parse_with_control_char_escape(stripped)
    except json.JSONDecodeError:
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
        if fence_match:
            payload = parse_with_control_char_escape(fence_match.group(1).strip())
        else:
            array_match = re.search(r"(\[\s*\{.*\}\s*\])", stripped, re.DOTALL)
            if not array_match:
                raise
            payload = parse_with_control_char_escape(array_match.group(1))
    if not isinstance(payload, list):
        raise ValueError("model payload is not a JSON array")
    return payload


def plan_model_scoring_batches(
    commits: list[dict],
    frontier_mode: str,
    batch_size: int = DEFAULT_MODEL_SCORING_BATCH_SIZE,
) -> list[list[dict]]:
    if not commits:
        return []
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if frontier_mode in {"topk", "diverse"} and len(commits) <= batch_size:
        return [commits]
    return [commits[start : start + batch_size] for start in range(0, len(commits), batch_size)]


def model_score_commits(
    profile: IssueProfile,
    commits: list[dict],
    config: ModelConfig,
    observations: list[CommitObservation] | None = None,
    observation_prompt_mode: str = "legacy",
) -> dict[str, dict]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url)
    prompt = build_model_scoring_prompt(
        profile,
        commits,
        observations,
        observation_prompt_mode=observation_prompt_mode,
    )

    response = client.chat.completions.create(
        model=config.model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    payload = parse_model_json_payload(content)
    by_sha: dict[str, dict] = {}
    for item in payload:
        sha = str(item["sha"])
        semantic_score = max(0.1, min(8.0, float(item["semantic_score"])))
        build_success_prob = max(0.05, min(0.99, float(item["build_success_prob"])))
        evidence = [str(entry) for entry in item.get("evidence", [])]
        features = [str(entry) for entry in item.get("features", [])]
        if not evidence:
            evidence = ["model returned no explicit evidence"]
        by_sha[sha] = {
            "semantic_score": semantic_score,
            "build_success_prob": build_success_prob,
            "evidence": evidence,
            "features": features,
        }
    return by_sha


def score_model_batch_with_backfill(
    profile: IssueProfile,
    commits: list[dict],
    config: ModelConfig | None,
    score_fn,
    observations: list[CommitObservation] | None = None,
    observation_prompt_mode: str = "legacy",
) -> dict[str, dict]:
    if observations is None:
        scored = score_fn(profile, commits, config)
    else:
        scored = score_fn(profile, commits, config, observations, observation_prompt_mode)
    missing = [item for item in commits if item["sha"] not in scored]
    if not missing:
        return scored

    recovered = dict(scored)
    for item in missing:
        if observations is None:
            single = score_fn(profile, [item], config)
        else:
            single = score_fn(profile, [item], config, observations, observation_prompt_mode)
        result = single.get(item["sha"])
        if result is None:
            semantic_score, semantic_evidence = score_semantics(
                profile,
                item.get("subject", ""),
                item.get("body", ""),
                item.get("files", []),
                item.get("diff", ""),
                heuristic_version="tuned",
            )
            build_success_prob, build_evidence = score_build_probability(
                item.get("subject", ""),
                item.get("body", ""),
                item.get("files", []),
                item.get("diff", ""),
            )
            result = {
                "semantic_score": semantic_score,
                "build_success_prob": build_success_prob,
                "evidence": [
                    "model-fallback: single-item response missing; using local heuristic estimate"
                ]
                + semantic_evidence[:2]
                + build_evidence[:1],
                "features": extract_commit_features(
                    item.get("subject", ""),
                    item.get("body", ""),
                    item.get("files", []),
                    item.get("diff", ""),
                ),
            }
        recovered[item["sha"]] = result
    return recovered


def extract_commit_features(subject: str, body: str, files: list[str], diff: str) -> list[str]:
    features: set[str] = set()
    text = " ".join([subject, body, diff]).lower()
    tokens = set(tokenize(text))

    for path in files:
        features.add(f"path:{stem_path(path)}")

    for word in RISKY_WORDS:
        normalized = normalize_token(word)
        if normalized and normalized in tokens:
            features.add(f"term:{normalized}")

    for word in BUILD_RISK_WORDS:
        normalized = normalize_token(word)
        if normalized and normalized in tokens:
            features.add(f"build:{normalized}")

    if "revert" in text:
        features.add("meta:revert")
    if any("cmakelists.txt" in path.lower() for path in files):
        features.add("meta:cmakelists")
    if any(path_matches(path, ("llvm/lib/Transforms", "llvm/lib/Analysis", "llvm/lib/CodeGen", "llvm/lib/Target")) for path in files):
        features.add("meta:core-llvm")

    return sorted(features)


def jaccard_similarity(lhs: Iterable[str], rhs: Iterable[str]) -> float:
    left = set(lhs)
    right = set(rhs)
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def load_observations(path: Path) -> list[CommitObservation]:
    if not path.exists():
        return []
    content = path.read_text().strip()
    if not content:
        return []
    raw = json.loads(content)
    observations: list[CommitObservation] = []
    for item in raw:
        verdict = item["verdict"]
        if verdict not in {"good", "bad", "skip"}:
            raise ValueError(f"unsupported observation verdict: {verdict}")
        observations.append(
            CommitObservation(
                sha=item["sha"],
                verdict=verdict,
                summary=item.get("summary", ""),
                features=list(item.get("features", [])),
                source=item.get("source", "manual"),
                evidence=list(item.get("evidence", [])),
                log_excerpt=item.get("log_excerpt", ""),
                trace_excerpt=item.get("trace_excerpt", ""),
            )
        )
    return observations


def save_observations(path: Path, observations: list[CommitObservation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "sha": obs.sha,
            "verdict": obs.verdict,
            "summary": obs.summary,
            "features": obs.features,
            "source": obs.source,
            "evidence": obs.evidence or [],
            "log_excerpt": obs.log_excerpt,
            "trace_excerpt": obs.trace_excerpt,
        }
        for obs in observations
    ]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(path)


def observation_path_for_issue(issue_id: str) -> Path:
    return DEFAULT_OBSERVATIONS_DIR / f"{issue_id}.json"


def method_label(
    scorer: str,
    model_name: str | None,
    search_policy: str = "ranked",
    model_frontier: str = "topk",
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
) -> str:
    if scorer == "heuristic":
        base = "heuristic"
    else:
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name or "model")
        base = f"model-{safe_model}"
        if model_frontier != "topk":
            base = f"{base}-{model_frontier}"
    if heuristic_version != "tuned":
        base = f"{base}-h{heuristic_version}"
    if search_policy == "ranked":
        label = base
    else:
        label = f"{base}-{search_policy}"
    if candidate_pruning != "off":
        label = f"{label}-prune-{candidate_pruning}"
    return label


def run_history_path_for_issue(
    issue_id: str,
    scorer: str,
    model_name: str | None,
    search_policy: str = "ranked",
    model_frontier: str = "topk",
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    observation_prompt_mode: str = "legacy",
    run_label: str | None = None,
) -> Path:
    label = method_label(scorer, model_name, search_policy, model_frontier, candidate_pruning, heuristic_version)
    if scorer == "model" and observation_prompt_mode != "legacy":
        safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", observation_prompt_mode)
        label = f"{label}-obs-{safe_mode}"
    if run_label:
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_label)
        label = f"{label}-{safe_label}"
    return (
        DEFAULT_RUN_HISTORY_DIR
        / f"{issue_id}-{label}.json"
    )


def issue_results_dir(issue_id: str) -> Path:
    return DEFAULT_ISSUE_RESULTS_DIR / issue_id


def unresolved_window_path_for_issue(
    issue_id: str,
    scorer: str,
    model_name: str | None,
    search_policy: str = "ranked",
    model_frontier: str = "topk",
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    observation_prompt_mode: str = "legacy",
    run_label: str | None = None,
) -> Path:
    label = method_label(scorer, model_name, search_policy, model_frontier, candidate_pruning, heuristic_version)
    if scorer == "model" and observation_prompt_mode != "legacy":
        safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", observation_prompt_mode)
        label = f"{label}-obs-{safe_mode}"
    if run_label:
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_label)
        label = f"{label}-{safe_label}"
    return (
        issue_results_dir(issue_id)
        / f"{issue_id}-{label}-unresolved-window.json"
    )


def load_run_history(path: Path) -> dict:
    if not path.exists():
        return {}
    content = path.read_text().strip()
    if not content:
        return {}
    return json.loads(content)


def save_run_history(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(path)


def save_unresolved_window(path: Path, commits: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(commits, indent=2) + "\n")
    tmp_path.replace(path)


def start_run_history_payload(
    issue_id: str,
    scorer: str,
    model_name: str | None,
    model_frontier: str,
    search_policy: str,
    hybrid_switch_window: int,
    lambda_weight: float,
    max_steps: int,
    observation_path: str,
    run_history_path: str,
    good_commit: str,
    bad_commit: str,
    initial_unresolved: int,
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    calibrated_prior_power: float = DEFAULT_CALIBRATED_PRIOR_POWER,
    calibrated_prior_bonus: float = DEFAULT_CALIBRATED_PRIOR_BONUS,
    weak_relevance_penalty: float = DEFAULT_WEAK_RELEVANCE_PENALTY,
    weak_relevance_threshold: float = DEFAULT_WEAK_RELEVANCE_THRESHOLD,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
    observation_prompt_mode: str = "legacy",
    run_label: str | None = None,
) -> dict:
    return {
        "issue": issue_id,
        "scorer": scorer,
        "model_name": model_name,
        "model_frontier": model_frontier,
        "search_policy": search_policy,
        "hybrid_switch_window": hybrid_switch_window,
        "candidate_pruning": candidate_pruning,
        "heuristic_version": heuristic_version,
        "calibrated_prior_power": calibrated_prior_power,
        "calibrated_prior_bonus": calibrated_prior_bonus,
        "weak_relevance_penalty": weak_relevance_penalty,
        "weak_relevance_threshold": weak_relevance_threshold,
        "build_success_power": build_success_power,
        "observation_prompt_mode": observation_prompt_mode,
        "run_label": run_label,
        "lambda_weight": lambda_weight,
        "max_steps": max_steps,
        "observation_path": observation_path,
        "run_history_path": run_history_path,
        "good_commit": good_commit,
        "bad_commit": bad_commit,
        "initial_unresolved": initial_unresolved,
        "status": "in_progress",
        "steps": [],
    }


def resolved_model_name(scorer: str, model_name: str | None) -> str | None:
    if scorer != "model":
        return None
    return model_name or os.getenv("CHATANYWHERE_MODEL", "gpt-5.4-mini")


def run_history_matches(
    history: dict,
    issue_id: str,
    scorer: str,
    model_name: str | None,
    model_frontier: str,
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    calibrated_prior_power: float = DEFAULT_CALIBRATED_PRIOR_POWER,
    calibrated_prior_bonus: float = DEFAULT_CALIBRATED_PRIOR_BONUS,
    weak_relevance_penalty: float = DEFAULT_WEAK_RELEVANCE_PENALTY,
    weak_relevance_threshold: float = DEFAULT_WEAK_RELEVANCE_THRESHOLD,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
    observation_prompt_mode: str = "legacy",
    run_label: str | None = None,
) -> bool:
    return (
        bool(history)
        and history.get("issue") == issue_id
        and history.get("scorer") == scorer
        and history.get("model_name") == model_name
        and history.get("model_frontier", "topk") == model_frontier
        and history.get("candidate_pruning", "off") == candidate_pruning
        and history.get("heuristic_version", "tuned") == heuristic_version
        and float(history.get("calibrated_prior_power", DEFAULT_CALIBRATED_PRIOR_POWER)) == calibrated_prior_power
        and float(history.get("calibrated_prior_bonus", DEFAULT_CALIBRATED_PRIOR_BONUS)) == calibrated_prior_bonus
        and float(history.get("weak_relevance_penalty", DEFAULT_WEAK_RELEVANCE_PENALTY)) == weak_relevance_penalty
        and float(history.get("weak_relevance_threshold", DEFAULT_WEAK_RELEVANCE_THRESHOLD))
        == weak_relevance_threshold
        and float(history.get("build_success_power", DEFAULT_BUILD_SUCCESS_POWER)) == build_success_power
        and history.get("observation_prompt_mode", "legacy") == observation_prompt_mode
        and history.get("run_label") == run_label
    )


def prepare_run_history(
    *,
    existing_history: dict,
    issue_id: str,
    scorer: str,
    model_name: str | None,
    model_frontier: str,
    search_policy: str,
    hybrid_switch_window: int,
    lambda_weight: float,
    max_steps: int,
    observation_path: str,
    run_history_path: str,
    good_commit: str,
    bad_commit: str,
    initial_unresolved: int,
    candidate_file: str | None,
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    calibrated_prior_power: float = DEFAULT_CALIBRATED_PRIOR_POWER,
    calibrated_prior_bonus: float = DEFAULT_CALIBRATED_PRIOR_BONUS,
    weak_relevance_penalty: float = DEFAULT_WEAK_RELEVANCE_PENALTY,
    weak_relevance_threshold: float = DEFAULT_WEAK_RELEVANCE_THRESHOLD,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
    observation_prompt_mode: str = "legacy",
    run_label: str | None = None,
) -> tuple[dict, int, bool]:
    history_matches = (
        run_history_matches(
            existing_history,
            issue_id,
            scorer,
            model_name,
            model_frontier,
            candidate_pruning,
            heuristic_version,
            calibrated_prior_power,
            calibrated_prior_bonus,
            weak_relevance_penalty,
            weak_relevance_threshold,
            build_success_power,
            observation_prompt_mode,
            run_label,
        )
        and existing_history.get("search_policy", "ranked") == search_policy
        and int(existing_history.get("hybrid_switch_window", hybrid_switch_window)) == hybrid_switch_window
    )
    if history_matches:
        history = existing_history
        history["status"] = "in_progress"
        history["model_frontier"] = model_frontier
        history["search_policy"] = search_policy
        history["hybrid_switch_window"] = hybrid_switch_window
        history["candidate_pruning"] = candidate_pruning
        history["heuristic_version"] = heuristic_version
        history["calibrated_prior_power"] = calibrated_prior_power
        history["calibrated_prior_bonus"] = calibrated_prior_bonus
        history["weak_relevance_penalty"] = weak_relevance_penalty
        history["weak_relevance_threshold"] = weak_relevance_threshold
        history["build_success_power"] = build_success_power
        history["observation_prompt_mode"] = observation_prompt_mode
        history["run_label"] = run_label
        history["lambda_weight"] = lambda_weight
        history["max_steps"] = max_steps
        history["observation_path"] = observation_path
        history["run_history_path"] = run_history_path
        event = {
            "remaining_unresolved_at_resume": initial_unresolved,
            "existing_steps": len(history.get("steps", [])),
        }
        if candidate_file:
            event["candidate_file"] = candidate_file
        history.setdefault("resume_events", []).append(event)
        return history, len(history.get("steps", [])), True

    history = start_run_history_payload(
        issue_id=issue_id,
        scorer=scorer,
        model_name=model_name,
        model_frontier=model_frontier,
        lambda_weight=lambda_weight,
        max_steps=max_steps,
        observation_path=observation_path,
        run_history_path=run_history_path,
        good_commit=good_commit,
        bad_commit=bad_commit,
        initial_unresolved=initial_unresolved,
        search_policy=search_policy,
        hybrid_switch_window=hybrid_switch_window,
        candidate_pruning=candidate_pruning,
        heuristic_version=heuristic_version,
        calibrated_prior_power=calibrated_prior_power,
        calibrated_prior_bonus=calibrated_prior_bonus,
        weak_relevance_penalty=weak_relevance_penalty,
        weak_relevance_threshold=weak_relevance_threshold,
        build_success_power=build_success_power,
        observation_prompt_mode=observation_prompt_mode,
        run_label=run_label,
    )
    if candidate_file:
        history["candidate_file"] = candidate_file
    return history, 0, False


def append_run_history_step(history: dict, step_payload: dict) -> None:
    history.setdefault("steps", []).append(step_payload)


def compact_candidate_view(record: CommitRecord, rank: int) -> dict:
    payload = {
        "rank": rank,
        "sha": record.sha,
        "subject": record.subject,
        "utility": round(record.utility, 6),
        "selection_score": round(record.selection_score, 6),
        "semantic_score": round(record.semantic_score, 6),
        "build_success_prob": round(record.build_success_prob, 6),
        "feedback_bias": round(record.feedback_bias, 6),
    }
    if record.posterior_bad_mass:
        payload["posterior_bad_mass"] = round(record.posterior_bad_mass, 6)
    if record.posterior_info_gain:
        payload["posterior_info_gain"] = round(record.posterior_info_gain, 6)
    if record.calibrated_suspicion_weight:
        payload["calibrated_suspicion_weight"] = round(record.calibrated_suspicion_weight, 6)
    if record.calibrated_posterior_bad_mass:
        payload["calibrated_posterior_bad_mass"] = round(record.calibrated_posterior_bad_mass, 6)
    if record.calibrated_posterior_info_gain:
        payload["calibrated_posterior_info_gain"] = round(record.calibrated_posterior_info_gain, 6)
    if record.weak_relevance_penalty:
        payload["weak_relevance_penalty"] = round(record.weak_relevance_penalty, 6)
    return payload


def selection_payload(record: CommitRecord) -> dict:
    payload = {
        "utility": round(record.utility, 6),
        "selection_score": round(record.selection_score, 6),
        "semantic_score": round(record.semantic_score, 6),
        "build_success_prob": round(record.build_success_prob, 6),
        "balance_score": round(record.balance_score, 6),
        "suspicion_weight": round(record.suspicion_weight, 6),
        "feedback_bias": round(record.feedback_bias, 6),
        "evidence": list(record.evidence or []),
    }
    if record.posterior_bad_mass:
        payload["posterior_bad_mass"] = round(record.posterior_bad_mass, 6)
    if record.posterior_info_gain:
        payload["posterior_info_gain"] = round(record.posterior_info_gain, 6)
    if record.calibrated_suspicion_weight:
        payload["calibrated_suspicion_weight"] = round(record.calibrated_suspicion_weight, 6)
    if record.calibrated_posterior_bad_mass:
        payload["calibrated_posterior_bad_mass"] = round(record.calibrated_posterior_bad_mass, 6)
    if record.calibrated_posterior_info_gain:
        payload["calibrated_posterior_info_gain"] = round(record.calibrated_posterior_info_gain, 6)
    if record.weak_relevance_penalty:
        payload["weak_relevance_penalty"] = round(record.weak_relevance_penalty, 6)
    return payload


def find_observation_by_sha(observations: list[CommitObservation], sha: str) -> CommitObservation | None:
    for obs in observations:
        if obs.sha == sha:
            return obs
    return None


def update_observation(
    observations: list[CommitObservation],
    observation: CommitObservation,
) -> list[CommitObservation]:
    updated = [obs for obs in observations if obs.sha != observation.sha]
    updated.append(observation)
    return updated


def verdict_from_runner_exit_code(return_code: int) -> str:
    if return_code == 0:
        return "good"
    if return_code == 1:
        return "bad"
    if return_code == 125:
        return "skip"
    raise RuntimeError(f"unexpected runner exit code: {return_code}")


def runner_path_for_issue(profile: IssueProfile) -> Path:
    runner = (ROOT_DIR / profile.runner).resolve()
    if not runner.exists():
        raise FileNotFoundError(f"runner not found for issue {profile.issue_id}: {runner}")
    return runner


def runner_evidence_lines(output: str, max_lines: int = 12) -> list[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    kept: list[str] = []
    interesting_markers = (
        "loop-vectorize",
        "LoopVectorizationCostModel::expectedCost",
        "PLEASE submit a bug report",
        "clang version",
        "source=",
        "error:",
        "Assertion",
        "Stack dump:",
        "Running pass",
    )
    for line in lines:
        if any(marker in line for marker in interesting_markers):
            kept.append(line)
    if not kept:
        kept = lines[-max_lines:]
    return kept[:max_lines]


def extract_trace_excerpt(output: str, max_lines: int = 10, max_chars: int = 10000) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""

    noise_patterns = (
        "PLEASE submit a bug report",
        "Print function",
        "Program arguments:",
        "Command terminated by signal",
        "elapsed_sec=",
        "max_rss_kb=",
        "exit_code=",
    )
    primary_patterns = ("Assertion", "assertion failed", "fatal error:", "clang frontend command failed")
    context_patterns = ("Stack dump:", "Running pass", "error:")
    kept: list[str] = []
    seen: set[str] = set()

    def add_line(line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped in seen:
            return
        if any(pattern in stripped for pattern in noise_patterns):
            return
        kept.append(stripped)
        seen.add(stripped)

    for line in lines:
        if any(pattern in line for pattern in primary_patterns):
            add_line(line)
        if len(kept) >= max_lines:
            break

    stack_anchor = next((idx for idx, line in enumerate(lines) if "Stack dump:" in line), None)

    if stack_anchor is not None:
        for line in lines[stack_anchor : stack_anchor + 14]:
            add_line(line)
            if len(kept) >= max_lines:
                break

    for line in lines:
        if any(pattern in line for pattern in context_patterns):
            add_line(line)
        if len(kept) >= max_lines:
            break

    if not kept:
        kept = [
            line.strip()
            for line in lines[-max_lines:]
            if not any(pattern in line for pattern in noise_patterns)
        ]
    excerpt = "\n".join(kept[:max_lines])
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars]
    return excerpt


def run_issue_runner(profile: IssueProfile, repo: Path) -> tuple[str, str, str, list[str]]:
    runner = runner_path_for_issue(profile)
    completed = subprocess.run(
        [str(runner), str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    verdict = verdict_from_runner_exit_code(completed.returncode)
    output = completed.stdout.strip()
    summary = output.splitlines()[-1].strip() if output else f"runner verdict {verdict}"
    evidence = runner_evidence_lines(output)
    return verdict, summary, output, evidence


def checkout_commit(repo: Path, sha: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", sha],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def build_commit_record(
    repo: Path,
    profile: IssueProfile,
    sha: str,
    index: int,
    scorer: str = "heuristic",
    model_config: ModelConfig | None = None,
    preloaded: dict | None = None,
    heuristic_version: str = "tuned",
    load_diff: bool = True,
) -> CommitRecord:
    if preloaded is None:
        subject = commit_subject(repo, sha)
        body = commit_body(repo, sha)
        files = commit_changed_files(repo, sha)
        diff = commit_diff_text(repo, sha) if load_diff else ""
    else:
        subject = preloaded["subject"]
        body = preloaded["body"]
        files = preloaded["files"]
        diff = preloaded.get("diff", "")
        if load_diff and not diff:
            diff = commit_diff_text(repo, sha)
    if scorer == "model":
        if model_config is None:
            raise RuntimeError("model_config is required for scorer=model")
        if preloaded is None or "model_result" not in preloaded:
            raise RuntimeError("preloaded model_result is required for scorer=model")
        model_result = preloaded["model_result"]
        semantic_score = float(model_result["semantic_score"])
        build_prob = float(model_result["build_success_prob"])
        model_evidence = [str(item) for item in model_result.get("evidence", [])]
        features = [str(item) for item in model_result.get("features", [])]
        if not features:
            features = extract_commit_features(subject, body, files, diff)
        evidence = ["model-scored"] + model_evidence
    else:
        semantic_score, semantic_evidence = score_semantics(
            profile,
            subject,
            body,
            files,
            diff,
            heuristic_version=heuristic_version,
        )
        build_prob, build_evidence = score_build_probability(subject, body, files, diff)
        features = extract_commit_features(subject, body, files, diff)
        evidence = semantic_evidence + build_evidence
    return CommitRecord(
        index=index,
        sha=sha,
        subject=subject,
        body=body,
        changed_files=files,
        diff_text=diff,
        semantic_score=semantic_score,
        build_success_prob=build_prob,
        suspicion_weight=0.0,
        evidence=evidence + [f"features: {', '.join(features[:6])}" if features else "features: none"],
        features=features,
    )


def apply_feedback_bias(
    profile: IssueProfile,
    records: list[CommitRecord],
    observations: list[CommitObservation],
) -> None:
    if not observations:
        return

    bad_observations = [obs for obs in observations if obs.verdict == "bad"]
    good_observations = [obs for obs in observations if obs.verdict == "good"]
    skip_observations = [obs for obs in observations if obs.verdict == "skip"]

    for record in records:
        base_semantic_score = record.semantic_score
        touches_relevant_path = any(path_matches(path, profile.relevant_paths) for path in record.changed_files)
        features = record.features or extract_commit_features(
            record.subject, record.body, record.changed_files, record.diff_text
        )
        bad_sim = max((jaccard_similarity(features, obs.features) for obs in bad_observations), default=0.0)
        good_sim = max((jaccard_similarity(features, obs.features) for obs in good_observations), default=0.0)
        skip_sim = max((jaccard_similarity(features, obs.features) for obs in skip_observations), default=0.0)

        strong_positive_bias = touches_relevant_path or base_semantic_score >= 1.35
        bad_weight = 1.8 if strong_positive_bias else 0.35
        bias = 1.0 + bad_weight * bad_sim - 1.2 * good_sim - 0.8 * skip_sim
        if not strong_positive_bias and bias > 1.0:
            bias = min(bias, 1.10)
        record.feedback_bias = max(0.20, bias)
        record.semantic_score *= record.feedback_bias

        feedback_bits = []
        if bad_sim:
            feedback_bits.append(f"bad-sim={bad_sim:.2f}")
        if good_sim:
            feedback_bits.append(f"good-sim={good_sim:.2f}")
        if skip_sim:
            feedback_bits.append(f"skip-sim={skip_sim:.2f}")
        if bad_sim and not strong_positive_bias:
            feedback_bits.append("feedback-gated=base-relevance")
        if feedback_bits:
            assert record.evidence is not None
            record.evidence.append("feedback: " + ", ".join(feedback_bits))


def build_success_weight(probability: float, power: float) -> float:
    if power <= 0.0:
        raise ValueError("build_success_power must be positive")
    return probability ** power


def compute_selection(
    records: list[CommitRecord],
    lambda_weight: float,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
) -> SuggestionResult:
    total = sum(record.semantic_score for record in records)
    if total <= 0:
        raise ValueError("semantic score sum must be positive")

    cumulative = 0.0
    for record in records:
        record.suspicion_weight = record.semantic_score / total
        cumulative += record.suspicion_weight
        record.cumulative_weight = cumulative
        record.balance_score = 1.0 - 2.0 * abs(record.cumulative_weight - 0.5)
        record.utility = (
            record.balance_score
            * build_success_weight(record.build_success_prob, build_success_power)
            * (1.0 + lambda_weight * record.suspicion_weight)
        )
        record.selection_score = record.utility
        record.posterior_bad_mass = record.cumulative_weight
        record.posterior_info_gain = 0.0

    selected = max(records, key=lambda record: (record.utility, record.suspicion_weight, -record.index))
    return SuggestionResult(
        selected=selected,
        candidates=records,
        lambda_weight=lambda_weight,
        build_success_power=build_success_power,
    )


def shannon_entropy(probabilities: Iterable[float]) -> float:
    total = 0.0
    for probability in probabilities:
        if probability <= 0.0:
            continue
        total -= probability * math.log2(probability)
    return total


def binary_split_info_gain(p_bad: float) -> float:
    """Information gained by learning whether the first bad commit is in a split."""
    p_bad = max(0.0, min(1.0, p_bad))
    return shannon_entropy((p_bad, 1.0 - p_bad))


def posterior_selection(
    records: list[CommitRecord],
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
) -> tuple[CommitRecord, list[CommitRecord]]:
    if not records:
        raise ValueError("records must not be empty")

    ordered = sorted(records, key=lambda record: record.index)
    probabilities = [record.suspicion_weight for record in ordered]
    cumulative = 0.0

    for idx, record in enumerate(ordered):
        cumulative += record.suspicion_weight
        record.posterior_bad_mass = cumulative
        record.posterior_info_gain = binary_split_info_gain(cumulative)
        record.selection_score = (
            record.posterior_info_gain
            * build_success_weight(record.build_success_prob, build_success_power)
        )

    ranked = sorted(
        ordered,
        key=lambda record: (
            record.selection_score,
            -abs(record.posterior_bad_mass - 0.5),
            record.build_success_prob,
            record.suspicion_weight,
            -record.index,
        ),
        reverse=True,
    )
    return ranked[0], ranked


def calibrated_prior_probabilities(
    records: list[CommitRecord],
    prior_power: float,
) -> list[float]:
    if not records:
        raise ValueError("records must not be empty")
    if prior_power <= 0.0:
        raise ValueError("prior_power must be positive")
    model_scored = all(any(item == "model-scored" for item in (record.evidence or [])) for record in records)
    if model_scored:
        max_score = max(record.semantic_score for record in records)
        weights = [
            math.exp(DEFAULT_MODEL_PRIOR_SOFTMAX_TEMPERATURE * (record.semantic_score - max_score))
            for record in records
        ]
    else:
        weights = [max(record.semantic_score, 1e-6) ** prior_power for record in records]
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("calibrated prior total must be positive")
    return [weight / total for weight in weights]


def weak_relevance_penalty_for_record(
    profile: IssueProfile,
    record: CommitRecord,
    weak_relevance_penalty: float,
    weak_relevance_threshold: float,
) -> float:
    touches_relevant_path = any(path_matches(path, profile.relevant_paths) for path in record.changed_files)
    if touches_relevant_path:
        return 0.0
    if record.semantic_score > weak_relevance_threshold:
        return 0.0
    return weak_relevance_penalty * record.build_success_prob


def keyword_feature_overlap_score(
    profile: IssueProfile,
    features: list[str] | None,
    feature_counts: dict[str, int],
) -> float:
    if not features:
        return 0.0
    total = 0.0
    for feature in features:
        normalized_feature = normalize_token(feature)
        if not normalized_feature:
            continue
        matched = False
        for keyword in profile.keywords:
            normalized_keyword = normalize_token(keyword)
            if not normalized_keyword:
                continue
            if (
                normalized_feature == normalized_keyword
                or normalized_feature in normalized_keyword
                or normalized_keyword in normalized_feature
            ):
                rarity = 1.0 / max(1, feature_counts.get(feature, 1))
                total += rarity
                matched = True
                break
        if matched:
            continue
    return total


def calibrated_posterior_selection(
    profile: IssueProfile,
    records: list[CommitRecord],
    prior_power: float,
    prior_bonus: float,
    weak_relevance_penalty: float,
    weak_relevance_threshold: float,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
) -> tuple[CommitRecord, list[CommitRecord]]:
    if not records:
        raise ValueError("records must not be empty")

    ordered = sorted(records, key=lambda record: record.index)
    probabilities = calibrated_prior_probabilities(ordered, prior_power)
    model_scored_window = all(any(item == "model-scored" for item in (record.evidence or [])) for record in ordered)
    feature_counts: dict[str, int] = {}
    keyword_overlap_by_sha: dict[str, float] = {}
    if model_scored_window:
        for record in ordered:
            for feature in record.features or []:
                feature_counts[feature] = feature_counts.get(feature, 0) + 1
        for record in ordered:
            keyword_overlap_by_sha[record.sha] = keyword_feature_overlap_score(
                profile,
                record.features,
                feature_counts,
            )
    semantic_ranked = sorted(
        ordered,
        key=lambda record: (record.semantic_score, record.build_success_prob, -record.index),
        reverse=True,
    )
    top_semantic_sha = semantic_ranked[0].sha if semantic_ranked else None
    semantic_rank_fraction_by_sha: dict[str, float] = {}
    if semantic_ranked:
        denom = max(1, len(semantic_ranked) - 1)
        for rank, record in enumerate(semantic_ranked):
            semantic_rank_fraction_by_sha[record.sha] = 1.0 - (rank / denom)
    distinctive_mechanism_gap_by_sha: dict[str, float] = {}
    if model_scored_window and len(ordered) >= 3:
        internal_candidates = [
            record
            for idx, record in enumerate(ordered)
            if 0 < idx < len(ordered) - 1 and record.semantic_score >= weak_relevance_threshold
        ]
        if internal_candidates:
            internal_ranked = sorted(
                internal_candidates,
                key=lambda record: (
                    keyword_overlap_by_sha.get(record.sha, 0.0),
                    record.semantic_score,
                    record.build_success_prob,
                    -record.index,
                ),
                reverse=True,
            )
            best_internal = internal_ranked[0]
            best_overlap = keyword_overlap_by_sha.get(best_internal.sha, 0.0)
            second_overlap = (
                keyword_overlap_by_sha.get(internal_ranked[1].sha, 0.0)
                if len(internal_ranked) > 1
                else 0.0
            )
            overlap_gap = best_overlap - second_overlap
            if best_overlap >= 2.0 and overlap_gap >= 1.0:
                distinctive_mechanism_gap_by_sha[best_internal.sha] = overlap_gap
    cumulative = 0.0

    for idx, record in enumerate(ordered):
        probability = probabilities[idx]
        cumulative += probability
        record.calibrated_suspicion_weight = probability
        record.calibrated_posterior_bad_mass = cumulative
        record.calibrated_posterior_info_gain = binary_split_info_gain(cumulative)
        record.weak_relevance_penalty = weak_relevance_penalty_for_record(
            profile,
            record,
            weak_relevance_penalty=weak_relevance_penalty,
            weak_relevance_threshold=weak_relevance_threshold,
        )
        model_rank_bonus = 0.0
        direct_hit_bonus = 0.0
        mechanism_bonus = 0.0
        distinctive_mechanism_bonus = 0.0
        build_weight = build_success_weight(record.build_success_prob, build_success_power)
        if any(item == "model-scored" for item in (record.evidence or [])):
            model_rank_bonus = DEFAULT_MODEL_RANK_BONUS * semantic_rank_fraction_by_sha.get(record.sha, 0.0)
            mechanism_bonus = (
                DEFAULT_MODEL_MECHANISM_BONUS
                * keyword_overlap_by_sha.get(record.sha, 0.0)
                * build_weight
            )
            distinctive_mechanism_bonus = (
                DEFAULT_MODEL_MECHANISM_OVERRIDE_SCALE
                * distinctive_mechanism_gap_by_sha.get(record.sha, 0.0)
                * build_weight
            )
        if (
            model_scored_window
            and len(ordered) >= 3
            and 0 < idx < len(ordered) - 1
            and record.sha == top_semantic_sha
            and record.semantic_score >= weak_relevance_threshold
        ):
            # On small model-scored windows, allow a likely internal culprit to beat
            # a slightly better midpoint split instead of drifting back to entropy-only probes.
            direct_hit_bonus = DEFAULT_MODEL_DIRECT_HIT_BONUS * record.calibrated_suspicion_weight
        record.selection_score = (
            record.calibrated_posterior_info_gain
            * build_weight
            * (1.0 + prior_bonus * record.calibrated_suspicion_weight + model_rank_bonus)
            + (direct_hit_bonus * build_weight)
            + mechanism_bonus
            + distinctive_mechanism_bonus
        ) - record.weak_relevance_penalty

    ranked = sorted(
        ordered,
        key=lambda record: (
            record.selection_score,
            record.calibrated_suspicion_weight,
            -abs(record.calibrated_posterior_bad_mass - 0.5),
            record.build_success_prob,
            -record.index,
        ),
        reverse=True,
    )
    return ranked[0], ranked


def boundary_selection(records: list[CommitRecord]) -> CommitRecord:
    if not records:
        raise ValueError("records must not be empty")
    ranked = sorted(records, key=lambda record: record.index)
    midpoint = (len(ranked) - 1) / 2.0
    return min(
        ranked,
        key=lambda record: (
            abs(record.index - 1 - midpoint),
            -record.build_success_prob,
            -record.utility,
            record.index,
        ),
    )


def select_next_commit(
    profile: IssueProfile,
    records: list[CommitRecord],
    lambda_weight: float,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
    search_policy: str = "ranked",
    hybrid_switch_window: int = 32,
    calibrated_prior_power: float = DEFAULT_CALIBRATED_PRIOR_POWER,
    calibrated_prior_bonus: float = DEFAULT_CALIBRATED_PRIOR_BONUS,
    weak_relevance_penalty: float = DEFAULT_WEAK_RELEVANCE_PENALTY,
    weak_relevance_threshold: float = DEFAULT_WEAK_RELEVANCE_THRESHOLD,
) -> SelectionDecision:
    selection = compute_selection(
        records,
        lambda_weight=lambda_weight,
        build_success_power=build_success_power,
    )
    ranked_candidates = sorted(records, key=lambda record: record.utility, reverse=True)
    if search_policy == "ranked":
        return SelectionDecision(
            selected=selection.selected,
            ranked_candidates=ranked_candidates,
            search_policy=search_policy,
            selection_mode="ranked",
        )
    if search_policy == "hybrid":
        if len(records) <= max(2, hybrid_switch_window):
            return SelectionDecision(
                selected=boundary_selection(records),
                ranked_candidates=ranked_candidates,
                search_policy=search_policy,
                selection_mode="boundary",
                hybrid_switch_window=hybrid_switch_window,
            )
        return SelectionDecision(
            selected=selection.selected,
            ranked_candidates=ranked_candidates,
            search_policy=search_policy,
            selection_mode="ranked",
            hybrid_switch_window=hybrid_switch_window,
        )
    if search_policy == "posterior":
        selected, posterior_ranked = posterior_selection(
            records,
            build_success_power=build_success_power,
        )
        return SelectionDecision(
            selected=selected,
            ranked_candidates=posterior_ranked,
            search_policy=search_policy,
            selection_mode="posterior",
        )
    if search_policy == "calibrated-posterior":
        selected, calibrated_ranked = calibrated_posterior_selection(
            profile,
            records,
            prior_power=calibrated_prior_power,
            prior_bonus=calibrated_prior_bonus,
            weak_relevance_penalty=weak_relevance_penalty,
            weak_relevance_threshold=weak_relevance_threshold,
            build_success_power=build_success_power,
        )
        return SelectionDecision(
            selected=selected,
            ranked_candidates=calibrated_ranked,
            search_policy=search_policy,
            selection_mode="calibrated-posterior",
        )
    raise ValueError(f"unsupported search policy: {search_policy}")


def posterior_anchor_by_mass(records: list[CommitRecord], target_mass: float) -> CommitRecord:
    ordered = sorted(records, key=lambda record: record.index)
    total = sum(max(record.semantic_score, 0.0) for record in ordered)
    if total <= 0.0:
        return boundary_selection(records)
    cumulative = 0.0
    best = ordered[0]
    best_distance = float("inf")
    for record in ordered:
        cumulative += max(record.semantic_score, 0.0) / total
        distance = abs(cumulative - target_mass)
        if distance < best_distance:
            best_distance = distance
            best = record
    return best


def index_anchor(records: list[CommitRecord], fraction: float) -> CommitRecord:
    ordered = sorted(records, key=lambda record: record.index)
    if len(ordered) == 1:
        return ordered[0]
    target = fraction * (len(ordered) - 1)
    return min(ordered, key=lambda record: (abs((record.index - 1) - target), record.index))


def select_model_frontier_shas(
    records: list[CommitRecord],
    target_count: int,
    frontier_mode: str,
) -> list[str]:
    if target_count <= 0:
        return []

    semantic_ranked = sorted(
        records,
        key=lambda record: (record.semantic_score, record.build_success_prob, -record.index),
        reverse=True,
    )
    if frontier_mode == "all":
        return [record.sha for record in records]
    if frontier_mode == "topk":
        return [record.sha for record in semantic_ranked[:target_count]]
    if frontier_mode != "diverse":
        raise ValueError(f"unsupported model frontier mode: {frontier_mode}")

    anchors: list[CommitRecord] = []
    if semantic_ranked:
        anchors.append(semantic_ranked[0])
    if target_count >= 2:
        anchors.append(posterior_anchor_by_mass(records, 0.5))
    if target_count >= 3:
        anchors.append(index_anchor(records, 0.5))
    if target_count >= 4:
        anchors.append(posterior_anchor_by_mass(records, 0.25))
    if target_count >= 5:
        anchors.append(posterior_anchor_by_mass(records, 0.75))
    if target_count >= 6:
        anchors.append(index_anchor(records, 0.25))
    if target_count >= 7:
        anchors.append(index_anchor(records, 0.75))

    selected: list[str] = []
    seen: set[str] = set()
    for record in anchors:
        if record.sha in seen:
            continue
        selected.append(record.sha)
        seen.add(record.sha)
        if len(selected) >= target_count:
            return selected

    for record in semantic_ranked:
        if record.sha in seen:
            continue
        selected.append(record.sha)
        seen.add(record.sha)
        if len(selected) >= target_count:
            break
    return selected


def load_issue_profile(profiles: dict[str, IssueProfile], issue_id: str) -> IssueProfile:
    if issue_id not in profiles:
        known = ", ".join(sorted(profiles))
        raise KeyError(f"unknown issue_id={issue_id}; known issues: {known}")
    return profiles[issue_id]


def make_records(
    repo: Path,
    profile: IssueProfile,
    max_candidates: int | None = None,
    scorer: str = "heuristic",
    model_config: ModelConfig | None = None,
    candidate_shas: list[str] | None = None,
    model_top_k: int | None = None,
    model_frontier: str = "topk",
    candidate_pruning: str = "off",
    heuristic_version: str = "tuned",
    observations: list[CommitObservation] | None = None,
    metadata_cache: dict[str, CommitMetadata] | None = None,
    model_cache: dict[str, dict] | None = None,
) -> tuple[list[CommitRecord], dict]:
    shas = candidate_shas if candidate_shas is not None else list_candidate_commits(repo, profile.good_commit, profile.bad_commit)
    if max_candidates is not None:
        shas = shas[:max_candidates]

    metadata_by_sha = metadata_cache if metadata_cache is not None else {}
    missing_shas = [sha for sha in shas if sha not in metadata_by_sha]
    if missing_shas:
        loaded_metadata = load_commit_metadata(
            repo,
            missing_shas,
            include_body=(scorer == "heuristic" and heuristic_version == "tuned"),
        )
        metadata_by_sha.update(loaded_metadata)
    shas, pruning_summary = apply_candidate_pruning(profile, shas, metadata_by_sha, candidate_pruning)
    preloaded_items: list[dict] = []
    for sha in shas:
        metadata = metadata_by_sha.get(sha)
        if metadata is None:
            raise RuntimeError(f"missing metadata for commit {sha}")
        preloaded_items.append(
            {
                "sha": sha,
                "subject": metadata.subject,
                "body": metadata.body,
                "files": metadata.changed_files,
                "diff": "",
            }
        )

    records = [
        build_commit_record(
            repo,
            profile,
            item["sha"],
            index + 1,
            preloaded=item,
            heuristic_version=heuristic_version,
            load_diff=scorer != "model",
        )
        for index, item in enumerate(preloaded_items)
    ]

    if scorer != "model":
        return records, pruning_summary

    if model_config is None:
        raise RuntimeError("model_config is required for scorer=model")

    target_count = len(records) if model_top_k is None else min(model_top_k, len(records))
    selected_shas = set(select_model_frontier_shas(records, target_count, model_frontier))

    cache_path = model_cache_path(
        profile.issue_id,
        model_config.model_name,
        scoring_version=resolved_model_scoring_version(model_config.observation_prompt_mode),
    )
    cache = model_cache if model_cache is not None else load_model_cache(cache_path)
    uncached_shas = [
        item["sha"]
        for item in preloaded_items
        if item["sha"] in selected_shas and cache.get(item["sha"]) is None
    ]
    deep_metadata_by_sha = load_commit_metadata(repo, uncached_shas, include_body=True) if uncached_shas else {}
    uncached: list[dict] = []
    for item in preloaded_items:
        if item["sha"] not in selected_shas:
            continue
        cached = cache.get(item["sha"])
        if cached is None:
            deep_metadata = deep_metadata_by_sha.get(item["sha"])
            if deep_metadata is None:
                raise RuntimeError(f"missing deep metadata for commit {item['sha']}")
            item["subject"] = deep_metadata.subject
            item["body"] = deep_metadata.body
            item["files"] = deep_metadata.changed_files
            item["diff"] = commit_diff_text(repo, item["sha"])
            uncached.append(item)
        else:
            item["model_result"] = cached

    if uncached:
        for batch in plan_model_scoring_batches(uncached, frontier_mode=model_frontier):
            scored = score_model_batch_with_backfill(
                profile,
                batch,
                model_config,
                model_score_commits,
                observations,
                getattr(model_config, "observation_prompt_mode", "legacy"),
            )
            for batch_item in batch:
                result = scored.get(batch_item["sha"])
                if result is None:
                    raise RuntimeError(f"model response missing commit {batch_item['sha']}")
                batch_item["model_result"] = result
                cache[batch_item["sha"]] = result
        save_model_cache(cache_path, cache)

    preloaded_by_sha = {item["sha"]: item for item in preloaded_items}
    for record in records:
        item = preloaded_by_sha[record.sha]
        if "model_result" not in item:
            continue
        result = item["model_result"]
        record.semantic_score = float(result["semantic_score"])
        record.build_success_prob = float(result["build_success_prob"])
        record.features = list(result.get("features", [])) or record.features
        record.evidence = ["model-scored"] + [str(entry) for entry in result.get("evidence", [])]
        if record.features:
            record.evidence.append(f"features: {', '.join(record.features[:6])}")

    return records, pruning_summary


def format_candidate(record: CommitRecord) -> str:
    evidence = "; ".join(record.evidence or [])
    return (
        f"{record.index:>4}  {record.sha[:12]}  "
        f"U={record.utility:.4f}  "
        f"w={record.suspicion_weight:.4f}  "
        f"pbuild={record.build_success_prob:.4f}  "
        f"b={record.balance_score:.4f}  "
        f"fb={record.feedback_bias:.4f}  "
        f"{record.subject}\n"
        f"      evidence: {evidence}"
    )


def command_suggest(args: argparse.Namespace) -> int:
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    model_config = None
    if args.scorer == "model":
        model_config = load_model_config(
            model_name=args.model_name,
            observation_prompt_mode=args.observation_prompt_mode,
        )
    candidate_shas = None
    if args.candidate_file:
        candidate_shas = load_candidate_commits_from_file(Path(args.candidate_file))
    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    metadata_cache: dict[str, CommitMetadata] = {}
    records, pruning_summary = make_records(
        repo,
        profile,
        max_candidates=args.max_candidates,
        scorer=args.scorer,
        model_config=model_config,
        candidate_shas=candidate_shas,
        model_top_k=args.model_top_k,
        model_frontier=args.model_frontier,
        candidate_pruning=args.candidate_pruning,
        heuristic_version=args.heuristic_version,
        observations=observations,
        metadata_cache=metadata_cache,
    )
    apply_feedback_bias(profile, records, observations)
    decision = select_next_commit(
        profile,
        records,
        lambda_weight=args.lambda_weight,
        build_success_power=args.build_success_power,
        search_policy=args.search_policy,
        hybrid_switch_window=args.hybrid_switch_window,
        calibrated_prior_power=args.calibrated_prior_power,
        calibrated_prior_bonus=args.calibrated_prior_bonus,
        weak_relevance_penalty=args.weak_relevance_penalty,
        weak_relevance_threshold=args.weak_relevance_threshold,
    )
    print(f"Issue: {profile.issue_id}")
    print(f"Title: {profile.title}")
    print(f"Scorer: {args.scorer}")
    print(f"Heuristic version: {args.heuristic_version}")
    print(f"Search policy: {args.search_policy}")
    print(f"Selection mode: {decision.selection_mode}")
    if args.search_policy == "calibrated-posterior":
        print(f"Calibrated prior power: {args.calibrated_prior_power}")
        print(f"Calibrated prior bonus: {args.calibrated_prior_bonus}")
        print(f"Weak relevance penalty: {args.weak_relevance_penalty}")
        print(f"Weak relevance threshold: {args.weak_relevance_threshold}")
    print(f"Build success power: {args.build_success_power}")
    if model_config is not None:
        print(f"Model: {model_config.model_name}")
        print(f"Model frontier: {args.model_frontier}")
    print(f"Candidate pruning: {args.candidate_pruning}")
    print(f"Range: {profile.good_commit[:12]}..{profile.bad_commit[:12]}")
    if args.candidate_file:
        print(f"Candidate file: {args.candidate_file}")
    print(f"Candidates: {len(records)}")
    if pruning_summary["pruned_count"]:
        print(
            "Candidates after pruning: "
            f"{pruning_summary['after_count']} / {pruning_summary['before_count']}"
        )
    print(f"Observations: {len(observations)}")
    print(f"Selected next commit: {decision.selected.sha}")
    print(f"Selected subject: {decision.selected.subject}")
    print("")
    print("Top candidates:")

    top_n = min(args.top, len(records))
    ranked = decision.ranked_candidates
    for record in ranked[:top_n]:
        print(format_candidate(record))

    if args.json:
        payload = {
            "issue_id": profile.issue_id,
            "scorer": args.scorer,
            "heuristic_version": args.heuristic_version,
            "search_policy": args.search_policy,
            "selection_mode": decision.selection_mode,
            "selected": {
                "sha": decision.selected.sha,
                "subject": decision.selected.subject,
                "utility": decision.selected.utility,
                "suspicion_weight": decision.selected.suspicion_weight,
                "build_success_prob": decision.selected.build_success_prob,
                "balance_score": decision.selected.balance_score,
                "feedback_bias": decision.selected.feedback_bias,
                "features": decision.selected.features,
                "evidence": decision.selected.evidence,
            },
            "candidate_pruning": pruning_summary,
            "candidates": [
                {
                    "index": record.index,
                    "sha": record.sha,
                    "subject": record.subject,
                    "utility": record.utility,
                    "suspicion_weight": record.suspicion_weight,
                    "build_success_prob": record.build_success_prob,
                    "balance_score": record.balance_score,
                    "feedback_bias": record.feedback_bias,
                    "features": record.features,
                    "evidence": record.evidence,
                }
                for record in ranked
            ],
        }
        if args.scorer == "model":
            payload["model_name"] = model_config.model_name if model_config is not None else args.model_name
            payload["model_frontier"] = args.model_frontier
        if args.search_policy == "calibrated-posterior":
            payload["calibrated_prior_power"] = args.calibrated_prior_power
            payload["calibrated_prior_bonus"] = args.calibrated_prior_bonus
            payload["weak_relevance_penalty"] = args.weak_relevance_penalty
            payload["weak_relevance_threshold"] = args.weak_relevance_threshold
        payload["build_success_power"] = args.build_success_power
        print("")
        print(json.dumps(payload, indent=2))
    return 0


def command_explain(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    print(f"Issue: {profile.issue_id}")
    print(f"Title: {profile.title}")
    print(f"URL: {profile.issue_url}")
    print(f"Good ref: {profile.good_ref}")
    print(f"Good commit: {profile.good_commit}")
    print(f"Bad commit: {profile.bad_commit}")
    print(f"Runner: {profile.runner}")
    print("Relevant paths:")
    for path in profile.relevant_paths:
        print(f"  - {path}")
    print("High-risk paths:")
    for path in profile.high_risk_paths:
        print(f"  - {path}")
    print("Keywords:")
    for keyword in profile.keywords:
        print(f"  - {keyword}")
    print("Summary:")
    print(profile.bug_report_summary)
    return 0


def command_record(args: argparse.Namespace) -> int:
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    sha = git(repo, "rev-parse", args.commit).strip()
    subject = commit_subject(repo, sha)
    body = commit_body(repo, sha)
    files = commit_changed_files(repo, sha)
    diff = commit_diff_text(repo, sha)
    features = extract_commit_features(subject, body, files, diff)

    summary = args.summary or subject
    observation = CommitObservation(
        sha=sha,
        verdict=args.verdict,
        summary=summary,
        features=features,
        source="manual",
    )

    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    observations = update_observation(observations, observation)
    save_observations(observation_path, observations)

    print(f"Issue: {profile.issue_id}")
    print(f"Recorded {args.verdict} observation for {sha}")
    print(f"Observation file: {observation_path}")
    print(f"Summary: {summary}")
    print(f"Features: {', '.join(features[:12]) if features else 'none'}")
    return 0


def command_show_observations(args: argparse.Namespace) -> int:
    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    print(f"Issue: {args.issue}")
    print(f"Observation file: {observation_path}")
    print(f"Observations: {len(observations)}")
    for obs in observations:
        print(f"- {obs.verdict:>4} {obs.sha[:12]} {obs.summary}")
        print(f"  features: {', '.join(obs.features[:10]) if obs.features else 'none'}")
    return 0


def rank_of_commit(records: list[CommitRecord], target_sha: str) -> tuple[int, CommitRecord] | None:
    ranked = sorted(records, key=lambda record: record.utility, reverse=True)
    for idx, record in enumerate(ranked, start=1):
        if record.sha == target_sha:
            return idx, record
    return None


def rank_of_commit_by_semantic_score(records: list[CommitRecord], target_sha: str) -> tuple[int, CommitRecord] | None:
    ranked = sorted(records, key=lambda record: record.semantic_score, reverse=True)
    for idx, record in enumerate(ranked, start=1):
        if record.sha == target_sha:
            return idx, record
    return None


def load_commit_verdict_map(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text())
    verdict_map: dict[str, str] = {}
    for item in raw:
        verdict_map[item["sha"]] = item["verdict"]
    return verdict_map


def first_bad_from_candidate_file(path: Path) -> str:
    raw = json.loads(path.read_text())
    bad_entries = [item["sha"] for item in raw if item["verdict"] == "bad"]
    if not bad_entries:
        raise ValueError(f"no bad commit in candidate file {path}")
    return bad_entries[-1]


def partition_interval(commits: list[str], tested_sha: str, verdict: str) -> list[str]:
    idx = commits.index(tested_sha)
    if verdict == "good":
        return commits[idx + 1 :]
    if verdict == "bad":
        return commits[: idx + 1]
    if verdict == "skip":
        return commits[:idx] + commits[idx + 1 :]
    raise ValueError(f"unsupported verdict: {verdict}")


def replay_history_steps_over_interval(commits: list[str], history_steps: list[dict]) -> tuple[list[str], list[dict]]:
    unresolved = commits[:]
    events: list[dict] = []
    for step in history_steps:
        sha = str(step.get("sha", ""))
        verdict = str(step.get("verdict", ""))
        step_number = step.get("step")
        if not sha or sha not in unresolved:
            events.append(
                {
                    "type": "history-step-outside-window",
                    "step": step_number,
                    "sha": sha,
                    "verdict": verdict,
                    "unresolved_before": len(unresolved),
                    "unresolved_after": len(unresolved),
                }
            )
            continue
        before = len(unresolved)
        unresolved = partition_interval(unresolved, sha, verdict)
        events.append(
            {
                "type": "history-replay",
                "step": step_number,
                "sha": sha,
                "verdict": verdict,
                "unresolved_before": before,
                "unresolved_after": len(unresolved),
            }
        )
    return unresolved, events


def select_non_noop_candidate(
    ordered_candidates: list[CommitRecord],
    observations: list[CommitObservation],
    unresolved: list[str],
) -> tuple[CommitRecord, CommitObservation | None]:
    if not ordered_candidates:
        raise ValueError("ordered_candidates must not be empty")

    unresolved_set = set(unresolved)
    has_internal_probe = len(unresolved) >= 3
    good_endpoint = unresolved[0] if unresolved else None
    bad_endpoint = unresolved[-1] if unresolved else None

    for candidate in ordered_candidates:
        if has_internal_probe and candidate.sha in unresolved_set and candidate.sha in {good_endpoint, bad_endpoint}:
            continue
        cached = find_observation_by_sha(observations, candidate.sha)
        if cached is None:
            return candidate, None
        next_unresolved = partition_interval(unresolved, candidate.sha, cached.verdict)
        if next_unresolved != unresolved:
            return candidate, cached

    fallback = ordered_candidates[0]
    return fallback, find_observation_by_sha(observations, fallback.sha)


def apply_cached_interval_prepass(
    commits: list[str],
    observations: list[CommitObservation],
) -> tuple[list[str], list[dict], bool]:
    if len(commits) <= 1 or not observations:
        return commits, [], False

    index_by_sha = {sha: idx for idx, sha in enumerate(commits)}
    good_indices: list[tuple[int, CommitObservation]] = []
    bad_indices: list[tuple[int, CommitObservation]] = []
    skip_entries: list[tuple[int, CommitObservation]] = []
    for observation in observations:
        idx = index_by_sha.get(observation.sha)
        if idx is None:
            continue
        if observation.verdict == "good":
            good_indices.append((idx, observation))
        elif observation.verdict == "bad":
            bad_indices.append((idx, observation))
        elif observation.verdict == "skip":
            skip_entries.append((idx, observation))

    if not good_indices and not bad_indices and not skip_entries:
        return commits, [], False

    max_good_idx = max((idx for idx, _obs in good_indices), default=-1)
    min_bad_idx = min((idx for idx, _obs in bad_indices), default=len(commits))
    contradiction = max_good_idx >= min_bad_idx if good_indices and bad_indices else False
    if contradiction:
        return commits, [{"type": "cached-contradiction", "good_index": max_good_idx, "bad_index": min_bad_idx}], True

    start = max_good_idx + 1
    end = min_bad_idx + 1 if bad_indices else len(commits)
    trimmed = commits[start:end]
    trimmed_set = set(trimmed)
    skip_set = {observation.sha for _idx, observation in skip_entries if observation.sha in trimmed_set}
    pruned = [sha for sha in trimmed if sha not in skip_set]

    events: list[dict] = []
    if start > 0 and good_indices:
        good_obs = max(good_indices, key=lambda item: item[0])[1]
        events.append(
            {
                "type": "cached-good-cut",
                "sha": good_obs.sha,
                "summary": good_obs.summary,
                "removed_count": start,
            }
        )
    if end < len(commits) and bad_indices:
        bad_obs = min(bad_indices, key=lambda item: item[0])[1]
        events.append(
            {
                "type": "cached-bad-cut",
                "sha": bad_obs.sha,
                "summary": bad_obs.summary,
                "removed_count": len(commits) - end,
            }
        )
    if skip_set:
        events.append(
            {
                "type": "cached-skip-drop",
                "removed_count": len(skip_set),
                "shas": sorted(skip_set),
            }
        )
    return pruned, events, False


def load_skip_shas(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict):
            return {item["sha"] for item in raw if item.get("verdict") == "skip"}
        return {str(item) for item in raw}
    raise ValueError(f"unsupported skip file format: {path}")


def command_eval_email_case(args: argparse.Namespace) -> int:
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    target_sha = args.target_sha
    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    candidate_shas = None
    if args.candidate_file:
        candidate_shas = load_candidate_commits_from_file(Path(args.candidate_file))

    results = []
    for scorer_name in ("heuristic", "model"):
        model_config = (
            load_model_config(
                model_name=args.model_name,
                observation_prompt_mode=args.observation_prompt_mode,
            )
            if scorer_name == "model"
            else None
        )
        records, pruning_summary = make_records(
            repo,
            profile,
            max_candidates=args.max_candidates,
            scorer=scorer_name,
            model_config=model_config,
            candidate_shas=candidate_shas,
            candidate_pruning=args.candidate_pruning,
            heuristic_version=args.heuristic_version,
            observations=observations,
        )
        apply_feedback_bias(profile, records, observations)
        compute_selection(
            records,
            lambda_weight=args.lambda_weight,
            build_success_power=args.build_success_power,
        )
        utility_rank_result = rank_of_commit(records, target_sha)
        semantic_rank_result = rank_of_commit_by_semantic_score(records, target_sha)
        if utility_rank_result is None or semantic_rank_result is None:
            raise RuntimeError(f"target commit {target_sha} was not found in range")
        utility_rank, record = utility_rank_result
        semantic_rank, _semantic_record = semantic_rank_result
        results.append((scorer_name, utility_rank, semantic_rank, record, len(records), pruning_summary))

    print(f"Issue: {profile.issue_id}")
    print(f"Ground-truth first bad: {target_sha}")
    if args.candidate_file:
        print(f"Candidate file: {args.candidate_file}")
    print(f"Observations: {len(observations)}")
    print(f"Heuristic version: {args.heuristic_version}")
    print(f"Candidate pruning: {args.candidate_pruning}")
    for scorer_name, utility_rank, semantic_rank, record, total, pruning_summary in results:
        print("")
        print(f"Scorer: {scorer_name}")
        if pruning_summary["pruned_count"]:
            print(f"Candidates after pruning: {pruning_summary['after_count']}/{pruning_summary['before_count']}")
        print(f"Semantic rank of ground truth: {semantic_rank}/{total}")
        print(f"Utility rank of ground truth: {utility_rank}/{total}")
        print(f"Subject: {record.subject}")
        print(f"Semantic score: {record.semantic_score:.4f}")
        print(f"Utility: {record.utility:.4f}")
        print(f"Evidence: {'; '.join(record.evidence or [])}")
    return 0


def command_simulate_online(args: argparse.Namespace) -> int:
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    first_bad_sha = args.first_bad_sha
    if first_bad_sha is None and args.oracle_file:
        first_bad_sha = first_bad_from_candidate_file(Path(args.oracle_file))
    if first_bad_sha is None:
        raise SystemExit("error: provide --first-bad-sha or --oracle-file")
    skip_shas = load_skip_shas(Path(args.skip_file)) if args.skip_file else set()

    full_interval = list_candidate_commits(repo, profile.good_commit, profile.bad_commit)
    unresolved = full_interval[:]
    if first_bad_sha not in unresolved:
        raise RuntimeError(f"first bad {first_bad_sha} not found in git interval")
    first_bad_index = full_interval.index(first_bad_sha)
    tested: list[dict] = []
    step = 0
    metadata_cache: dict[str, CommitMetadata] = {}
    model_config = (
        load_model_config(
            model_name=args.model_name,
            observation_prompt_mode=args.observation_prompt_mode,
        )
        if args.scorer == "model"
        else None
    )
    shared_model_cache = (
        load_model_cache(
            model_cache_path(
                profile.issue_id,
                model_config.model_name,
                scoring_version=resolved_model_scoring_version(model_config.observation_prompt_mode),
            )
        )
        if model_config is not None
        else None
    )

    while unresolved and len(unresolved) > 1 and step < args.max_steps:
        step += 1
        records, pruning_summary = make_records(
            repo,
            profile,
            scorer=args.scorer,
            model_config=model_config,
            candidate_shas=unresolved,
            model_top_k=args.model_top_k,
            model_frontier=args.model_frontier,
            candidate_pruning=args.candidate_pruning,
            heuristic_version=args.heuristic_version,
            observations=[],
            metadata_cache=metadata_cache,
            model_cache=shared_model_cache,
        )
        decision = select_next_commit(
            profile,
            records,
            lambda_weight=args.lambda_weight,
            build_success_power=args.build_success_power,
            search_policy=args.search_policy,
            hybrid_switch_window=args.hybrid_switch_window,
            calibrated_prior_power=args.calibrated_prior_power,
            calibrated_prior_bonus=args.calibrated_prior_bonus,
            weak_relevance_penalty=args.weak_relevance_penalty,
            weak_relevance_threshold=args.weak_relevance_threshold,
        )
        selected, _cached = select_non_noop_candidate(
            decision.ranked_candidates,
            [],
            unresolved,
        )
        absolute_index = full_interval.index(selected.sha)
        if selected.sha in skip_shas:
            verdict = "skip"
        elif absolute_index < first_bad_index:
            verdict = "good"
        else:
            verdict = "bad"
        tested.append(
            {
                "sha": selected.sha,
                "verdict": verdict,
                "unresolved_before": len(unresolved),
                "pruning": pruning_summary,
            }
        )
        unresolved = partition_interval(unresolved, selected.sha, verdict)

    print(f"Issue: {profile.issue_id}")
    print(f"Scorer: {args.scorer}")
    print(f"Heuristic version: {args.heuristic_version}")
    print(f"Search policy: {args.search_policy}")
    if args.search_policy == "calibrated-posterior":
        print(f"Calibrated prior power: {args.calibrated_prior_power}")
        print(f"Calibrated prior bonus: {args.calibrated_prior_bonus}")
        print(f"Weak relevance penalty: {args.weak_relevance_penalty}")
        print(f"Weak relevance threshold: {args.weak_relevance_threshold}")
    print(f"Build success power: {args.build_success_power}")
    print(f"Candidate pruning: {args.candidate_pruning}")
    if args.scorer == "model":
        print(f"Model: {args.model_name or os.getenv('CHATANYWHERE_MODEL', 'gpt-5.4-mini')}")
        print(f"Model frontier: {args.model_frontier}")
    if args.oracle_file:
        print(f"Oracle file: {args.oracle_file}")
    print(f"Ground-truth first bad: {first_bad_sha}")
    print(f"Steps executed: {len(tested)}")
    if unresolved:
        print(f"Remaining unresolved commits: {len(unresolved)}")
    print("Tested path:")
    for item in tested:
        sha = item["sha"]
        verdict = item["verdict"]
        unresolved_count = item["unresolved_before"]
        pruning = item["pruning"]
        subject = commit_subject(repo, sha)
        print(
            f"- {verdict:>4} {sha[:12]} unresolved={unresolved_count} "
            f"candidates={pruning['after_count']}/{pruning['before_count']} {subject}"
        )
    if unresolved:
        print("Final unresolved window:")
        for sha in unresolved[:5]:
            print(f"  - {sha}")
    return 0


def command_run_online(args: argparse.Namespace) -> int:
    repo = Path(args.llvm_dir).resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"error: expected git checkout at {repo}")

    log_progress(f"start issue={args.issue} scorer={args.scorer} run_label={args.run_label or '<none>'}")
    profiles = load_profiles()
    profile = load_issue_profile(profiles, args.issue)
    log_progress("loading candidate window")
    unresolved = (
        load_candidate_commits_from_file(Path(args.candidate_file))
        if args.candidate_file
        else list_candidate_commits(repo, profile.good_commit, profile.bad_commit)
    )
    log_progress(f"loaded candidate window: {len(unresolved)} commits")
    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    log_progress(f"loaded observations: {len(observations)} from {observation_path}")
    model_name = resolved_model_name(args.scorer, args.model_name)
    run_history_path = run_history_path_for_issue(
        args.issue,
        args.scorer,
        model_name,
        args.search_policy,
        args.model_frontier,
        args.candidate_pruning,
        args.heuristic_version,
        args.observation_prompt_mode,
        args.run_label,
    )
    unresolved_window_path = unresolved_window_path_for_issue(
        args.issue,
        args.scorer,
        model_name,
        args.search_policy,
        args.model_frontier,
        args.candidate_pruning,
        args.heuristic_version,
        args.observation_prompt_mode,
        args.run_label,
    )
    existing_run_history = load_run_history(run_history_path)
    log_progress(f"history path: {run_history_path}")
    run_history, completed_steps, resumed = prepare_run_history(
        existing_history=existing_run_history,
        issue_id=profile.issue_id,
        scorer=args.scorer,
        model_name=model_name,
        model_frontier=args.model_frontier,
        search_policy=args.search_policy,
        hybrid_switch_window=args.hybrid_switch_window,
        candidate_pruning=args.candidate_pruning,
        lambda_weight=args.lambda_weight,
        max_steps=args.max_steps,
        observation_path=str(observation_path),
        run_history_path=str(run_history_path),
        good_commit=profile.good_commit,
        bad_commit=profile.bad_commit,
        initial_unresolved=len(unresolved),
        candidate_file=str(Path(args.candidate_file).resolve()) if args.candidate_file else None,
        heuristic_version=args.heuristic_version,
        calibrated_prior_power=args.calibrated_prior_power,
        calibrated_prior_bonus=args.calibrated_prior_bonus,
        weak_relevance_penalty=args.weak_relevance_penalty,
        weak_relevance_threshold=args.weak_relevance_threshold,
        build_success_power=args.build_success_power,
        observation_prompt_mode=args.observation_prompt_mode,
        run_label=args.run_label,
    )
    save_run_history(run_history_path, run_history)
    log_progress(f"history initialized: completed_steps={completed_steps} resumed={resumed}")

    if resumed and run_history.get("steps"):
        unresolved_before_history_replay = len(unresolved)
        unresolved, history_replay_events = replay_history_steps_over_interval(unresolved, run_history.get("steps", []))
        run_history.setdefault("history_replay_events", []).append(
            {
                "before_step": completed_steps + 1,
                "unresolved_before": unresolved_before_history_replay,
                "unresolved_after": len(unresolved),
                "events": history_replay_events,
            }
        )
        save_run_history(run_history_path, run_history)

    save_unresolved_window(unresolved_window_path, unresolved)
    log_progress(f"unresolved window saved: {unresolved_window_path}")

    original_head = git(repo, "rev-parse", "--verify", "HEAD").strip()
    log_progress(f"original HEAD: {original_head[:12]}")
    tested: list[dict] = []
    metadata_cache: dict[str, CommitMetadata] = {}
    model_config = (
        load_model_config(
            model_name=args.model_name,
            observation_prompt_mode=args.observation_prompt_mode,
        )
        if args.scorer == "model"
        else None
    )
    if model_config is not None:
        log_progress(f"model config loaded: {model_config.model_name}")
    shared_model_cache = (
        load_model_cache(
            model_cache_path(
                profile.issue_id,
                model_config.model_name,
                scoring_version=resolved_model_scoring_version(model_config.observation_prompt_mode),
            )
        )
        if model_config is not None
        else None
    )
    if shared_model_cache is not None:
        log_progress(f"model cache loaded: {len(shared_model_cache)} entries")

    try:
        step = completed_steps
        while unresolved and len(unresolved) > 1 and step < args.max_steps:
            log_progress(f"step {step + 1}: unresolved before prepass={len(unresolved)}")
            unresolved_before_prepass = len(unresolved)
            unresolved, prepass_events, prepass_contradiction = apply_cached_interval_prepass(unresolved, observations)
            if prepass_events:
                run_history.setdefault("prepass_events", []).append(
                    {
                        "before_step": step + 1,
                        "unresolved_before": unresolved_before_prepass,
                        "unresolved_after": len(unresolved),
                        "contradiction": prepass_contradiction,
                        "events": prepass_events,
                    }
                )
                save_run_history(run_history_path, run_history)
                save_unresolved_window(unresolved_window_path, unresolved)
            if len(unresolved) <= 1:
                break

            step += 1
            unresolved_before = len(unresolved)
            log_progress(f"step {step}: make_records start unresolved={unresolved_before}")
            records, pruning_summary = make_records(
                repo,
                profile,
                scorer=args.scorer,
                model_config=model_config,
                candidate_shas=unresolved,
                model_top_k=args.model_top_k,
                model_frontier=args.model_frontier,
                candidate_pruning=args.candidate_pruning,
                heuristic_version=args.heuristic_version,
                observations=observations,
                metadata_cache=metadata_cache,
                model_cache=shared_model_cache,
            )
            log_progress(f"step {step}: make_records done records={len(records)}")
            apply_feedback_bias(profile, records, observations)
            log_progress(f"step {step}: select_next_commit start")
            decision = select_next_commit(
                profile,
                records,
                lambda_weight=args.lambda_weight,
                build_success_power=args.build_success_power,
                search_policy=args.search_policy,
                hybrid_switch_window=args.hybrid_switch_window,
                calibrated_prior_power=args.calibrated_prior_power,
                calibrated_prior_bonus=args.calibrated_prior_bonus,
                weak_relevance_penalty=args.weak_relevance_penalty,
                weak_relevance_threshold=args.weak_relevance_threshold,
            )
            log_progress(f"step {step}: selected {decision.selected.sha[:12]} mode={decision.selection_mode}")
            selected, cached = select_non_noop_candidate(
                decision.ranked_candidates,
                observations,
                unresolved,
            )
            log_progress(f"step {step}: non-noop selected {selected.sha[:12]} source={'cache' if cached else 'runner'}")
            top_candidates = [
                compact_candidate_view(record, rank + 1)
                for rank, record in enumerate(decision.ranked_candidates[:5])
            ]
            if selected.sha != decision.selected.sha:
                top_candidates.insert(
                    0,
                    {
                        "rank": 0,
                        "sha": selected.sha,
                        "subject": selected.subject,
                        "selection_score": selected.selection_score,
                        "semantic_score": selected.semantic_score,
                        "build_success_prob": selected.build_success_prob,
                        "feedback_bias": selected.feedback_bias,
                        "posterior_bad_mass": selected.posterior_bad_mass,
                        "calibrated_suspicion_weight": selected.calibrated_suspicion_weight,
                        "calibrated_posterior_bad_mass": selected.calibrated_posterior_bad_mass,
                        "calibrated_posterior_info_gain": selected.calibrated_posterior_info_gain,
                    },
                )

            source = "cache"
            runner_duration_sec = None
            if cached is not None:
                verdict = cached.verdict
                summary = cached.summary or selected.subject
            else:
                source = "runner"
                log_progress(f"step {step}: checkout {selected.sha[:12]}")
                checkout_commit(repo, selected.sha)
                runner_started = time.monotonic()
                log_progress(f"step {step}: runner start")
                verdict, summary, runner_output, runner_evidence = run_issue_runner(profile, repo)
                runner_duration_sec = time.monotonic() - runner_started
                log_progress(f"step {step}: runner done verdict={verdict} duration={runner_duration_sec:.1f}s")
                features = extract_commit_features(
                    selected.subject,
                    selected.body,
                    selected.changed_files,
                    selected.diff_text,
                )
                observation = CommitObservation(
                    sha=selected.sha,
                    verdict=verdict,
                    summary=summary,
                    features=features,
                    source="runner",
                    evidence=runner_evidence,
                    log_excerpt=runner_output[-4000:],
                    trace_excerpt=extract_trace_excerpt(runner_output),
                )
                observations = update_observation(observations, observation)
                save_observations(observation_path, observations)

            tested.append(
                {
                    "sha": selected.sha,
                    "subject": selected.subject,
                    "verdict": verdict,
                    "unresolved_before": len(unresolved),
                    "source": source,
                    "pruning": pruning_summary,
                }
            )
            unresolved = partition_interval(unresolved, selected.sha, verdict)
            append_run_history_step(
                run_history,
                {
                    "step": step,
                    "sha": selected.sha,
                    "subject": selected.subject,
                    "verdict": verdict,
                    "source": source,
                    "summary": summary,
                    "unresolved_before": unresolved_before,
                    "unresolved_after": len(unresolved),
                    "candidate_pruning": pruning_summary,
                    "search_policy": args.search_policy,
                    "selection_mode": decision.selection_mode,
                    "selection": selection_payload(selected),
                    "top_candidates": top_candidates,
                },
            )
            if runner_duration_sec is not None:
                run_history["steps"][-1]["runner_duration_sec"] = round(runner_duration_sec, 3)
            save_run_history(run_history_path, run_history)
            save_unresolved_window(unresolved_window_path, unresolved)
    finally:
        checkout_commit(repo, original_head)

    runner_durations = [
        float(step_payload["runner_duration_sec"])
        for step_payload in run_history.get("steps", [])
        if step_payload.get("source") == "runner" and step_payload.get("runner_duration_sec") is not None
    ]
    run_history["status"] = "completed"
    run_history["steps_executed"] = len(run_history.get("steps", []))
    run_history["remaining_unresolved"] = len(unresolved)
    run_history["final_unresolved_window"] = unresolved[:]
    run_history["unresolved_window_path"] = str(unresolved_window_path)
    run_history["runner_build_count"] = len(runner_durations)
    run_history["runner_build_avg_duration_sec"] = (
        round(sum(runner_durations) / len(runner_durations), 3) if runner_durations else None
    )
    save_run_history(run_history_path, run_history)
    save_unresolved_window(unresolved_window_path, unresolved)

    print(f"Issue: {profile.issue_id}")
    print(f"Scorer: {args.scorer}")
    print(f"Heuristic version: {args.heuristic_version}")
    print(f"Search policy: {args.search_policy}")
    if args.search_policy == "calibrated-posterior":
        print(f"Calibrated prior power: {args.calibrated_prior_power}")
        print(f"Calibrated prior bonus: {args.calibrated_prior_bonus}")
        print(f"Weak relevance penalty: {args.weak_relevance_penalty}")
        print(f"Weak relevance threshold: {args.weak_relevance_threshold}")
    print(f"Build success power: {args.build_success_power}")
    print(f"Candidate pruning: {args.candidate_pruning}")
    if args.scorer == "model":
        print(f"Model: {args.model_name or os.getenv('CHATANYWHERE_MODEL', 'gpt-5.4-mini')}")
        print(f"Model frontier: {args.model_frontier}")
    print(f"Observation file: {observation_path}")
    print(f"Run history file: {run_history_path}")
    print(f"Unresolved window file: {unresolved_window_path}")
    print(f"Resumed: {'yes' if resumed else 'no'}")
    print(f"Steps executed this run: {len(tested)}")
    print(f"Total recorded steps: {len(run_history.get('steps', []))}")
    if run_history["runner_build_avg_duration_sec"] is not None:
        print(f"Average runner build time (sec): {run_history['runner_build_avg_duration_sec']}")
    if unresolved:
        print(f"Remaining unresolved commits: {len(unresolved)}")
    print("Tested path:")
    for item in tested:
        sha = item["sha"]
        verdict = item["verdict"]
        unresolved_count = item["unresolved_before"]
        source = item["source"]
        pruning = item["pruning"]
        subject = item.get("subject") or commit_subject(repo, sha)
        print(
            f"- {verdict:>4} {sha[:12]} unresolved={unresolved_count} "
            f"candidates={pruning['after_count']}/{pruning['before_count']} "
            f"source={source} {subject}"
        )
    if unresolved:
        print("Final unresolved window:")
        for sha in unresolved[:5]:
            print(f"  - {sha}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="First-version LLM-assisted bisect selector for existing LLVM issue ranges."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    suggest = subparsers.add_parser("suggest", help="rank commits in a good/bad range and select the next commit to test")
    suggest.add_argument("--issue", required=True, help="issue profile id, e.g. pr187875")
    suggest.add_argument(
        "--llvm-dir",
        default="/home/derek331/research/gitbisect-work/llvm-project",
        help="path to llvm-project checkout",
    )
    suggest.add_argument("--lambda-weight", type=float, default=2.0, help="lambda in U(n)=b*pbuild*(1+lambda*w)")
    suggest.add_argument("--top", type=int, default=10, help="number of ranked candidates to print")
    suggest.add_argument("--max-candidates", type=int, default=None, help="optional cap for candidate enumeration")
    suggest.add_argument("--candidate-file", default=None, help="optional JSON file listing the candidate commit set to rank")
    suggest.add_argument("--observations", default=None, help="optional path to tested-commit observation JSON")
    suggest.add_argument("--scorer", choices=("heuristic", "model"), default="heuristic", help="scoring backend")
    suggest.add_argument("--heuristic-version", choices=("v1", "tuned"), default="tuned", help="heuristic scoring version to use for baseline vs tuned comparisons")
    suggest.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    suggest.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    suggest.add_argument("--model-frontier", choices=("topk", "diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    suggest.add_argument("--observation-prompt-mode", choices=("legacy", "trace-only"), default="legacy", help="how runner-backed crash observations are formatted when scorer=model")
    suggest.add_argument("--search-policy", choices=("ranked", "hybrid", "posterior", "calibrated-posterior"), default="ranked", help="commit selection policy")
    suggest.add_argument("--candidate-pruning", choices=("off", "conservative"), default="off", help="prune obviously irrelevant commits before scoring")
    suggest.add_argument("--hybrid-switch-window", type=int, default=32, help="switch hybrid search to boundary closing at or below this unresolved window size")
    suggest.add_argument("--build-success-power", type=float, default=DEFAULT_BUILD_SUCCESS_POWER, help="exponent applied to pbuild before it affects selector scoring")
    suggest.add_argument("--calibrated-prior-power", type=float, default=DEFAULT_CALIBRATED_PRIOR_POWER, help="power used to sharpen the prior for calibrated-posterior search")
    suggest.add_argument("--calibrated-prior-bonus", type=float, default=DEFAULT_CALIBRATED_PRIOR_BONUS, help="bonus multiplier for high-prior commits under calibrated-posterior search")
    suggest.add_argument("--weak-relevance-penalty", type=float, default=DEFAULT_WEAK_RELEVANCE_PENALTY, help="light penalty applied to weak-relevance commits under calibrated-posterior search")
    suggest.add_argument("--weak-relevance-threshold", type=float, default=DEFAULT_WEAK_RELEVANCE_THRESHOLD, help="semantic-score threshold above which the weak-relevance penalty does not apply")
    suggest.add_argument("--json", action="store_true", help="also print machine-readable JSON")
    suggest.set_defaults(func=command_suggest)

    explain = subparsers.add_parser("explain", help="show the local issue profile used for scoring")
    explain.add_argument("--issue", required=True, help="issue profile id, e.g. pr176682")
    explain.set_defaults(func=command_explain)

    record = subparsers.add_parser("record", help="record the outcome of a tested commit for future ranking")
    record.add_argument("--issue", required=True, help="issue profile id, e.g. pr187875")
    record.add_argument("--commit", required=True, help="tested commit SHA or rev")
    record.add_argument("--verdict", required=True, choices=("good", "bad", "skip"), help="observed outcome")
    record.add_argument("--summary", default=None, help="optional short human summary of why this verdict happened")
    record.add_argument(
        "--llvm-dir",
        default="/home/derek331/research/gitbisect-work/llvm-project",
        help="path to llvm-project checkout",
    )
    record.add_argument("--observations", default=None, help="optional path to tested-commit observation JSON")
    record.set_defaults(func=command_record)

    show_observations = subparsers.add_parser("show-observations", help="print recorded tested-commit observations")
    show_observations.add_argument("--issue", required=True, help="issue profile id, e.g. pr187875")
    show_observations.add_argument("--observations", default=None, help="optional path to tested-commit observation JSON")
    show_observations.set_defaults(func=command_show_observations)

    eval_email = subparsers.add_parser("eval-email-case", help="compare heuristic and model ranking on a known-ground-truth email case")
    eval_email.add_argument("--issue", required=True, help="issue profile id, e.g. pr172195")
    eval_email.add_argument("--target-sha", required=True, help="ground-truth first-bad commit SHA")
    eval_email.add_argument(
        "--llvm-dir",
        default="/home/derek331/research/gitbisect-work/llvm-project",
        help="path to llvm-project checkout",
    )
    eval_email.add_argument("--lambda-weight", type=float, default=2.0, help="lambda in U(n)=b*pbuild*(1+lambda*w)")
    eval_email.add_argument("--max-candidates", type=int, default=None, help="optional cap for candidate enumeration")
    eval_email.add_argument("--candidate-file", default=None, help="optional JSON file listing the candidate commit set to rank")
    eval_email.add_argument("--observations", default=None, help="optional path to tested-commit observation JSON")
    eval_email.add_argument("--heuristic-version", choices=("v1", "tuned"), default="tuned", help="heuristic scoring version to use for baseline vs tuned comparisons")
    eval_email.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    eval_email.add_argument("--observation-prompt-mode", choices=("legacy", "trace-only"), default="legacy", help="how runner-backed crash observations are formatted when scorer=model")
    eval_email.add_argument("--candidate-pruning", choices=("off", "conservative"), default="off", help="prune obviously irrelevant commits before scoring")
    eval_email.add_argument("--build-success-power", type=float, default=DEFAULT_BUILD_SUCCESS_POWER, help="exponent applied to pbuild before it affects selector scoring")
    eval_email.set_defaults(func=command_eval_email_case)

    simulate = subparsers.add_parser("simulate-online", help="simulate online search using only current unresolved interval and an oracle file")
    simulate.add_argument("--issue", required=True, help="issue profile id, e.g. pr172195")
    simulate.add_argument("--oracle-file", default=None, help="optional JSON file containing the retrospective email-trace verdicts")
    simulate.add_argument("--first-bad-sha", default=None, help="ground-truth first-bad commit for the hidden oracle")
    simulate.add_argument("--skip-file", default=None, help="optional JSON file listing known skip commits")
    simulate.add_argument(
        "--llvm-dir",
        default="/home/derek331/research/gitbisect-work/llvm-project",
        help="path to llvm-project checkout",
    )
    simulate.add_argument("--scorer", choices=("heuristic", "model"), default="heuristic", help="scoring backend")
    simulate.add_argument("--heuristic-version", choices=("v1", "tuned"), default="tuned", help="heuristic scoring version to use for baseline vs tuned comparisons")
    simulate.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    simulate.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    simulate.add_argument("--model-frontier", choices=("topk", "diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    simulate.add_argument("--observation-prompt-mode", choices=("legacy", "trace-only"), default="legacy", help="how runner-backed crash observations are formatted when scorer=model")
    simulate.add_argument("--search-policy", choices=("ranked", "hybrid", "posterior", "calibrated-posterior"), default="ranked", help="commit selection policy")
    simulate.add_argument("--candidate-pruning", choices=("off", "conservative"), default="off", help="prune obviously irrelevant commits before scoring")
    simulate.add_argument("--hybrid-switch-window", type=int, default=32, help="switch hybrid search to boundary closing at or below this unresolved window size")
    simulate.add_argument("--build-success-power", type=float, default=DEFAULT_BUILD_SUCCESS_POWER, help="exponent applied to pbuild before it affects selector scoring")
    simulate.add_argument("--calibrated-prior-power", type=float, default=DEFAULT_CALIBRATED_PRIOR_POWER, help="power used to sharpen the prior for calibrated-posterior search")
    simulate.add_argument("--calibrated-prior-bonus", type=float, default=DEFAULT_CALIBRATED_PRIOR_BONUS, help="bonus multiplier for high-prior commits under calibrated-posterior search")
    simulate.add_argument("--weak-relevance-penalty", type=float, default=DEFAULT_WEAK_RELEVANCE_PENALTY, help="light penalty applied to weak-relevance commits under calibrated-posterior search")
    simulate.add_argument("--weak-relevance-threshold", type=float, default=DEFAULT_WEAK_RELEVANCE_THRESHOLD, help="semantic-score threshold above which the weak-relevance penalty does not apply")
    simulate.add_argument("--lambda-weight", type=float, default=2.0, help="lambda in U(n)=b*pbuild*(1+lambda*w)")
    simulate.add_argument("--max-steps", type=int, default=20, help="maximum number of simulated steps")
    simulate.set_defaults(func=command_simulate_online)

    run_online = subparsers.add_parser("run-online", help="run real online search using the issue-specific runner and an issue-specific tested-commit cache")
    run_online.add_argument("--issue", required=True, help="issue profile id, e.g. pr176682")
    run_online.add_argument(
        "--llvm-dir",
        default="/home/derek331/research/gitbisect-work/llvm-project",
        help="path to llvm-project checkout",
    )
    run_online.add_argument("--scorer", choices=("heuristic", "model"), default="heuristic", help="scoring backend")
    run_online.add_argument("--heuristic-version", choices=("v1", "tuned"), default="tuned", help="heuristic scoring version to use for baseline vs tuned comparisons")
    run_online.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    run_online.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    run_online.add_argument("--model-frontier", choices=("topk", "diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    run_online.add_argument("--observation-prompt-mode", choices=("legacy", "trace-only"), default="legacy", help="how runner-backed crash observations are formatted when scorer=model")
    run_online.add_argument("--search-policy", choices=("ranked", "hybrid", "posterior", "calibrated-posterior"), default="ranked", help="commit selection policy")
    run_online.add_argument("--candidate-pruning", choices=("off", "conservative"), default="off", help="prune obviously irrelevant commits before scoring")
    run_online.add_argument("--hybrid-switch-window", type=int, default=32, help="switch hybrid search to boundary closing at or below this unresolved window size")
    run_online.add_argument("--build-success-power", type=float, default=DEFAULT_BUILD_SUCCESS_POWER, help="exponent applied to pbuild before it affects selector scoring")
    run_online.add_argument("--calibrated-prior-power", type=float, default=DEFAULT_CALIBRATED_PRIOR_POWER, help="power used to sharpen the prior for calibrated-posterior search")
    run_online.add_argument("--calibrated-prior-bonus", type=float, default=DEFAULT_CALIBRATED_PRIOR_BONUS, help="bonus multiplier for high-prior commits under calibrated-posterior search")
    run_online.add_argument("--weak-relevance-penalty", type=float, default=DEFAULT_WEAK_RELEVANCE_PENALTY, help="light penalty applied to weak-relevance commits under calibrated-posterior search")
    run_online.add_argument("--weak-relevance-threshold", type=float, default=DEFAULT_WEAK_RELEVANCE_THRESHOLD, help="semantic-score threshold above which the weak-relevance penalty does not apply")
    run_online.add_argument("--lambda-weight", type=float, default=2.0, help="lambda in U(n)=b*pbuild*(1+lambda*w)")
    run_online.add_argument("--max-steps", type=int, default=20, help="maximum number of real runner-backed steps")
    run_online.add_argument("--observations", default=None, help="optional path to issue-specific tested-commit cache JSON")
    run_online.add_argument("--candidate-file", default=None, help="optional JSON file listing the exact unresolved candidate window to continue from")
    run_online.add_argument("--run-label", default=None, help="optional suffix to create a distinct run-history and unresolved-window artifact")
    run_online.set_defaults(func=command_run_online)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
