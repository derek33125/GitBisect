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
_PASS_RE = re.compile(r"Running pass\s+(?:'([^']+)'|\"([^\"]+)\")")
_DEMANGLED_FRAME_RE = re.compile(
    r"^[ \t]*\d+[ \t]+\S+[ \t]+0x[0-9a-f]+[ \t]+(\S[^\n]*?)[ \t]*\+[ \t]*\d+[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_MANGLED_SYMBOL_RE = re.compile(r"(_Z[A-Za-z0-9_]+)")
_SOURCE_PATH_RE = re.compile(r"(?:\.\./)*(?:llvm|clang|polly|mlir|lldb|compiler-rt)/[\w./+-]*\.(?:cpp|cc|c|h|hpp|inc)")
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
    kind: str = "unknown"
    assertion: str | None = None
    assert_source_file: str | None = None
    assert_function: str | None = None
    assert_class: str | None = None
    source_paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    pass_tokens: list[str] = field(default_factory=list)
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
    return value.lstrip("./").removeprefix("../")


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
    elif re.search(r"fatal error: error in backend", text):
        kind = "backend-fatal-error"
    elif re.search(r"Segmentation fault|SIGSEGV", text, re.IGNORECASE):
        kind = "segfault"

    source_paths = _unique([_normalize_path(item) for item in _SOURCE_PATH_RE.findall(text)])
    if assert_source_file and assert_source_file not in source_paths:
        source_paths.insert(0, assert_source_file)

    passes = [match.group(1) or match.group(2) or "" for match in _PASS_RE.finditer(text)]
    pass_tokens = _unique([token for value in passes for token in _clean_baseline_pass_tokens(value)])
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
    flags = [arg for arg in args if arg.startswith("-") and arg != "-o"]
    return CrashSignals(
        kind=kind,
        assertion=assertion,
        assert_source_file=assert_source_file,
        assert_function=assert_function,
        assert_class=assert_class,
        source_paths=source_paths,
        symbols=symbols,
        pass_tokens=pass_tokens,
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
