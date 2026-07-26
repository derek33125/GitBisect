#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
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

# Archived on 2026-07-13 after the first scoped general-keyword ablation.
# Keep this list so completed `general` histories remain interpretable.
GENERAL_CRASH_KEYWORDS_V1 = (
    "crash",
    "assertion",
    "abort",
    "segfault",
    "ice",
    "internal compiler error",
    "diagnostic",
    "parser",
    "sema",
    "ast",
    "ir",
    "optimizer",
    "analysis",
    "codegen",
    "lowering",
    "vectorizer",
    "loop",
    "target",
)

# The active fixed vocabulary is a deliberately weak maintenance-language
# control. It is shared across all issues and avoids crash mechanisms and LLVM
# subsystem names, leaving only the keyword signal different from tuned runs.
GENERAL_KEYWORDS = (
    "add",
    "update",
    "change",
    "test",
    "support",
    "cleanup",
    "refactor",
    "rename",
    "remove",
    "document",
)

HEURISTIC_VERSIONS = ("v1", "tuned", "general", "none", "neutral", "oracle-first-bad")

# This diagnostic deliberately derives keywords from the validated answer. The
# filter keeps source-level identifiers while removing syntax and commit noise.
ORACLE_KEYWORD_STOPWORDS = frozenset(
    {
        "add",
        "added",
        "change",
        "changed",
        "cleanup",
        "const",
        "document",
        "else",
        "false",
        "fix",
        "fixed",
        "for",
        "from",
        "include",
        "llvm",
        "clang",
        "lib",
        "namespace",
        "nullptr",
        "remove",
        "removed",
        "return",
        "static",
        "support",
        "test",
        "tests",
        "the",
        "this",
        "true",
        "update",
        "using",
        "void",
    }
)
ORACLE_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_:]{3,}\b")
ORACLE_SUBJECT_TAG_RE = re.compile(r"\[([^\]]+)\]")
ORACLE_KEYWORD_LIMIT = 16

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
MODEL_SCORING_VERSION = "v7-first-bad-risk-guidance"
TRACE_PROMPT_SIGNATURE_SUFFIX = " [same crash signature repeated "
DEFAULT_MODEL_SCORING_BATCH_SIZE = 12
DEFAULT_MODEL_REQUEST_TIMEOUT = 120.0
DEFAULT_MODEL_REQUEST_RETRIES = 3
DEFAULT_MODEL_RETRY_BACKOFF_SECONDS = 5.0
DEFAULT_MODEL_RANK_BONUS = 0.35
DEFAULT_MODEL_PRIOR_SOFTMAX_TEMPERATURE = 4.0
DEFAULT_MODEL_DIRECT_HIT_BONUS = 2.5
DEFAULT_MODEL_MECHANISM_BONUS = 0.25
DEFAULT_MODEL_MECHANISM_OVERRIDE_SCALE = 2.0
DEFAULT_OBSERVATION_POSTERIOR_BAD_STRENGTH = 1.5
DEFAULT_OBSERVATION_POSTERIOR_GOOD_STRENGTH = 1.0
DEFAULT_OBSERVATION_POSTERIOR_COMPONENT_WEIGHT = 0.75
TRACE_PROMPT_MAX_CHARS = 2400
DEFAULT_DIFF_TEXT_MAX_CHARS = 12000
DIFF_EXTRACTION_MAX_INPUT_CHARS = 600000
DIFF_EXTRACTION_VERSION = "llm-v2-600k"
CAUSAL_DIFF_EXTRACTION_VERSION = "causal-llm-v1-retrieved-context"
CAUSAL_IMPL_DIFF_EXTRACTION_VERSION = "causal-llm-v2-implementation-first"
DEFAULT_DIFF_EXTRACTION_BATCH_SIZE = 20
DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS = 240000
CAUSAL_DIFF_MAX_SELECTED_HUNKS = 8
CAUSAL_DIFF_MAX_HUNK_CHARS = 4000
CAUSAL_DIFF_MAX_FUNCTION_CONTEXTS = 4
CAUSAL_DIFF_MAX_FUNCTION_CONTEXT_CHARS = 2400

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
TRANSITION_DIFF_FILE_LIMIT = 20


def git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    completed = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    return completed.stdout.decode("utf-8", "replace")


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


def oracle_keyword_term(value: str) -> str | None:
    """Keep a stable source-level term suitable for the oracle diagnostic."""
    candidate = value.strip().strip("_-:")
    normalized = compact_alnum(candidate)
    if len(normalized) < 5 or normalized in ORACLE_KEYWORD_STOPWORDS or normalized.isdigit():
        return None
    if not (
        any(char.isupper() for char in candidate)
        or "_" in candidate
        or "::" in candidate
        or "-" in candidate
    ):
        return None
    return candidate


def oracle_first_bad_keyword_derivation(repo: Path, first_bad_sha: str) -> dict[str, object]:
    """Derive diagnostic-only keyword features from a known first-bad commit."""
    subject = commit_subject(repo, first_bad_sha)
    body = commit_body(repo, first_bad_sha)
    changed_files = commit_changed_files(repo, first_bad_sha)
    diff_text = commit_diff_text(repo, first_bad_sha, max_chars=DIFF_EXTRACTION_MAX_INPUT_CHARS)
    source_texts = ORACLE_SUBJECT_TAG_RE.findall(subject)
    source_texts.extend(Path(path).stem for path in changed_files)
    source_texts.extend(
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    keywords: list[str] = []
    normalized_keywords: set[str] = set()
    for text in source_texts:
        for raw_term in ORACLE_IDENTIFIER_RE.findall(text):
            term = oracle_keyword_term(raw_term)
            if term is None:
                continue
            normalized = compact_alnum(term)
            if normalized in normalized_keywords:
                continue
            keywords.append(term)
            normalized_keywords.add(normalized)
            if len(keywords) >= ORACLE_KEYWORD_LIMIT:
                return {
                    "kind": "oracle-first-bad-diff",
                    "first_bad_sha": first_bad_sha,
                    "first_bad_subject": subject,
                    "changed_files": changed_files[:12],
                    "keywords": keywords,
                    "keyword_limit": ORACLE_KEYWORD_LIMIT,
                }
    return {
        "kind": "oracle-first-bad-diff",
        "first_bad_sha": first_bad_sha,
        "first_bad_subject": subject,
        "changed_files": changed_files[:12],
        "keywords": keywords,
        "keyword_limit": ORACLE_KEYWORD_LIMIT,
    }


def oracle_first_bad_keywords(repo: Path, first_bad_sha: str) -> list[str]:
    return list(oracle_first_bad_keyword_derivation(repo, first_bad_sha)["keywords"])


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
    observation_bad_similarity: float = 0.0
    observation_good_similarity: float = 0.0
    observation_posterior_evidence: float = 0.0
    observation_conditioned_posterior_mass: float = 0.0
    weak_relevance_penalty: float = 0.0
    evidence: list[str] | None = None
    feedback_bias: float = 0.0
    features: list[str] | None = None
    diff_mode: str = "parent"
    diff_extraction: str = "raw"
    diff_base_sha: str | None = None
    diff_summary: str = ""
    causal_evidence: dict[str, object] | None = None


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
    build_failure: dict[str, object] | None = None


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str
    model_name: str
    observation_prompt_mode: str = "legacy"


@dataclass(frozen=True)
class AdaptiveTopKConfig:
    threshold: int
    large_k: int
    small_k: int

    def payload(self) -> dict[str, int]:
        return {
            "threshold": self.threshold,
            "large_k": self.large_k,
            "small_k": self.small_k,
        }


@dataclass(frozen=True)
class ConfidenceAdaptiveFrontierConfig:
    """Choose a fixed-size model frontier from pre-model semantic confidence."""

    threshold: float

    def payload(self) -> dict[str, float]:
        return {"threshold": self.threshold}


@dataclass(frozen=True)
class ObservationConditionedPosteriorConfig:
    """Convert runner good/bad evidence into a mechanism-aware posterior update."""

    bad_strength: float = DEFAULT_OBSERVATION_POSTERIOR_BAD_STRENGTH
    good_strength: float = DEFAULT_OBSERVATION_POSTERIOR_GOOD_STRENGTH
    component_weight: float = DEFAULT_OBSERVATION_POSTERIOR_COMPONENT_WEIGHT

    def payload(self) -> dict[str, float]:
        return {
            "bad_strength": self.bad_strength,
            "good_strength": self.good_strength,
            "component_weight": self.component_weight,
        }


@dataclass(frozen=True)
class ModelFrontierDecision:
    selected_shas: list[str]
    configured_frontier: str
    effective_frontier: str
    confidence: float | None
    threshold: float | None
    reason: str
    top_semantic_candidates: list[dict[str, object]]
    observation_count: int
    role_assignments: list[dict[str, object]]


@dataclass
class CommitMetadata:
    sha: str
    subject: str
    body: str
    changed_files: list[str]


class NoProgressCandidateError(RuntimeError):
    """Raised when every selectable candidate would leave the interval unchanged."""


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


def model_cache_path(
    issue_id: str,
    model_name: str,
    scoring_version: str = MODEL_SCORING_VERSION,
    namespace: str | None = None,
) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name)
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", scoring_version)
    safe_namespace = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace or "").strip("_")
    suffix = f"-ns-{safe_namespace}" if safe_namespace else ""
    return DEFAULT_MODEL_CACHE_DIR / f"{issue_id}-{safe_model}-{safe_version}{suffix}.json"


def resolved_model_scoring_version(observation_prompt_mode: str = "legacy") -> str:
    if observation_prompt_mode == "legacy":
        return MODEL_SCORING_VERSION
    safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", observation_prompt_mode)
    return f"{MODEL_SCORING_VERSION}-obs-{safe_mode}"


def model_score_cache_key(
    sha: str,
    diff_mode: str = "parent",
    diff_base_sha: str | None = None,
    diff_extraction: str = "raw",
    score_context: str | None = None,
) -> str:
    if diff_extraction not in {"raw", "llm", "causal-llm", "causal-llm-impl"}:
        raise ValueError(f"unsupported diff_extraction: {diff_extraction}")
    if diff_mode == "parent":
        base_key = sha
    elif diff_mode == "last-tested" and diff_base_sha:
        base_key = f"{sha}|diff:last-tested-candidate-files|base:{diff_base_sha}"
    else:
        base_key = f"{sha}|diff:{diff_mode}"
    if diff_extraction != "raw":
        base_key = f"{base_key}|extract:{diff_extraction}-{diff_extraction_version(diff_extraction)}"
    if score_context:
        safe_context = hashlib.sha256(score_context.encode("utf-8")).hexdigest()[:16]
        base_key = f"{base_key}|context:{safe_context}"
    return base_key


def diff_extraction_version(diff_extraction: str) -> str:
    if diff_extraction == "llm":
        return DIFF_EXTRACTION_VERSION
    if diff_extraction == "causal-llm":
        return CAUSAL_DIFF_EXTRACTION_VERSION
    if diff_extraction == "causal-llm-impl":
        return CAUSAL_IMPL_DIFF_EXTRACTION_VERSION
    if diff_extraction == "raw":
        return "raw"
    raise ValueError(f"unsupported diff_extraction: {diff_extraction}")


def adaptive_top_k_config_from_args(args: argparse.Namespace) -> AdaptiveTopKConfig | None:
    values = (
        getattr(args, "adaptive_top_k_threshold", None),
        getattr(args, "adaptive_top_k_large", None),
        getattr(args, "adaptive_top_k_small", None),
    )
    if values == (None, None, None):
        return None
    if any(value is None for value in values):
        raise ValueError(
            "adaptive top-k requires --adaptive-top-k-threshold, "
            "--adaptive-top-k-large, and --adaptive-top-k-small together"
        )
    threshold, large_k, small_k = (int(value) for value in values)
    if threshold < 0:
        raise ValueError("adaptive top-k threshold must be non-negative")
    if large_k <= 0 or small_k <= 0:
        raise ValueError("adaptive top-k values must be positive")
    return AdaptiveTopKConfig(threshold=threshold, large_k=large_k, small_k=small_k)


def confidence_adaptive_frontier_config_from_args(args: argparse.Namespace) -> ConfidenceAdaptiveFrontierConfig | None:
    threshold = getattr(args, "confidence_adaptive_frontier_threshold", None)
    if threshold is None:
        return None
    threshold = float(threshold)
    if threshold < 0.0 or threshold > 1.0:
        raise ValueError("confidence-adaptive frontier threshold must be between 0 and 1")
    return ConfidenceAdaptiveFrontierConfig(threshold=threshold)


def validate_confidence_adaptive_frontier(
    configured_frontier: str,
    confidence_config: ConfidenceAdaptiveFrontierConfig | None,
) -> None:
    if confidence_config is not None and configured_frontier != "topk":
        raise ValueError("confidence-adaptive frontier requires --model-frontier topk")


def observation_conditioned_posterior_config_from_args(
    args: argparse.Namespace,
) -> ObservationConditionedPosteriorConfig | None:
    if not getattr(args, "observation_conditioned_posterior", False):
        return None
    return ObservationConditionedPosteriorConfig()


def validate_observation_conditioned_posterior(
    *,
    scorer: str,
    search_policy: str,
    configured_frontier: str,
    model_top_k: int | None,
    model_diff_mode: str,
    model_diff_extraction: str,
    posterior_config: ObservationConditionedPosteriorConfig | None,
    adaptive_config: AdaptiveTopKConfig | None,
    confidence_config: ConfidenceAdaptiveFrontierConfig | None,
) -> None:
    if posterior_config is None:
        return
    if scorer != "model":
        raise ValueError("observation-conditioned posterior requires --scorer model")
    if search_policy != "calibrated-posterior":
        raise ValueError("observation-conditioned posterior requires --search-policy calibrated-posterior")
    if configured_frontier != "topk":
        raise ValueError("observation-conditioned posterior requires --model-frontier topk")
    if model_top_k not in {3, 12}:
        raise ValueError("observation-conditioned posterior requires fixed --model-top-k 3 or 12")
    if model_diff_mode != "parent" or model_diff_extraction != "llm":
        raise ValueError("observation-conditioned posterior requires parent diff with LLM extraction")
    if adaptive_config is not None or confidence_config is not None:
        raise ValueError(
            "observation-conditioned posterior cannot be combined with interval adaptive top-k "
            "or confidence-adaptive frontier"
        )


def effective_model_top_k(
    unresolved_count: int,
    fixed_top_k: int | None,
    adaptive_config: AdaptiveTopKConfig | None = None,
) -> int:
    if unresolved_count <= 0:
        return 0
    if adaptive_config is None:
        requested = fixed_top_k if fixed_top_k is not None else unresolved_count
    elif unresolved_count > adaptive_config.threshold:
        requested = adaptive_config.large_k
    else:
        requested = adaptive_config.small_k
    return min(requested, unresolved_count)


def resolved_model_cache_namespace(
    explicit_namespace: str | None,
    adaptive_config: AdaptiveTopKConfig | None,
    confidence_config: ConfidenceAdaptiveFrontierConfig | None = None,
    observation_posterior_config: ObservationConditionedPosteriorConfig | None = None,
) -> str | None:
    if explicit_namespace:
        return explicit_namespace
    if confidence_config is not None:
        return f"confidence-frontier-{confidence_config.threshold:g}"
    if observation_posterior_config is not None:
        return "observation-posterior-k3"
    if adaptive_config is None:
        return None
    return (
        f"adaptive-{adaptive_config.threshold}-"
        f"{adaptive_config.large_k}-{adaptive_config.small_k}"
    )


def model_score_context_payload(
    unresolved: list[str],
    observations: list[CommitObservation],
    model_top_k: int,
    model_frontier: str,
    model_diff_mode: str,
    model_diff_extraction: str,
    observation_prompt_mode: str,
    confidence_adaptive_frontier: dict[str, float] | None = None,
    observation_conditioned_posterior: dict[str, float] | None = None,
) -> dict[str, object]:
    candidate_digest = hashlib.sha256("\n".join(unresolved).encode("utf-8")).hexdigest()
    observation_payload = [
        {
            "sha": observation.sha,
            "verdict": observation.verdict,
            "summary": observation.summary,
            "features": observation.features,
            "source": observation.source,
            "trace_excerpt": observation.trace_excerpt,
            "log_excerpt": observation.log_excerpt,
            "build_failure": observation.build_failure or {},
        }
        for observation in observations
    ]
    observation_digest = hashlib.sha256(
        json.dumps(observation_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "version": "v1",
        "unresolved_count": len(unresolved),
        "candidate_window_sha256": candidate_digest,
        "observation_sha256": observation_digest,
        "model_top_k": model_top_k,
        "model_frontier": model_frontier,
        "model_diff_mode": model_diff_mode,
        "model_diff_extraction": model_diff_extraction,
        "observation_prompt_mode": observation_prompt_mode,
        "confidence_adaptive_frontier": confidence_adaptive_frontier,
        "observation_conditioned_posterior": observation_conditioned_posterior,
    }


def model_score_context_id(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"v1-{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]}"


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


def commit_is_ancestor(repo: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


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


def git_limited_output(repo: Path, args: list[str], max_chars: int) -> str:
    cmd = ["git", "-C", str(repo), *args]
    # Bound diff extraction at the subprocess pipe. Large transition diffs can
    # span thousands of commits; reading the full diff only to crop it later is
    # too expensive for top-k model scoring.
    max_bytes = max(1, max_chars * 4 + 1024)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while total < max_bytes:
        chunk = process.stdout.read(min(8192, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total >= max_bytes:
        truncated = True
        process.kill()
    _stdout_tail, stderr = process.communicate()
    if not truncated and process.returncode:
        stderr_text = stderr.decode("utf-8", "replace")
        raise subprocess.CalledProcessError(process.returncode, cmd, output=b"".join(chunks), stderr=stderr_text)
    return b"".join(chunks).decode("utf-8", "replace")[:max_chars]


def commit_diff_text(repo: Path, sha: str, max_chars: int = DEFAULT_DIFF_TEXT_MAX_CHARS) -> str:
    return git_limited_output(repo, ["show", "--no-renames", "--format=", "--unified=0", sha], max_chars)


def commit_parent_diff_for_files(
    repo: Path,
    sha: str,
    files: list[str],
    max_chars: int = DEFAULT_DIFF_TEXT_MAX_CHARS,
    max_files: int = TRANSITION_DIFF_FILE_LIMIT,
) -> str:
    scoped_files = [path for path in files if path][:max_files]
    if not scoped_files:
        return ""
    return git_limited_output(
        repo,
        ["show", "--no-renames", "--format=", "--unified=0", sha, "--", *scoped_files],
        max_chars,
    )


def commit_transition_diff_text(
    repo: Path,
    base_sha: str,
    candidate_sha: str,
    max_chars: int = DEFAULT_DIFF_TEXT_MAX_CHARS,
) -> str:
    return git_limited_output(repo, ["diff", "--no-renames", "--unified=0", base_sha, candidate_sha], max_chars)


def commit_transition_changed_files(repo: Path, base_sha: str, candidate_sha: str) -> list[str]:
    output = git(repo, "diff", "--no-renames", "--name-only", base_sha, candidate_sha)
    return [line.strip() for line in output.splitlines() if line.strip()]


def commit_transition_diff_for_files(
    repo: Path,
    base_sha: str,
    candidate_sha: str,
    files: list[str],
    max_chars: int = DEFAULT_DIFF_TEXT_MAX_CHARS,
    max_files: int = TRANSITION_DIFF_FILE_LIMIT,
) -> str:
    scoped_files = [path for path in files if path][:max_files]
    if not scoped_files:
        return ""
    return git_limited_output(
        repo,
        ["diff", "--no-renames", "--unified=0", base_sha, candidate_sha, "--", *scoped_files],
        max_chars,
    )


def parse_unified_diff_hunks(diff: str) -> list[dict[str, object]]:
    """Split a parent diff into file hunks so retrieval is auditable."""
    hunks: list[dict[str, object]] = []
    current_path = ""
    current_lines: list[str] = []
    current_header = ""

    def flush() -> None:
        if current_path and current_lines:
            hunks.append(
                {
                    "path": current_path,
                    "header": current_header,
                    "patch": "\n".join(current_lines).strip(),
                }
            )

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_path = ""
            current_lines = [line]
            current_header = ""
            continue
        if line.startswith("+++ b/"):
            current_path = line[6:]
            current_lines.append(line)
            continue
        if line.startswith("@@ "):
            if current_path and current_lines and current_header:
                flush()
                current_lines = [line]
            else:
                current_lines.append(line)
            current_header = line
            continue
        if current_lines:
            current_lines.append(line)
    flush()
    return hunks


def is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return normalized.startswith("test/") or "/test/" in normalized or "/tests/" in normalized


def causal_hunk_match_reasons(profile: IssueProfile, path: str, hunk_text: str) -> list[str]:
    reasons: list[str] = []
    if path_matches(path, profile.relevant_paths):
        reasons.append("relevant-path")
    if path_matches(path, profile.high_risk_paths):
        reasons.append("high-risk-path")
    lowered = hunk_text.lower()
    keyword_hits = [keyword for keyword in profile.keywords if keyword.lower() in lowered]
    if keyword_hits:
        reasons.append("issue-keyword")
    path_tokens = set(tokenize(path))
    issue_tokens = set(tokenize(" ".join((profile.title, profile.bug_report_summary, *profile.keywords))))
    if path_tokens & issue_tokens:
        reasons.append("issue-path-token")
    return reasons


def select_causal_retrieval_files(
    profile: IssueProfile,
    changed_files: list[str],
    *,
    retrieval_policy: str = "balanced",
) -> list[str]:
    """Choose a bounded, profile-matched path set before reading a parent diff."""
    if retrieval_policy not in {"balanced", "implementation-first"}:
        raise ValueError(f"unsupported causal retrieval policy: {retrieval_policy}")
    issue_tokens = set(tokenize(" ".join((profile.title, profile.bug_report_summary, *profile.keywords))))
    candidates: list[tuple[int, int, str]] = []
    fallback: list[str] = []
    seen: set[str] = set()
    for index, path in enumerate(changed_files):
        if not path or path in seen:
            continue
        seen.add(path)
        fallback.append(path)
        score = 0
        if path_matches(path, profile.relevant_paths):
            score += 8
        if path_matches(path, profile.high_risk_paths):
            score += 4
        if set(tokenize(path)) & issue_tokens:
            score += 2
        if score:
            candidates.append((score, index, path))
    if not candidates:
        candidates = [(0, index, path) for index, path in enumerate(fallback)]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    paths = [path for _score, _index, path in candidates]
    if retrieval_policy == "implementation-first":
        source_paths = [path for path in fallback if not is_test_path(path)]
        test_paths = [path for path in paths if is_test_path(path)]
        paths = source_paths + test_paths
    return paths[:TRANSITION_DIFF_FILE_LIMIT]


def causal_hunk_symbols(hunk_text: str) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for candidate in ORACLE_IDENTIFIER_RE.findall(hunk_text):
        normalized = compact_alnum(candidate)
        if len(normalized) < 5 or normalized in seen:
            continue
        if not ("::" in candidate or any(char.isupper() for char in candidate) or "_" in candidate):
            continue
        seen.add(normalized)
        symbols.append(candidate)
        if len(symbols) >= 8:
            break
    return symbols


def function_context_at_commit(repo: Path, sha: str, path: str, symbol: str) -> str:
    normalized_symbol = symbol.split("(", 1)[0].strip()
    if not normalized_symbol:
        return ""
    try:
        return git_limited_output(
            repo,
            ["show", f"{sha}:{path}"],
            CAUSAL_DIFF_MAX_FUNCTION_CONTEXT_CHARS * 8,
        )
    except subprocess.CalledProcessError:
        return ""


def extract_function_context(source: str, symbol: str) -> str:
    if not source:
        return ""
    simple_symbol = symbol.rsplit("::", 1)[-1]
    match = re.search(rf"^.*\b{re.escape(simple_symbol)}\s*\([^\n]*\)", source, re.MULTILINE)
    if match is None:
        return ""
    start = match.start()
    return source[start : start + CAUSAL_DIFF_MAX_FUNCTION_CONTEXT_CHARS].strip()


def retrieve_causal_diff_evidence(
    repo: Path,
    profile: IssueProfile,
    item: dict,
    *,
    retrieval_policy: str = "balanced",
) -> dict[str, object]:
    """Retrieve issue-matched parent-diff hunks and local symbol context."""
    if retrieval_policy not in {"balanced", "implementation-first"}:
        raise ValueError(f"unsupported causal retrieval policy: {retrieval_policy}")
    raw_diff = str(item.get("diff", ""))
    if not raw_diff:
        selected_files = select_causal_retrieval_files(
            profile,
            list(item.get("files", [])),
            retrieval_policy=retrieval_policy,
        )
        raw_diff = commit_parent_diff_for_files(
            repo,
            str(item["sha"]),
            selected_files,
            max_chars=DIFF_EXTRACTION_MAX_INPUT_CHARS,
        )
    scored_hunks: list[dict[str, object]] = []
    for hunk in parse_unified_diff_hunks(raw_diff):
        path = str(hunk["path"])
        patch = str(hunk["patch"])
        reasons = causal_hunk_match_reasons(profile, path, patch)
        symbols = causal_hunk_symbols(patch)
        score = 4 * ("relevant-path" in reasons) + 2 * ("issue-keyword" in reasons) + len(symbols)
        scored_hunks.append(
            {
                **hunk,
                "match_reasons": reasons,
                "symbols": symbols,
                "retrieval_score": score,
                "source_kind": "test" if is_test_path(path) else "implementation",
            }
        )

    scored_hunks.sort(
        key=lambda hunk: (
            int(hunk["retrieval_score"]),
            bool(hunk["match_reasons"]),
            -len(str(hunk["patch"])),
        ),
        reverse=True,
    )
    selected = [hunk for hunk in scored_hunks if hunk["match_reasons"]]
    if not selected:
        selected = scored_hunks[:CAUSAL_DIFF_MAX_SELECTED_HUNKS]
    if retrieval_policy == "implementation-first":
        implementation_hunks = [
            hunk for hunk in scored_hunks if hunk["source_kind"] == "implementation"
        ]
        test_hunks = [
            hunk
            for hunk in selected
            if hunk["source_kind"] == "test"
        ]
        # Keep test-only commits observable without letting their evidence crowd
        # out source changes that can explain a regression mechanism.
        selected = (implementation_hunks + test_hunks)[:CAUSAL_DIFF_MAX_SELECTED_HUNKS]
        test_fallback_used = not implementation_hunks and bool(test_hunks)
    else:
        selected = selected[:CAUSAL_DIFF_MAX_SELECTED_HUNKS]
        test_fallback_used = False

    selected_hunks = [
        {
            "path": hunk["path"],
            "header": hunk["header"],
            "patch": str(hunk["patch"])[:CAUSAL_DIFF_MAX_HUNK_CHARS],
            "match_reasons": hunk["match_reasons"],
            "symbols": hunk["symbols"],
            "source_kind": hunk["source_kind"],
        }
        for hunk in selected
    ]
    contexts: list[dict[str, str]] = []
    seen_contexts: set[tuple[str, str]] = set()
    for hunk in selected_hunks:
        for symbol in hunk["symbols"]:
            key = (str(hunk["path"]), str(symbol))
            if key in seen_contexts:
                continue
            source = function_context_at_commit(repo, str(item["sha"]), key[0], key[1])
            context = extract_function_context(source, key[1])
            if not context:
                continue
            seen_contexts.add(key)
            contexts.append({"path": key[0], "symbol": key[1], "context": context})
            if len(contexts) >= CAUSAL_DIFF_MAX_FUNCTION_CONTEXTS:
                break
        if len(contexts) >= CAUSAL_DIFF_MAX_FUNCTION_CONTEXTS:
            break
    return {
        "selected_files": sorted({str(hunk["path"]) for hunk in selected_hunks}),
        "selected_hunks": selected_hunks,
        "retrieval_policy": retrieval_policy,
        "test_fallback_used": test_fallback_used,
        "function_contexts": contexts,
        "omitted_hunk_count": max(0, len(scored_hunks) - len(selected_hunks)),
        "raw_diff_chars": len(raw_diff),
        "raw_diff_truncated": len(raw_diff) >= DIFF_EXTRACTION_MAX_INPUT_CHARS,
    }


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
    if heuristic_version == "neutral":
        # This control intentionally provides no semantic ranking signal.
        return 0.05, ["neutral heuristic: no semantic guidance"]
    if heuristic_version in {"general", "none"}:
        return score_semantics_tuned(
            replace(profile, keywords=effective_heuristic_keywords(profile, heuristic_version)),
            subject,
            body,
            files,
            diff,
        )
    if heuristic_version == "oracle-first-bad":
        # The selection profile already contains the explicitly derived terms.
        return score_semantics_tuned(profile, subject, body, files, diff)
    raise ValueError(f"unsupported heuristic version: {heuristic_version}")


def effective_heuristic_keywords(
    profile: IssueProfile,
    heuristic_version: str,
    oracle_keywords: list[str] | None = None,
) -> list[str]:
    if heuristic_version == "general":
        return list(GENERAL_KEYWORDS)
    if heuristic_version in {"none", "neutral"}:
        return []
    if heuristic_version == "oracle-first-bad":
        if oracle_keywords is None:
            raise ValueError("oracle-first-bad heuristic requires derived first-bad keywords")
        return list(oracle_keywords)
    return list(profile.keywords)


def heuristic_selection_profile(
    profile: IssueProfile,
    heuristic_version: str,
    oracle_keywords: list[str] | None = None,
) -> IssueProfile:
    """Remove manually authored issue signals for the profile-free control."""
    if heuristic_version == "neutral":
        return replace(profile, keywords=[], relevant_paths=[], high_risk_paths=[])
    if heuristic_version == "oracle-first-bad":
        return replace(profile, keywords=effective_heuristic_keywords(profile, heuristic_version, oracle_keywords))
    return profile


def oracle_first_bad_sha_from_args(repo: Path, args: argparse.Namespace) -> str | None:
    if getattr(args, "heuristic_version", "tuned") != "oracle-first-bad":
        return None
    raw_sha = getattr(args, "oracle_first_bad_sha", None)
    if not raw_sha:
        raise ValueError("oracle-first-bad heuristic requires --oracle-first-bad-sha")
    return git(repo, "rev-parse", "--verify", str(raw_sha)).strip()


def resolved_heuristic_selection_profile(
    repo: Path,
    profile: IssueProfile,
    heuristic_version: str,
    oracle_first_bad_sha: str | None,
) -> tuple[IssueProfile, dict[str, object] | None]:
    if heuristic_version != "oracle-first-bad":
        return heuristic_selection_profile(profile, heuristic_version), None
    if oracle_first_bad_sha is None:
        raise ValueError("oracle-first-bad heuristic requires a resolved first-bad SHA")
    derivation = oracle_first_bad_keyword_derivation(repo, oracle_first_bad_sha)
    keywords = [str(keyword) for keyword in derivation["keywords"]]
    if not keywords:
        raise ValueError(f"oracle-first-bad derivation produced no keywords for {oracle_first_bad_sha}")
    return heuristic_selection_profile(profile, heuristic_version, keywords), derivation


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


def compact_diff_for_prompt(
    files: list[str],
    diff: str,
    max_chars: int = 4000,
    diff_mode: str = "parent",
    base_sha: str | None = None,
    candidate_sha: str | None = None,
    diff_extraction: str = "raw",
    diff_summary: str = "",
) -> str:
    file_block = "\n".join(files[:20])
    metadata = [f"Diff mode: {diff_mode}"]
    if base_sha:
        metadata.append(f"Base commit: {base_sha}")
    if candidate_sha:
        metadata.append(f"Candidate commit: {candidate_sha}")
    if diff_extraction != "raw":
        metadata.append(f"Diff extraction: {diff_extraction}")
        summary_text = diff_summary.strip() or "<empty extracted diff evidence>"
        text = "\n".join(metadata) + f"\nChanged files:\n{file_block}\n\nExtracted diff evidence:\n{summary_text}"
    else:
        text = "\n".join(metadata) + f"\nChanged files:\n{file_block}\n\nDiff:\n{diff}"
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
    verdicts: set[str] | None = None,
) -> list[tuple[CommitObservation, int]]:
    if verdicts is None:
        verdicts = {"bad"}
    bad_runner_observations = [
        obs
        for obs in observations
        if obs.verdict in verdicts
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
    is_skip = observation.verdict == "skip"
    observation_label = (
        f"Observed skipped build, not a bug reproduction: {observation.sha}"
        if is_skip
        else f"Observed bad commit: {observation.sha}"
    )
    evidence_label = "Primary build/configuration failure evidence" if is_skip else "Primary crash/assertion evidence"
    structured_failure = ""
    if is_skip and observation.build_failure:
        fields = []
        for key in (
            "phase",
            "failed_target",
            "failed_source",
            "failed_header",
            "missing_include",
            "primary_error",
            "cmake_stack",
        ):
            value = observation.build_failure.get(key)
            if value:
                fields.append(f"{key}: {value}")
        if fields:
            structured_failure = "Structured build failure:\n" + "\n".join(fields) + "\n"
    if trace_only:
        if not trace_excerpt:
            return ""
        if len(trace_excerpt) > TRACE_PROMPT_MAX_CHARS:
            trace_excerpt = trace_excerpt[:TRACE_PROMPT_MAX_CHARS]
        repeat_note = ""
        if repeat_count > 1:
            repeat_note = f"{TRACE_PROMPT_SIGNATURE_SUFFIX}{repeat_count} times]"
        return (
            f"{observation_label}\n"
            f"{structured_failure}"
            f"{evidence_label}{repeat_note}:\n{trace_excerpt}\n"
        )
    if not trace_excerpt and not trace_only:
        trace_excerpt = "\n".join((observation.evidence or [])[:8]).strip()
    if not trace_excerpt and not trace_only and observation.log_excerpt:
        trace_excerpt = observation.log_excerpt.strip()[:1200]
    if len(trace_excerpt) > 1200:
        trace_excerpt = trace_excerpt[:1200]
    return (
        f"{observation_label}\n"
        f"Summary: {observation.summary}\n"
        f"{structured_failure}"
        f"{'Build failure excerpt' if is_skip else 'Trace excerpt'}:\n{trace_excerpt or '<none>'}\n"
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
{compact_diff_for_prompt(
    item['files'],
    item.get('diff', ''),
    diff_mode=item.get('diff_mode', 'parent'),
    base_sha=item.get('diff_base_sha'),
    candidate_sha=item.get('sha'),
    diff_extraction=item.get('diff_extraction', 'raw'),
    diff_summary=item.get('diff_summary', ''),
) if item.get('diff') or item.get('diff_summary') else summarize_files_for_prompt(item['files'])}
"""
        )
    trace_only = observation_prompt_mode == "trace-only"
    recent_bad_observations = recent_crash_observations_for_prompt(
        observations or [],
        limit=2,
        trace_only=trace_only,
        verdicts={"bad"},
    )
    recent_skip_observations = recent_crash_observations_for_prompt(
        observations or [],
        limit=2,
        trace_only=trace_only,
        verdicts={"skip"},
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
    observed_skip_context = ""
    if recent_skip_observations:
        formatted_skip_observations = [
            format_observation_for_prompt(obs, trace_only=trace_only, repeat_count=count)
            for obs, count in recent_skip_observations
        ]
        formatted_skip_observations = [item for item in formatted_skip_observations if item.strip()]
    else:
        formatted_skip_observations = []
    if formatted_skip_observations:
        observed_skip_context = (
            "Observed skipped build/configuration failures:\n\n"
            + "\n".join(formatted_skip_observations)
        )
    observation_context = "\n\n".join(
        item for item in (observed_crash_context, observed_skip_context) if item
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

{observation_context if observation_context else ''}

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
- If observed skipped build/configuration failures are provided, do not treat them as target-bug reproductions.
- Use skip evidence mainly to reduce build_success_prob for candidates likely to hit the same build/configuration failure.
- Only let skip evidence increase semantic suspicion if the skip itself is a compiler crash/assertion that matches the issue mechanism.
- Treat the candidate as high first-bad risk when its diff changes the exact file, function, class, pass, checker, or component named by the assertion, stack trace, running pass, or issue reproducer.
- Treat the candidate as high first-bad risk when it introduces, enables, or rewires the specific mechanism in the report, such as token collection, constexpr evaluation, module/PCH serialization, codegen debug info, AST matching, loop/vector analysis, or target lowering.
- Treat a build-skip candidate as semantically high risk only when the build error itself points to the changed file/function/component and that failure is plausibly the regression mechanism; otherwise it is mainly build risk.
- Treat the candidate as lower first-bad risk when it only shares a broad subsystem, path prefix, or keyword with the issue but the diff mechanism does not explain the observed crash.
- Treat NFC, formatting, documentation, test-only, release, version-bump, and unrelated build-system commits as low semantic risk unless the observed failure is specifically in that build/test/configuration path.
- semantic_score should be highest for commits that most directly match the reported bug mechanism, stack trace terms, relevant paths, or likely faulty optimization logic.
- Lower the score when a commit only touches the same subsystem but does not strongly match the actual failure mechanism.
- build_success_prob should be lower when the commit looks likely to fail build or be unstable to test.
- features should be normalized reusable hints for later similarity matching.
- Prefer substantive mechanisms over superficial keyword overlap.
Only output JSON.
""".strip()


def _remove_trailing_json_commas(candidate: str) -> str:
    """Remove commas immediately before a JSON closing delimiter outside strings."""
    repaired: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(candidate):
        char = candidate[index]
        if in_string:
            repaired.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            repaired.append(char)
            index += 1
            continue
        if char == ",":
            next_index = index + 1
            while next_index < len(candidate) and candidate[next_index].isspace():
                next_index += 1
            if next_index < len(candidate) and candidate[next_index] in "}]":
                index += 1
                continue
        repaired.append(char)
        index += 1
    return "".join(repaired)


def parse_model_json_value(content: str) -> object:
    def parse_with_control_char_escape(candidate: str):
        candidates = [candidate]
        repaired = _remove_trailing_json_commas(candidate)
        if repaired != candidate:
            candidates.append(repaired)
        last_error: json.JSONDecodeError | None = None
        for current in candidates:
            try:
                return json.loads(current)
            except json.JSONDecodeError as exc:
                last_error = exc
                if "Invalid control character" not in str(exc):
                    continue
                escaped = re.sub(
                    r"(?<!\\)[\x00-\x08\x0b-\x0c\x0e-\x1f]",
                    lambda match: f"\\u{ord(match.group(0)):04x}",
                    current,
                )
                try:
                    return json.loads(escaped)
                except json.JSONDecodeError as escaped_error:
                    last_error = escaped_error
        assert last_error is not None
        raise last_error

    stripped = content.strip()
    if not stripped:
        raise ValueError("model returned empty content")
    try:
        return parse_with_control_char_escape(stripped)
    except json.JSONDecodeError as direct_error:
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
        if fence_match:
            return parse_with_control_char_escape(fence_match.group(1).strip())
        array_match = re.search(r"(\[\s*\{.*\}\s*\])", stripped, re.DOTALL)
        if array_match:
            return parse_with_control_char_escape(array_match.group(1))
        object_match = re.search(r"(\{\s*.*\})", stripped, re.DOTALL)
        if object_match:
            return parse_with_control_char_escape(object_match.group(1))
        raise direct_error


def parse_model_json_payload(content: str) -> list[dict]:
    payload = parse_model_json_value(content)
    if not isinstance(payload, list):
        raise ValueError("model payload is not a JSON array")
    return payload


def parse_model_json_object(content: str) -> dict:
    payload = parse_model_json_value(content)
    if not isinstance(payload, dict):
        raise ValueError("model payload is not a JSON object")
    return payload


def build_diff_extraction_prompt(profile: IssueProfile, item: dict) -> str:
    raw_diff = str(item.get("diff", ""))

    def render(diff_excerpt: str, truncated: bool) -> str:
        changed_files = "\n".join(str(path) for path in item.get("files", [])[:30])
        return f"""
You are compressing Git diff evidence for a later LLVM bug-bisect scoring model.

Task:
- Extract general, reusable change evidence from this diff.
- Keep mechanisms, touched symbols, file-level intent, and build-risk signals.
- Do not decide the final score and do not overfit only to the issue text.
- Omit cosmetic hunks, generated noise, and repetitive context.

Issue context:
- id: {profile.issue_id}
- title: {profile.title}
- summary: {profile.bug_report_summary}
- relevant paths: {', '.join(profile.relevant_paths)}
- keywords: {', '.join(profile.keywords)}

Commit:
- sha: {item.get('sha', '')}
- subject: {item.get('subject', '')}
- diff mode: {item.get('diff_mode', 'parent')}
- base commit: {item.get('diff_base_sha', '')}
- candidate commit: {item.get('sha', '')}

Changed files:
{changed_files or '<none>'}

Diff excerpt:
{diff_excerpt or '<empty diff>'}

Return strict JSON with this schema:
{{
  "summary": "one paragraph summary of the substantive code change",
  "mechanisms": ["mechanism or algorithm touched"],
  "touched_symbols": ["function/class/pass/checker names when visible"],
  "risk_relevance": ["signals that may explain a compiler crash/regression"],
  "build_risk": ["signals that may affect build/testability"],
  "raw_diff_truncated": {str(truncated).lower()}
}}
Only output JSON.
""".strip()

    diff_excerpt = raw_diff[:DIFF_EXTRACTION_MAX_INPUT_CHARS]
    prompt = render(diff_excerpt, len(raw_diff) > len(diff_excerpt))
    if len(prompt) <= DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS:
        return prompt

    # A 600k raw-diff allowance is useful for extraction, but the provider has
    # a smaller request limit. Keep an individual fallback prompt below the
    # same budget used by batch planning instead of sending an oversized call.
    empty_prompt = render("", bool(raw_diff))
    available_diff_chars = max(0, DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS - len(empty_prompt))
    return render(raw_diff[:available_diff_chars], len(raw_diff) > available_diff_chars)


def build_diff_extraction_batch_prompt(profile: IssueProfile, items: list[dict]) -> str:
    def build_commit_block(item: dict, diff_excerpt: str, truncated: bool) -> str:
        changed_files = "\n".join(str(path) for path in item.get("files", [])[:30])
        return f"""
Commit:
- sha: {item.get('sha', '')}
- subject: {item.get('subject', '')}
- diff mode: {item.get('diff_mode', 'parent')}
- base commit: {item.get('diff_base_sha', '')}
- candidate commit: {item.get('sha', '')}
- raw diff truncated: {str(truncated).lower()}

Changed files:
{changed_files or '<none>'}

Diff excerpt:
{diff_excerpt or '<empty diff>'}
""".strip()

    commit_blocks = []
    for item in items:
        raw_diff = str(item.get("diff", ""))
        diff_excerpt = raw_diff[:DIFF_EXTRACTION_MAX_INPUT_CHARS]
        commit_blocks.append(
            build_commit_block(item, diff_excerpt, len(raw_diff) > len(diff_excerpt))
        )

    def render(commit_blocks: list[str]) -> str:
        return f"""
You are compressing Git diff evidence for a later LLVM bug-bisect scoring model.

Task:
- Extract general, reusable change evidence for each commit.
- Keep mechanisms, touched symbols, file-level intent, and build-risk signals.
- Do not decide the final score and do not overfit only to the issue text.
- Omit cosmetic hunks, generated noise, and repetitive context.

Issue context:
- id: {profile.issue_id}
- title: {profile.title}
- summary: {profile.bug_report_summary}
- relevant paths: {', '.join(profile.relevant_paths)}
- keywords: {', '.join(profile.keywords)}

Candidate commits:

{chr(10).join(commit_blocks)}

Return strict JSON as an array. One object per commit, with this schema:
[
  {{
    "sha": "<commit sha>",
    "summary": "one paragraph summary of the substantive code change",
    "mechanisms": ["mechanism or algorithm touched"],
    "touched_symbols": ["function/class/pass/checker names when visible"],
    "risk_relevance": ["signals that may explain a compiler crash/regression"],
    "build_risk": ["signals that may affect build/testability"],
    "raw_diff_truncated": <true or false>
  }}
]
Only output JSON.
""".strip()

    prompt = render(commit_blocks)
    if len(items) != 1 or len(prompt) <= DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS:
        return prompt

    # `plan_diff_extraction_batches()` can split multi-item prompts, but it
    # cannot split one large diff. Preserve the batch JSON-array schema while
    # shrinking the individual excerpt to the same provider request budget.
    item = items[0]
    raw_diff = str(item.get("diff", ""))
    empty_prompt = render([build_commit_block(item, "", bool(raw_diff))])
    available_diff_chars = max(0, DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS - len(empty_prompt))
    return render(
        [
            build_commit_block(
                item,
                raw_diff[:available_diff_chars],
                len(raw_diff) > available_diff_chars,
            )
        ]
    )


def plan_diff_extraction_batches(
    profile: IssueProfile,
    items: list[dict],
    batch_size: int = DEFAULT_DIFF_EXTRACTION_BATCH_SIZE,
    max_prompt_chars: int = DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS,
) -> list[list[dict]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars must be positive")
    batches: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        candidate = [*current, item]
        if current and (
            len(candidate) > batch_size
            or len(build_diff_extraction_batch_prompt(profile, candidate)) > max_prompt_chars
        ):
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def format_extracted_diff_evidence(payload: dict) -> str:
    fields = []
    summary = str(payload.get("summary", "")).strip()
    if summary:
        fields.append(f"Summary: {summary}")
    for label, key in (
        ("Mechanisms", "mechanisms"),
        ("Touched symbols", "touched_symbols"),
        ("Risk relevance", "risk_relevance"),
        ("Build risk", "build_risk"),
    ):
        values = [str(value).strip() for value in payload.get(key, []) if str(value).strip()]
        if values:
            fields.append(f"{label}: " + "; ".join(values[:8]))
    if payload.get("raw_diff_truncated"):
        fields.append("Raw diff truncated before extraction: yes")
    return "\n".join(fields).strip() or "Summary: model returned no substantive diff evidence."


def causal_retrieval_prompt_block(retrieval: dict[str, object]) -> str:
    hunk_blocks = []
    for hunk in retrieval.get("selected_hunks", []):
        hunk_blocks.append(
            "\n".join(
                [
                    f"File: {hunk.get('path', '')}",
                    f"Source kind: {hunk.get('source_kind', 'implementation')}",
                    f"Retrieval reasons: {', '.join(hunk.get('match_reasons', [])) or '<fallback coverage>'}",
                    f"Visible symbols: {', '.join(hunk.get('symbols', [])) or '<none>'}",
                    f"Patch:\n{hunk.get('patch', '') or '<empty>'}",
                ]
            )
        )
    context_blocks = [
        "\n".join(
            [
                f"File: {context.get('path', '')}",
                f"Symbol: {context.get('symbol', '')}",
                f"Context:\n{context.get('context', '')}",
            ]
        )
        for context in retrieval.get("function_contexts", [])
    ]
    return "\n\n".join(
        [
            "Retrieved hunks:\n" + ("\n\n".join(hunk_blocks) or "<none>"),
            "Function context:\n" + ("\n\n".join(context_blocks) or "<none available>"),
            "Coverage: "
            f"selected_files={len(retrieval.get('selected_files', []))}, "
            f"selected_hunks={len(retrieval.get('selected_hunks', []))}, "
            f"omitted_hunks={retrieval.get('omitted_hunk_count', 0)}, "
            f"raw_diff_chars={retrieval.get('raw_diff_chars', 0)}, "
            f"raw_diff_truncated={str(bool(retrieval.get('raw_diff_truncated'))).lower()}",
            f"retrieval_policy={retrieval.get('retrieval_policy', 'balanced')}, "
            f"test_fallback_used={str(bool(retrieval.get('test_fallback_used'))).lower()}",
        ]
    )


def build_causal_diff_extraction_prompt(profile: IssueProfile, item: dict) -> str:
    retrieval = item.get("causal_retrieval") or {}
    return f"""
You are producing structured causal evidence for an LLVM bug-bisect candidate.

Task:
- Analyze only the retrieved diff hunks and function context below, not a raw diff prefix.
- Identify the changed symbols and behavioral or mechanism change.
- Explicitly link the change to the issue assertion, stack trace, reproducer, pass, or subsystem when evidence supports it.
- State confidence from 0.0 to 1.0. Low confidence is valid when the retrieved evidence is insufficient.
- Do not choose the next bisect commit and do not infer facts absent from the retrieved evidence.

Issue context:
- id: {profile.issue_id}
- title: {profile.title}
- summary: {profile.bug_report_summary}
- relevant paths: {', '.join(profile.relevant_paths)}
- keywords: {', '.join(profile.keywords)}

Commit:
- sha: {item.get('sha', '')}
- subject: {item.get('subject', '')}
- diff mode: {item.get('diff_mode', 'parent')}

{causal_retrieval_prompt_block(retrieval)}

Return strict JSON with this schema:
{{
  "summary": "one-paragraph description of the substantive change",
  "changed_symbols": ["function/class/pass/checker names visible in evidence"],
  "behavioral_change": ["specific algorithm, invariant, or control-flow change"],
  "issue_link": {{
    "assertion_or_trace": ["matching assertion/stack/diagnostic terms"],
    "reproducer": ["matching reproducer behavior or input property"],
    "pass_or_subsystem": ["LLVM pass/subsystem involved"],
    "explanation": "why this change could or could not cause the issue"
  }},
  "confidence": 0.0,
  "build_risk": ["build or testability signals"]
}}
Only output JSON.
""".strip()


def build_causal_diff_extraction_batch_prompt(profile: IssueProfile, items: list[dict]) -> str:
    blocks = []
    for item in items:
        blocks.append(
            "\n".join(
                [
                    "Commit:",
                    f"- sha: {item.get('sha', '')}",
                    f"- subject: {item.get('subject', '')}",
                    causal_retrieval_prompt_block(item.get("causal_retrieval") or {}),
                ]
            )
        )
    return f"""
You are producing structured causal evidence for LLVM bug-bisect candidates.

For each candidate, analyze only the retrieved hunks and function context. State
changed symbols, behavioral change, an evidence-backed link to the issue, and a
0.0-to-1.0 confidence. Do not choose the next commit or invent unavailable facts.

Issue context:
- id: {profile.issue_id}
- title: {profile.title}
- summary: {profile.bug_report_summary}
- relevant paths: {', '.join(profile.relevant_paths)}
- keywords: {', '.join(profile.keywords)}

Candidates:

{chr(10).join(blocks)}

Return strict JSON as an array. One object per commit, with this schema:
[
  {{
    "sha": "<commit sha>",
    "summary": "one-paragraph description of the substantive change",
    "changed_symbols": ["function/class/pass/checker names"],
    "behavioral_change": ["specific algorithm or invariant change"],
    "issue_link": {{
      "assertion_or_trace": ["matching assertion/stack/diagnostic terms"],
      "reproducer": ["matching reproducer behavior"],
      "pass_or_subsystem": ["LLVM pass/subsystem"],
      "explanation": "why the change could or could not cause the issue"
    }},
    "confidence": 0.0,
    "build_risk": ["build/testability signals"]
  }}
]
Only output JSON.
""".strip()


def normalized_string_list(value: object, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        normalized = compact_alnum(text)
        if not text or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def normalize_causal_diff_evidence(item: dict, payload: dict) -> dict[str, object]:
    raw_issue_link = payload.get("issue_link")
    issue_link = raw_issue_link if isinstance(raw_issue_link, dict) else {}
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    retrieval = item.get("causal_retrieval")
    if not isinstance(retrieval, dict):
        retrieval = {}
    return {
        "summary": str(payload.get("summary", "")).strip(),
        "changed_symbols": normalized_string_list(payload.get("changed_symbols")),
        "behavioral_change": normalized_string_list(payload.get("behavioral_change")),
        "issue_link": {
            "assertion_or_trace": normalized_string_list(issue_link.get("assertion_or_trace")),
            "reproducer": normalized_string_list(issue_link.get("reproducer")),
            "pass_or_subsystem": normalized_string_list(issue_link.get("pass_or_subsystem")),
            "explanation": str(issue_link.get("explanation", "")).strip(),
        },
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "build_risk": normalized_string_list(payload.get("build_risk")),
        "retrieval": retrieval,
    }


def causal_evidence_features(payload: dict[str, object]) -> list[str]:
    features: set[str] = set()
    for path in payload.get("retrieval", {}).get("selected_files", []):
        features.add(f"path:{stem_path(str(path))}")
    issue_link = payload.get("issue_link", {})
    feature_values = [
        *payload.get("changed_symbols", []),
        *payload.get("behavioral_change", []),
        *issue_link.get("assertion_or_trace", []),
        *issue_link.get("reproducer", []),
        *issue_link.get("pass_or_subsystem", []),
    ]
    for value in feature_values:
        normalized = normalize_token(str(value))
        if normalized:
            features.add(f"term:{normalized}")
    return sorted(features)


def format_causal_diff_evidence(payload: dict[str, object]) -> str:
    fields = []
    if payload.get("summary"):
        fields.append(f"Summary: {payload['summary']}")
    for label, key in (("Changed symbols", "changed_symbols"), ("Behavioral change", "behavioral_change")):
        values = payload.get(key, [])
        if values:
            fields.append(f"{label}: " + "; ".join(values))
    issue_link = payload.get("issue_link", {})
    for label, key in (
        ("Assertion or trace link", "assertion_or_trace"),
        ("Reproducer link", "reproducer"),
        ("Pass or subsystem link", "pass_or_subsystem"),
    ):
        values = issue_link.get(key, [])
        if values:
            fields.append(f"{label}: " + "; ".join(values))
    if issue_link.get("explanation"):
        fields.append(f"Causal explanation: {issue_link['explanation']}")
    fields.append(f"Causal confidence: {float(payload.get('confidence', 0.0)):.2f}")
    if payload.get("build_risk"):
        fields.append("Build risk: " + "; ".join(payload["build_risk"]))
    return "\n".join(fields).strip() or "Summary: model returned no causal diff evidence."


def normalize_model_usage(usage: object) -> dict[str, int] | None:
    if usage is None:
        return None

    def value(name: str) -> int:
        if isinstance(usage, dict):
            raw = usage.get(name, 0)
        else:
            raw = getattr(usage, name, 0)
        return int(raw or 0)

    prompt_tokens = value("prompt_tokens")
    completion_tokens = value("completion_tokens")
    total_tokens = value("total_tokens")
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def record_model_usage(summary: dict[str, object], kind: str, response_or_usage: object) -> None:
    usage_source = getattr(response_or_usage, "usage", response_or_usage)
    usage = normalize_model_usage(usage_source)
    if usage is None:
        return
    summary["requests"] = int(summary.get("requests", 0)) + 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        summary[key] = int(summary.get(key, 0)) + usage[key]

    by_kind = summary.setdefault("by_kind", {})
    if not isinstance(by_kind, dict):
        by_kind = {}
        summary["by_kind"] = by_kind
    kind_summary = by_kind.setdefault(
        kind,
        {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    )
    kind_summary["requests"] = int(kind_summary.get("requests", 0)) + 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        kind_summary[key] = int(kind_summary.get(key, 0)) + usage[key]


def is_transient_model_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


def model_completion_with_retry(client, config: ModelConfig, prompt: str, request_kind: str):
    for attempt in range(1, DEFAULT_MODEL_REQUEST_RETRIES + 1):
        try:
            return client.chat.completions.create(
                model=config.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
        except Exception as exc:
            if not is_transient_model_error(exc) or attempt == DEFAULT_MODEL_REQUEST_RETRIES:
                raise
            delay = DEFAULT_MODEL_RETRY_BACKOFF_SECONDS * attempt
            log_progress(
                f"{request_kind} request failed ({exc}); retry {attempt}/{DEFAULT_MODEL_REQUEST_RETRIES - 1} in {delay:.0f}s"
            )
            time.sleep(delay)


def extract_diff_evidence_with_model(
    profile: IssueProfile,
    item: dict,
    config: ModelConfig,
    usage_summary: dict[str, object] | None = None,
) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=DEFAULT_MODEL_REQUEST_TIMEOUT)
    response = model_completion_with_retry(
        client,
        config,
        build_diff_extraction_prompt(profile, item),
        "diff extraction",
    )
    if usage_summary is not None:
        record_model_usage(usage_summary, "diff_extraction", response)
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError:
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if not fence_match:
            raise
        payload = json.loads(fence_match.group(1).strip())
    if not isinstance(payload, dict):
        raise ValueError("diff extraction payload is not a JSON object")
    return format_extracted_diff_evidence(payload)


def extract_diff_evidence_batch_with_model(
    profile: IssueProfile,
    items: list[dict],
    config: ModelConfig,
    batch_size: int = DEFAULT_DIFF_EXTRACTION_BATCH_SIZE,
    usage_summary: dict[str, object] | None = None,
) -> dict[str, str]:
    if not items:
        return {}
    if len(items) == 1:
        return {items[0]["sha"]: extract_diff_evidence_with_model(profile, items[0], config, usage_summary)}
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=DEFAULT_MODEL_REQUEST_TIMEOUT)
    extracted: dict[str, str] = {}
    batches = plan_diff_extraction_batches(profile, items, batch_size=batch_size)
    total_batches = len(batches)
    for batch_index, batch in enumerate(batches, start=1):
        log_progress(
            f"diff extraction batch {batch_index}/{total_batches} size={len(batch)}"
        )
        try:
            response = model_completion_with_retry(
                client,
                config,
                build_diff_extraction_batch_prompt(profile, batch),
                "diff extraction batch",
            )
            if usage_summary is not None:
                record_model_usage(usage_summary, "diff_extraction", response)
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("diff extraction batch response contained no choices")
            content = choices[0].message.content or ""
            payload = parse_model_json_payload(content)
            for entry in payload:
                sha = str(entry.get("sha", ""))
                if sha:
                    extracted[sha] = format_extracted_diff_evidence(entry)
        except Exception as exc:
            log_progress(
                f"diff extraction batch {batch_index}/{total_batches} failed ({exc}); retrying items individually"
            )
            for item in batch:
                extracted[item["sha"]] = extract_diff_evidence_with_model(profile, item, config, usage_summary)

    for item in items:
        sha = item["sha"]
        if sha not in extracted:
            extracted[sha] = extract_diff_evidence_with_model(profile, item, config, usage_summary)
    return extracted


def extract_causal_diff_evidence_with_model(
    profile: IssueProfile,
    item: dict,
    config: ModelConfig,
    usage_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=DEFAULT_MODEL_REQUEST_TIMEOUT)
    response = model_completion_with_retry(
        client,
        config,
        build_causal_diff_extraction_prompt(profile, item),
        "causal diff extraction",
    )
    if usage_summary is not None:
        record_model_usage(usage_summary, "causal_diff_extraction", response)
    content = response.choices[0].message.content or ""
    payload = parse_model_json_object(content)
    return normalize_causal_diff_evidence(item, payload)


def plan_causal_diff_extraction_batches(
    profile: IssueProfile,
    items: list[dict],
    batch_size: int = 3,
    max_prompt_chars: int = DEFAULT_DIFF_EXTRACTION_MAX_PROMPT_CHARS,
) -> list[list[dict]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars must be positive")
    batches: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        candidate = [*current, item]
        if current and (
            len(candidate) > batch_size
            or len(build_causal_diff_extraction_batch_prompt(profile, candidate)) > max_prompt_chars
        ):
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def extract_causal_diff_evidence_batch_with_model(
    profile: IssueProfile,
    items: list[dict],
    config: ModelConfig,
    usage_summary: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    if not items:
        return {}
    if len(items) == 1:
        return {items[0]["sha"]: extract_causal_diff_evidence_with_model(profile, items[0], config, usage_summary)}
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=DEFAULT_MODEL_REQUEST_TIMEOUT)
    extracted: dict[str, dict[str, object]] = {}
    batches = plan_causal_diff_extraction_batches(profile, items)
    for batch_index, batch in enumerate(batches, start=1):
        log_progress(f"causal diff extraction batch {batch_index}/{len(batches)} size={len(batch)}")
        try:
            response = model_completion_with_retry(
                client,
                config,
                build_causal_diff_extraction_batch_prompt(profile, batch),
                "causal diff extraction batch",
            )
            if usage_summary is not None:
                record_model_usage(usage_summary, "causal_diff_extraction", response)
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("causal diff extraction batch response contained no choices")
            for payload in parse_model_json_payload(choices[0].message.content or ""):
                sha = str(payload.get("sha", ""))
                item = next((candidate for candidate in batch if candidate["sha"] == sha), None)
                if item is not None:
                    extracted[sha] = normalize_causal_diff_evidence(item, payload)
        except Exception as exc:
            log_progress(
                f"causal diff extraction batch {batch_index}/{len(batches)} failed ({exc}); retrying items individually"
            )
        for item in batch:
            if item["sha"] not in extracted:
                extracted[item["sha"]] = extract_causal_diff_evidence_with_model(
                    profile,
                    item,
                    config,
                    usage_summary,
                )
    return extracted


def plan_model_scoring_batches(
    commits: list[dict],
    frontier_mode: str,
    batch_size: int = DEFAULT_MODEL_SCORING_BATCH_SIZE,
) -> list[list[dict]]:
    if not commits:
        return []
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if frontier_mode in {"topk", "diverse", "evidence-diverse"} and len(commits) <= batch_size:
        return [commits]
    return [commits[start : start + batch_size] for start in range(0, len(commits), batch_size)]


def model_score_commits(
    profile: IssueProfile,
    commits: list[dict],
    config: ModelConfig,
    observations: list[CommitObservation] | None = None,
    observation_prompt_mode: str = "legacy",
    usage_summary: dict[str, object] | None = None,
) -> dict[str, dict]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("openai package is not available; install it in the local venv") from exc

    client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=DEFAULT_MODEL_REQUEST_TIMEOUT)
    prompt = build_model_scoring_prompt(
        profile,
        commits,
        observations,
        observation_prompt_mode=observation_prompt_mode,
    )

    response = model_completion_with_retry(client, config, prompt, "scoring")
    if usage_summary is not None:
        record_model_usage(usage_summary, "scoring", response)
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
    usage_summary: dict[str, object] | None = None,
) -> dict[str, dict]:
    score_kwargs = {"usage_summary": usage_summary} if usage_summary is not None else {}
    if observations is None:
        scored = score_fn(profile, commits, config, **score_kwargs)
    else:
        scored = score_fn(profile, commits, config, observations, observation_prompt_mode, **score_kwargs)
    missing = [item for item in commits if item["sha"] not in scored]
    if not missing:
        return scored

    recovered = dict(scored)
    for item in missing:
        if observations is None:
            single = score_fn(profile, [item], config, **score_kwargs)
        else:
            single = score_fn(profile, [item], config, observations, observation_prompt_mode, **score_kwargs)
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


def merge_commit_features(*feature_sets: Iterable[str] | None) -> list[str]:
    """Retain model mechanism hints alongside stable path and token features."""
    merged = {
        str(feature).strip()
        for feature_set in feature_sets
        for feature in (feature_set or [])
        if str(feature).strip()
    }
    return sorted(merged)


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
                build_failure=item.get("build_failure") or None,
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
            "build_failure": obs.build_failure or {},
        }
        for obs in observations
    ]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(path)


def runner_observation_history_fields(observation: CommitObservation) -> dict[str, object]:
    """Return runner evidence fields that should survive in run-history JSON."""
    payload: dict[str, object] = {
        "evidence": observation.evidence or [],
        "log_excerpt": observation.log_excerpt,
        "trace_excerpt": observation.trace_excerpt,
    }
    if observation.build_failure:
        payload["build_failure"] = observation.build_failure
    return payload


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


def copy_if_exists(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return True


def issue_artifact_bundle_dir(issue_id: str) -> Path:
    return issue_results_dir(issue_id) / "report-trace-model-guided"


def save_issue_artifact_bundle(
    *,
    issue_id: str,
    observation_path: Path,
    run_history_path: Path,
    unresolved_window_path: Path,
    model_cache_path_value: Path | None,
) -> Path:
    """Keep a self-contained JSON bundle under results/issues/<issue>/.

    Remote result copies often pull only results/issues/<issue>/.  The canonical
    LM artifacts live in global results/lm_bisect_* directories, so mirror the
    relevant JSON files here as well.
    """
    bundle_dir = issue_artifact_bundle_dir(issue_id)
    copied: dict[str, str] = {}
    for label, source in (
        ("observations", observation_path),
        ("run_history", run_history_path),
        ("unresolved_window", unresolved_window_path),
        ("model_cache", model_cache_path_value),
    ):
        if source is None:
            continue
        destination = bundle_dir / source.name
        if copy_if_exists(source, destination):
            copied[label] = str(destination)

    manifest = {
        "issue": issue_id,
        "artifact_bundle_dir": str(bundle_dir),
        "canonical_paths": {
            "observations": str(observation_path),
            "run_history": str(run_history_path),
            "unresolved_window": str(unresolved_window_path),
            "model_cache": str(model_cache_path_value) if model_cache_path_value else None,
        },
        "copied_paths": copied,
    }
    manifest_path = bundle_dir / f"{issue_id}-artifact-bundle-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp_path.replace(manifest_path)
    return bundle_dir


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
    model_diff_mode: str = "parent",
    model_diff_extraction: str = "raw",
    model_top_k: int | None = None,
    adaptive_top_k: dict[str, int] | None = None,
    confidence_adaptive_frontier: dict[str, float] | None = None,
    observation_conditioned_posterior: dict[str, float] | None = None,
    model_cache_namespace: str | None = None,
    oracle_first_bad_sha: str | None = None,
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
        "model_diff_mode": model_diff_mode,
        "model_diff_extraction": model_diff_extraction,
        "model_top_k": model_top_k,
        "adaptive_top_k": adaptive_top_k,
        "confidence_adaptive_frontier": confidence_adaptive_frontier,
        "observation_conditioned_posterior": observation_conditioned_posterior,
        "model_cache_namespace": model_cache_namespace,
        "oracle_first_bad_sha": oracle_first_bad_sha,
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
    model_diff_mode: str = "parent",
    model_diff_extraction: str = "raw",
    model_top_k: int | None = None,
    adaptive_top_k: dict[str, int] | None = None,
    confidence_adaptive_frontier: dict[str, float] | None = None,
    observation_conditioned_posterior: dict[str, float] | None = None,
    model_cache_namespace: str | None = None,
    oracle_first_bad_sha: str | None = None,
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
        and history.get("model_diff_mode", "parent") == model_diff_mode
        and history.get("model_diff_extraction", "raw") == model_diff_extraction
        and history.get("model_top_k") == model_top_k
        and history.get("adaptive_top_k") == adaptive_top_k
        and history.get("confidence_adaptive_frontier") == confidence_adaptive_frontier
        and history.get("observation_conditioned_posterior") == observation_conditioned_posterior
        and history.get("model_cache_namespace") == model_cache_namespace
        and history.get("oracle_first_bad_sha") == oracle_first_bad_sha
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
    model_diff_mode: str = "parent",
    model_diff_extraction: str = "raw",
    model_top_k: int | None = None,
    adaptive_top_k: dict[str, int] | None = None,
    confidence_adaptive_frontier: dict[str, float] | None = None,
    observation_conditioned_posterior: dict[str, float] | None = None,
    model_cache_namespace: str | None = None,
    oracle_first_bad_sha: str | None = None,
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
            model_diff_mode,
            model_diff_extraction,
            model_top_k,
            adaptive_top_k,
            confidence_adaptive_frontier,
            observation_conditioned_posterior,
            model_cache_namespace,
            oracle_first_bad_sha,
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
        history["model_diff_mode"] = model_diff_mode
        history["model_diff_extraction"] = model_diff_extraction
        history["model_top_k"] = model_top_k
        history["adaptive_top_k"] = adaptive_top_k
        history["confidence_adaptive_frontier"] = confidence_adaptive_frontier
        history["observation_conditioned_posterior"] = observation_conditioned_posterior
        history["model_cache_namespace"] = model_cache_namespace
        history["oracle_first_bad_sha"] = oracle_first_bad_sha
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
        model_diff_mode=model_diff_mode,
        model_diff_extraction=model_diff_extraction,
        model_top_k=model_top_k,
        adaptive_top_k=adaptive_top_k,
        confidence_adaptive_frontier=confidence_adaptive_frontier,
        observation_conditioned_posterior=observation_conditioned_posterior,
        model_cache_namespace=model_cache_namespace,
        oracle_first_bad_sha=oracle_first_bad_sha,
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
    if record.observation_conditioned_posterior_mass:
        payload["observation_bad_similarity"] = round(record.observation_bad_similarity, 6)
        payload["observation_good_similarity"] = round(record.observation_good_similarity, 6)
        payload["observation_posterior_evidence"] = round(record.observation_posterior_evidence, 6)
        payload["observation_conditioned_posterior_mass"] = round(
            record.observation_conditioned_posterior_mass,
            6,
        )
    if record.weak_relevance_penalty:
        payload["weak_relevance_penalty"] = round(record.weak_relevance_penalty, 6)
    add_diff_metadata_to_payload(payload, record)
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
    if record.observation_conditioned_posterior_mass:
        payload["observation_bad_similarity"] = round(record.observation_bad_similarity, 6)
        payload["observation_good_similarity"] = round(record.observation_good_similarity, 6)
        payload["observation_posterior_evidence"] = round(record.observation_posterior_evidence, 6)
        payload["observation_conditioned_posterior_mass"] = round(
            record.observation_conditioned_posterior_mass,
            6,
        )
    if record.weak_relevance_penalty:
        payload["weak_relevance_penalty"] = round(record.weak_relevance_penalty, 6)
    add_diff_metadata_to_payload(payload, record)
    return payload


def add_diff_metadata_to_payload(payload: dict, record: CommitRecord) -> None:
    if record.diff_mode != "parent" or record.diff_extraction != "raw" or record.diff_summary:
        payload["diff_mode"] = record.diff_mode
        payload["diff_extraction"] = record.diff_extraction
    if record.diff_base_sha:
        payload["diff_base_sha"] = record.diff_base_sha
    if record.diff_summary:
        payload["diff_summary"] = record.diff_summary
    if record.causal_evidence:
        payload["causal_evidence"] = record.causal_evidence


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


def normalize_build_path(path: str) -> str:
    cleaned = path.strip().strip('"')
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
    for prefix in ("llvm/", "clang/", "clang-tools-extra/"):
        if cleaned.startswith(prefix):
            return cleaned
    marker_prefixes = (
        "/llvm-project/",
    )
    for marker in marker_prefixes:
        idx = cleaned.find(marker)
        if idx >= 0:
            return cleaned[idx + len(marker) :]
    for prefix in ("llvm/", "clang/", "clang-tools-extra/"):
        idx = cleaned.find(prefix)
        if idx >= 0:
            return cleaned[idx:]
    return cleaned


def extract_build_failure_summary(output: str) -> dict[str, object]:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    summary: dict[str, object] = {}
    lowered = "\n".join(lines).lower()

    if "configure failed" in lowered or any(line.startswith("CMake Error") for line in lines):
        summary["phase"] = "configure"
    elif "ninja: build stopped" in lowered or "build failed" in lowered:
        summary["phase"] = "build"
    else:
        summary["phase"] = "unknown"

    ninja_line = next((line for line in lines if re.match(r"^\[\d+/\d+\]", line)), "")
    if ninja_line:
        edge_match = re.match(r"^\[(\d+/\d+)\]\s+(.*)$", ninja_line)
        if edge_match:
            summary["ninja_edge"] = edge_match.group(1)
            summary["ninja_action"] = edge_match.group(2)

    target_match = next(
        (re.search(r"CMakeFiles/([^/]+)\.dir/", line) for line in lines if "CMakeFiles/" in line),
        None,
    )
    if target_match:
        summary["failed_target"] = target_match.group(1)

    compile_source = ""
    for line in lines:
        source_match = re.search(r"(?:^|\s)-c\s+(\S+)", line)
        if source_match:
            compile_source = normalize_build_path(source_match.group(1))
            break
    if compile_source:
        summary["failed_source"] = compile_source

    primary_error = ""
    for line in lines:
        if " error:" in line or line.startswith("error:") or line.startswith("CMake Error"):
            primary_error = line.strip()
            break
    if primary_error:
        summary["primary_error"] = primary_error

    header = ""
    for line in lines:
        error_match = re.match(r"^(.+?):\d+:\d+:\s+error:", line.strip())
        if error_match:
            header = normalize_build_path(error_match.group(1))
            break
    if header:
        summary["failed_header"] = header

    missing_include_match = re.search(
        r"(?:did you forget to\s+['`\"]?#include\s+|is defined in header\s+['`\"]?)(<[^>]+>)",
        output,
    )
    if missing_include_match:
        summary["missing_include"] = missing_include_match.group(1)

    cmake_stack = next((line.strip() for line in lines if line.startswith("CMake Error at ")), "")
    if cmake_stack:
        summary["cmake_stack"] = cmake_stack

    if not primary_error and lines:
        summary["primary_error"] = lines[-1].strip()

    return summary


def issue_runner_log_path(profile: IssueProfile) -> Path:
    run_id = os.environ.get("RUN_ID", "manual")
    return DEFAULT_ISSUE_RESULTS_DIR / profile.issue_id / f"{profile.issue_id}-git-bisect-runner-{run_id}.log"


def runner_log_tail(path: Path, max_chars: int = 20000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_chars), os.SEEK_SET)
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def append_runner_log_tail(output: str, profile: IssueProfile, verdict: str) -> str:
    if verdict != "skip":
        return output
    log_tail = runner_log_tail(issue_runner_log_path(profile))
    if not log_tail:
        return output
    if log_tail.strip() in output:
        return output
    parts = [output.strip()] if output.strip() else []
    parts.extend(
        [
            "=== runner log tail ===",
            log_tail.strip(),
        ]
    )
    return "\n".join(parts)


def run_issue_runner(profile: IssueProfile, repo: Path) -> tuple[str, str, str, list[str]]:
    runner = runner_path_for_issue(profile)
    completed = subprocess.run(
        [str(runner), str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = completed.stdout.strip()
    if re.search(r"(?:missing issue definition|no repro for)\s*:", output, re.IGNORECASE):
        verdict = "skip"
        summary = output.splitlines()[-1].strip()
        evidence = ["runner configuration error"] + runner_evidence_lines(output)
        output = append_runner_log_tail(output, profile, verdict)
        return verdict, summary, output, evidence
    verdict = verdict_from_runner_exit_code(completed.returncode)
    summary = output.splitlines()[-1].strip() if output else f"runner verdict {verdict}"
    output = append_runner_log_tail(output, profile, verdict)
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
        diff_mode = "parent"
        diff_extraction = "raw"
        diff_base_sha = None
        diff_summary = ""
        causal_evidence = None
    else:
        subject = preloaded["subject"]
        body = preloaded["body"]
        files = preloaded["files"]
        diff = preloaded.get("diff", "")
        if load_diff and not diff:
            diff = commit_diff_text(repo, sha)
        preloaded_result = preloaded.get("model_result", {})
        diff_mode = str(preloaded.get("diff_mode") or preloaded_result.get("diff_mode") or "parent")
        diff_extraction = str(preloaded.get("diff_extraction") or preloaded_result.get("diff_extraction") or "raw")
        diff_base_sha = preloaded.get("diff_base_sha") or preloaded_result.get("diff_base_sha")
        diff_summary = str(preloaded.get("diff_summary") or preloaded_result.get("diff_summary") or "")
        causal_evidence = preloaded.get("causal_evidence") or preloaded_result.get("causal_evidence")
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
        diff_mode=diff_mode,
        diff_extraction=diff_extraction,
        diff_base_sha=diff_base_sha,
        diff_summary=diff_summary,
        causal_evidence=causal_evidence if isinstance(causal_evidence, dict) else None,
    )


def apply_feedback_bias(
    profile: IssueProfile,
    records: list[CommitRecord],
    observations: list[CommitObservation],
    *,
    enabled: bool = True,
) -> None:
    if not enabled or not observations:
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
        bias = 1.0 + bad_weight * bad_sim - 1.2 * good_sim
        if not strong_positive_bias and bias > 1.0:
            bias = min(bias, 1.10)
        record.feedback_bias = max(0.20, bias)
        record.semantic_score *= record.feedback_bias
        if skip_sim:
            build_risk_multiplier = max(0.20, 1.0 - 0.60 * skip_sim)
            record.build_success_prob = max(0.05, record.build_success_prob * build_risk_multiplier)

        feedback_bits = []
        if bad_sim:
            feedback_bits.append(f"bad-sim={bad_sim:.2f}")
        if good_sim:
            feedback_bits.append(f"good-sim={good_sim:.2f}")
        if skip_sim:
            feedback_bits.append(f"skip-build-risk={skip_sim:.2f}")
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


def observation_semantic_features(features: Iterable[str] | None) -> set[str]:
    """Keep mechanism and component evidence, excluding generic build/meta noise."""
    normalized_features: set[str] = set()
    for feature in features or []:
        feature = str(feature).strip()
        if not feature or feature.startswith(("build:", "meta:")):
            continue
        if feature.startswith(("path:", "term:")):
            normalized_features.add(feature)
            continue
        normalized = normalize_token(feature)
        if normalized:
            normalized_features.add(f"term:{normalized}")
    return normalized_features


def observation_feature_affinity(
    candidate_features: Iterable[str] | None,
    observation_features: Iterable[str] | None,
    component_weight: float,
) -> float:
    candidate = observation_semantic_features(candidate_features)
    observed = observation_semantic_features(observation_features)
    if not candidate or not observed:
        return 0.0
    candidate_paths = {feature for feature in candidate if feature.startswith("path:")}
    observed_paths = {feature for feature in observed if feature.startswith("path:")}
    candidate_mechanisms = candidate - candidate_paths
    observed_mechanisms = observed - observed_paths
    path_similarity = jaccard_similarity(candidate_paths, observed_paths)
    mechanism_similarity = jaccard_similarity(candidate_mechanisms, observed_mechanisms)
    if mechanism_similarity > 0.0 and path_similarity > 0.0:
        return (mechanism_similarity + component_weight * path_similarity) / (1.0 + component_weight)
    return max(mechanism_similarity, component_weight * path_similarity)


def observation_conditioned_posterior_probabilities(
    records: list[CommitRecord],
    observations: list[CommitObservation],
    config: ObservationConditionedPosteriorConfig,
    prior_power: float = DEFAULT_CALIBRATED_PRIOR_POWER,
) -> list[float]:
    """Reweight calibrated priors using observed good/bad mechanism affinity."""
    if not records:
        raise ValueError("records must not be empty")
    priors = calibrated_prior_probabilities(records, prior_power)
    bad_observations = [item for item in observations if item.verdict == "bad"]
    good_observations = [item for item in observations if item.verdict == "good"]
    weights: list[float] = []
    for record, prior in zip(records, priors):
        features = merge_commit_features(
            record.features,
            extract_commit_features(
                record.subject,
                record.body,
                record.changed_files,
                record.diff_text,
            ),
        )
        bad_similarity = max(
            (
                observation_feature_affinity(features, observation.features, config.component_weight)
                for observation in bad_observations
            ),
            default=0.0,
        )
        good_similarity = max(
            (
                observation_feature_affinity(features, observation.features, config.component_weight)
                for observation in good_observations
            ),
            default=0.0,
        )
        evidence = config.bad_strength * bad_similarity - config.good_strength * good_similarity
        record.observation_bad_similarity = bad_similarity
        record.observation_good_similarity = good_similarity
        record.observation_posterior_evidence = evidence
        weights.append(prior * math.exp(evidence))
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("observation-conditioned posterior total must be positive")
    return [weight / total for weight in weights]


def calibrated_posterior_selection(
    profile: IssueProfile,
    records: list[CommitRecord],
    prior_power: float,
    prior_bonus: float,
    weak_relevance_penalty: float,
    weak_relevance_threshold: float,
    build_success_power: float = DEFAULT_BUILD_SUCCESS_POWER,
    observations: list[CommitObservation] | None = None,
    observation_posterior_config: ObservationConditionedPosteriorConfig | None = None,
) -> tuple[CommitRecord, list[CommitRecord]]:
    if not records:
        raise ValueError("records must not be empty")

    ordered = sorted(records, key=lambda record: record.index)
    probabilities = (
        observation_conditioned_posterior_probabilities(
            ordered,
            observations or [],
            observation_posterior_config,
            prior_power,
        )
        if observation_posterior_config is not None
        else calibrated_prior_probabilities(ordered, prior_power)
    )
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
        record.observation_conditioned_posterior_mass = (
            cumulative if observation_posterior_config is not None else 0.0
        )
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
    observations: list[CommitObservation] | None = None,
    observation_posterior_config: ObservationConditionedPosteriorConfig | None = None,
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
            observations=observations,
            observation_posterior_config=observation_posterior_config,
        )
        return SelectionDecision(
            selected=selected,
            ranked_candidates=calibrated_ranked,
            search_policy=search_policy,
            selection_mode=(
                "observation-conditioned-posterior"
                if observation_posterior_config is not None
                else "calibrated-posterior"
            ),
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


def semantic_frontier_records(records: list[CommitRecord]) -> list[CommitRecord]:
    return sorted(
        records,
        key=lambda record: (record.semantic_score, record.build_success_prob, -record.index),
        reverse=True,
    )


def matching_profile_components(profile: IssueProfile, record: CommitRecord) -> list[str]:
    """Return issue-relevant changed-file prefixes, ordered most specific first."""
    matched: set[str] = set()
    for path in record.changed_files:
        for prefix in profile.relevant_paths:
            if path.startswith(prefix):
                matched.add(prefix.rstrip("/"))
        for prefix in profile.high_risk_paths:
            if path.startswith(prefix):
                matched.add(prefix.rstrip("/"))
    return sorted(matched, key=lambda component: (-len(component), component))


def select_evidence_diverse_frontier(
    profile: IssueProfile,
    records: list[CommitRecord],
    target_count: int,
) -> tuple[list[str], list[dict[str, object]]]:
    """Mix semantic, posterior, and component evidence before LLM rescoring."""
    if target_count <= 0 or not records:
        return [], []

    semantic_ranked = semantic_frontier_records(records)
    selected: list[CommitRecord] = []
    assignments: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(record: CommitRecord | None, role: str, component: str | None = None) -> bool:
        if record is None or record.sha in seen or len(selected) >= target_count:
            return False
        selected.append(record)
        seen.add(record.sha)
        assignment: dict[str, object] = {"role": role, "sha": record.sha}
        if component:
            assignment["component"] = component
        assignments.append(assignment)
        return True

    add(semantic_ranked[0], "semantic-leader")

    midpoint = posterior_anchor_by_mass(records, 0.5)
    if not add(midpoint, "posterior-midpoint"):
        # A dominant semantic leader can also be the weighted posterior
        # midpoint. In that case keep the role exploratory via index midpoint.
        add(index_anchor(records, 0.5), "posterior-midpoint")

    represented_components = {
        component
        for record in selected
        for component in matching_profile_components(profile, record)
    }
    component_candidate: CommitRecord | None = None
    component_name: str | None = None
    for record in semantic_ranked:
        if record.sha in seen:
            continue
        components = matching_profile_components(profile, record)
        unrepresented = [component for component in components if component not in represented_components]
        if unrepresented:
            component_candidate = record
            component_name = unrepresented[0]
            break
    if not add(component_candidate, "relevant-component", component_name):
        # Some narrow issues have only one matching component. Retain a third
        # distinct probe and make the lack of component diversity explicit.
        for record in semantic_ranked:
            if add(record, "component-fallback"):
                break

    structural_anchors = (
        (posterior_anchor_by_mass(records, 0.25), "posterior-q25"),
        (posterior_anchor_by_mass(records, 0.75), "posterior-q75"),
        (index_anchor(records, 0.25), "index-q25"),
        (index_anchor(records, 0.75), "index-q75"),
    )
    for record, role in structural_anchors:
        add(record, role)

    for record in semantic_ranked:
        add(record, "semantic-fill")
        if len(selected) >= target_count:
            break

    return [record.sha for record in selected], assignments


def semantic_frontier_confidence(records: list[CommitRecord]) -> tuple[float, list[CommitRecord]]:
    """Return a scale-free pre-model confidence score for the semantic leader."""
    ranked = semantic_frontier_records(records)
    if len(ranked) < 2:
        return 1.0, ranked
    best_score = ranked[0].semantic_score
    runner_up_score = ranked[1].semantic_score
    confidence = max(0.0, best_score - runner_up_score) / max(1.0, abs(best_score))
    return min(1.0, confidence), ranked


def resolve_model_frontier(
    profile: IssueProfile,
    records: list[CommitRecord],
    observations: list[CommitObservation],
    *,
    target_count: int,
    configured_frontier: str,
    confidence_config: ConfidenceAdaptiveFrontierConfig | None = None,
) -> ModelFrontierDecision:
    """Resolve the LLM frontier before model scoring without spending extra calls."""
    if confidence_config is None:
        if configured_frontier == "evidence-diverse":
            selected_shas, role_assignments = select_evidence_diverse_frontier(
                profile, records, target_count
            )
            reason = "fixed mixed frontier: semantic leader, posterior anchors, and relevant components"
        else:
            selected_shas = select_model_frontier_shas(records, target_count, configured_frontier)
            role_assignments = []
            reason = "configured frontier; confidence policy disabled"
        return ModelFrontierDecision(
            selected_shas=selected_shas,
            configured_frontier=configured_frontier,
            effective_frontier=configured_frontier,
            confidence=None,
            threshold=None,
            reason=reason,
            top_semantic_candidates=[],
            observation_count=len(observations),
            role_assignments=role_assignments,
        )

    # Feedback is used only to decide the next model frontier. The final
    # selection path applies it once to the actual model-scored records.
    confidence_records = [replace(record, evidence=list(record.evidence or [])) for record in records]
    apply_feedback_bias(profile, confidence_records, observations)
    confidence, semantic_ranked = semantic_frontier_confidence(confidence_records)
    effective_frontier = "topk" if confidence >= confidence_config.threshold else "diverse"
    selected_shas = select_model_frontier_shas(confidence_records, target_count, effective_frontier)
    top_semantic_candidates = [
        {
            "sha": record.sha,
            "semantic_score": round(record.semantic_score, 6),
            "build_success_prob": round(record.build_success_prob, 6),
        }
        for record in semantic_ranked[:2]
    ]
    return ModelFrontierDecision(
        selected_shas=selected_shas,
        configured_frontier=configured_frontier,
        effective_frontier=effective_frontier,
        confidence=confidence,
        threshold=confidence_config.threshold,
        reason=(
            "semantic leader confidence meets threshold; use semantic top-k"
            if effective_frontier == "topk"
            else "semantic scores are ambiguous; add posterior and midpoint anchors"
        ),
        top_semantic_candidates=top_semantic_candidates,
        observation_count=len(observations),
        role_assignments=[],
    )


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
    model_diff_mode: str = "parent",
    model_diff_extraction: str = "raw",
    last_tested_sha: str | None = None,
    model_usage_summary: dict[str, object] | None = None,
    model_cache_namespace: str | None = None,
    model_score_context: str | None = None,
    confidence_adaptive_frontier: ConfidenceAdaptiveFrontierConfig | None = None,
    model_frontier_decision_out: list[ModelFrontierDecision] | None = None,
) -> tuple[list[CommitRecord], dict]:
    if model_diff_mode not in {"parent", "last-tested"}:
        raise ValueError(f"unsupported model_diff_mode: {model_diff_mode}")
    if model_diff_extraction not in {"raw", "llm", "causal-llm", "causal-llm-impl"}:
        raise ValueError(f"unsupported model_diff_extraction: {model_diff_extraction}")
    if model_diff_extraction in {"causal-llm", "causal-llm-impl"} and model_diff_mode != "parent":
        raise ValueError("causal-llm extraction supports parent diffs only")
    shas = candidate_shas if candidate_shas is not None else list_candidate_commits(repo, profile.good_commit, profile.bad_commit)
    if max_candidates is not None:
        shas = shas[:max_candidates]

    metadata_by_sha = metadata_cache if metadata_cache is not None else {}
    missing_shas = [sha for sha in shas if sha not in metadata_by_sha]
    if missing_shas:
        loaded_metadata = load_commit_metadata(
            repo,
            missing_shas,
            include_body=(scorer == "heuristic" and heuristic_version in {"tuned", "general"}),
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
    frontier_decision = resolve_model_frontier(
        profile,
        records,
        observations or [],
        target_count=target_count,
        configured_frontier=model_frontier,
        confidence_config=confidence_adaptive_frontier,
    )
    if model_frontier_decision_out is not None:
        model_frontier_decision_out.append(frontier_decision)
    selected_shas = set(frontier_decision.selected_shas)
    frontier_score_context = model_score_context
    if model_score_context:
        frontier_digest = hashlib.sha256(
            "\n".join(sorted(selected_shas)).encode("utf-8")
        ).hexdigest()
        frontier_score_context = f"{model_score_context}|frontier:{frontier_digest}"

    cache_path = model_cache_path(
        profile.issue_id,
        model_config.model_name,
        scoring_version=resolved_model_scoring_version(model_config.observation_prompt_mode),
        namespace=model_cache_namespace,
    )
    cache = model_cache if model_cache is not None else load_model_cache(cache_path)
    effective_model_diff_mode = "last-tested" if model_diff_mode == "last-tested" and last_tested_sha else "parent"
    effective_diff_base_sha = last_tested_sha if effective_model_diff_mode == "last-tested" else None
    diff_fetch_max_chars = (
        DIFF_EXTRACTION_MAX_INPUT_CHARS
        if model_diff_extraction == "llm"
        else DEFAULT_DIFF_TEXT_MAX_CHARS
    )

    def resolve_candidate_diff_mode(candidate_sha: str) -> tuple[str, str | None]:
        if effective_model_diff_mode != "last-tested" or not last_tested_sha:
            return "parent", None
        if commit_is_ancestor(repo, last_tested_sha, candidate_sha):
            return "last-tested", last_tested_sha
        return "parent", None

    def is_complete_model_cache_entry(entry: dict | None) -> bool:
        if entry is None:
            return False
        if model_diff_extraction == "llm" and not entry.get("diff_summary"):
            return False
        if model_diff_extraction in {"causal-llm", "causal-llm-impl"} and not entry.get("causal_evidence"):
            return False
        return True

    uncached_shas = []
    for item in preloaded_items:
        if item["sha"] not in selected_shas:
            continue
        item_diff_mode, item_diff_base_sha = resolve_candidate_diff_mode(item["sha"])
        item["diff_mode"] = item_diff_mode
        item["diff_extraction"] = model_diff_extraction
        if item_diff_base_sha:
            item["diff_base_sha"] = item_diff_base_sha
        else:
            item.pop("diff_base_sha", None)
        cached = cache.get(
            model_score_cache_key(
                item["sha"],
                item_diff_mode,
                item_diff_base_sha,
                model_diff_extraction,
                frontier_score_context,
            )
        )
        if not is_complete_model_cache_entry(cached):
            uncached_shas.append(item["sha"])
    deep_metadata_by_sha = load_commit_metadata(repo, uncached_shas, include_body=True) if uncached_shas else {}
    uncached: list[dict] = []
    for item in preloaded_items:
        if item["sha"] not in selected_shas:
            continue
        item_diff_mode = str(item.get("diff_mode") or "parent")
        diff_base_sha = item.get("diff_base_sha")
        item["diff_extraction"] = model_diff_extraction
        cache_key = model_score_cache_key(
            item["sha"],
            item_diff_mode,
            diff_base_sha,
            model_diff_extraction,
            frontier_score_context,
        )
        cached = cache.get(cache_key)
        if not is_complete_model_cache_entry(cached):
            deep_metadata = deep_metadata_by_sha.get(item["sha"])
            if deep_metadata is None:
                raise RuntimeError(f"missing deep metadata for commit {item['sha']}")
            item["subject"] = deep_metadata.subject
            item["body"] = deep_metadata.body
            item["files"] = deep_metadata.changed_files
            if item_diff_mode == "last-tested" and diff_base_sha:
                item["diff"] = commit_transition_diff_for_files(
                    repo,
                    diff_base_sha,
                    item["sha"],
                    deep_metadata.changed_files,
                    max_chars=diff_fetch_max_chars,
                )
                item["files"] = deep_metadata.changed_files
                item["diff_mode"] = "last-tested"
                item["diff_base_sha"] = diff_base_sha
            elif model_diff_extraction in {"causal-llm", "causal-llm-impl"}:
                # Retrieval fetches only profile-matched parent-diff files.
                item["diff"] = ""
                item["diff_mode"] = "parent"
                item.pop("diff_base_sha", None)
            else:
                item["diff"] = commit_diff_text(repo, item["sha"], max_chars=diff_fetch_max_chars)
                item["diff_mode"] = "parent"
                item.pop("diff_base_sha", None)
            item["diff_extraction"] = model_diff_extraction
            uncached.append(item)
        else:
            item["model_result"] = cached
            if cached.get("diff_summary"):
                item["diff_summary"] = str(cached["diff_summary"])
            if cached.get("causal_evidence"):
                item["causal_evidence"] = cached["causal_evidence"]

    if uncached:
        if model_diff_extraction == "llm":
            extraction_kwargs = {}
            if model_usage_summary is not None:
                extraction_kwargs["usage_summary"] = model_usage_summary
            extracted_diffs = extract_diff_evidence_batch_with_model(
                profile,
                uncached,
                model_config,
                **extraction_kwargs,
            )
            for item in uncached:
                item["diff_summary"] = extracted_diffs[item["sha"]]
        elif model_diff_extraction in {"causal-llm", "causal-llm-impl"}:
            for item in uncached:
                item["causal_retrieval"] = retrieve_causal_diff_evidence(
                    repo,
                    profile,
                    item,
                    retrieval_policy=(
                        "implementation-first"
                        if model_diff_extraction == "causal-llm-impl"
                        else "balanced"
                    ),
                )
            extraction_kwargs = {}
            if model_usage_summary is not None:
                extraction_kwargs["usage_summary"] = model_usage_summary
            extracted_evidence = extract_causal_diff_evidence_batch_with_model(
                profile,
                uncached,
                model_config,
                **extraction_kwargs,
            )
            for item in uncached:
                causal_evidence = extracted_evidence[item["sha"]]
                item["causal_evidence"] = causal_evidence
                item["diff_summary"] = format_causal_diff_evidence(causal_evidence)
        for batch in plan_model_scoring_batches(uncached, frontier_mode=frontier_decision.effective_frontier):
            score_kwargs = {}
            if model_usage_summary is not None:
                score_kwargs["usage_summary"] = model_usage_summary
            scored = score_model_batch_with_backfill(
                profile,
                batch,
                model_config,
                model_score_commits,
                observations,
                getattr(model_config, "observation_prompt_mode", "legacy"),
                **score_kwargs,
            )
            for batch_item in batch:
                result = scored.get(batch_item["sha"])
                if result is None:
                    raise RuntimeError(f"model response missing commit {batch_item['sha']}")
                result = dict(result)
                result["diff_mode"] = batch_item.get("diff_mode", "parent")
                result["diff_extraction"] = batch_item.get("diff_extraction", model_diff_extraction)
                if batch_item.get("diff_base_sha"):
                    result["diff_base_sha"] = batch_item["diff_base_sha"]
                if batch_item.get("diff_summary"):
                    result["diff_summary"] = batch_item["diff_summary"]
                    result["diff_summary_version"] = diff_extraction_version(
                        batch_item.get("diff_extraction", model_diff_extraction)
                    )
                if batch_item.get("causal_evidence"):
                    result["causal_evidence"] = batch_item["causal_evidence"]
                batch_item["model_result"] = result
                cache_key = model_score_cache_key(
                    batch_item["sha"],
                    batch_item.get("diff_mode", "parent"),
                    batch_item.get("diff_base_sha"),
                    batch_item.get("diff_extraction", model_diff_extraction),
                    frontier_score_context,
                )
                cache[cache_key] = result
        save_model_cache(cache_path, cache)

    preloaded_by_sha = {item["sha"]: item for item in preloaded_items}
    for record in records:
        item = preloaded_by_sha[record.sha]
        if "model_result" not in item:
            continue
        result = item["model_result"]
        record.semantic_score = float(result["semantic_score"])
        record.build_success_prob = float(result["build_success_prob"])
        causal_evidence = item.get("causal_evidence") or result.get("causal_evidence")
        if isinstance(causal_evidence, dict):
            record.features = merge_commit_features(
                result.get("features", []),
                causal_evidence_features(causal_evidence),
                record.features,
            )
        else:
            record.features = list(result.get("features", [])) or record.features
        record.evidence = ["model-scored"] + [str(entry) for entry in result.get("evidence", [])]
        if record.features:
            record.evidence.append(f"features: {', '.join(record.features[:6])}")
        record.diff_mode = str(item.get("diff_mode") or result.get("diff_mode") or "parent")
        record.diff_extraction = str(item.get("diff_extraction") or result.get("diff_extraction") or "raw")
        record.diff_base_sha = item.get("diff_base_sha") or result.get("diff_base_sha")
        record.diff_summary = str(item.get("diff_summary") or result.get("diff_summary") or "")
        record.causal_evidence = causal_evidence if isinstance(causal_evidence, dict) else None

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
    oracle_first_bad_sha = oracle_first_bad_sha_from_args(repo, args)
    selection_profile, oracle_derivation = (
        resolved_heuristic_selection_profile(repo, profile, args.heuristic_version, oracle_first_bad_sha)
        if args.scorer == "heuristic"
        else (profile, None)
    )
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
        selection_profile,
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
        model_diff_mode=args.model_diff_mode,
        model_diff_extraction=args.model_diff_extraction,
    )
    apply_feedback_bias(
        selection_profile,
        records,
        observations,
        enabled=args.scorer != "heuristic" or args.heuristic_version != "neutral",
    )
    decision = select_next_commit(
        selection_profile,
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
    if oracle_derivation is not None:
        print(f"Oracle first-bad: {oracle_first_bad_sha}")
        print(f"Oracle-derived keywords: {', '.join(selection_profile.keywords)}")
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
        if oracle_derivation is not None:
            payload["oracle_first_bad_derivation"] = oracle_derivation
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

    skipped_endpoints = 0
    cached_noops: list[str] = []
    for candidate in ordered_candidates:
        if has_internal_probe and candidate.sha in unresolved_set and candidate.sha in {good_endpoint, bad_endpoint}:
            skipped_endpoints += 1
            continue
        cached = find_observation_by_sha(observations, candidate.sha)
        if cached is None:
            return candidate, None
        next_unresolved = partition_interval(unresolved, candidate.sha, cached.verdict)
        if next_unresolved != unresolved:
            return candidate, cached
        cached_noops.append(f"{candidate.sha[:12]}:{cached.verdict}")

    detail = ", ".join(cached_noops[:5])
    if len(cached_noops) > 5:
        detail += ", ..."
    raise NoProgressCandidateError(
        "no candidate can shrink unresolved window "
        f"(unresolved={len(unresolved)}, candidates={len(ordered_candidates)}, "
        f"skipped_endpoints={skipped_endpoints}, cached_noops=[{detail}])"
    )


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
    oracle_first_bad_sha = oracle_first_bad_sha_from_args(repo, args)
    target_sha = args.target_sha
    observation_path = Path(args.observations) if args.observations else observation_path_for_issue(args.issue)
    observations = load_observations(observation_path)
    candidate_shas = None
    if args.candidate_file:
        candidate_shas = load_candidate_commits_from_file(Path(args.candidate_file))

    results = []
    for scorer_name in ("heuristic", "model"):
        selection_profile = (
            resolved_heuristic_selection_profile(repo, profile, args.heuristic_version, oracle_first_bad_sha)[0]
            if scorer_name == "heuristic"
            else profile
        )
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
            selection_profile,
            max_candidates=args.max_candidates,
            scorer=scorer_name,
            model_config=model_config,
            candidate_shas=candidate_shas,
            candidate_pruning=args.candidate_pruning,
            heuristic_version=args.heuristic_version,
            observations=observations,
        )
        apply_feedback_bias(
            selection_profile,
            records,
            observations,
            enabled=scorer_name != "heuristic" or args.heuristic_version != "neutral",
        )
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
    first_bad_sha = git(repo, "rev-parse", "--verify", first_bad_sha).strip()
    oracle_first_bad_sha = oracle_first_bad_sha_from_args(repo, args)
    selection_profile, oracle_derivation = (
        resolved_heuristic_selection_profile(repo, profile, args.heuristic_version, oracle_first_bad_sha)
        if args.scorer == "heuristic"
        else (profile, None)
    )
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
            selection_profile,
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
            model_diff_mode=args.model_diff_mode,
            model_diff_extraction=args.model_diff_extraction,
            last_tested_sha=tested[-1]["sha"] if tested else None,
        )
        decision = select_next_commit(
            selection_profile,
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
        try:
            selected, _cached = select_non_noop_candidate(
                decision.ranked_candidates,
                [],
                unresolved,
            )
        except NoProgressCandidateError:
            if args.candidate_pruning == "off":
                raise
            records, fallback_pruning_summary = make_records(
                repo,
                selection_profile,
                scorer=args.scorer,
                model_config=model_config,
                candidate_shas=unresolved,
                model_top_k=args.model_top_k,
                model_frontier=args.model_frontier,
                candidate_pruning="off",
                heuristic_version=args.heuristic_version,
                observations=[],
                metadata_cache=metadata_cache,
                model_cache=shared_model_cache,
                model_diff_mode=args.model_diff_mode,
                model_diff_extraction=args.model_diff_extraction,
                last_tested_sha=tested[-1]["sha"] if tested else None,
            )
            decision = select_next_commit(
                selection_profile,
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
            pruning_summary = {
                **fallback_pruning_summary,
                "fallback_reason": "no-progress-after-pruning",
                "fallback_from": pruning_summary,
            }
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
    if oracle_derivation is not None:
        print(f"Oracle keyword source: {oracle_first_bad_sha}")
        print(f"Oracle-derived keywords: {', '.join(selection_profile.keywords)}")
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
        print(f"Model diff mode: {args.model_diff_mode}")
        print(f"Model diff extraction: {args.model_diff_extraction}")
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
    oracle_first_bad_sha = oracle_first_bad_sha_from_args(repo, args)
    selection_profile, oracle_derivation = (
        resolved_heuristic_selection_profile(repo, profile, args.heuristic_version, oracle_first_bad_sha)
        if args.scorer == "heuristic"
        else (profile, None)
    )
    if oracle_derivation is not None:
        log_progress(
            "oracle-only heuristic enabled: "
            f"first_bad={oracle_first_bad_sha[:12]} keywords={len(selection_profile.keywords)}"
        )
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
    adaptive_top_k = adaptive_top_k_config_from_args(args) if args.scorer == "model" else None
    confidence_adaptive_frontier = (
        confidence_adaptive_frontier_config_from_args(args) if args.scorer == "model" else None
    )
    observation_conditioned_posterior = (
        observation_conditioned_posterior_config_from_args(args) if args.scorer == "model" else None
    )
    if adaptive_top_k is not None and confidence_adaptive_frontier is not None:
        raise ValueError("interval adaptive top-k and confidence-adaptive frontier cannot be combined")
    validate_confidence_adaptive_frontier(args.model_frontier, confidence_adaptive_frontier)
    validate_observation_conditioned_posterior(
        scorer=args.scorer,
        search_policy=args.search_policy,
        configured_frontier=args.model_frontier,
        model_top_k=args.model_top_k if args.scorer == "model" else None,
        model_diff_mode=args.model_diff_mode,
        model_diff_extraction=args.model_diff_extraction,
        posterior_config=observation_conditioned_posterior,
        adaptive_config=adaptive_top_k,
        confidence_config=confidence_adaptive_frontier,
    )
    model_cache_namespace = resolved_model_cache_namespace(
        args.model_cache_namespace if isinstance(getattr(args, "model_cache_namespace", None), str) else None,
        adaptive_top_k,
        confidence_adaptive_frontier,
        observation_conditioned_posterior,
    )
    if adaptive_top_k is not None:
        log_progress(
            "adaptive model frontier enabled: "
            f"k={adaptive_top_k.large_k} above {adaptive_top_k.threshold}, "
            f"k={adaptive_top_k.small_k} at or below"
        )
    if confidence_adaptive_frontier is not None:
        log_progress(
            "confidence-adaptive model frontier enabled: "
            f"k={args.model_top_k}, threshold={confidence_adaptive_frontier.threshold:g}, "
            "low-confidence=diverse"
        )
    if observation_conditioned_posterior is not None:
        log_progress(
            "observation-conditioned posterior enabled: "
            f"fixed-k=3, bad={observation_conditioned_posterior.bad_strength:g}, "
            f"good={observation_conditioned_posterior.good_strength:g}"
        )
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
        model_diff_mode=args.model_diff_mode,
        model_diff_extraction=args.model_diff_extraction if args.scorer == "model" else "raw",
        model_top_k=args.model_top_k if args.scorer == "model" else None,
        adaptive_top_k=adaptive_top_k.payload() if adaptive_top_k else None,
        confidence_adaptive_frontier=(
            confidence_adaptive_frontier.payload() if confidence_adaptive_frontier else None
        ),
        observation_conditioned_posterior=(
            observation_conditioned_posterior.payload() if observation_conditioned_posterior else None
        ),
        model_cache_namespace=model_cache_namespace,
        oracle_first_bad_sha=oracle_first_bad_sha,
    )
    run_history["heuristic_keywords"] = (
        list(selection_profile.keywords)
        if oracle_derivation is not None
        else effective_heuristic_keywords(profile, args.heuristic_version)
    )
    if oracle_derivation is not None:
        run_history["oracle_first_bad_derivation"] = oracle_derivation
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
                namespace=model_cache_namespace,
            )
        )
        if model_config is not None
        else None
    )
    shared_model_cache_path = (
        model_cache_path(
            profile.issue_id,
            model_config.model_name,
            scoring_version=resolved_model_scoring_version(model_config.observation_prompt_mode),
            namespace=model_cache_namespace,
        )
        if model_config is not None
        else None
    )
    if shared_model_cache is not None:
        log_progress(f"model cache loaded: {len(shared_model_cache)} entries")

    model_usage_summary = run_history.setdefault("model_usage", {}) if args.scorer == "model" else None
    if model_usage_summary is not None and not isinstance(model_usage_summary, dict):
        model_usage_summary = {}
        run_history["model_usage"] = model_usage_summary

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
            effective_top_k = effective_model_top_k(
                unresolved_before,
                args.model_top_k if args.scorer == "model" else None,
                adaptive_top_k,
            )
            score_context_payload = (
                model_score_context_payload(
                    unresolved,
                    observations,
                    effective_top_k,
                    args.model_frontier,
                    args.model_diff_mode,
                    args.model_diff_extraction,
                    args.observation_prompt_mode,
                    confidence_adaptive_frontier.payload() if confidence_adaptive_frontier else None,
                    observation_conditioned_posterior.payload()
                    if observation_conditioned_posterior
                    else None,
                )
                if args.scorer == "model"
                else None
            )
            score_context = (
                model_score_context_id(score_context_payload)
                if score_context_payload is not None
                else None
            )
            last_tested_sha = None
            if run_history.get("steps"):
                last_tested_sha = str(run_history["steps"][-1].get("sha") or "") or None
            log_progress(f"step {step}: make_records start unresolved={unresolved_before}")
            frontier_decisions: list[ModelFrontierDecision] = []
            records, pruning_summary = make_records(
                repo,
                selection_profile,
                scorer=args.scorer,
                model_config=model_config,
                candidate_shas=unresolved,
                model_top_k=effective_top_k if args.scorer == "model" else None,
                model_frontier=args.model_frontier,
                candidate_pruning=args.candidate_pruning,
                heuristic_version=args.heuristic_version,
                observations=observations,
                metadata_cache=metadata_cache,
                model_cache=shared_model_cache,
                model_diff_mode=args.model_diff_mode,
                model_diff_extraction=args.model_diff_extraction,
                last_tested_sha=last_tested_sha,
                model_usage_summary=model_usage_summary,
                model_cache_namespace=model_cache_namespace,
                model_score_context=score_context,
                confidence_adaptive_frontier=confidence_adaptive_frontier,
                model_frontier_decision_out=frontier_decisions,
            )
            frontier_decision = frontier_decisions[0] if frontier_decisions else None
            if model_usage_summary is not None:
                run_history["model_usage"] = model_usage_summary
                save_run_history(run_history_path, run_history)
            log_progress(f"step {step}: make_records done records={len(records)}")
            apply_feedback_bias(
                selection_profile,
                records,
                observations,
                enabled=(
                    (args.scorer != "heuristic" or args.heuristic_version != "neutral")
                    and observation_conditioned_posterior is None
                ),
            )
            log_progress(f"step {step}: select_next_commit start")
            decision = select_next_commit(
                selection_profile,
                records,
                lambda_weight=args.lambda_weight,
                build_success_power=args.build_success_power,
                search_policy=args.search_policy,
                hybrid_switch_window=args.hybrid_switch_window,
                calibrated_prior_power=args.calibrated_prior_power,
                calibrated_prior_bonus=args.calibrated_prior_bonus,
                weak_relevance_penalty=args.weak_relevance_penalty,
                weak_relevance_threshold=args.weak_relevance_threshold,
                observations=observations,
                observation_posterior_config=observation_conditioned_posterior,
            )
            log_progress(f"step {step}: selected {decision.selected.sha[:12]} mode={decision.selection_mode}")
            try:
                selected, cached = select_non_noop_candidate(
                    decision.ranked_candidates,
                    observations,
                    unresolved,
                )
            except NoProgressCandidateError as exc:
                if args.candidate_pruning == "off":
                    raise
                log_progress(f"step {step}: pruning produced no progress ({exc}); retrying with pruning disabled")
                records, fallback_pruning_summary = make_records(
                    repo,
                    selection_profile,
                    scorer=args.scorer,
                    model_config=model_config,
                    candidate_shas=unresolved,
                    model_top_k=effective_top_k if args.scorer == "model" else None,
                    model_frontier=args.model_frontier,
                    candidate_pruning="off",
                    heuristic_version=args.heuristic_version,
                    observations=observations,
                    metadata_cache=metadata_cache,
                    model_cache=shared_model_cache,
                    model_diff_mode=args.model_diff_mode,
                    model_diff_extraction=args.model_diff_extraction,
                    last_tested_sha=last_tested_sha,
                    model_usage_summary=model_usage_summary,
                    model_cache_namespace=model_cache_namespace,
                    model_score_context=score_context,
                    confidence_adaptive_frontier=confidence_adaptive_frontier,
                    model_frontier_decision_out=frontier_decisions,
                )
                frontier_decision = frontier_decisions[-1] if frontier_decisions else None
                log_progress(f"step {step}: fallback make_records done records={len(records)}")
                apply_feedback_bias(
                    selection_profile,
                    records,
                    observations,
                    enabled=(
                        (args.scorer != "heuristic" or args.heuristic_version != "neutral")
                        and observation_conditioned_posterior is None
                    ),
                )
                decision = select_next_commit(
                    selection_profile,
                    records,
                    lambda_weight=args.lambda_weight,
                    build_success_power=args.build_success_power,
                    search_policy=args.search_policy,
                    hybrid_switch_window=args.hybrid_switch_window,
                    calibrated_prior_power=args.calibrated_prior_power,
                    calibrated_prior_bonus=args.calibrated_prior_bonus,
                    weak_relevance_penalty=args.weak_relevance_penalty,
                    weak_relevance_threshold=args.weak_relevance_threshold,
                    observations=observations,
                    observation_posterior_config=observation_conditioned_posterior,
                )
                selected, cached = select_non_noop_candidate(
                    decision.ranked_candidates,
                    observations,
                    unresolved,
                )
                pruning_summary = {
                    **fallback_pruning_summary,
                    "fallback_reason": "no-progress-after-pruning",
                    "fallback_from": pruning_summary,
                }
                log_progress(f"step {step}: fallback selected {selected.sha[:12]} mode={decision.selection_mode}")
            log_progress(f"step {step}: non-noop selected {selected.sha[:12]} source={'cache' if cached else 'runner'}")
            top_candidates = [
                compact_candidate_view(record, rank + 1)
                for rank, record in enumerate(decision.ranked_candidates[:5])
            ]
            if selected.sha != decision.selected.sha:
                selected_compact = compact_candidate_view(selected, 0)
                selected_compact.update(
                    {
                        "selection_score": selected.selection_score,
                        "semantic_score": selected.semantic_score,
                        "build_success_prob": selected.build_success_prob,
                        "feedback_bias": selected.feedback_bias,
                        "posterior_bad_mass": selected.posterior_bad_mass,
                        "calibrated_suspicion_weight": selected.calibrated_suspicion_weight,
                        "calibrated_posterior_bad_mass": selected.calibrated_posterior_bad_mass,
                        "calibrated_posterior_info_gain": selected.calibrated_posterior_info_gain,
                    }
                )
                top_candidates.insert(
                    0,
                    selected_compact,
                )

            source = "cache"
            runner_duration_sec = None
            runner_history_fields: dict[str, object] = {}
            if cached is not None:
                verdict = cached.verdict
                summary = cached.summary or selected.subject
                runner_history_fields = runner_observation_history_fields(cached)
            else:
                source = "runner"
                log_progress(f"step {step}: checkout {selected.sha[:12]}")
                checkout_commit(repo, selected.sha)
                runner_started = time.monotonic()
                log_progress(f"step {step}: runner start")
                verdict, summary, runner_output, runner_evidence = run_issue_runner(profile, repo)
                runner_duration_sec = time.monotonic() - runner_started
                log_progress(f"step {step}: runner done verdict={verdict} duration={runner_duration_sec:.1f}s")
                features = merge_commit_features(
                    selected.features,
                    extract_commit_features(
                        selected.subject,
                        selected.body,
                        selected.changed_files,
                        selected.diff_text,
                    ),
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
                    build_failure=extract_build_failure_summary(runner_output) if verdict == "skip" else None,
                )
                observations = update_observation(observations, observation)
                save_observations(observation_path, observations)
                runner_history_fields = runner_observation_history_fields(observation)

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
                    "model_top_k": effective_top_k if args.scorer == "model" else None,
                    "adaptive_top_k": adaptive_top_k.payload() if adaptive_top_k else None,
                    "confidence_adaptive_frontier": (
                        confidence_adaptive_frontier.payload() if confidence_adaptive_frontier else None
                    ),
                    "observation_conditioned_posterior": (
                        observation_conditioned_posterior.payload()
                        if observation_conditioned_posterior
                        else None
                    ),
                    "model_frontier_decision": (
                        {
                            "configured_frontier": frontier_decision.configured_frontier,
                            "effective_frontier": frontier_decision.effective_frontier,
                            "confidence": round(frontier_decision.confidence, 6)
                            if frontier_decision.confidence is not None
                            else None,
                            "threshold": frontier_decision.threshold,
                            "reason": frontier_decision.reason,
                            "top_semantic_candidates": frontier_decision.top_semantic_candidates,
                            "selected_frontier_shas": frontier_decision.selected_shas,
                            "role_assignments": frontier_decision.role_assignments,
                            "observation_count": frontier_decision.observation_count,
                        }
                        if frontier_decision is not None
                        else None
                    ),
                    "model_score_context": score_context,
                    "model_score_context_payload": score_context_payload,
                    "candidate_pruning": pruning_summary,
                    "search_policy": args.search_policy,
                    "selection_mode": decision.selection_mode,
                    "selection": selection_payload(selected),
                    "top_candidates": top_candidates,
                    **runner_history_fields,
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
    if len(unresolved) == 1:
        run_history["first_bad_commit"] = unresolved[0]
    run_history["unresolved_window_path"] = str(unresolved_window_path)
    run_history["runner_build_count"] = len(runner_durations)
    run_history["runner_build_avg_duration_sec"] = (
        round(sum(runner_durations) / len(runner_durations), 3) if runner_durations else None
    )
    save_run_history(run_history_path, run_history)
    save_unresolved_window(unresolved_window_path, unresolved)
    artifact_bundle_dir = save_issue_artifact_bundle(
        issue_id=profile.issue_id,
        observation_path=observation_path,
        run_history_path=run_history_path,
        unresolved_window_path=unresolved_window_path,
        model_cache_path_value=shared_model_cache_path,
    )

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
    print(f"Issue artifact bundle: {artifact_bundle_dir}")
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
    suggest.add_argument("--heuristic-version", choices=HEURISTIC_VERSIONS, default="tuned", help="heuristic scoring version; oracle-first-bad is a diagnostic that leaks the validated answer")
    suggest.add_argument("--oracle-first-bad-sha", default=None, help="required known first-bad SHA for heuristic-version=oracle-first-bad")
    suggest.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    suggest.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    suggest.add_argument("--model-frontier", choices=("topk", "diverse", "evidence-diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    suggest.add_argument("--model-diff-mode", choices=("parent", "last-tested"), default="parent", help="diff evidence passed to model-scored candidates")
    suggest.add_argument("--model-diff-extraction", choices=("raw", "llm", "causal-llm", "causal-llm-impl"), default="raw", help="whether model-scored candidates use raw diff text, an LLM summary, balanced causal evidence, or implementation-first causal evidence")
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
    eval_email.add_argument("--heuristic-version", choices=HEURISTIC_VERSIONS, default="tuned", help="heuristic scoring version; oracle-first-bad is a diagnostic that leaks the validated answer")
    eval_email.add_argument("--oracle-first-bad-sha", default=None, help="required known first-bad SHA for heuristic-version=oracle-first-bad")
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
    simulate.add_argument("--heuristic-version", choices=HEURISTIC_VERSIONS, default="tuned", help="heuristic scoring version; oracle-first-bad is a diagnostic that leaks the validated answer")
    simulate.add_argument("--oracle-first-bad-sha", default=None, help="required known first-bad SHA for heuristic-version=oracle-first-bad")
    simulate.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    simulate.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    simulate.add_argument("--model-frontier", choices=("topk", "diverse", "evidence-diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    simulate.add_argument("--model-diff-mode", choices=("parent", "last-tested"), default="parent", help="diff evidence passed to model-scored candidates")
    simulate.add_argument("--model-diff-extraction", choices=("raw", "llm", "causal-llm", "causal-llm-impl"), default="raw", help="whether model-scored candidates use raw diff text, an LLM summary, balanced causal evidence, or implementation-first causal evidence")
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
    run_online.add_argument("--heuristic-version", choices=HEURISTIC_VERSIONS, default="tuned", help="heuristic scoring version; oracle-first-bad is a diagnostic that leaks the validated answer")
    run_online.add_argument("--oracle-first-bad-sha", default=None, help="required known first-bad SHA for heuristic-version=oracle-first-bad")
    run_online.add_argument("--heuristic-top-k", type=int, default=None, help=argparse.SUPPRESS)
    run_online.add_argument("--model-name", default=None, help="optional model override for scorer=model")
    run_online.add_argument("--model-top-k", type=int, default=3, help="number of heuristic-prefiltered commits to rescore with the model")
    run_online.add_argument(
        "--adaptive-top-k-threshold",
        type=int,
        default=None,
        help="use the large model frontier only above this unresolved-commit count",
    )
    run_online.add_argument(
        "--adaptive-top-k-large",
        type=int,
        default=None,
        help="model frontier size while unresolved commits exceed the adaptive threshold",
    )
    run_online.add_argument(
        "--adaptive-top-k-small",
        type=int,
        default=None,
        help="model frontier size while unresolved commits are at or below the adaptive threshold",
    )
    run_online.add_argument(
        "--confidence-adaptive-frontier-threshold",
        type=float,
        default=None,
        help="at fixed model k, use diverse anchors when the pre-model semantic confidence is below this value",
    )
    run_online.add_argument(
        "--observation-conditioned-posterior",
        action="store_true",
        help="use runner good/bad mechanism and component evidence to update the fixed-k3 calibrated posterior",
    )
    run_online.add_argument(
        "--model-cache-namespace",
        default=None,
        help="isolate model scores from other experiment configurations",
    )
    run_online.add_argument("--model-frontier", choices=("topk", "diverse", "evidence-diverse", "all"), default="topk", help="how to choose the model rescoring frontier")
    run_online.add_argument("--model-diff-mode", choices=("parent", "last-tested"), default="parent", help="diff evidence passed to model-scored candidates")
    run_online.add_argument("--model-diff-extraction", choices=("raw", "llm", "causal-llm", "causal-llm-impl"), default="raw", help="whether model-scored candidates use raw diff text, an LLM summary, balanced causal evidence, or implementation-first causal evidence")
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
