#!/usr/bin/env python3
"""Render the public Master-50 CEG/evidence result table from its inventory."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import shutil
import statistics
import subprocess


def fmt_int(value: object) -> str:
    return "—" if value is None else f"{int(value):,}"


def fmt_float(value: float) -> str:
    return f"{value:.2f}"


def method_steps(method: dict[str, object]) -> str:
    if method.get("availability") != "available":
        status = method.get("status")
        return "—" if status == "not_run" else html.escape(str(status))
    builds = int(method["runner_builds"])
    skips = int(method.get("skips") or 0)
    return f"{builds}" if skips == 0 else f"{builds} ({skips} skip)"


def short_sha(value: object) -> str:
    if not value:
        return "—"
    sha = str(value)
    return f'<span class="mono" title="{html.escape(sha)}">{sha[:12]}</span>'


def stats(values: list[int]) -> dict[str, object]:
    return {
        "count": len(values),
        "total": sum(values),
        "mean": statistics.mean(values) if values else 0.0,
        "median": statistics.median(values) if values else 0.0,
    }


def splice_section(text: str, start: str, end: str, replacement: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at)
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


def render(args: argparse.Namespace) -> None:
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    rows = inventory["rows"]
    if len(rows) != 50:
        raise ValueError(f"expected 50 inventory rows, found {len(rows)}")
    issues = {
        row["issue"]: row
        for row in json.loads(args.issues.read_text(encoding="utf-8"))
    }
    ablation = json.loads(args.ablation.read_text(encoding="utf-8"))
    ranks = ablation["reference"]["perCase"]

    ceg_available = [row for row in rows if row["ceg"]["availability"] == "available"]
    evidence_available = [
        row
        for row in rows
        if row["evidence_lm_bisect"]["availability"] == "available"
    ]
    paired = [
        row
        for row in rows
        if row["ceg"]["availability"] == "available"
        and row["evidence_lm_bisect"]["availability"] == "available"
    ]
    ceg_pair_steps = [int(row["ceg"]["runner_builds"]) for row in paired]
    evidence_pair_steps = [
        int(row["evidence_lm_bisect"]["runner_builds"]) for row in paired
    ]
    ceg_stats = stats(ceg_pair_steps)
    evidence_stats = stats(evidence_pair_steps)
    wins = sum(c < e for c, e in zip(ceg_pair_steps, evidence_pair_steps))
    ties = sum(c == e for c, e in zip(ceg_pair_steps, evidence_pair_steps))
    losses = sum(c > e for c, e in zip(ceg_pair_steps, evidence_pair_steps))
    exact = sum(
        row["ceg"]["first_bad_commit"]
        == row["git_bisect"]["first_bad_commit"]
        for row in ceg_available
    )

    table_rows = []
    for row in rows:
        issue = row["issue"]
        ceg = row["ceg"]
        evidence = row["evidence_lm_bisect"]
        ledger = issues[issue]
        classes = []
        if ceg["availability"] != "available":
            classes.append("bug-row")
        elif evidence["availability"] == "available":
            c = int(ceg["runner_builds"])
            e = int(evidence["runner_builds"])
            classes.append("win-row" if c < e else "warn-row" if c == e else "loss-row")
        else:
            classes.append("muted-row")
        rank = ranks.get(issue)
        empty_prior = bool(ledger.get("ceg_prior_empty")) or rank is None
        if empty_prior:
            classes.append("empty-prior-row")
        rank_cell = "无先验" if empty_prior else fmt_int(rank)
        delta = "—"
        delta_class = ""
        if ceg["availability"] == "available" and evidence["availability"] == "available":
            difference = int(ceg["runner_builds"]) - int(evidence["runner_builds"])
            delta = f"{difference:+d}"
            delta_class = "delta-win" if difference < 0 else "delta-loss" if difference > 0 else ""
        status_bits = []
        if ceg["availability"] == "available":
            status_bits.append(
                "CEG 边界一致"
                if ceg["first_bad_commit"] == row["git_bisect"]["first_bad_commit"]
                else "CEG 边界不同"
            )
        else:
            status_bits.append("CEG 无有效结果")
        if evidence["availability"] != "available":
            status_bits.append("证据 LM 无有效结果")
        class_attr = f' class="{" ".join(classes)}"' if classes else ""
        table_rows.append(
            f"<tr{class_attr}>"
            f'<td class="mono"><button type="button" class="result-issue" data-issue="{issue}">{issue}</button></td>'
            f'<td class="mono">{fmt_int(row["interval_commits"])}</td>'
            f'<td class="mono">{rank_cell}</td>'
            f'<td class="mono">{method_steps(ceg)}</td>'
            f"<td>{short_sha(ceg.get('first_bad_commit'))}</td>"
            f'<td class="mono">{method_steps(evidence)}</td>'
            f"<td>{short_sha(evidence.get('first_bad_commit'))}</td>"
            f'<td class="mono {delta_class}">{delta}</td>'
            f'<td class="mono">{fmt_int(row["git_bisect"]["runner_builds"])}</td>'
            f"<td>{'；'.join(status_bits)}</td>"
            "</tr>"
        )

    table_block = f"""
          <h3 class="brief-h" id="result-table">全表（50 例）</h3>
          <p class="brief-note">
            区间大小统一取配置的 <code>good..bad</code> 候选提交数。
            “证据 LM”是同一区间的 <code>evidence-diverse-k12</code> LM-Bisect 基线；
            没有有效终态 history 的格子明确显示为不可用，不做跨区间或历史 9 例替代。
            <code>Δ = CEG − 证据 LM</code>。完整原始 history 与 SHA-256 清单见随页发布的 ZIP。
          </p>
          <div class="table-scroll">
            <table class="matrix result-full-table">
              <thead>
                <tr>
                  <th>Issue</th>
                  <th>区间大小</th>
                  <th>GT 最坏名次</th>
                  <th>CEG 步数</th>
                  <th>CEG first-bad</th>
                  <th>证据 LM 步数</th>
                  <th>证据 LM first-bad</th>
                  <th>Δ</th>
                  <th>Git 步数</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
{chr(10).join(table_rows)}
              </tbody>
            </table>
          </div>
"""

    coverage_block = f"""
          <h3 class="brief-h">效果：50 例 CEG 与证据 LM-Bisect</h3>
          <p class="brief-lead">
            本快照覆盖全部 50 个配置区间。CEG 有效终态 <strong>{len(ceg_available)} / 50</strong>；
            endpoint-matched <code>evidence-diverse-k12</code> 有效终态
            <strong>{len(evidence_available)} / 50</strong>。下表只把真实存在的同区间 history
            显示为基线；缺失、runner 故障和进行中结果继续保留为显式状态。
          </p>
          <div class="table-scroll"><table class="matrix"><thead><tr><th>状态</th><th>数量</th><th>说明</th></tr></thead><tbody>
            <tr><td>Master-50 全集合</td><td>50</td><td>每例均显示区间大小</td></tr>
            <tr><td>CEG 有效结果</td><td>{len(ceg_available)}</td><td>严格终态：remaining_unresolved = 1</td></tr>
            <tr><td>证据 LM 有效结果</td><td>{len(evidence_available)}</td><td>同一 good/bad 区间的 evidence-diverse history</td></tr>
            <tr><td>方法配对</td><td>{len(paired)}</td><td>仅这些行计算 CEG − 证据 LM</td></tr>
          </tbody></table></div>
"""

    paired_block = f"""
          <h3 class="brief-h">同区间 CEG 对照证据 LM-Bisect（{len(paired)} 例）</h3>
          <p class="brief-lead">
            预指定 LM-Bisect 基线是 <code>evidence-diverse-k12</code>。这里不拿旧 LM、
            BCR 或历史 scoped-10 聚合替代缺失行。
          </p>
          <div class="table-scroll"><table class="matrix"><thead><tr><th></th><th>CEG-Bisect</th><th>证据 LM-Bisect</th></tr></thead><tbody>
            <tr><td>例数</td><td>{len(paired)}</td><td>{len(paired)}</td></tr>
            <tr><td>总构建</td><td>{ceg_stats["total"]}</td><td>{evidence_stats["total"]}</td></tr>
            <tr><td>均值</td><td>{fmt_float(float(ceg_stats["mean"]))}</td><td>{fmt_float(float(evidence_stats["mean"]))}</td></tr>
            <tr><td>中位</td><td>{fmt_float(float(ceg_stats["median"]))}</td><td>{fmt_float(float(evidence_stats["median"]))}</td></tr>
            <tr><td>CEG 胜 / 平 / 负</td><td><strong>{wins} / {ties} / {losses}</strong></td><td>—</td></tr>
          </tbody></table></div>
"""

    effect_block = f"""
          <div class="brief-stats" id="result-effect">
            <div class="bstat">
              <div class="bstat-num">{len(ceg_available)} / 50</div>
              <div class="bstat-label">CEG 有效终态<br>每行公开区间大小与 first-bad</div>
            </div>
            <div class="bstat">
              <div class="bstat-num">{len(evidence_available)} / 50</div>
              <div class="bstat-label">证据 LM-Bisect 有效终态<br>只显示 endpoint-matched history</div>
            </div>
            <div class="bstat">
              <div class="bstat-num">{exact} / {len(ceg_available)}</div>
              <div class="bstat-label">CEG first-bad 与 Git 台账一致<br>不同边界继续显式标记</div>
            </div>
          </div>
"""

    index = args.index.read_text(encoding="utf-8")
    start = index.index('<div id="strategy-results"')
    end = index.index('<div id="strategy-llm"')
    prefix, result_page, suffix = index[:start], index[start:end], index[end:]
    result_page = splice_section(
        result_page,
        '<div class="brief-stats" id="result-effect">',
        '<h3 class="brief-h">效果：',
        effect_block,
    )
    if '<h3 class="brief-h">效果：50 例对照 Git Bisect</h3>' in result_page:
        coverage_start = '<h3 class="brief-h">效果：50 例对照 Git Bisect</h3>'
    else:
        coverage_start = '<h3 class="brief-h">效果：50 例 CEG 与证据 LM-Bisect</h3>'
    if '<h3 class="brief-h">已配对、已定位的 Git 对照（41 例）</h3>' in result_page:
        paired_start = '<h3 class="brief-h">已配对、已定位的 Git 对照（41 例）</h3>'
    else:
        paired_start = result_page[
            result_page.index('<h3 class="brief-h">同区间 CEG 对照证据 LM-Bisect') :
            result_page.index('<h3 class="brief-h" id="result-table">')
        ]
        paired_start = paired_start[: paired_start.index("</h3>") + 5]
    result_page = splice_section(
        result_page,
        coverage_start,
        paired_start if paired_start.startswith("<h3") else '<h3 class="brief-h">已配对、已定位的 Git 对照（41 例）</h3>',
        coverage_block,
    )
    if '<h3 class="brief-h">已配对、已定位的 Git 对照（41 例）</h3>' in result_page:
        paired_heading = '<h3 class="brief-h">已配对、已定位的 Git 对照（41 例）</h3>'
    else:
        marker = '<h3 class="brief-h">同区间 CEG 对照证据 LM-Bisect'
        paired_heading = result_page[result_page.index(marker) : result_page.index("</h3>", result_page.index(marker)) + 5]
    result_page = splice_section(
        result_page,
        paired_heading,
        '<h3 class="brief-h" id="result-table">',
        paired_block,
    )
    result_page = splice_section(
        result_page,
        '<h3 class="brief-h" id="result-table">',
        '<h3 class="brief-h" id="result-rank">',
        table_block,
    )
    args.index.write_text(prefix + result_page + suffix, encoding="utf-8")

    public_data = args.index.parent / "master50-ceg-evidence-results.json"
    shutil.copy2(args.inventory, public_data)
    if args.build_standalone:
        subprocess.run(
            ["node", "build-standalone.js"],
            cwd=args.index.parent,
            check=True,
        )
    print(public_data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--ablation", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--build-standalone", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    render(parse_args())
