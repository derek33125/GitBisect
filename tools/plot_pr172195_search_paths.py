#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "results" / "pr172195-search-path-data.json"
OUT_PNG = ROOT / "results" / "pr172195-search-paths.png"
OUT_SVG = ROOT / "results" / "pr172195-search-paths.svg"
DEFAULT_LLVM_DIR = Path("/home/derek331/research/gitbisect-work/llvm-project")
KNOWN_GOOD_SHA = "8e2cd28cd4ba46613a46467b0c91b1cabead26cd"
KNOWN_BAD_SHA = "86c5539aa89ac61058e3ba4fc0ae578c2879bf9e"
FIRST_BAD_SHA = "e8219e5ce84db26fd521ce5091d18e75c7afbc6a"

METHOD_STYLES = {
    "Email git bisect": {"color": "#4C78A8", "linestyle": "-", "marker": "o"},
    "Real Heuristic LM-bisect": {"color": "#E45756", "linestyle": "--", "marker": "s"},
    "Real Model-Guided LM-bisect": {"color": "#54A24B", "linestyle": "-.", "marker": "D"},
}

VERDICT_COLORS = {
    "good": "#54A24B",
    "bad": "#E45756",
    "skip": "#F58518",
}


def load_data() -> dict:
    return json.loads(DATA_PATH.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot PR172195 email git bisect, real heuristic LM-bisect, and real model-guided LM-bisect on a commit-order axis."
    )
    parser.add_argument(
        "--llvm-dir",
        type=Path,
        default=Path(os.environ.get("LLVM_PROJECT_DIR", DEFAULT_LLVM_DIR)),
        help="Path to the llvm-project checkout used to look up commit metadata.",
    )
    return parser.parse_args()


def load_commit_metadata(llvm_dir: Path, shas: list[str]) -> dict[str, dict]:
    fmt = "%H%x09%ct%x09%cs%x09%s"
    result = subprocess.run(
        ["git", "-C", str(llvm_dir), "show", "-s", f"--format={fmt}", *shas],
        check=True,
        capture_output=True,
        text=True,
    )

    metadata = {}
    for line in result.stdout.splitlines():
        sha, timestamp, date_text, subject = line.split("\t", 3)
        metadata[sha] = {
            "sha": sha,
            "short": sha[:7],
            "timestamp": int(timestamp),
            "date": date_text,
            "subject": subject,
        }
    return metadata


def build_commit_axis(data: dict, metadata: dict[str, dict]) -> tuple[list[str], dict[str, int]]:
    shas = {KNOWN_GOOD_SHA, KNOWN_BAD_SHA}
    for rows in (data["email_path"], data["heuristic_path"], data["model_path"]):
        shas.update(row["sha"] for row in rows)

    ordered = sorted(shas, key=lambda sha: (metadata[sha]["timestamp"], sha))
    positions = {sha: index + 1 for index, sha in enumerate(ordered)}
    return ordered, positions


def tick_label(sha: str, metadata: dict[str, dict]) -> str:
    meta = metadata[sha]
    date_suffix = meta["date"][5:]
    if sha == KNOWN_GOOD_SHA:
        return f"G {meta['short']}\n{date_suffix}"
    if sha == KNOWN_BAD_SHA:
        return f"B {meta['short']}\n{date_suffix}"
    return f"{meta['short']}\n{date_suffix}"


def marker_text_color(verdict: str) -> str:
    if verdict == "skip":
        return "#1F1F1F"
    return "white"


def draw_path(ax: plt.Axes, rows: list[dict], positions: dict[str, int], label: str) -> None:
    style = METHOD_STYLES[label]
    xs = [positions[row["sha"]] for row in rows]
    ys = [row["step"] for row in rows]

    ax.plot(
        xs,
        ys,
        color=style["color"],
        linestyle=style["linestyle"],
        linewidth=2.2,
        alpha=0.9,
        zorder=2,
    )

    for row, x_value, y_value in zip(rows, xs, ys):
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


def annotate_shared_commits(
    ax: plt.Axes,
    data: dict,
    positions: dict[str, int],
    metadata: dict[str, dict],
) -> None:
    named_paths = {
        "email": {row["sha"]: row["step"] for row in data["email_path"]},
        "heuristic": {row["sha"]: row["step"] for row in data["heuristic_path"]},
        "model": {row["sha"]: row["step"] for row in data["model_path"]},
    }
    visit_counts = {}
    for path in named_paths.values():
        for sha in path:
            visit_counts[sha] = visit_counts.get(sha, 0) + 1
    shared = sorted((sha for sha, count in visit_counts.items() if count >= 2), key=lambda sha: positions[sha])

    for sha in shared:
        x_value = positions[sha]
        top_step = max(path.get(sha, 0) for path in named_paths.values())
        ax.annotate(
            f"shared {metadata[sha]['short']}",
            xy=(x_value, top_step),
            xytext=(0, 16),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333333",
            arrowprops={"arrowstyle": "-", "color": "#888888", "lw": 0.9},
        )


def plot(llvm_dir: Path) -> None:
    data = load_data()
    metadata = load_commit_metadata(
        llvm_dir,
        [KNOWN_GOOD_SHA, KNOWN_BAD_SHA]
        + [row["sha"] for row in data["email_path"]]
        + [row["sha"] for row in data["heuristic_path"]]
        + [row["sha"] for row in data["model_path"]],
    )
    ordered_shas, positions = build_commit_axis(data, metadata)

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(18, 7), constrained_layout=True)

    draw_path(ax, data["email_path"], positions, "Email git bisect")
    draw_path(ax, data["heuristic_path"], positions, "Real Heuristic LM-bisect")
    draw_path(ax, data["model_path"], positions, "Real Model-Guided LM-bisect")
    annotate_shared_commits(ax, data, positions, metadata)

    for endpoint_sha, label in (
        (KNOWN_GOOD_SHA, "known good endpoint"),
        (KNOWN_BAD_SHA, "known bad endpoint"),
        (FIRST_BAD_SHA, "true first bad commit"),
    ):
        x_value = positions[endpoint_sha]
        ax.axvline(x_value, color="#9E9E9E", linestyle=":", linewidth=1.2, zorder=1)
        ax.text(
            x_value,
            16.8,
            label,
            ha="center",
            va="bottom",
            fontsize=9,
            color="#666666",
            rotation=90,
        )

    ax.set_title("PR172195 Three-Way Search Paths On A Commit-Order Axis", fontsize=15, weight="bold")
    ax.set_xlabel("Commit chronology (short SHA, with MM-DD date under each commit)")
    ax.set_ylabel("Search step")
    ax.set_xlim(0.5, len(ordered_shas) + 0.5)
    ax.set_ylim(0.5, 17.2)
    ax.set_xticks(range(1, len(ordered_shas) + 1))
    ax.set_xticklabels([tick_label(sha, metadata) for sha in ordered_shas], rotation=55, ha="right", fontsize=8)
    ax.set_yticks(range(1, 17))
    ax.grid(axis="x", linestyle=":", alpha=0.18)
    ax.grid(axis="y", linestyle="--", alpha=0.30)

    method_handles = [
        Line2D(
            [0],
            [0],
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markerfacecolor="white",
            markeredgecolor=style["color"],
            linewidth=2.2,
            markersize=8,
            label=label,
        )
        for label, style in METHOD_STYLES.items()
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

    fig.savefig(OUT_PNG, dpi=220, bbox_inches="tight")
    fig.savefig(OUT_SVG, bbox_inches="tight")


if __name__ == "__main__":
    args = parse_args()
    plot(args.llvm_dir)
