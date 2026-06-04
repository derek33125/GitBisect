#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = ROOT / "tools" / "lm_bisect_profiles.json"
RUN_HISTORY_DIR = ROOT / "results" / "lm_bisect_runs"
ISSUES_DIR = ROOT / "results" / "issues"
DEFAULT_LLVM_DIR = Path("/home/derek331/research/gitbisect-work/llvm-project")

METHOD_STYLES = {
    "Git bisect": {"color": "#4C78A8", "linestyle": "-", "marker": "o"},
    "Real Heuristic LM-bisect": {"color": "#E45756", "linestyle": "--", "marker": "s"},
    "Real Model-Guided LM-bisect": {"color": "#54A24B", "linestyle": "-.", "marker": "D"},
    "Real Heuristic Calibrated Posterior": {"color": "#B279A2", "linestyle": "--", "marker": "^"},
    "Real Model Calibrated Posterior": {"color": "#72B7B2", "linestyle": ":", "marker": "P"},
    "Sim Heuristic Calibrated Posterior": {"color": "#B279A2", "linestyle": "--", "marker": "^"},
    "Sim Model Calibrated Posterior": {"color": "#72B7B2", "linestyle": ":", "marker": "P"},
}

VERDICT_COLORS = {
    "good": "#54A24B",
    "bad": "#E45756",
    "skip": "#F58518",
}

BISECT_STATE_RE = re.compile(r"^# (good|bad|skip): \[([0-9a-f]{7,40})\] (.*)$")
BISECT_CMD_RE = re.compile(r"^git bisect (good|bad|skip) ([0-9a-f]{7,40})$")
FIRST_BAD_RE = re.compile(r"^# first bad commit: \[([0-9a-f]{7,40})\] (.*)$")
ONLINE_STEP_RE = re.compile(
    r"^-\s+(good|bad|skip)\s+([0-9a-f]{7,40})\s+unresolved=(\d+)\s+"
    r"(?:candidates=\d+/\d+\s+)?"
    r"(?:source=([A-Za-z0-9_-]+)\s+)?"
    r"(.*)$"
)
ONLINE_FINAL_RE = re.compile(r"^\s*-\s+([0-9a-f]{7,40})$")
ANY_SEARCH_POLICY = object()
ANY_MODEL_NAME = object()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate commit-axis search-path data plus PNG/SVG figures for an LLVM bisect issue."
    )
    parser.add_argument("--issue", required=True, help="issue id from tools/lm_bisect_profiles.json, e.g. pr176682")
    parser.add_argument(
        "--llvm-dir",
        type=Path,
        default=Path(os.environ.get("LLVM_PROJECT_DIR", DEFAULT_LLVM_DIR)),
        help="Path to the llvm-project checkout used to resolve commit metadata.",
    )
    parser.add_argument(
        "--output-suffix",
        default="",
        help="Optional suffix appended to generated search-path artifact names, e.g. v2 -> pr176682-search-paths-v2.png",
    )
    parser.add_argument(
        "--method-set",
        choices=("all", "original", "new"),
        default="all",
        help="Restrict the plotted methods to the original baseline set or the new calibrated-posterior set.",
    )
    return parser.parse_args()


def load_profiles() -> dict[str, dict]:
    return json.loads(PROFILES_PATH.read_text())


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    content = path.read_text().strip()
    if not content:
        return {}
    return json.loads(content)


def parse_git_bisect_log(text: str, *, good_endpoint: str, bad_endpoint: str) -> tuple[list[dict], dict | None]:
    subjects: dict[str, str] = {}
    steps: list[dict] = []
    first_bad: dict | None = None

    for line in text.splitlines():
        state_match = BISECT_STATE_RE.match(line)
        if state_match:
            subjects[state_match.group(2)] = state_match.group(3)
            continue

        cmd_match = BISECT_CMD_RE.match(line)
        if cmd_match:
            verdict = cmd_match.group(1)
            sha = cmd_match.group(2)
            if sha in {good_endpoint, bad_endpoint}:
                continue
            steps.append(
                {
                    "step": len(steps) + 1,
                    "sha": sha,
                    "verdict": verdict,
                    "subject": subjects.get(sha, ""),
                }
            )
            continue

        first_bad_match = FIRST_BAD_RE.match(line)
        if first_bad_match:
            first_bad = {
                "sha": first_bad_match.group(1),
                "subject": first_bad_match.group(2),
            }

    return steps, first_bad


def parse_online_log(text: str) -> tuple[list[dict], list[str]]:
    in_path = False
    in_final_window = False
    steps: list[dict] = []
    final_window: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Tested path:":
            in_path = True
            in_final_window = False
            continue
        if stripped == "Final unresolved window:":
            in_path = False
            in_final_window = True
            continue

        if in_path:
            match = ONLINE_STEP_RE.match(line)
            if not match:
                continue
            steps.append(
                {
                    "step": len(steps) + 1,
                    "sha": match.group(2),
                    "verdict": match.group(1),
                    "subject": match.group(5),
                    "source": match.group(4),
                    "unresolved_before": int(match.group(3)),
                }
            )
            continue

        if in_final_window:
            match = ONLINE_FINAL_RE.match(line)
            if match:
                final_window.append(match.group(1))

    return steps, final_window


def merge_online_log_segments(segments: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    merged: list[dict] = []
    final_window: list[str] = []

    for _name, text in segments:
        steps, segment_final = parse_online_log(text)
        if not steps:
            continue

        while steps and merged and steps[0]["sha"] == merged[-1]["sha"] and steps[0]["verdict"] == merged[-1]["verdict"]:
            steps.pop(0)

        for row in steps:
            next_row = dict(row)
            next_row["step"] = len(merged) + 1
            merged.append(next_row)

        if segment_final:
            final_window = segment_final

    return merged, final_window


def history_matches(
    history: dict,
    *,
    issue_id: str,
    scorer: str,
    search_policy=ANY_SEARCH_POLICY,
    model_name=ANY_MODEL_NAME,
) -> bool:
    if history.get("issue") != issue_id or history.get("scorer") != scorer:
        return False
    if search_policy is not ANY_SEARCH_POLICY and history.get("search_policy") != search_policy:
        return False
    if model_name is not ANY_MODEL_NAME and history.get("model_name") != model_name:
        return False
    return True


def find_best_run_history(
    run_history_dir: Path,
    *,
    issue_id: str,
    scorer: str,
    search_policy=ANY_SEARCH_POLICY,
    model_name=ANY_MODEL_NAME,
) -> tuple[Path | None, dict]:
    best_path: Path | None = None
    best_history: dict = {}
    best_key = (-1, "")

    for candidate in sorted(run_history_dir.glob(f"{issue_id}*.json")):
        history = load_json(candidate)
        if not history:
            continue
        if not history_matches(
            history,
            issue_id=issue_id,
            scorer=scorer,
            search_policy=search_policy,
            model_name=model_name,
        ):
            continue
        steps = history.get("steps", [])
        key = (len(steps), candidate.name)
        if key > best_key:
            best_path = candidate
            best_history = history
            best_key = key

    return best_path, best_history


def list_matching_run_history_artifacts(
    run_history_dir: Path,
    *,
    issue_id: str,
    scorer: str,
    search_policy=ANY_SEARCH_POLICY,
    model_name=ANY_MODEL_NAME,
) -> list[str]:
    artifacts: list[str] = []
    for candidate in sorted(run_history_dir.glob(f"{issue_id}*.json")):
        history = load_json(candidate)
        if not history:
            continue
        if not history_matches(
            history,
            issue_id=issue_id,
            scorer=scorer,
            search_policy=search_policy,
            model_name=model_name,
        ):
            continue
        artifacts.append(relative_to_root(candidate))
    return artifacts


def run_history_steps(history: dict) -> list[dict]:
    rows: list[dict] = []
    for row in history.get("steps", []):
        rows.append(
            {
                "step": row["step"],
                "sha": row["sha"],
                "verdict": row["verdict"],
                "subject": row["subject"],
                "source": row.get("source"),
                "unresolved_before": row.get("unresolved_before"),
                "unresolved_after": row.get("unresolved_after"),
            }
        )
    return rows


def issue_log_sort_key(issue_id: str, scorer: str, path: Path) -> tuple[int, str]:
    base_name = f"{issue_id}-run-online-{scorer}.log"
    if path.name == base_name:
        return (0, path.name)
    if "full" in path.stem:
        return (1, path.name)
    if "continue" in path.stem:
        return (2, path.name)
    return (1, path.name)


def renumber_steps(steps: list[dict]) -> list[dict]:
    numbered: list[dict] = []
    for row in steps:
        next_row = dict(row)
        next_row["step"] = len(numbered) + 1
        numbered.append(next_row)
    return numbered


def load_online_log_trace_from_paths(paths: list[Path]) -> tuple[list[dict], list[str], list[str]]:
    candidates = [path for path in paths if path.is_file() and path.stat().st_size > 0]
    if not candidates:
        return [], [], []
    segments = [(relative_to_root(path), path.read_text()) for path in candidates]
    steps, final_window = merge_online_log_segments(segments)
    sources = [name for name, _text in segments if _text.strip()]
    return steps, final_window, sources


def load_best_online_log_trace(issue_id: str, scorer: str) -> tuple[list[dict], list[str], list[str]]:
    issue_dir = ISSUES_DIR / issue_id
    candidates = [
        path
        for path in issue_dir.glob(f"{issue_id}-run-online-{scorer}*.log")
        if path.is_file() and path.stat().st_size > 0
    ]
    candidates.sort(key=lambda path: issue_log_sort_key(issue_id, scorer, path))
    return load_online_log_trace_from_paths(candidates)


def load_simulated_trace(issue_id: str, scorer: str, search_policy: str) -> tuple[list[dict], list[str], list[str]]:
    issue_dir = ISSUES_DIR / issue_id
    path = issue_dir / f"{issue_id}-simulate-{scorer}-{search_policy}.log"
    if not path.exists() or path.stat().st_size == 0:
        return [], [], []
    steps, final_window = parse_online_log(path.read_text())
    if not steps:
        return [], [], []
    return steps, final_window, [relative_to_root(path)]


def prefer_log_trace(history_steps: list, log_steps: list) -> bool:
    return len(history_steps) < 2 and len(log_steps) > len(history_steps)


def choose_method_trace(issue_id: str, scorer: str, label: str) -> dict | None:
    history_path, history = find_best_run_history(
        RUN_HISTORY_DIR,
        issue_id=issue_id,
        scorer=scorer,
        search_policy=None,
    )
    history_steps = run_history_steps(history)
    history_final_window = history.get("final_unresolved_window", [])

    issue_dir = ISSUES_DIR / issue_id
    log_steps, log_final_window, log_sources = load_online_log_trace_from_paths(
        [issue_dir / f"{issue_id}-run-online-{scorer}.log"]
    )

    if not history_steps and not log_steps:
        return None

    if prefer_log_trace(history_steps, log_steps):
        source_artifacts = list(log_sources)
        for artifact in list_matching_run_history_artifacts(
            RUN_HISTORY_DIR,
            issue_id=issue_id,
            scorer=scorer,
            search_policy=None,
        ):
            if artifact not in source_artifacts:
                source_artifacts.append(artifact)
        return {
            "label": label,
            "steps": log_steps,
            "final_unresolved_window": log_final_window,
            "source_kind": "summary-log-reconstruction",
            "source_artifacts": source_artifacts,
        }

    artifacts = [relative_to_root(history_path)] if history_path is not None else []
    return {
        "label": label,
        "steps": history_steps,
        "final_unresolved_window": history_final_window,
        "source_kind": "run-history-json",
        "source_artifacts": artifacts,
    }


def build_trace(
    *,
    label: str,
    steps: list[dict],
    final_unresolved_window: list[str],
    source_kind: str,
    source_artifacts: list[str],
) -> dict | None:
    if not steps:
        return None
    return {
        "label": label,
        "steps": renumber_steps(steps),
        "final_unresolved_window": final_unresolved_window,
        "source_kind": source_kind,
        "source_artifacts": source_artifacts,
    }


def choose_real_calibrated_heuristic_pr172195_trace() -> dict | None:
    issue_dir = ISSUES_DIR / "pr172195"
    fullrerun = issue_dir / "pr172195-run-online-heuristic-calibrated-posterior-fullrerun.log"
    continuation = issue_dir / "pr172195-run-online-heuristic-calibrated-posterior-stuck-rerun.log"
    if not fullrerun.exists() or not continuation.exists():
        return None

    full_steps, _full_final_window = parse_online_log(fullrerun.read_text())
    prefix_steps = [row for row in full_steps if row.get("unresolved_before") is None or row["unresolved_before"] > 3]
    continuation_steps, final_window = parse_online_log(continuation.read_text())

    return build_trace(
        label="Real Heuristic Calibrated Posterior",
        steps=prefix_steps + continuation_steps,
        final_unresolved_window=final_window,
        source_kind="summary-log-reconstruction",
        source_artifacts=[relative_to_root(fullrerun), relative_to_root(continuation)],
    )


def choose_real_calibrated_method_trace(issue_id: str, scorer: str, label: str) -> dict | None:
    if issue_id == "pr172195" and scorer == "heuristic":
        special = choose_real_calibrated_heuristic_pr172195_trace()
        if special is not None:
            return special

    if issue_id == "pr172195" and scorer == "model":
        trace_only_path = RUN_HISTORY_DIR / "pr172195-model-gpt-5.4-mini-calibrated-posterior-obs-trace-only.json"
        trace_only_history = load_json(trace_only_path)
        trace_only_steps = run_history_steps(trace_only_history)
        trace_only_final_window = trace_only_history.get("final_unresolved_window", [])
        if trace_only_steps:
            return build_trace(
                label=label,
                steps=trace_only_steps,
                final_unresolved_window=trace_only_final_window,
                source_kind="run-history-json",
                source_artifacts=[relative_to_root(trace_only_path)],
            )

    history_path, history = find_best_run_history(
        RUN_HISTORY_DIR,
        issue_id=issue_id,
        scorer=scorer,
        search_policy="calibrated-posterior",
    )
    history_steps = run_history_steps(history)
    history_final_window = history.get("final_unresolved_window", [])

    issue_dir = ISSUES_DIR / issue_id
    log_paths = [
        issue_dir / f"{issue_id}-run-online-{scorer}-calibrated-posterior-coldstart.log",
        issue_dir / f"{issue_id}-run-online-{scorer}-calibrated-posterior.log",
    ]
    log_steps, log_final_window, log_sources = load_online_log_trace_from_paths(log_paths)

    if not history_steps and not log_steps:
        return None

    if prefer_log_trace(history_steps, log_steps):
        source_artifacts = list(log_sources)
        for artifact in list_matching_run_history_artifacts(
            RUN_HISTORY_DIR,
            issue_id=issue_id,
            scorer=scorer,
            search_policy="calibrated-posterior",
        ):
            if artifact not in source_artifacts:
                source_artifacts.append(artifact)
        return build_trace(
            label=label,
            steps=log_steps,
            final_unresolved_window=log_final_window,
            source_kind="summary-log-reconstruction",
            source_artifacts=source_artifacts,
        )

    return build_trace(
        label=label,
        steps=history_steps,
        final_unresolved_window=history_final_window,
        source_kind="run-history-json",
        source_artifacts=[relative_to_root(history_path)] if history_path is not None else [],
    )


def choose_simulated_method_trace(issue_id: str, scorer: str, search_policy: str, label: str) -> dict | None:
    steps, final_window, artifacts = load_simulated_trace(issue_id, scorer, search_policy)
    if not steps:
        return None
    return {
        "label": label,
        "steps": steps,
        "final_unresolved_window": final_window,
        "source_kind": "simulate-online-log",
        "source_artifacts": artifacts,
    }


def choose_calibrated_method_trace(issue_id: str, scorer: str, *, real_label: str, sim_label: str) -> dict | None:
    real = choose_real_calibrated_method_trace(issue_id, scorer, real_label)
    if real is not None:
        return real
    return choose_simulated_method_trace(issue_id, scorer, "calibrated-posterior", sim_label)


def resolve_full_shas(llvm_dir: Path, shas: list[str]) -> dict[str, str]:
    unique = []
    seen = set()
    for sha in shas:
        if sha and sha not in seen:
            seen.add(sha)
            unique.append(sha)
    if not unique:
        return {}

    completed = subprocess.run(
        ["git", "-C", str(llvm_dir), "rev-parse", *[f"{sha}^{{commit}}" for sha in unique]],
        check=True,
        capture_output=True,
        text=True,
    )
    resolved = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return dict(zip(unique, resolved))


def load_commit_metadata(llvm_dir: Path, shas: list[str]) -> dict[str, dict]:
    unique = []
    seen = set()
    for sha in shas:
        if sha not in seen:
            seen.add(sha)
            unique.append(sha)
    if not unique:
        return {}

    fmt = "%H%x09%ct%x09%cs%x09%s"
    completed = subprocess.run(
        ["git", "-C", str(llvm_dir), "show", "-s", f"--format={fmt}", *unique],
        check=True,
        capture_output=True,
        text=True,
    )

    metadata: dict[str, dict] = {}
    for line in completed.stdout.splitlines():
        sha, timestamp, date_text, subject = line.split("\t", 3)
        metadata[sha] = {
            "sha": sha,
            "short": sha[:12],
            "timestamp": int(timestamp),
            "date": date_text,
            "subject": subject,
        }
    return metadata


def expand_shas(steps: list[dict], resolution: dict[str, str]) -> list[dict]:
    expanded: list[dict] = []
    for row in steps:
        next_row = dict(row)
        next_row["sha"] = resolution.get(row["sha"], row["sha"])
        expanded.append(next_row)
    return expanded


def build_commit_axis(methods: list[dict], metadata: dict[str, dict], endpoints: list[str]) -> tuple[list[str], dict[str, int]]:
    shas = set(endpoints)
    for method in methods:
        shas.update(row["sha"] for row in method["steps"])
        shas.update(method.get("final_unresolved_window", []))

    ordered = sorted(shas, key=lambda sha: (metadata[sha]["timestamp"], sha))
    positions = {sha: index + 1 for index, sha in enumerate(ordered)}
    return ordered, positions


def marker_text_color(verdict: str) -> str:
    if verdict == "skip":
        return "#1F1F1F"
    return "white"


def tick_label(sha: str, metadata: dict[str, dict], *, good_sha: str, bad_sha: str) -> str:
    meta = metadata[sha]
    date_suffix = meta["date"][5:]
    if sha == good_sha:
        return f"G {meta['short']}\n{date_suffix}"
    if sha == bad_sha:
        return f"B {meta['short']}\n{date_suffix}"
    return f"{meta['short']}\n{date_suffix}"


def build_markers(reference_first_bad: str, data: dict) -> list[dict]:
    markers = [
        {"sha": data["good_commit"], "label": "known good endpoint", "color": "#9E9E9E", "linestyle": ":"},
        {"sha": data["bad_commit"], "label": "known bad endpoint", "color": "#9E9E9E", "linestyle": ":"},
        {"sha": reference_first_bad, "label": "reference first bad commit", "color": "#666666", "linestyle": ":"},
    ]

    seen = {marker["sha"] for marker in markers}
    for method in data["methods"]:
        final_window = method.get("final_unresolved_window", [])
        if len(final_window) != 1:
            continue
        culprit = final_window[0]
        if culprit in seen:
            continue
        label = f"{method['label']} final culprit"
        markers.append({"sha": culprit, "label": label, "color": "#B279A2", "linestyle": "--"})
        seen.add(culprit)

    return markers


def method_set_filter(method_set: str) -> set[str] | None:
    if method_set == "all":
        return None
    if method_set == "original":
        return {
            "Git bisect",
            "Real Heuristic LM-bisect",
            "Real Model-Guided LM-bisect",
        }
    if method_set == "new":
        return {
            "Git bisect",
            "Real Heuristic Calibrated Posterior",
            "Real Model Calibrated Posterior",
            "Sim Heuristic Calibrated Posterior",
            "Sim Model Calibrated Posterior",
        }
    raise ValueError(f"unknown method set: {method_set}")


def filter_methods(methods: list[dict], method_set: str) -> list[dict]:
    allowed = method_set_filter(method_set)
    if allowed is None:
        return methods
    filtered = [method for method in methods if method["label"] in allowed]
    if not filtered:
        raise RuntimeError(f"no methods matched method set {method_set!r}")
    return filtered


def annotate_shared_commits(ax, methods: list[dict], positions: dict[str, int], metadata: dict[str, dict]) -> None:
    visit_counts: dict[str, int] = {}
    last_step_by_sha: dict[str, int] = {}

    for method in methods:
        for row in method["steps"]:
            visit_counts[row["sha"]] = visit_counts.get(row["sha"], 0) + 1
            last_step_by_sha[row["sha"]] = max(last_step_by_sha.get(row["sha"], 0), row["step"])

    shared = sorted((sha for sha, count in visit_counts.items() if count >= 2), key=lambda sha: positions[sha])
    for sha in shared:
        ax.annotate(
            f"shared {metadata[sha]['short']}",
            xy=(positions[sha], last_step_by_sha[sha]),
            xytext=(0, 16),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333333",
            arrowprops={"arrowstyle": "-", "color": "#888888", "lw": 0.9},
        )


def plot_issue_data(data: dict, out_png: Path, out_svg: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.style.use("seaborn-v0_8-whitegrid")

    ordered_shas = [row["sha"] for row in data["commit_axis"]]
    positions = {row["sha"]: row["position"] for row in data["commit_axis"]}
    metadata = {row["sha"]: row for row in data["commit_axis"]}
    max_step = max(len(method["steps"]) for method in data["methods"])

    fig, ax = plt.subplots(figsize=(max(14, len(ordered_shas) * 0.52), 7), constrained_layout=True)

    for method in data["methods"]:
        style = METHOD_STYLES[method["label"]]
        xs = [positions[row["sha"]] for row in method["steps"]]
        ys = [row["step"] for row in method["steps"]]

        ax.plot(
            xs,
            ys,
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=2.2,
            alpha=0.9,
            zorder=2,
        )

        for row, x_value, y_value in zip(method["steps"], xs, ys):
            verdict = row["verdict"]
            ax.scatter(
                x_value,
                y_value,
                s=190,
                marker=style["marker"],
                facecolor=VERDICT_COLORS[verdict],
                edgecolor=style["color"],
                linewidth=1.6,
                zorder=3,
            )
            ax.text(
                x_value,
                y_value,
                str(row["step"]),
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
                color=marker_text_color(verdict),
                zorder=4,
            )

    annotate_shared_commits(ax, data["methods"], positions, metadata)

    for marker in data["markers"]:
        x_value = positions[marker["sha"]]
        ax.axvline(x_value, color=marker["color"], linestyle=marker["linestyle"], linewidth=1.2, zorder=1)
        ax.text(
            x_value,
            max_step + 0.8,
            marker["label"],
            ha="center",
            va="bottom",
            fontsize=9,
            color=marker["color"],
            rotation=90,
        )

    ax.set_title(f"{data['issue'].upper()} Search Paths On A Commit-Order Axis", fontsize=15, weight="bold")
    ax.set_xlabel("Commit chronology (short SHA, with MM-DD date under each commit)")
    ax.set_ylabel("Search step")
    ax.set_xlim(0.5, len(ordered_shas) + 0.5)
    ax.set_ylim(0.5, max_step + 1.2)
    ax.set_xticks(range(1, len(ordered_shas) + 1))
    ax.set_xticklabels(
        [
            tick_label(row["sha"], metadata, good_sha=data["good_commit"], bad_sha=data["bad_commit"])
            for row in data["commit_axis"]
        ],
        rotation=55,
        ha="right",
        fontsize=8,
    )
    ax.set_yticks(range(1, max_step + 1))
    ax.grid(axis="x", linestyle=":", alpha=0.18)
    ax.grid(axis="y", linestyle="--", alpha=0.30)

    method_handles = [
        Line2D(
            [0],
            [0],
            color=METHOD_STYLES[method["label"]]["color"],
            linestyle=METHOD_STYLES[method["label"]]["linestyle"],
            marker=METHOD_STYLES[method["label"]]["marker"],
            markerfacecolor="white",
            markeredgecolor=METHOD_STYLES[method["label"]]["color"],
            linewidth=2.2,
            markersize=8,
            label=method["label"],
        )
        for method in data["methods"]
    ]
    verdict_handles = [
        Line2D(
            [0],
            [0],
            color="none",
            marker="o",
            markerfacecolor=color,
            markeredgecolor="#555555",
            markersize=8,
            label=verdict,
        )
        for verdict, color in VERDICT_COLORS.items()
    ]
    legend_methods = ax.legend(handles=method_handles, loc="upper left", frameon=True, title="Method")
    ax.add_artist(legend_methods)
    ax.legend(handles=verdict_handles, loc="upper right", frameon=True, title="Verdict")

    fig.savefig(out_png, dpi=220, bbox_inches="tight")
    fig.savefig(out_svg, bbox_inches="tight")


def render_method_path(method: dict, metadata: dict[str, dict]) -> str:
    lines = []
    for row in method["steps"]:
        meta = metadata[row["sha"]]
        verdict = row["verdict"].ljust(4)
        lines.append(f"{row['step']:2d}. {verdict} {meta['short']} {meta['date']} {row['subject']}")
    return "\n".join(lines)


def write_markdown_summary(data: dict, out_md: Path) -> None:
    metadata = {row["sha"]: row for row in data["commit_axis"]}

    lines = [
        f"# {data['issue'].upper()} Search-Path Axis View",
        "",
        "## Figure Files",
        "",
        f"- `{relative_to_root(data['data_path'])}`",
        f"- `{relative_to_root(data['png_path'])}`",
        f"- `{relative_to_root(data['svg_path'])}`",
        "",
        "## What The Figure Shows",
        "",
        "- x-axis: chronological order of every commit touched by any plotted path, plus the known good and bad endpoints",
        "- y-axis: search step number inside each method",
        "- marker fill: `good`, `bad`, or `skip` verdict",
        "- marker text: the step number for that method",
        "",
        "## Plotted Methods",
        "",
    ]

    for method in data["methods"]:
        lines.append(f"- `{method['label']}` from {', '.join(f'`{path}`' for path in method['source_artifacts'])}")
        if method["source_kind"] == "summary-log-reconstruction":
            lines.append("  reconstructed from the saved tested-path summary log because a longer run-history JSON was not available")

    lines.extend(["", "## Marker Notes", ""])
    for marker in data["markers"]:
        lines.append(f"- `{metadata[marker['sha']]['short']}`: {marker['label']}")

    if not any(method["label"] == "Git bisect" for method in data["methods"]):
        lines.extend(
            [
                "",
                "## Baseline Note",
                "",
                "- No saved `git bisect` log is available for this issue yet, so this figure only shows the available LM-bisect trace(s).",
            ]
        )

    if len(data["methods"]) < 3:
        lines.extend(
            [
                "",
                "## Coverage Note",
                "",
                "- No real model-guided run history is available for this issue yet, so the figure is intentionally incomplete rather than mixing in simulated data.",
            ]
        )

    if data.get("non_monotonic_note"):
        lines.extend(["", "## Non-Monotonic Note", "", f"- {data['non_monotonic_note']}"])

    lines.extend(["", "## Search Paths In Order", ""])
    for method in data["methods"]:
        lines.extend(
            [
                f"### {method['label']}",
                "",
                "```text",
                render_method_path(method, metadata),
                "```",
                "",
            ]
        )

    out_md.write_text("\n".join(lines).rstrip() + "\n")


def assemble_issue_data(issue_id: str, llvm_dir: Path, output_suffix: str = "", method_set: str = "all") -> dict:
    profiles = load_profiles()
    if issue_id not in profiles:
        raise SystemExit(f"unknown issue id: {issue_id}")

    profile = profiles[issue_id]
    issue_dir = ISSUES_DIR / issue_id
    bisect_path = ROOT / profile["bisect_log"]
    methods = []
    if bisect_path.exists() and bisect_path.stat().st_size > 0:
        bisect_steps, first_bad = parse_git_bisect_log(
            bisect_path.read_text(),
            good_endpoint=profile["good_commit"],
            bad_endpoint=profile["bad_commit"],
        )
        if first_bad is None:
            raise RuntimeError(f"failed to parse first bad commit from {bisect_path}")

        methods.append(
            {
                "label": "Git bisect",
                "steps": bisect_steps,
                "final_unresolved_window": [first_bad["sha"]],
                "source_kind": "bisect-log",
                "source_artifacts": [relative_to_root(bisect_path)],
            }
        )
    else:
        first_bad = {
            "sha": profile["bad_commit"],
            "subject": "reference first bad commit unavailable because bisect log is missing",
        }

    heuristic = choose_method_trace(issue_id, "heuristic", "Real Heuristic LM-bisect")
    if heuristic is not None:
        methods.append(heuristic)

    model = choose_method_trace(issue_id, "model", "Real Model-Guided LM-bisect")
    if model is not None:
        methods.append(model)

    heuristic_calibrated = choose_calibrated_method_trace(
        issue_id,
        "heuristic",
        real_label="Real Heuristic Calibrated Posterior",
        sim_label="Sim Heuristic Calibrated Posterior",
    )
    if heuristic_calibrated is not None:
        methods.append(heuristic_calibrated)

    model_calibrated = choose_calibrated_method_trace(
        issue_id,
        "model",
        real_label="Real Model Calibrated Posterior",
        sim_label="Sim Model Calibrated Posterior",
    )
    if model_calibrated is not None:
        methods.append(model_calibrated)

    methods = filter_methods(methods, method_set)

    raw_shas = [profile["good_commit"], profile["bad_commit"], first_bad["sha"]]
    for method in methods:
        raw_shas.extend(row["sha"] for row in method["steps"])
        raw_shas.extend(method.get("final_unresolved_window", []))

    resolution = resolve_full_shas(llvm_dir, raw_shas)
    good_commit = resolution.get(profile["good_commit"], profile["good_commit"])
    bad_commit = resolution.get(profile["bad_commit"], profile["bad_commit"])
    reference_first_bad = resolution.get(first_bad["sha"], first_bad["sha"])

    expanded_methods = []
    for method in methods:
        expanded_methods.append(
            {
                **method,
                "steps": expand_shas(method["steps"], resolution),
                "final_unresolved_window": [resolution.get(sha, sha) for sha in method.get("final_unresolved_window", [])],
            }
        )

    metadata = load_commit_metadata(
        llvm_dir,
        [good_commit, bad_commit, reference_first_bad]
        + [row["sha"] for method in expanded_methods for row in method["steps"]]
        + [sha for method in expanded_methods for sha in method.get("final_unresolved_window", [])],
    )
    ordered_shas, positions = build_commit_axis(expanded_methods, metadata, [good_commit, bad_commit, reference_first_bad])

    suffix = ""
    if output_suffix:
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", output_suffix.strip())
        if safe_suffix:
            suffix = f"-{safe_suffix}"

    data_path = issue_dir / f"{issue_id}-search-path-data{suffix}.json"
    png_path = issue_dir / f"{issue_id}-search-paths{suffix}.png"
    svg_path = issue_dir / f"{issue_id}-search-paths{suffix}.svg"

    non_monotonic_note = None
    if issue_id == "pr187875":
        for method in expanded_methods:
            final_window = method.get("final_unresolved_window", [])
            if len(final_window) == 1 and final_window[0] != reference_first_bad:
                non_monotonic_note = (
                    f"{method['label']} converged to later bad commit {metadata[final_window[0]]['short']}, "
                    f"while the reference first bad commit remains {metadata[reference_first_bad]['short']}."
                )
                break

    data = {
        "issue": issue_id,
        "issue_url": profile["issue_url"],
        "good_commit": good_commit,
        "bad_commit": bad_commit,
        "reference_first_bad": reference_first_bad,
        "reference_first_bad_subject": first_bad["subject"],
        "methods": expanded_methods,
        "markers": [],
        "commit_axis": [
            {
                "position": positions[sha],
                **metadata[sha],
            }
            for sha in ordered_shas
        ],
        "data_path": data_path,
        "png_path": png_path,
        "svg_path": svg_path,
        "non_monotonic_note": non_monotonic_note,
    }
    data["markers"] = build_markers(reference_first_bad, data)
    return data


def write_data_json(data: dict, out_path: Path) -> None:
    serializable = dict(data)
    serializable["data_path"] = relative_to_root(data["data_path"])
    serializable["png_path"] = relative_to_root(data["png_path"])
    serializable["svg_path"] = relative_to_root(data["svg_path"])
    out_path.write_text(json.dumps(serializable, indent=2) + "\n")


def main() -> None:
    args = parse_args()
    data = assemble_issue_data(args.issue, args.llvm_dir, args.output_suffix, args.method_set)
    issue_dir = ISSUES_DIR / args.issue
    issue_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if args.output_suffix:
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.output_suffix.strip())
        if safe_suffix:
            suffix = f"-{safe_suffix}"

    write_data_json(data, data["data_path"])
    plot_issue_data(data, data["png_path"], data["svg_path"])
    markdown_path = issue_dir / f"{args.issue}-search-paths{suffix}.md"
    write_markdown_summary(data, markdown_path)

    print(f"Wrote {relative_to_root(data['data_path'])}")
    print(f"Wrote {relative_to_root(data['png_path'])}")
    print(f"Wrote {relative_to_root(data['svg_path'])}")
    print(f"Wrote {relative_to_root(markdown_path)}")


if __name__ == "__main__":
    main()
