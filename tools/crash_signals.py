"""Extract stable, pre-bisect signals from an LLVM crash artifact."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath


_ASSERTION_RE = re.compile(
    r'^(?:(.*?):\s*)?(?:(\S+\.(?:cpp|cc|c|h|hpp|inc)):(\d+):\s*)?'
    r'.*?Assertion\s+[`\'\"]([\s\S]*?)[\'\"]\s+failed\.?',
    re.MULTILINE,
)
_ASSERT_SIGNATURE_RE = re.compile(r"^[^\n]*?\.(?:cpp|cc|c|h|hpp|inc):\d+:\s*(.+?):\s*Assertion", re.MULTILINE)
_DARWIN_ASSERT_RE = re.compile(
    r"Assertion failed:\s*\(([\s\S]*?)\),\s*function\s+([^,]+),\s*file\s+(\S+),\s*line\s+(\d+)"
)
_PASS_RE = re.compile(
    r"Running pass\s+(?:'([^']+)'|\"([\s\S]*?)\")"
    r"(?: on (?:function|module)\s+(?:'([^']*)'|\"([^\"]*)\"))?"
)
_DEMANGLED_FRAME_RE = re.compile(
    r"^[ \t]*\d+[ \t]+\S+[ \t]+0x[0-9a-f]+[ \t]+(\S[^\n]*?)[ \t]*\+[ \t]*\d+[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_MANGLED_SYMBOL_RE = re.compile(r"(_Z[A-Za-z0-9_]+)")
_SOURCE_PATH_RE = re.compile(
    r"(?:\.\./)*(?:llvm|clang-tools-extra|clang|polly|mlir|lldb|compiler-rt|lld|flang)"
    r"/[\w./+-]*\.(?:cpp|cc|c|h|hpp|inc)"
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_HUMAN_STUDY_PASS_STOPWORDS = frozenset(
    {
        "function",
        "module",
        "require",
        "invalidate",
        "devirt",
        "loop",
        "pass",
        "manager",
        "true",
        "false",
        "none",
        "verify",
        "fixpoint",
        "iterations",
        "threshold",
        "instcombine",
        "annotation",
        "attrs",
        "summary",
        "profile",
        "should",
        "run",
        "passes",
        "eager",
        "inv",
        "rerun",
        "skip",
        "recursive",
        "non",
        "bonus",
        "inst",
        "cond",
        "switch",
        "range",
        "icmp",
        "lookup",
        "keep",
        "loops",
        "hoist",
        "common",
        "insts",
        "loads",
        "stores",
        "with",
        "faulting",
        "sink",
        "speculate",
        "blocks",
        "simplify",
        "branch",
        "unpredictables",
        "forward",
        "arithmetic",
        "modify",
        "cfg",
        "max",
        "allowspeculation",
        "header",
        "duplication",
        "prepare",
        "for",
        "lto",
        "check",
        "exit",
        "count",
        "nontrivial",
        "trivial",
    }
)
_NOISE_SYMBOLS = frozenset(
    {
        "llvm::sys::PrintStackTrace",
        "llvm::sys::RunSignalHandlers",
        "llvm::sys::CleanupOnSignal",
        "llvm::CrashRecoveryContext::RunSafely",
        "pthread_kill",
        "gsignal",
        "abort",
    }
)
_HUMAN_STUDY_NOISE_SYMBOL_RE = re.compile(
    r"^(?:abort|gsignal|raise|pthread_kill|__assert_fail|__libc_\w+|"
    r"llvm::sys::|llvm::CrashRecoveryContext::|llvm::legacy::PassManagerImpl::run|"
    r"llvm::FPPassManager::runOn(?:Function|Module)|llvm::MachineFunctionPass::runOnFunction|"
    r"clang::ParseAST|clang::FrontendAction::Execute|clang::CompilerInstance::ExecuteAction|"
    r"clang::ExecuteCompilerInvocation|clang::driver::Compilation::Execute(?:Command|Jobs)|"
    r"clang::driver::Driver::ExecuteCompilation|cc1_main|main|_start|__libc_start_main)$"
)


@dataclass(frozen=True)
class QueryTerm:
    term: str
    kind: str


@dataclass(frozen=True)
class CrashSignals:
    headline: str = ""
    kind: str = "unknown"
    assertion: str | None = None
    assert_source_file: str | None = None
    assert_function: str | None = None
    assert_class: str | None = None
    source_paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)
    pass_records: list[dict[str, object]] = field(default_factory=list)
    pass_tokens: list[str] = field(default_factory=list)
    phases: list[dict[str, object]] = field(default_factory=list)
    verifier_message: str | None = None
    legacy_pass_format: bool = False
    tool: str | None = None
    flags: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["query_terms"] = [asdict(term) for term in derive_query_terms(self)]
        return payload


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [item for item in items if item and not (item in seen or seen.add(item))]


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    project_at = normalized.lower().rfind("llvm-project/")
    if project_at >= 0:
        normalized = normalized[project_at + len("llvm-project/") :]
    normalized = normalized.lstrip("/").removeprefix("../")
    return normalized.replace("llvm/tools/clang/", "clang/", 1)


def _strip_angle_groups(value: str) -> str:
    output: list[str] = []
    depth = 0
    for char in value:
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif not depth:
            output.append(char)
    return "".join(output)


def _clean_baseline_pass_tokens(pass_text: str) -> list[str]:
    """Preserve the general parser's broad pass signal for baseline BCR."""
    cleaned = _strip_angle_groups(pass_text)
    return _unique(
        [
            token.lower()
            for token in _TOKEN_RE.findall(cleaned)
            if token.lower() not in {"function", "module", "pass", "manager", "require", "invalidate"}
        ]
    )


def _clean_human_study_pass_tokens(pass_text: str) -> list[str]:
    """Use the fixed retrospective study normalization only for its pilot."""
    cleaned = _strip_angle_groups(pass_text)
    tokens: list[str] = []
    # Traverse the pipeline from its deepest portion first, matching the
    # retrospective human-study parser that defines the signal-pool cohort.
    for token in reversed(_TOKEN_RE.findall(cleaned)):
        normalized = token.lower()
        if normalized.startswith("no-") or len(normalized.replace("-", "")) < 5:
            continue
        if all(part in _HUMAN_STUDY_PASS_STOPWORDS for part in normalized.split("-")):
            continue
        tokens.append(normalized)
    return _unique(tokens)


def _clean_symbol(value: str) -> str:
    return _strip_angle_groups(value).split("(", 1)[0].strip()


def _demangle_symbols(text: str) -> list[str]:
    """Use the system demangler when a saved report has only Itanium frames."""
    symbols = _unique(_MANGLED_SYMBOL_RE.findall(text))
    if not symbols:
        return []
    try:
        import subprocess

        completed = subprocess.run(
            ["c++filt", "-n"],
            input="\n".join(symbols) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return _unique(
        [
            _clean_symbol(value)
            for value in completed.stdout.splitlines()
            if _clean_symbol(value) and _clean_symbol(value) not in _NOISE_SYMBOLS
        ]
    )


def parse_crash_report(text: str) -> CrashSignals:
    """Parse assertion, stack, pass, and invocation facts from a crash report."""
    headline = next(
        (
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and not re.search(
                r"PLEASE submit a bug report|^Stack dump:|^#\d+\s|"
                r"^clang\+\+: error: clang frontend command failed",
                line,
                re.IGNORECASE,
            )
        ),
        "",
    )
    assertion = None
    assert_source_file = None
    assert_function = None
    assert_class = None
    kind = "verifier" if re.search(r"Broken (?:function|module) found", text) else "unknown"
    match = _ASSERTION_RE.search(text)
    if match:
        kind = "assertion"
        assertion = match.group(4).strip()
        if match.group(2):
            assert_source_file = _normalize_path(match.group(2))
        signature = _ASSERT_SIGNATURE_RE.search(text)
        if signature:
            qualified = re.search(r"([A-Za-z_][\w:]*)\s*\(", signature.group(1))
            if qualified:
                parts = [part for part in qualified.group(1).split("::") if part and not part.startswith("{")]
                assert_function = parts[-1] if parts else None
                assert_class = parts[-2] if len(parts) > 1 else None
    elif (darwin_match := _DARWIN_ASSERT_RE.search(text)) is not None:
        kind = "assertion"
        assertion = darwin_match.group(1).strip()
        assert_function = darwin_match.group(2).strip()
        assert_source_file = _normalize_path(darwin_match.group(3))
    elif (unreachable := re.search(r"UNREACHABLE executed at\s+(\S+):(\d+)", text)) is not None:
        kind = "unreachable"
        assert_source_file = _normalize_path(unreachable.group(1))
        previous = text[: unreachable.start()].strip().splitlines()
        if previous:
            candidate = previous[-1].strip()
            if candidate and len(candidate) < 200 and not re.search(
                r"PLEASE submit|Compiler returned|Stack dump|Program arguments|^#\d",
                candidate,
                re.IGNORECASE,
            ):
                assertion = candidate
    elif re.search(r"fatal error: error in backend", text):
        kind = "backend-fatal-error"
    elif re.search(r"Segmentation fault|SIGSEGV", text, re.IGNORECASE):
        kind = "segfault"

    source_paths = _unique([_normalize_path(item) for item in _SOURCE_PATH_RE.findall(text)])
    if assert_source_file and assert_source_file not in source_paths:
        source_paths.insert(0, assert_source_file)

    pass_records = [
        {
            "pass": pass_match.group(1) or pass_match.group(2) or "",
            "on": pass_match.group(3) or pass_match.group(4) or None,
        }
        for pass_match in _PASS_RE.finditer(text)
    ]
    passes = [str(record["pass"]) for record in pass_records]
    pass_tokens = _unique([token for value in passes for token in _clean_baseline_pass_tokens(value)])
    phases: list[dict[str, object]] = []
    dump_at = text.find("Stack dump:")
    if dump_at >= 0:
        for phase_match in re.finditer(r"^[ \t]*(\d+)\.\s+(.*)$", text[dump_at:], re.MULTILINE):
            phrase = phase_match.group(2).strip()
            if phrase.startswith(("Program arguments:", "Running pass")):
                continue
            phrase = re.sub(r"^\S*?[^\s:]+:\d+:\d+:\s*", "", phrase)
            phrase = re.sub(r"^<[^>]*>\s*", "", phrase)
            phrase = re.sub(r"""['"][\s\S]*$""", "", phrase)
            phrase = re.sub(r"\(.*$", "", phrase).strip()
            if 2 <= len(phrase.split()) and len(phrase) <= 80:
                phases.append({"index": int(phase_match.group(1)), "text": phrase})
    demangled_symbols = _unique(
        [
            _clean_symbol(match.group(1))
            for match in _DEMANGLED_FRAME_RE.finditer(text)
            if _clean_symbol(match.group(1)) not in _NOISE_SYMBOLS
        ]
    )
    symbols = _unique([*demangled_symbols, *_demangle_symbols(text)])
    args_match = re.search(r"Program arguments:\s*(.+)", text)
    args = args_match.group(1).split() if args_match else []
    tool = PurePosixPath(args[0]).name if args else None
    if not tool and match and match.group(1):
        candidate_tool = PurePosixPath(match.group(1).strip()).name
        if re.fullmatch(r"[\w.+-]+", candidate_tool):
            tool = candidate_tool
    flags = [arg for arg in args if arg.startswith("-") and arg != "-o"]
    verifier_message = None
    verifier_at = next(
        (
            line.start()
            for line in re.finditer(r"^.*$", text, re.MULTILINE)
            if re.search(r"Broken (?:function|module) found", line.group(0))
        ),
        None,
    )
    if verifier_at is not None:
        verifier_message = text[:verifier_at].strip() or None
    return CrashSignals(
        headline=headline,
        kind=kind,
        assertion=assertion,
        assert_source_file=assert_source_file,
        assert_function=assert_function,
        assert_class=assert_class,
        source_paths=source_paths,
        symbols=symbols,
        passes=passes,
        pass_records=pass_records,
        pass_tokens=pass_tokens,
        phases=phases,
        verifier_message=verifier_message,
        legacy_pass_format=bool(re.search(r"Running pass\s+'", text)),
        tool=tool,
        flags=flags,
    )


def human_study_signal_payload(text: str) -> dict[str, object]:
    """Return the fixed human-study signal normalization for the pilot only."""
    payload = parse_crash_report(text).payload()
    passes = [match.group(1) or match.group(2) or "" for match in _PASS_RE.finditer(text)]
    payload["pass_tokens"] = _unique(
        [token for value in passes for token in _clean_human_study_pass_tokens(value)]
    )
    payload["symbols"] = [
        symbol
        for symbol in payload["symbols"]
        if not _HUMAN_STUDY_NOISE_SYMBOL_RE.match(symbol)
    ]
    payload["normalization"] = "human-study-v1"
    return payload


def causal_evidence_guided_signal_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    """Remove infrastructure frames before CEG prior and retrieval use.

    Keep the general parser unchanged for historical lanes. CEG consumes the
    stricter compiler-frame normalization so wrapper families such as
    ExecuteAction/ExecuteJobs cannot become independent causal signals.
    """
    normalized = dict(payload)
    normalized_symbols = [
        str(symbol)
        for symbol in payload.get("symbols", [])
        if not _HUMAN_STUDY_NOISE_SYMBOL_RE.match(str(symbol))
    ]
    normalized["symbols"] = normalized_symbols
    allowed_stack_terms = {
        symbol.rsplit("::", 1)[-1]
        for symbol in normalized_symbols
    }
    normalized["query_terms"] = [
        dict(entry)
        for entry in payload.get("query_terms", [])
        if isinstance(entry, dict)
        and (
            str(entry.get("kind", "")) != "stack-symbol"
            or str(entry.get("term", "")) in allowed_stack_terms
        )
    ]
    normalized["normalization"] = "ceg-v3-framework-filter"
    return normalized


def derive_query_terms(signals: CrashSignals) -> list[QueryTerm]:
    """Derive ordered anchors without using issue-specific or answer-derived data."""
    terms: list[QueryTerm] = []

    def add(value: str | None, kind: str) -> None:
        if not value or len(value) < 4 or any(term.term == value for term in terms):
            return
        terms.append(QueryTerm(value, kind))

    add(signals.assert_function, "assertion-function")
    add(signals.assert_class, "assertion-class")
    source_paths = list(signals.source_paths)
    if signals.assert_source_file and signals.assert_source_file not in source_paths:
        source_paths.insert(0, signals.assert_source_file)
    for path in source_paths[:2]:
        stem = PurePosixPath(path).stem
        add(stem, "assertion-file-component")
        add(f"{stem}Updater", "component-updater")
    for symbol in signals.symbols[:8]:
        add(symbol.rsplit("::", 1)[-1], "stack-symbol")
    for pass_token in signals.pass_tokens[:8]:
        add(pass_token, "running-pass")
    return terms
