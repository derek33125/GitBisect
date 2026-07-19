"use strict";

const METHOD_KEYS = ["tuned-heuristic", "general-heuristic", "parent-llm-topk3"];
const state = { data: null, issueIndex: 0, methodKey: METHOD_KEYS[0] };

const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtInt(n) {
  if (typeof n !== "number") return n;
  return n.toLocaleString("en-US");
}

function isCleanStatus(status) {
  return !/skip|partial|missing|in_progress/i.test(String(status || ""));
}

async function boot() {
  try {
    const res = await fetch("./data/site-data.json", { cache: "no-cache" });
    state.data = await res.json();
  } catch (e) {
    const target = $("#steps") || $("#runtime-example-content") || $("#live-topk20");
    if (target) {
      target.innerHTML =
        '<div class="git-note">Could not load <code>data/site-data.json</code>. Run <code>node scripts/build-data.mjs</code> first.</div>';
    }
    return;
  }
  const d = state.data;
  const scopeNote = $("#scope-note");
  if (scopeNote) scopeNote.textContent = d.scope || "";
  const footerNote = $("#footer-note");
  if (footerNote) {
    footerNote.innerHTML =
      "Generated " +
      esc((d.generated_at || "").slice(0, 10)) +
      ". Self-contained bundle — the page only reads <code>data/site-data.json</code>.";
  }

  if ($("#agg-cards")) {
    renderAggregates();
    renderComparison();
    renderLiveTopK20();
  }
  if ($("#topk-body")) renderFocusedComparisons();
  if ($("#runtime-example-content")) renderRuntimeExample();
  if ($("#issue-select")) {
    buildControls();
    renderExplorer();
  }
}

function renderKeywordExamples() {
  const el = $("#kw-examples");
  if (!el) return;
  // Show a few representative issues with their authored crash keywords.
  const picks = state.data.issues.filter((i) => (i.keywords || []).length).slice(0, 4);
  el.innerHTML = picks
    .map((iss) => {
      const chips = (iss.keywords || [])
        .slice(0, 9)
        .map((k, idx) => `<span class="chip ${idx < 3 ? "kw-hi" : ""}">${esc(k)}</span>`)
        .join("");
      return `<div class="kw-row">
        <div class="kw-head">
          <span class="kw-id">${esc(iss.issue)}</span>
          <span class="kw-title">${esc(iss.title)}</span>
        </div>
        <div class="chips">${chips}</div>
      </div>`;
    })
    .join("");
}

function renderAggregates() {
  const agg = state.data.aggregate || {};
  const cards = [
    ["git", "git bisect"],
    ["old_model", "Old model-guided"],
    ["specific_heuristic", "Issue-specific heuristic"],
    ["general_heuristic", "Fixed-vocabulary heuristic"],
    ["parent_llm_topk3", "Parent-diff + LLM"],
  ];
  // best = lowest average among the non-git methods
  let best = null;
  for (const [k] of cards) {
    if (k === "git") continue;
    const a = agg[k];
    if (a && typeof a.avg === "number" && (best === null || a.avg < agg[best].avg)) best = k;
  }
  $("#agg-cards").innerHTML = cards
    .map(([k, label]) => {
      const a = agg[k] || {};
      const isBest = k === best;
      return `<div class="agg ${isBest ? "best" : ""}">
        <div class="name">${esc(label)}</div>
        <div class="big">${a.avg != null ? a.avg : "—"}</div>
        <div class="sub">avg steps · median ${a.median != null ? a.median : "—"} · n=${a.count ?? 0}</div>
      </div>`;
    })
    .join("");

  const ablation = state.data.keyword_ablation;
  const el = $("#ablation-summary");
  if (!el || !ablation) return;
  const specific = ablation.aggregate.specific_keyword;
  const general = ablation.aggregate.general_keyword;
  const delta = Math.round((general.avg_steps - specific.avg_steps) * 10) / 10;
  el.innerHTML = `<strong>Keyword ablation:</strong> the issue-specific vocabulary averages
    <strong>${specific.avg_steps}</strong> builds versus <strong>${general.avg_steps}</strong>
    for the shared fixed vocabulary (${delta > 0 ? "+" : ""}${delta} builds).
    Specific wins ${ablation.aggregate.specific_keyword_wins}, fixed wins
    ${ablation.aggregate.general_keyword_wins}, ties ${ablation.aggregate.step_ties};
    final first-bad commits match exactly in ${ablation.aggregate.first_bad_exact_matches}/10 cases.
    The one mismatch is the documented <code>pr49535</code> apply/reapply pair.`;

  const weak = state.data.weak_maintenance_keyword_control;
  const weakEl = $("#weak-maintenance-summary");
  if (!weakEl || !weak) return;
  const terms = (weak.keywords || []).map((term) => `<code>${esc(term)}</code>`).join(", ");
  const completed = (weak.completed_clean_issues || []).length;
  const progress = weak.active_progress;
  const superseded = weak.superseded_remote_runs;
  const rows = weak.completed_clean_issues || [];
  const average = rows.length
    ? Math.round((rows.reduce((sum, row) => sum + row.steps, 0) / rows.length) * 10) / 10
    : null;
  const progressText = progress
    ? ` ${completed}/10 compatible rows are clean; <code>${esc(progress.issue)}</code> is at ${esc(
        progress.steps
      )} steps and <code>${esc(progress.next_issue)}</code> remains queued.`
    : "";
  const completeText =
    weak.status === "complete"
      ? ` All ${completed}/10 compatible rows completed cleanly (average <strong>${average}</strong> builds).`
      : "";
  weakEl.innerHTML = `<strong>Maintenance-keyword control (${esc(weak.status)}):</strong>
    a deliberately weak shared ten-term vocabulary is used on the same ten cases: ${terms}.${progressText}${completeText}
    The first attempt is excluded because old LLVM failed before reproduction on a missing
    <code>uintptr_t</code> header; the compatible rerun uses the same forced standard-header
    settings as the clean comparison rows.${superseded ? ` Earlier remote rows are excluded: ${esc(
      superseded.reason
    )}` : ""}`;
}

function renderLiveTopK20() {
  const el = $("#live-topk20");
  const snapshot = state.data.live_lanes?.parent_llm_topk20;
  if (!el || !snapshot) return;
  const clean = snapshot.clean || [];
  const partial = snapshot.partial || [];
  const cleanRows = clean
    .map(
      (row) => `<tr><td>${esc(row.issue)}</td><td class="cell-good">${esc(row.steps)}</td><td><code>${esc(
        row.first_bad
      )}</code></td><td>${esc(row.source)}</td></tr>`
    )
    .join("");
  const partialRows = partial
    .map(
      (row) => `<tr><td>${esc(row.issue)}</td><td>${esc(row.steps)}</td><td>${esc(row.state)}</td><td>${esc(
        row.reason
      )}</td></tr>`
    )
    .join("");
  el.innerHTML = `
    <div class="live-summary"><strong>${clean.length}/10 clean rows available.</strong> ${esc(
      snapshot.result_policy || ""
    )}</div>
    <div class="live-grid">
      <div><h3>Clean current rows</h3><div class="table-scroll"><table class="mini-table"><thead><tr><th>Issue</th><th>Steps</th><th>First bad</th><th>Source</th></tr></thead><tbody>${cleanRows}</tbody></table></div></div>
      <div><h3>Still partial</h3><div class="table-scroll"><table class="mini-table"><thead><tr><th>Issue</th><th>Steps</th><th>State</th><th>Reason</th></tr></thead><tbody>${partialRows}</tbody></table></div></div>
    </div>`;
}

function renderFocusedCards(target, entries) {
  const el = $(target);
  if (!el) return;
  const best = entries.reduce(
    (current, entry) => (current == null || entry.metrics.avg_steps < current.metrics.avg_steps ? entry : current),
    null
  );
  el.innerHTML = entries
    .map((entry) => {
      const metrics = entry.metrics;
      const suffix = metrics.skip_rows ? " - " + metrics.skip_rows + " run with skip" : "";
      return (
        '<article class="focused-card ' +
        (entry === best ? "best" : "") +
        '"><div class="name">' +
        esc(entry.label) +
        '</div><div class="big">' +
        esc(metrics.avg_steps) +
        '</div><div class="sub">avg steps, median ' +
        esc(metrics.median_steps) +
        ", " +
        esc(metrics.first_bad_matches) +
        "/10 canonical first-bad" +
        suffix +
        "</div></article>"
      );
    })
    .join("");
}

function resultCell(result, bestSteps) {
  const skip = result.skips > 0;
  const cls = "num " + (result.steps === bestSteps ? "cell-best " : "") + (skip ? "cell-warn" : "");
  return '<td class="' + cls + '">' + esc(result.steps) + (skip ? " *" : "") + "</td>";
}

function sameCommit(left, right) {
  return String(left || "").slice(0, 12) === String(right || "").slice(0, 12);
}

function agreementCell(results, canonical) {
  const mismatches = results.filter((result) => !sameCommit(result.first_bad, canonical)).length;
  if (!mismatches) return '<td class="cell-good">all match</td>';
  return '<td class="cell-warn">' + mismatches + " alternate boundary</td>";
}

function renderTopkComparison(topk) {
  renderFocusedCards("#topk-cards", [
    { label: topk.adaptive.label, metrics: topk.aggregate.adaptive },
    { label: "LLM top-k3", metrics: topk.aggregate.topk3 },
    { label: "LLM top-k10", metrics: topk.aggregate.topk10 },
    { label: "LLM top-k20", metrics: topk.aggregate.topk20 },
  ]);
  $("#topk-body").innerHTML = topk.rows
    .map((row) => {
      const candidates = [row.topk3, row.topk10, row.topk20, row.adaptive];
      const bestSteps = Math.min(...candidates.map((result) => result.steps));
      return (
        '<tr><td class="left"><strong>' +
        esc(row.issue) +
        '</strong><span class="issue-title">' +
        esc(row.title) +
        '</span></td><td class="num">' +
        esc(row.git.steps) +
        "</td>" +
        candidates.map((result) => resultCell(result, bestSteps)).join("") +
        agreementCell(candidates, row.canonical_first_bad) +
        "</tr>"
      );
    })
    .join("");
  const note = document.createElement("p");
  note.className = "table-footnote";
  note.innerHTML = "<strong>Adaptive schedule:</strong> " + esc(topk.adaptive.definition) + " " + esc(topk.adaptive.provenance);
  $("#topk-body").closest(".table-scroll").after(note);
  renderTopkSensitivity(topk.sensitivity);
}

function signedNumber(value) {
  return value > 0 ? "+" + value : String(value);
}

function renderTopkSensitivity(sensitivity) {
  const summary = $("#topk-sensitivity");
  const body = $("#topk-trajectory-body");
  const phaseBody = $("#topk-phase-body");
  const typeBody = $("#topk-type-body");
  if (!summary || !body || !phaseBody || !typeBody || !sensitivity) return;
  const pair = sensitivity.comparable_pair;
  const controlled = sensitivity.controlled_topk3;
  summary.innerHTML =
    '<article class="topk-decision">' +
    '<div class="page-kicker">Decision / 600k selected histories</div>' +
    "<h3>Operational default: " +
    esc(sensitivity.operational_default.replace("topk", "top-k")) +
    "</h3>" +
    "<p>" +
    esc(sensitivity.recommendation) +
    "</p>" +
    '<div class="topk-metrics">' +
    "<span><b>" +
    esc(controlled.avg_steps) +
    "</b> k3 avg fresh 600k</span>" +
    "<span><b>" +
    esc(controlled.first_bad_matches + "/10") +
    "</b> k3 boundaries</span>" +
    "<span><b>" +
    esc(pair.topk20_step_wins) +
    "</b> k20 wins</span>" +
    "<span><b>" +
    esc(pair.topk10_step_wins) +
    "</b> k10 wins</span>" +
    "<span><b>" +
    esc(pair.step_ties) +
    "</b> ties</span>" +
    "<span><b>" +
    esc(pair.average_step_delta_topk20_minus_topk10) +
    "</b> avg k20-k10 steps</span>" +
    "<span><b>" +
    esc(pair.same_first_bad + "/10") +
    "</b> same boundary</span>" +
    "<span><b>" +
    esc(pair.topk10_scoring_batches_per_step + "/" + pair.topk20_scoring_batches_per_step) +
    "</b> prompt batches k10/k20</span>" +
    "</div>" +
    "</article>" +
    '<div class="topk-caveats"><strong>Why the curves split:</strong><ul>' +
    sensitivity.limitations.map((item) => "<li>" + esc(item) + "</li>").join("") +
    "</ul></div>";
  body.innerHTML = sensitivity.rows
    .map((row) => {
      const deltaClass = row.step_delta_topk20_minus_topk10 < 0 ? "cell-good" : row.step_delta_topk20_minus_topk10 > 0 ? "cell-warn" : "";
      const boundary = row.first_bad_agrees ? '<td class="cell-good">same</td>' : '<td class="cell-warn">alternate</td>';
      return (
        '<tr><td class="left"><strong>' +
        esc(row.issue) +
        "</strong></td>" +
        '<td class="num">' + esc(row.topk3_to_ten_percent) + "</td>" +
        '<td class="num">' + esc(row.topk10_to_ten_percent) + "</td>" +
        '<td class="num">' + esc(row.topk20_to_ten_percent) + "</td>" +
        '<td class="num">' + esc(row.topk3_to_32) + "</td>" +
        '<td class="num">' + esc(row.topk10_to_32) + "</td>" +
        '<td class="num">' + esc(row.topk20_to_32) + "</td>" +
        '<td class="num ' + deltaClass + '">' +
        esc(signedNumber(row.step_delta_topk20_minus_topk10)) +
        "</td>" +
        boundary +
        "</tr>"
      );
    })
    .join("");
  phaseBody.innerHTML = sensitivity.phase_analysis.thresholds
    .map((row) => {
      const tail = (key) => row.methods[key].mean_tail_steps;
      const record = (comparison) => {
        const value = row[comparison];
        return value.wins + "/" + value.ties + "/" + value.losses;
      };
      return (
        "<tr><td class=\"num\">&le;" + esc(row.threshold) + "</td>" +
        '<td class="num">' + esc(tail("topk3")) + "</td>" +
        '<td class="num">' + esc(tail("topk10")) + "</td>" +
        '<td class="num">' + esc(tail("topk20")) + "</td>" +
        '<td class="num">' + esc(record("k3_vs_k10")) + "</td>" +
        '<td class="num">' + esc(record("k3_vs_k20")) + "</td></tr>"
      );
    })
    .join("");
  typeBody.innerHTML = sensitivity.issue_type_summary
    .map((row) => {
      const method = (key, metric) => row.methods[key][metric];
      return (
        '<tr><td class="left"><strong>' + esc(row.label) + "</strong><span class=\"issue-title\">" +
        esc(row.issues.join(", ")) + "</span></td>" +
        '<td class="num">' + esc(row.count) + "</td>" +
        '<td class="num">' + esc(method("topk3", "mean_steps")) + "</td>" +
        '<td class="num">' + esc(method("topk10", "mean_steps")) + "</td>" +
        '<td class="num">' + esc(method("topk20", "mean_steps")) + "</td>" +
        '<td class="num">' + esc(method("topk3", "mean_tail_steps_at_128")) + "</td>" +
        '<td class="num">' + esc(method("topk10", "mean_tail_steps_at_128")) + "</td>" +
        '<td class="num">' + esc(method("topk20", "mean_tail_steps_at_128")) + "</td></tr>"
      );
    })
    .join("");
}

function renderKeywordVocabulary(keywords) {
  const vocab = [
    ["Issue-specific", keywords.issue_specific.definition, null],
    ["Shared crash", keywords.shared_crash.definition, keywords.shared_crash.terms],
    ["Weak maintenance", keywords.weak_maintenance.definition, keywords.weak_maintenance.terms],
  ];
  $("#keyword-vocab").innerHTML = vocab
    .map(([label, definition, terms]) => {
      const body = terms
        ? terms.map((term) => "<code>" + esc(term) + "</code>").join(" ")
        : keywords.rows
            .map((row) => {
              const issueTerms = keywords.issue_specific.terms_by_issue[row.issue] || [];
              return (
                '<div class="issue-vocab"><strong>' +
                esc(row.issue) +
                "</strong> " +
                issueTerms.map((term) => "<code>" + esc(term) + "</code>").join(" ") +
                "</div>"
              );
            })
            .join("");
      return (
        "<article><h3>" +
        esc(label) +
        "</h3><p>" +
        esc(definition) +
        '</p><div class="vocab-terms">' +
        body +
        "</div></article>"
      );
    })
    .join("");
}

function renderKeywordComparison(keywords) {
  renderFocusedCards("#keyword-cards", [
    { label: "Parent+LLM top-k3", metrics: keywords.aggregate.best_parent_llm },
    { label: "Issue-specific heuristic", metrics: keywords.aggregate.issue_specific },
    { label: "Shared crash heuristic", metrics: keywords.aggregate.shared_crash },
    { label: "Weak maintenance heuristic", metrics: keywords.aggregate.weak_maintenance },
  ]);
  $("#keyword-note").innerHTML =
    "<strong>Interpretation:</strong> " +
    esc(keywords.comparison_note) +
    " The known <code>pr49535</code> apply/reapply ambiguity is shown as an alternate boundary rather than counted as a runner failure. " +
    '<strong>Diagnostic only:</strong> the first-bad-derived column has <strong>' +
    esc(keywords.aggregate.oracle_first_bad.avg_steps) +
    "</strong> mean builds, <strong>" +
    esc(keywords.aggregate.oracle_first_bad.first_bad_matches) +
    "/10</strong> canonical first-bad boundaries, and is excluded from the comparison because " +
    esc(keywords.oracle_first_bad.warning);
  renderKeywordVocabulary(keywords);
  $("#keyword-body").innerHTML = keywords.rows
    .map((row) => {
      const candidates = [row.best_parent_llm, row.issue_specific, row.shared_crash, row.weak_maintenance];
      const bestSteps = Math.min(...candidates.map((result) => result.steps));
      const mismatch = candidates.some((result) => !sameCommit(result.first_bad, row.canonical_first_bad));
      const caveat = mismatch ? '<span class="table-caveat">apply/reapply</span>' : "";
      const generatedKeywords = row.oracle_first_bad.generated_keywords
        .map((term) => "<code>" + esc(term) + "</code>")
        .join("");
      return (
        '<tr><td class="left"><strong>' +
        esc(row.issue) +
        '</strong><span class="issue-title">' +
        esc(row.title) +
        "</span>" +
        caveat +
        '</td><td class="num">' +
        esc(row.git.steps) +
        "</td>" +
        candidates.map((result) => resultCell(result, bestSteps)).join("") +
        '<td class="num oracle-diagnostic" title="Diagnostic only; derived from the known first-bad commit.">' +
        esc(row.oracle_first_bad.steps) +
        "</td>" +
        '<td class="left oracle-keywords"><details><summary>' +
        esc(row.oracle_first_bad.generated_keywords.length) +
        ' generated terms</summary><div class="oracle-keyword-list">' +
        generatedKeywords +
        "</div></details></td>" +
        "</tr>"
      );
    })
    .join("");
}

const CONVERGENCE_COLORS = {
  topk3: "#71c5e8",
  topk10: "#edb46a",
  topk20: "#8fd399",
};

const CONVERGENCE_LABELS = {
  topk3: "top-k3",
  topk10: "top-k10",
  topk20: "top-k20",
};

function convergencePath(points, xForStep, yForRemaining) {
  return points.map((point, index) => `${index ? "L" : "M"}${xForStep(point.step).toFixed(1)},${yForRemaining(point.remaining).toFixed(1)}`).join(" ");
}

function renderConvergenceCharts(convergence) {
  const legend = $("#convergence-legend");
  const charts = $("#convergence-charts");
  if (!legend || !charts) return;
  const keys = ["topk3", "topk10", "topk20"];
  legend.innerHTML = keys
    .map(
      (key) =>
        `<span><i style="background:${CONVERGENCE_COLORS[key]}"></i>${esc(CONVERGENCE_LABELS[key])}</span>`
    )
    .join("");
  charts.innerHTML = convergence.rows
    .map((row) => {
      const curves = keys.map((key) => row.curves[key]);
      const maxStep = Math.max(...curves.flatMap((curve) => curve.points.map((point) => point.step)));
      const maxRemaining = Math.max(...curves.map((curve) => curve.initial_unresolved));
      const width = 500;
      const height = 254;
      const left = 52;
      const right = 16;
      const top = 18;
      const bottom = 34;
      const plotWidth = width - left - right;
      const plotHeight = height - top - bottom;
      const maxLog = Math.max(1, Math.log10(maxRemaining));
      const xForStep = (step) => left + (step / Math.max(1, maxStep)) * plotWidth;
      const yForRemaining = (remaining) => top + ((maxLog - Math.log10(Math.max(1, remaining))) / maxLog) * plotHeight;
      const gridValues = [...new Set([1, 10, 100, 1000, 10000, maxRemaining].filter((value) => value <= maxRemaining))].sort(
        (a, b) => a - b
      );
      const grid = gridValues
        .map((value) => {
          const y = yForRemaining(value).toFixed(1);
          return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="conv-grid" /><text x="${left - 8}" y="${Number(y) + 3}" class="conv-axis" text-anchor="end">${esc(fmtInt(value))}</text>`;
        })
        .join("");
      const xTicks = Array.from(new Set([0, Math.ceil(maxStep / 2), maxStep]));
      const xAxis = xTicks
        .map((step) => `<text x="${xForStep(step)}" y="${height - 10}" class="conv-axis" text-anchor="middle">${step}</text>`)
        .join("");
      const lines = keys
        .map((key) => {
          const curve = row.curves[key];
          const color = CONVERGENCE_COLORS[key];
          const path = convergencePath(curve.points, xForStep, yForRemaining);
          const dots = curve.points
            .map(
              (point) =>
                `<circle cx="${xForStep(point.step).toFixed(1)}" cy="${yForRemaining(point.remaining).toFixed(1)}" r="2.6" fill="${color}"><title>${esc(CONVERGENCE_LABELS[key])}: step ${point.step}, ${fmtInt(point.remaining)} commits remaining${point.verdict === "start" ? "" : `, ${point.verdict}`}</title></circle>`
            )
            .join("");
          return `<path d="${path}" stroke="${color}" class="conv-line" />${dots}`;
        })
        .join("");
      return `<article class="convergence-card"><h3>${esc(row.issue)}</h3><p>${esc(row.title)}</p><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(row.issue)} unresolved commit window by model top-k"><text x="${left}" y="12" class="conv-axis">commits remaining</text>${grid}<line x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}" class="conv-axis-line" />${xAxis}<text x="${left + plotWidth / 2}" y="${height - 1}" class="conv-axis" text-anchor="middle">runner step</text>${lines}</svg></article>`;
    })
    .join("");
}

function renderFocusedComparisons() {
  const focused = state.data.focused_comparisons;
  if (!focused) return;
  renderTopkComparison(focused.topk);
  renderKeywordComparison(focused.keywords);
  renderConvergenceCharts(focused.convergence);
}

function renderRuntimeExample() {
  const el = $("#runtime-example-content");
  const ex = state.data.runtime_example;
  if (!el || !ex) return;
  const s = ex.step;
  const inputKeywords = (ex.keywords || []).slice(0, 8).map((item) => `<span class="chip">${esc(item)}</span>`).join("");
  const inputPaths = (ex.relevant_paths || []).slice(0, 4).map((item) => `<code>${esc(item)}</code>`).join(" ");
  const evidence = (s.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const runnerEvidence = (s.runner_evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const artifacts = (ex.artifacts || [])
    .map(
      (item) => `<article><code>${esc(item.name)}</code><p>${esc(item.role)}</p><pre class="artifact-json">${esc(
        item.example || "{}"
      )}</pre></article>`
    )
    .join("");
  el.innerHTML = `
    <div class="example-header"><div><span class="example-label">${esc(ex.issue)} / step ${esc(
      s.number
    )}</span><h3>${esc(s.subject)}</h3></div><div class="example-verdict">Runner verdict <strong>${esc(s.verdict)}</strong></div></div>
    <div class="example-metrics"><span>window <b>${fmtInt(s.unresolved_before)}</b> -> <b>${fmtInt(
      s.unresolved_after
    )}</b></span><span>semantic_score <b>${esc(s.semantic_score)}</b></span><span>build_success_prob <b>${esc(
      s.build_success_prob
    )}</b></span><span>bad_mass <b>${esc(s.posterior_bad_mass)}</b></span><span>info_gain <b>${esc(
      s.info_gain
    )}</b></span></div>
    <div class="runtime-flow-grid">
      <article class="runtime-card input-card"><h3>Sent to extraction</h3><p><strong>Issue:</strong> ${esc(ex.title)}</p><p>${esc(ex.crash_summary || "")}</p><div class="chips">${inputKeywords}</div><p><strong>Relevant paths:</strong> ${inputPaths}</p><p><strong>Candidate:</strong> <code>${esc(s.selected_sha)}</code> via <code>${esc(ex.method.diff_mode)}</code> diff; extraction mode <code>${esc(ex.method.diff_extraction)}</code>.</p><p class="tiny-note">The raw changed-line diff is available to the extractor up to 600k characters; the scorer receives the extraction instead.</p></article>
      <article class="runtime-card output-card"><h3>LLM extraction + score output</h3><div class="diff-summary">${esc(s.diff_summary || "No saved diff summary.")}</div><h4>Score evidence</h4><ul class="evidence">${evidence}</ul></article>
      <article class="runtime-card runner-card"><h3>What never comes from the LLM</h3><p>The pipeline checks out the selected SHA, builds it, and runs the issue reproducer.</p><h4>Runner evidence</h4><ul class="evidence">${runnerEvidence}</ul><details class="diff"><summary>Captured runner log excerpt</summary><div class="diff-body"><div class="diff-summary">${esc(s.runner_log_excerpt || "No excerpt saved.")}</div></div></details></article>
    </div>
    <div class="artifact-flow runtime-artifacts"><div class="artifact-title">Actual JSON records written for this run</div><div class="runtime-artifact-grid">${artifacts}</div></div>`;
}

function renderComparison() {
  const rows = state.data.issues
    .map((iss) => {
      const c = iss.comparison;
      const cells = [
        { key: "git", ...c.git },
        { key: "old_model", ...c.old_model },
        { key: "specific_heuristic", ...c.specific_heuristic },
        { key: "general_heuristic", ...c.general_heuristic },
        { key: "parent_llm_topk3", ...c.parent_llm_topk3 },
      ];
      // best among clean numeric cells
      let bestVal = Infinity;
      for (const cell of cells) {
        if (typeof cell.steps === "number" && cell.steps > 0 && isCleanStatus(cell.status)) {
          bestVal = Math.min(bestVal, cell.steps);
        }
      }
      const tds = cells
        .map((cell) => {
          const clean = isCleanStatus(cell.status);
          const num = typeof cell.steps === "number" && cell.steps > 0;
          let cls = "num";
          let content = num ? cell.steps : "—";
          let title = cell.status || "";
          if (num && clean && cell.steps === bestVal) cls += " cell-best";
          if (num && !clean) {
            cls += " cell-warn";
            content = cell.steps + " ⚠";
          }
          return `<td class="${cls}" title="${esc(title)}">${content}</td>`;
        })
        .join("");
      return `<tr>
        <td class="left">
          <a class="issue-link" href="#explorer" data-issue="${esc(iss.issue)}">${esc(iss.issue)}</a>
          <span class="issue-title">${esc(iss.title)}</span>
        </td>
        ${tds}
      </tr>`;
    })
    .join("");
  $("#cmp-body").innerHTML = rows;

  // clicking an issue in the table jumps to explorer with it selected
  document.querySelectorAll(".issue-link").forEach((a) => {
    a.addEventListener("click", () => {
      const idx = state.data.issues.findIndex((x) => x.issue === a.dataset.issue);
      if (idx >= 0) {
        state.issueIndex = idx;
        $("#issue-select").value = String(idx);
        renderExplorer();
      }
    });
  });
}

function buildControls() {
  const sel = $("#issue-select");
  sel.innerHTML = state.data.issues
    .map((iss, i) => `<option value="${i}">${esc(iss.issue)} — ${esc(iss.title)}</option>`)
    .join("");
  sel.addEventListener("change", () => {
    state.issueIndex = Number(sel.value);
    renderExplorer();
  });

  const seg = $("#method-seg");
  const methodLabels = {
    "tuned-heuristic": "Issue-specific heuristic",
    "general-heuristic": "Fixed-vocabulary heuristic",
    "parent-llm-topk3": "Parent-diff + LLM top-k3",
  };
  seg.innerHTML = METHOD_KEYS.map((k) => `<button data-key="${k}">${esc(methodLabels[k])}</button>`).join("");
  seg.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      state.methodKey = b.dataset.key;
      renderExplorer();
    });
  });
}

function renderExplorer() {
  const iss = state.data.issues[state.issueIndex];
  const method = iss.methods[state.methodKey];

  // active seg button
  $("#method-seg")
    .querySelectorAll("button")
    .forEach((b) => b.classList.toggle("active", b.dataset.key === state.methodKey));

  renderIssueContext(iss);
  renderRunHeader(iss, method);
  renderWindowViz(method);
  renderSteps(iss, method);
}

function renderIssueContext(iss) {
  const kw = (iss.keywords || []).slice(0, 10).map((k) => `<span class="chip">${esc(k)}</span>`).join("");
  const link = iss.issue_url
    ? ` <a href="${esc(iss.issue_url)}" target="_blank" rel="noopener">issue ↗</a>`
    : "";
  $("#issue-context").innerHTML = `
    <h3>${esc(iss.issue)} — ${esc(iss.title)}${link}</h3>
    ${iss.crash_summary ? `<p class="summary">${esc(iss.crash_summary)}</p>` : ""}
    <div class="chips">${kw}</div>`;
}

function renderRunHeader(iss, method) {
  const t = method.trace;
  if (!t) {
    $("#run-header").innerHTML = "";
    return;
  }
  const scorerLabel =
    t.scorer === "model"
      ? `LLM (${esc(t.model_name || "model")}, top-k${t.model_top_k ?? "?"})`
      : "Heuristic scorer";
  const firstBad = method.first_bad || t.last_first_bad || "—";
  const stats = [
    ["Method", method.short],
    ["Selection", scorerLabel],
    ["Build steps", method.steps_count != null ? String(method.steps_count) : String(t.total_steps)],
    ["Start window", fmtInt(t.initial_unresolved) + " commits"],
    ["Good commit", `<span class="mono">${esc(t.good_commit)}</span>`],
    ["Bad commit", `<span class="mono">${esc(t.bad_commit)}</span>`],
    ["First-bad found", `<span class="mono">${esc(String(firstBad).slice(0, 12))}</span>`],
    ["Status", esc(method.status || t.status)],
  ];
  $("#run-header").innerHTML = stats
    .map(
      ([k, v]) =>
        `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${
          /commit|mono/.test(v) ? "mono" : ""
        }">${v}</div></div>`
    )
    .join("");
}

function renderWindowViz(method) {
  const t = method.trace;
  const box = $("#window-viz");
  if (!t || !t.steps.length) {
    box.innerHTML = "";
    return;
  }
  const series = [{ v: t.initial_unresolved, step: 0 }].concat(
    t.steps.map((s) => ({ v: s.unresolved_after, step: s.step, verdict: s.verdict }))
  );
  const maxLog = Math.log10(Math.max(2, t.initial_unresolved));
  const bars = series
    .map((pt) => {
      const val = Math.max(1, pt.v || 1);
      const h = Math.max(4, (Math.log10(val) / maxLog) * 100);
      const cls = pt.verdict === "bad" ? "bar bad" : "bar";
      const label = pt.step === 0 ? "start" : "s" + pt.step;
      return `<div class="barcol">
        <div class="blabel">${fmtInt(pt.v)}</div>
        <div class="${cls}" style="height:${h}%"></div>
        <div class="bstep">${label}</div>
      </div>`;
    })
    .join("");
  box.innerHTML = `
    <h4>Candidate window shrinking</h4>
    <p class="cap">Unresolved commits remaining after each build step (log scale). Red = the step whose verdict was <em>bad</em>.</p>
    <div class="bars">${bars}</div>`;
}

function verdictBadge(v) {
  if (v === "good") return '<span class="badge badge-good">good</span>';
  if (v === "bad") return '<span class="badge badge-bad">bad</span>';
  return `<span class="badge badge-skip">${esc(v || "skip")}</span>`;
}

function scoreChips(s, isModel) {
  const items = [
    ["semantic", s.semantic_score],
    ["build-ok prob", s.build_success_prob],
    ["bad-mass", s.posterior_bad_mass],
    ["info gain", s.info_gain],
  ];
  return items
    .filter(([, v]) => v != null)
    .map(([k, v]) => `<div class="score"><span class="sk">${esc(k)}</span><span class="sv">${esc(v)}</span></div>`)
    .join("");
}

function renderSteps(iss, method) {
  const t = method.trace;
  const box = $("#steps");
  if (!t) {
    box.innerHTML = `<div class="git-note">No per-step trace stored for this run (${esc(
      method.status || "unavailable"
    )}).</div>`;
    return;
  }
  const isModel = t.scorer === "model";

  const stepsHtml = t.steps
    .map((s) => {
      const reduction =
        s.unresolved_before && s.unresolved_after
          ? Math.round((1 - s.unresolved_after / s.unresolved_before) * 100)
          : null;

      const cands = (s.top_candidates || [])
        .map((c) => {
          const chosen = c.sha === s.sha;
          const diff =
            isModel && c.diff_summary
              ? `<details class="diff cdiff"><summary>What the model read from the diff</summary><div class="diff-body"><div class="diff-summary">${esc(
                  c.diff_summary
                )}</div></div></details>`
              : "";
          return `<div class="cand ${chosen ? "chosen" : ""}">
            <span class="crank">#${c.rank}</span>
            <span class="csha">${esc(c.sha)}</span>${chosen ? '<span class="chosen-tag">← chosen</span>' : ""}
            <div class="csubj">${esc(c.subject)}</div>
            <div class="cscores">semantic ${esc(c.semantic_score)} · bad-mass ${esc(
            c.posterior_bad_mass
          )} · build-ok ${esc(c.build_success_prob)} · info gain ${esc(c.info_gain)}</div>
            ${diff}
          </div>`;
        })
        .join("");

      const diffBlock =
        isModel && s.diff_summary
          ? `<details class="diff" open><summary>LLM diff extraction for the chosen commit</summary><div class="diff-body"><div class="diff-summary">${esc(
              s.diff_summary
            )}</div></div></details>`
          : "";

      const evidence = (s.evidence || [])
        .map((e) => `<li>${esc(e)}</li>`)
        .join("");

      return `<div class="step">
        <div class="step-head">
          <span class="step-no">${s.step}</span>
          <div class="step-commit">
            <div class="sha">${esc(s.sha)}</div>
            <div class="subj">${esc(s.subject)}</div>
          </div>
          ${verdictBadge(s.verdict)}
        </div>
        <div class="step-body">
          <div class="window-row">
            <span class="wtag">window</span>
            <span class="wnum">${fmtInt(s.unresolved_before)}</span>
            <span class="arrow">→</span>
            <span class="wnum">${fmtInt(s.unresolved_after)}</span>
            ${reduction != null ? `<span class="cut">−${reduction}%</span>` : ""}
          </div>
          <div class="scores">${scoreChips(s, isModel)}</div>
          ${evidence ? `<div class="block-title">Why this commit</div><ul class="evidence">${evidence}</ul>` : ""}
          ${diffBlock}
          ${
            cands
              ? `<details class="cands"><summary>${
                  isModel
                    ? "Top-3 candidates the model compared this step"
                    : "Top-ranked candidates this step"
                }</summary>${cands}</details>`
              : ""
          }
        </div>
      </div>`;
    })
    .join("");

  const firstBad = method.first_bad || t.last_first_bad;
  const finalBanner =
    firstBad && isCleanStatus(method.status)
      ? `<div class="final-banner">
          <div class="fb-t">First-bad commit identified in ${t.total_steps} build steps</div>
          <div><span class="sha">${esc(String(firstBad).slice(0, 12))}</span></div>
        </div>`
      : "";

  box.innerHTML = stepsHtml + finalBanner;
}

boot();
