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
  if ($("#k12-variant-body")) {
    renderK12VariantComparison();
    renderExpansionComparison();
    renderTerraBcrComparison();
  }
  if ($("#deterministic-facts-bcr-body")) {
    renderDeterministicFactsBcr();
    renderDeterministicFactsV16Window();
  }
  if ($("#heuristic-factor-ablation-body")) {
    renderHeuristicFactorAblation();
  }
  if ($("#runtime-example-content")) renderRuntimeExample();
  if ($("#master-status-body")) renderMasterStatus();
  if ($("#causality-cases")) renderCausalityStudy();
  if ($("#issue-select")) {
    buildControls();
    renderExplorer();
  }
}

function renderCausalityStudy() {
  const study = state.data.first_bad_causality_study;
  const summary = $("#causality-summary");
  const deployability = $("#causality-deployability");
  const cases = $("#causality-cases");
  const rules = $("#causality-rules");
  const exclusions = $("#causality-exclusions");
  if (!study || !summary || !deployability || !cases || !rules || !exclusions) return;

  const chips = (items, kind) =>
    items.filter(Boolean).map((item) => '<code class="causality-chip ' + kind + '">' + esc(item) + "</code>").join("");
  const roleLabel = (role) => role === "indirect-enabling" ? "indirect producer" : role;

  summary.innerHTML = [
    ["Validated boundaries", study.aggregate.total_cases, "scoped issues reviewed"],
    ["Direct mechanism match", study.aggregate.direct, "same subsystem, symbol, or feature path"],
    ["Indirect producer match", study.aggregate.indirect_enabling, "upstream state later rejected downstream"],
    ["No visible match", study.aggregate.no_visible_match, "after manual source review"],
  ].map(
    ([label, value, detail]) =>
      '<article class="causality-metric"><span>' + esc(label) + "</span><strong>" + esc(value) +
      "</strong><small>" + esc(detail) + "</small></article>"
  ).join("");
  deployability.textContent = study.deployability_notice;

  cases.innerHTML = study.cases.map((row, index) => {
    const role = roleLabel(row.causal_role);
    const traceLimited = /wrapper-only|not retained/i.test(row.trace_evidence.quality);
    const traceNote = row.trace_evidence.artifact === "not retained"
      ? "Raw crash artifact not retained; classification is source-level."
      : "Saved artifact retained in the canonical study.";
    return '<details class="causality-case"' + (index < 2 ? " open" : "") + ">" +
      "<summary>" +
      '<span class="causality-case-index">' + String(index + 1).padStart(2, "0") + "</span>" +
      '<span class="causality-case-title"><strong>' + esc(row.issue) + "</strong><span>" + esc(row.title) + "</span></span>" +
      '<span class="causality-role ' + esc(row.causal_role) + '">' + esc(role) + "</span>" +
      '<span class="causality-overlap">' + esc(row.static_overlap) + " overlap</span>" +
      "</summary>" +
      '<div class="causality-case-body">' +
      '<div class="causality-case-head"><div><span class="variant-label">Validated first bad</span><h3><code>' +
      esc(row.first_bad.slice(0, 12)) + "</code></h3><p>" + esc(row.subject) + "</p></div>" +
      '<div class="trace-evidence ' + (traceLimited ? "trace-limited" : "") + '"><span class="variant-label">Saved trace evidence / ' +
      esc(row.trace_evidence.quality) + "</span><p>" + esc(row.trace_evidence.text) + "</p><small>" +
      esc(traceNote) + "</small></div></div>" +
      '<div class="causality-evidence-grid">' +
      "<article><span>Static overlap</span><strong>" + esc(row.static_overlap) + "</strong><p>" + esc(row.overlap) + "</p></article>" +
      "<article><span>Causal interpretation</span><strong>" + esc(role) + "</strong><p>" + esc(row.causal_explanation) + "</p></article>" +
      "<article><span>Interpretation limit</span><strong>Boundary validated</strong><p>" + esc(row.interpretation_limit) + "</p></article>" +
      "</div>" +
      '<div class="causality-token-groups">' +
      "<div><span>Crash-derived terms</span><div>" + chips(row.keywords.slice(0, 12), "term") + "</div></div>" +
      "<div><span>Relevant paths</span><div>" + chips(row.relevant_paths.slice(0, 7), "path") + "</div></div>" +
      "<div><span>Changed files</span><div>" + chips(row.changed_files.slice(0, 8), "file") + "</div></div>" +
      "</div>" +
      '<details class="causality-diff"><summary>Audited first-bad diff excerpt</summary><pre>' +
      esc(row.diff_excerpt) + "</pre></details></div></details>";
  }).join("");

  rules.innerHTML = study.rules.map((rule) =>
    '<article class="causality-rule"><div class="causality-rule-number">' + esc(rule.number) + "</div><div><h3>" +
    esc(rule.title) + "</h3><p>" + esc(rule.summary) + "</p><ul>" +
    rule.rationale.map((item) => "<li>" + esc(item) + "</li>").join("") +
    "</ul></div></article>"
  ).join("");
  exclusions.innerHTML = "<h3>Excluded from deployable runtime input</h3><ul>" +
    study.exclusions.map((item) => "<li>" + esc(item) + "</li>").join("") +
    "</ul>";
}

function renderMasterStatus() {
  const status = state.data.master_status;
  const body = $("#master-status-body");
  const count = $("#master-status-count");
  const search = $("#master-status-search");
  const metrics = $("#master-status-metrics");
  if (!status || !body || !count || !search || !metrics) return;

  metrics.innerHTML = [
    `<article class="master-metric"><span>Tracked issues</span><strong>${esc(status.summary.total_issues)}</strong><small>all rows in the canonical ledger</small></article>`,
    ...status.summary.methods.map(
      (method) => `<article class="master-metric"><span>${esc(method.label)}</span><strong>${esc(method.valid_results)}</strong><small>valid terminal results</small></article>`,
    ),
  ].join("");

  const renderMethodCell = (result) => {
    if (result.state === "completed") return `<td class="master-method completed">${esc(result.steps)} / ${esc(result.skips)}</td>`;
    if (result.state === "running") return '<td class="master-method running">running</td>';
    if (result.state === "stopped") return `<td class="master-method running">stopped<br /><small>${esc(result.steps ?? 0)} steps retained</small></td>`;
    if (result.state === "non_clean") return `<td class="master-method non-clean">${esc(result.steps ?? "-")} / ${esc(result.skips ?? "-")}<br /><small>non-clean</small></td>`;
    return '<td class="master-method">-</td>';
  };

  const renderRows = () => {
    const query = search.value.trim().toLowerCase();
    const rows = status.rows.filter((row) => {
      if (!query) return true;
      return Object.entries(row).some(([key, value]) => {
        if (value && typeof value === "object") {
          return `${key} ${value.state} ${value.steps ?? ""} ${value.skips ?? ""}`.toLowerCase().includes(query);
        }
        return String(value).toLowerCase().includes(query);
      });
    });
    count.textContent = `${rows.length} / ${status.rows.length} rows`;
    body.innerHTML = rows
      .map(
        (row) => `<tr>
          <td class="left"><strong>${esc(row.issue)}</strong></td>
          <td class="left">${esc(row.state)}</td>
          <td class="master-interval" title="${esc(row.interval_source || "no endpoint-matched run history or explicit ledger interval")}">${row.interval_commits === null ? "<small>unavailable</small>" : esc(Number(row.interval_commits).toLocaleString())}</td>
          ${renderMethodCell(row.git)}
          ${renderMethodCell(row.legacy_lm)}
          ${renderMethodCell(row.tuned_heuristic)}
          ${renderMethodCell(row.weak_general_heuristic)}
          ${renderMethodCell(row.bcr)}
          <td><code>${esc(row.good_anchor)}</code></td>
          <td><code>${esc(row.bad_anchor)}</code></td>
          <td><code>${esc(row.first_bad)}</code></td>
          <td class="left master-notes">${esc(row.notes)}</td>
        </tr>`
      )
      .join("");
  };
  search.addEventListener("input", renderRows);
  renderRows();
}

function renderCausalRetrievalExample() {
  const example = state.data.k12_variants?.causal_retrieval_example;
  const el = $("#causal-retrieval-example");
  if (!example || !el) return;

  const retrieval = example.retrieval;
  const causal = example.causal_evidence;
  const handoff = example.shared_scorer_handoff;
  const hunk = retrieval.selected_hunks[0];
  const context = retrieval.function_contexts[0];
  const link = causal.issue_link || {};
  el.innerHTML = `
    <article class="causal-retrieval-summary">
      <div><span class="variant-label">Saved example / ${esc(example.issue)} step ${esc(example.step)}</span><h3>Candidate <code>${esc(example.candidate_sha)}</code> is retrieved before it is scored</h3><p>${esc(example.candidate_subject)} was ranked ${esc(example.candidate_rank)} in the causal top-k12 frontier. It is not the SHA built in this step, which lets the example distinguish BCR evidence generation from the later deterministic choice and runner verdict.</p></div>
      <dl><dt>Parent diff</dt><dd>${esc(Number(retrieval.raw_diff_chars).toLocaleString())} chars, ${retrieval.raw_diff_truncated ? "truncated" : "not truncated"}</dd><dt>Retrieved</dt><dd>${esc(retrieval.selected_files.length)} files, ${esc(retrieval.selected_hunks.length)} hunks, ${esc(retrieval.function_contexts.length)} contexts</dd><dt>Omitted</dt><dd>${esc(retrieval.omitted_hunk_count)} lower-ranked hunks</dd></dl>
    </article>
    <div class="causal-retrieval-flow">
      <article><span class="variant-label">1 / deterministic retrieval</span><h3>From changed files to top hunks</h3><p>Changed files are ranked first: <code>+8 relevant path</code>, <code>+4 high-risk path</code>, <code>+2 issue/path-token overlap</code>, keeping at most 20. Parent-diff hunks are then parsed and ranked by <code>4 * relevant-path + 2 * issue-keyword + visible-symbol count</code>; ties prefer any matched hunk, then the shorter patch. High-risk paths influence file selection and remain an auditable match reason. The first eight matching hunks are retained, or the top eight fall back when none match.</p><p class="code-ref"><code>select_causal_retrieval_files</code> &rarr; <code>commit_parent_diff_for_files</code> &rarr; <code>parse_unified_diff_hunks</code> &rarr; <code>retrieve_causal_diff_evidence</code>.</p></article>
      <article><span class="variant-label">2 / actual retrieved hunk</span><h3><code>${esc(hunk.path)}</code></h3><p><strong>Reasons:</strong> ${esc(hunk.match_reasons.join(", "))}<br /><strong>Symbols:</strong> ${esc(hunk.symbols.join(", "))}</p><pre class="formula">${esc(hunk.header)}\n${esc(hunk.patch)}</pre></article>
      <article><span class="variant-label">3 / retrieved function context</span><h3><code>${esc(context.path)}</code></h3><p><strong>Symbol:</strong> <code>${esc(context.symbol)}</code>. Context is loaded at the candidate SHA for up to four unique hunk symbols.</p><pre class="formula">${esc(context.context)}</pre></article>
    </div>
    <article class="causal-evidence-card">
      <span class="variant-label">4 / BCR-only causal extractor</span><h3>Structured evidence retained under <code>causal_evidence</code></h3><p>${esc(causal.summary)}</p><div class="causal-evidence-grid"><div><strong>Changed symbols</strong><p>${esc(causal.changed_symbols.join(", "))}</p></div><div><strong>Mechanism</strong><p>${esc(causal.behavioral_change.join(" "))}</p></div><div><strong>Issue link</strong><p>${esc(link.explanation || "No explanation saved.")}</p></div><div><strong>Confidence</strong><p>${esc(causal.confidence)}; ${esc(causal.build_risk.join("; "))}</p></div></div>
    </article>
    <article class="causal-handoff-card">
      <span class="variant-label">5 / shared scorer handoff</span><h3>BCR retrieval becomes the shared scorer's <code>diff_summary</code>; it is not copied into generic <code>evidence</code></h3><p>The causal extractor serializes its output with <code>format_causal_diff_evidence</code>, and the normal <code>model_score_commits</code> prompt receives that text for this candidate. The shared scorer then returns a semantic score and a build-success probability. Later, <code>record.evidence</code> contains only <code>model-scored</code>, the scorer's short reasons, and features; it is not the BCR retrieval payload.</p><div class="causal-handoff-grid"><span>extraction <code>${esc(handoff.diff_extraction)}</code></span><span>summary injected ${handoff.diff_summary_injected ? "yes" : "no"}</span><span>semantic score <strong>${esc(handoff.semantic_score)}</strong></span><span>build success <strong>${esc(handoff.build_success_prob)}</strong></span><span>selection score <strong>${esc(handoff.selection_score)}</strong></span></div><p class="tiny-note">This candidate was not selected for the runner. The recorded <code>${esc(handoff.runner_verdict)}</code> verdict belongs to selected SHA <code>${esc(handoff.runner_selected_sha)}</code>, and is another separate artifact.</p>
    </article>`;
}

function renderK12VariantCell(cell, referenceSteps) {
  if (cell.state === "completed") {
    const delta = cell.steps - referenceSteps;
    const cls = delta < 0 ? "cell-good" : delta > 0 ? "cell-warn" : "";
    const suffix = delta < 0 ? ` <small>(${delta})</small>` : delta > 0 ? ` <small>(+${delta})</small>` : "";
    return `<td class="num ${cls}" title="${esc(cell.note || "completed endpoint-valid run")}">${esc(cell.steps)}${suffix}</td>`;
  }
  if (cell.state === "running") {
    return `<td class="variant-state running" title="${esc(cell.note || "in progress")}">running<br /><small>${esc(cell.steps || 0)} steps</small></td>`;
  }
  if (cell.state === "invalid") {
    return `<td class="variant-state invalid" title="${esc(cell.note || "invalid result")}">invalid</td>`;
  }
  return `<td class="variant-state">not run</td>`;
}

function renderK12VariantComparison() {
  const variants = state.data.k12_variants;
  if (!variants) return;
  renderCausalRetrievalExample();
  const cards = $("#k12-variant-cards");
  cards.innerHTML = variants.configurations
    .map((config) => {
      const arms = [
        ["k3", "historical k3"],
        ["k12", "fixed k12"],
      ];
      return `<article class="variant-summary-card"><h3>${esc(config.label)}</h3>${arms
        .map(([arm, label]) => {
          const aggregate = config[arm].aggregate;
          const complete = aggregate.completed;
          const mean = aggregate.mean_steps == null ? "-" : aggregate.mean_steps;
          const refMean = aggregate.matched_reference_mean == null ? "-" : aggregate.matched_reference_mean;
          return `<div class="variant-arm"><strong>${esc(label)}</strong><b>${esc(mean)}</b><span>mean builds, n=${esc(complete)}</span><small>matched reference: ${esc(refMean)} mean; W/T/L ${esc(aggregate.wins)}/${esc(aggregate.ties)}/${esc(aggregate.losses)}</small></div>`;
        })
        .join("")}</article>`;
    })
    .join("");

  $("#k12-variant-note").innerHTML = `<strong>Reference:</strong> ${esc(
    variants.reference.label
  )} completes all ${esc(variants.reference.aggregate.completed)} cases in <strong>${esc(
    variants.reference.aggregate.total_steps
  )}</strong> runner builds (${esc(variants.reference.aggregate.mean_steps)} mean). ${esc(variants.validity_note)}`;

  $("#k12-variant-body").innerHTML = variants.rows
    .map((row) => {
      const cells = variants.configurations
        .map((config) => [row[config.key].k3, row[config.key].k12])
        .flat();
      return `<tr><td class="left"><strong>${esc(row.issue)}</strong><span class="issue-title">${esc(
        row.title
      )}</span></td><td class="num cell-reference">${esc(row.reference.steps)}</td>${cells
        .map((cell) => renderK12VariantCell(cell, row.reference.steps))
        .join("")}</tr>`;
    })
    .join("");
}

function renderExpansionComparison() {
  const comparison = state.data.expansion_comparison;
  if (!comparison) return;
  const original = comparison.aggregate.original;
  const causal = comparison.aggregate.causal;
  const heuristic = comparison.aggregate.heuristic;
  $("#expansion-method-cards").innerHTML = comparison.methods
    .map((method) => {
      const stats = comparison.aggregate[method.key];
      const delta = stats.vs_original?.step_delta;
      const comparisonLine = method.key === "original"
        ? `${stats.count}/${stats.count} boundary matches in the common reference.`
        : `${stats.vs_original.wins}/${stats.vs_original.ties}/${stats.vs_original.losses} W/T/L vs original; ${delta > 0 ? "+" : ""}${delta} total builds.`;
      return `<article class="expansion-method"><div class="variant-label">${esc(method.label)}</div><h3>${esc(stats.avg_steps)} mean builds</h3><p><strong>Observed:</strong> ${esc(method.observed_advantage)}</p><p><strong>Limit:</strong> ${esc(method.observed_limitation)}</p><p><strong>Expansion role:</strong> ${esc(method.expansion_role)}</p><small>${esc(comparisonLine)}</small></article>`;
    })
    .join("");
  $("#expansion-type-body").innerHTML = comparison.by_type
    .map((group) => {
      const delta = group.causal_step_delta_vs_original;
      const cls = delta < 0 ? "cell-good" : delta > 0 ? "cell-warn" : "";
      return `<tr><td class="left">${esc(group.label)}</td><td>${esc(group.count)}</td><td>${esc(group.heuristic_avg_steps)}</td><td>${esc(group.original_avg_steps)}</td><td>${esc(group.causal_avg_steps)}</td><td>${esc(group.causal_wins)} / ${esc(group.causal_ties)} / ${esc(group.causal_losses)}</td><td class="${cls}">${delta > 0 ? "+" : ""}${esc(delta)}</td></tr>`;
    })
    .join("");
  $("#expansion-recommendation").innerHTML = `<strong>Observed aggregate:</strong> heuristic ${esc(heuristic.total_steps)} builds (${esc(heuristic.avg_steps)} mean), original model ${esc(original.total_steps)} (${esc(original.avg_steps)}), causal model ${esc(causal.total_steps)} (${esc(causal.avg_steps)}). <strong>Recommendation:</strong> ${esc(comparison.recommendation)}<br /><span>${esc(comparison.caveat)}</span>`;
}

function renderTerraBcrComparison() {
  const comparison = state.data.terra_bcr_comparison;
  if (!comparison) return;
  const terra = comparison.aggregate.terra;
  const parentWindow = comparison.aggregate.parent_window;
  const formatDelta = (result) =>
    `${result.wins} / ${result.ties} / ${result.losses} W/T/L, ${result.step_delta > 0 ? "+" : ""}${result.step_delta} builds`;

  $("#terra-bcr-summary").innerHTML = [
    ["Terra BCR", `${terra.mean_steps} mean`, `${terra.total_steps} builds, ${terra.completed} terminal cases`],
    ["Versus mini BCR", formatDelta(terra.vs_mini_bcr), "same BCR/top-k12 contract"],
    ["Versus parent+LLM k3", formatDelta(terra.vs_parent_llm_topk3), "different frontier size"],
    ["Boundary evidence", `${terra.canonical_boundary_matches}/10 canonical`, `${terra.skip_total} skips; one apply/reapply representation case`],
  ]
    .map(
      ([label, metric, detail]) =>
        `<article class="variant-summary-card"><h3>${esc(label)}</h3><div class="variant-arm"><strong>${esc(metric)}</strong><span>${esc(detail)}</span></div></article>`
    )
    .join("");

  $("#parent-window-summary").innerHTML = [
    ["Parent window", `${parentWindow.mean_steps} mean`, `${parentWindow.total_steps} builds, ${parentWindow.completed} terminal cases`],
    ["Versus single-parent Terra", formatDelta(parentWindow.vs_terra_single_parent), "same model, frontier, and causal extractor"],
    ["Versus parent+LLM k3", formatDelta(parentWindow.vs_parent_llm_topk3), "different model, extraction, and frontier size"],
    ["Boundary evidence", `${parentWindow.canonical_boundary_matches}/10 canonical`, `${parentWindow.skip_total} skip; one apply/reapply representation case`],
  ]
    .map(
      ([label, metric, detail]) =>
        `<article class="variant-summary-card"><h3>${esc(label)}</h3><div class="variant-arm"><strong>${esc(metric)}</strong><span>${esc(detail)}</span></div></article>`
    )
    .join("");

  $("#terra-bcr-body").innerHTML = comparison.rows
    .map((row) => {
      const delta = row.terra_bcr_steps - row.mini_bcr_steps;
      const deltaClass = delta < 0 ? "cell-good" : delta > 0 ? "cell-warn" : "";
      const windowDelta = row.parent_window_steps - row.terra_bcr_steps;
      const windowDeltaClass = windowDelta < 0 ? "cell-good" : windowDelta > 0 ? "cell-warn" : "";
      const terraCell = `${row.terra_bcr_steps}${delta ? ` <small>(${delta > 0 ? "+" : ""}${delta} vs mini)</small>` : ""}`;
      const windowCell = `${row.parent_window_steps}${windowDelta ? ` <small>(${windowDelta > 0 ? "+" : ""}${windowDelta} vs single)</small>` : ""}`;
      return `<tr><td class="left"><strong>${esc(row.issue)}</strong><span class="issue-title">${esc(row.title)}</span></td><td class="num">${esc(row.legacy_lm_steps)}</td><td class="num">${esc(row.parent_llm_topk3_steps)}</td><td class="num">${esc(row.mini_bcr_steps)}</td><td class="num ${deltaClass}" title="${esc(row.run_label)} on ${esc(row.source)}">${terraCell}</td><td class="num ${windowDeltaClass}" title="${esc(row.parent_window_run_label)} on ${esc(row.parent_window_source)}; ${esc(row.parent_window_skips)} skips">${windowCell}</td><td class="left" title="${esc(row.note)}">${row.parent_window_canonical_boundary ? "canonical" : "apply/reapply"}</td></tr>`;
    })
    .join("");

  $("#terra-bcr-note").innerHTML = `<strong>Verified model:</strong> <code>${esc(
    comparison.model.name
  )}</code> with <code>${esc(comparison.model.reasoning_effort)}</code> reasoning effort. ${esc(
    comparison.model.note
  )}<br /><strong>Parent-window contract:</strong> ${esc(comparison.parent_window_configuration)}<br /><strong>Scope:</strong> ${esc(comparison.scope)}<br /><strong>Caveat:</strong> ${esc(
    comparison.caveat
  )}`;
  renderLmRepeatability();
}

function renderLmRepeatability() {
  const repeatability = state.data.terra_bcr_comparison?.repeatability;
  if (!repeatability) return;
  const replay = repeatability.cached_same_host_replay;
  const topk3 = repeatability.cross_host_parent_topk3;
  const topk20 = repeatability.cross_host_parent_topk20;
  $("#lm-repeatability-summary").innerHTML = [
    ["Cached replay", `${replay.exact_step_and_verdict_paths}/${replay.issue_groups} exact paths`, "same-host default score cache"],
    ["Cross-host top-k3", `${topk3.boundary_agreements}/${topk3.issue_groups} boundaries`, `mean |step delta| ${topk3.mean_absolute_step_delta}`],
    ["Cross-host top-k20", `${topk20.boundary_agreements}/${topk20.issue_groups} boundaries`, `mean selected-SHA overlap ${topk20.mean_selected_sha_jaccard}`],
    ["Parent window", `${repeatability.parent_window_independent_repeats} independent repeats`, "repeatability not yet measured for this exact method"],
  ]
    .map(
      ([label, metric, detail]) =>
        `<article class="variant-summary-card"><h3>${esc(label)}</h3><div class="variant-arm"><strong>${esc(metric)}</strong><span>${esc(detail)}</span></div></article>`
    )
    .join("");

  const cohorts = [
    ["Parent+LLM top-k3, AWS vs EDU", topk3],
    ["Parent+LLM 600k top-k20, AWS vs EDU", topk20],
  ];
  $("#lm-repeatability-body").innerHTML = cohorts
    .map(
      ([label, row]) => `<tr><td class="left"><strong>${esc(label)}</strong></td><td>${esc(row.issue_groups)}</td><td>${esc(row.boundary_agreements)} / ${esc(row.issue_groups)}</td><td>${esc(row.mean_absolute_step_delta)}</td><td>${esc(row.max_absolute_step_delta)}</td><td>${esc(row.mean_selected_sha_jaccard)}</td><td>${esc(row.mean_same_position_rate)}</td></tr>`
    )
    .join("");
  $("#lm-repeatability-note").innerHTML = `<strong>Observed:</strong> ${esc(
    repeatability.conclusion
  )}<br /><strong>Cache control:</strong> ${esc(
    repeatability.cache_caveat
  )}<br /><strong>Current window-method limit:</strong> ${esc(repeatability.parent_window_caveat)}`;
}

function renderExperimentCell(result, baselineSteps = null) {
  if (!result || result.state === "not_run") return '<span class="variant-state">not run</span>';
  if (result.state !== "completed") {
    const steps = typeof result.steps === "number" && result.steps > 0 ? ` (${result.steps})` : "";
    return `<span class="variant-state ${esc(result.state)}">${esc(result.state)}${esc(steps)}</span>`;
  }
  const delta = typeof baselineSteps === "number" ? result.steps - baselineSteps : 0;
  const cls = delta < 0 ? "cell-good" : delta > 0 ? "cell-warn" : "";
  const suffix = baselineSteps != null && delta ? ` <small>(${delta > 0 ? "+" : ""}${delta})</small>` : "";
  return `<span class="${cls}">${esc(result.steps)}${suffix}</span>`;
}

function renderDeterministicFactsBcr() {
  const comparison = state.data.deterministic_facts_bcr;
  if (!comparison) return;
  const aggregate = comparison.aggregate;
  const deltaText = (value) =>
    `${value.wins} / ${value.ties} / ${value.losses} W/T/L; ${value.step_delta > 0 ? "+" : ""}${value.step_delta} builds on n=${value.compared}`;
  $("#deterministic-facts-bcr-summary").innerHTML = `<strong>Human-study BCR V15:</strong> ${esc(aggregate.completed)} terminal zero-skip cases, ${esc(aggregate.total_steps)} runner builds, ${esc(aggregate.mean_steps)} mean. ${esc(aggregate.canonical_boundary_matches)} match the Git/reference boundary; ${esc(aggregate.accepted_alternate_boundaries)} is the known original-apply versus reapply representation. No accepted V15 histories remain interrupted or queued.<br /><strong>Matched comparison:</strong> versus single-parent BCR ${esc(deltaText(aggregate.vs_terra_single_parent))}; versus historical human-guided BCR ${esc(deltaText(aggregate.vs_human_guided))}; versus five-parent-window BCR ${esc(deltaText(aggregate.vs_parent_window))}.`;
  $("#deterministic-facts-rule-cards").innerHTML = comparison.integration.rules
    .map(
      (rule) =>
        `<article class="expansion-method"><div class="variant-label">Rule ${esc(rule.number)}</div><h3>${esc(rule.title)}</h3><p>${esc(rule.applied_as)}</p></article>`
    )
    .join("");
  $("#deterministic-facts-bcr-body").innerHTML = comparison.rows
    .map(
      (row) =>
        `<tr><td class="left"><strong>${esc(row.issue)}</strong><span class="issue-title">${esc(row.title)}</span></td><td class="num">${esc(row.terra_single_parent.steps)}</td><td class="num">${esc(row.human_guided.steps)}</td><td class="num">${esc(row.parent_window.steps)}</td><td class="num">${renderExperimentCell(row.deterministic_facts, row.terra_single_parent.steps)}</td><td class="left" title="${esc(row.deterministic_facts.note || row.deterministic_facts.run_label || "")}">${esc(row.deterministic_facts.state)}</td></tr>`
    )
    .join("");
  $("#deterministic-facts-bcr-note").innerHTML = `<strong>What changed:</strong> ${esc(comparison.integration.summary)} <strong>Configuration:</strong> ${esc(comparison.configuration)} <strong>Scope:</strong> ${esc(comparison.scope)}`;
}

function renderDeterministicFactsV16Window() {
  const v15 = state.data.deterministic_facts_bcr;
  const v16 = state.data.deterministic_facts_bcr_v16_window;
  if (!v15 || !v16) return;
  const aggregate = v16.aggregate;
  const delta = aggregate.vs_v15;
  const deltaText = `${delta.wins} / ${delta.ties} / ${delta.losses} W/T/L; ${
    delta.step_delta > 0 ? "+" : ""
  }${delta.step_delta} builds on n=${delta.compared}`;
  const v16ByIssue = new Map(v16.rows.map((row) => [row.issue, row.v16_window]));

  $("#deterministic-facts-bcr-summary").innerHTML += `<br /><strong>V16 artifact-complete, five-parent window:</strong> ${esc(aggregate.completed)} terminal zero-skip cases, ${esc(aggregate.total_steps)} runner builds, ${esc(aggregate.mean_steps)} mean. It is ${esc(aggregate.total_steps - v15.aggregate.total_steps)} build higher than V15 on this cohort (${esc(deltaText)}), so the added artifact lookup and parent window do not show a scoped-ten efficiency gain.`;
  $("#deterministic-facts-bcr-body").innerHTML = v15.rows
    .map((row) => {
      const v16Row = v16ByIssue.get(row.issue);
      return `<tr><td class="left"><strong>${esc(row.issue)}</strong><span class="issue-title">${esc(row.title)}</span></td><td class="num">${esc(row.terra_single_parent.steps)}</td><td class="num">${esc(row.human_guided.steps)}</td><td class="num">${esc(row.parent_window.steps)}</td><td class="num">${renderExperimentCell(row.deterministic_facts, row.terra_single_parent.steps)}</td><td class="num">${renderExperimentCell(v16Row, row.deterministic_facts.steps)}</td><td class="left" title="${esc(v16Row.note || v16Row.run_label || "")}">${esc(v16Row.state)}</td></tr>`;
    })
    .join("");
  $("#deterministic-facts-bcr-note").innerHTML += ` <strong>V16 configuration:</strong> ${esc(v16.configuration)} <strong>V16 scope:</strong> ${esc(v16.scope)}`;
}

function renderHeuristicFactorAblation() {
  const ablation = state.data.heuristic_factor_ablation;
  if (!ablation) return;
  $("#heuristic-factor-formula").textContent = ablation.baseline.formula;
  $("#heuristic-factor-ablation-cards").innerHTML = ablation.factors
    .map((factor) => {
      const aggregate = factor.aggregate;
      const delta = aggregate.step_delta > 0 ? `+${aggregate.step_delta}` : aggregate.step_delta;
      return `<article class="variant-summary-card"><h3>${esc(factor.label)}</h3><div class="variant-arm"><strong>${esc(aggregate.mean_steps ?? "-")}</strong><span>clean mean, n=${esc(aggregate.completed_clean)}</span><small>${esc(factor.disabled_component)} disabled. W/T/L ${esc(aggregate.wins)}/${esc(aggregate.ties)}/${esc(aggregate.losses)} vs baseline; ${esc(delta)} builds. ${esc(aggregate.non_clean)} skip-capped and ${esc(aggregate.interrupted_or_running)} running excluded.</small></div></article>`;
    })
    .join("");
  const factorsByIssue = new Map();
  for (const factor of ablation.factors) {
    for (const row of factor.rows) factorsByIssue.set(`${factor.key}:${row.issue}`, row);
  }
  const issues = ablation.factors[0]?.rows || [];
  $("#heuristic-factor-ablation-body").innerHTML = issues
    .map((base) => {
      const cells = ablation.factors.map(
        (factor) => factorsByIssue.get(`${factor.key}:${base.issue}`)?.result
      );
      return `<tr><td class="left"><strong>${esc(base.issue)}</strong><span class="issue-title">${esc(base.title)}</span></td><td class="num cell-reference">${esc(base.baseline.steps)}</td>${cells.map((result) => `<td class="num">${renderExperimentCell(result, base.baseline.steps)}</td>`).join("")}</tr>`;
    })
    .join("");
  $("#heuristic-factor-ablation-note").innerHTML = `<strong>Baseline contract:</strong> ${esc(ablation.baseline.scope)} A factor is only compared on rows with a clean terminal result; the current evidence is partial and must not be read as a full-cohort ranking.`;
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
  const patchProof = keywords.oracle_patch_proof;
  const failedPreflight = patchProof.failed_preflight.join(", ");
  const completedIssues = patchProof.completed_issues.join(", ") || "none";
  const runningIssues = patchProof.running_issues.join(", ") || "none";
  const unresolvedIssues = patchProof.unresolved_issues.join(", ") || "none";
  $("#oracle-patch-proof").innerHTML =
    "<strong>Patch-fingerprint proof diagnostic:</strong> " +
    esc(patchProof.definition) +
    " <strong>Current result:</strong> " +
    esc(patchProof.completed_count) +
    " accepted proofs (" +
    esc(completedIssues) +
    "), " +
    esc(patchProof.running_count) +
    " active run (" +
    esc(runningIssues) +
    "), and " +
    esc(patchProof.unresolved_count) +
    " retained 30-step all-skip non-results (" +
    esc(unresolvedIssues) +
    "). The retained zero-step preflight failures are " +
    esc(failedPreflight) +
    ". " +
    esc(patchProof.warning);
  $("#keyword-note").innerHTML =
    "<strong>Interpretation:</strong> " +
    esc(keywords.comparison_note) +
    " The known <code>pr49535</code> apply/reapply ambiguity is shown as an alternate boundary rather than counted as a runner failure. " +
    '<strong>Diagnostic only:</strong> the first-bad-derived column has <strong>' +
    esc(keywords.aggregate.oracle_first_bad.avg_steps) +
    "</strong> mean builds, <strong>" +
    esc(keywords.aggregate.oracle_first_bad.first_bad_matches) +
    "/10</strong> canonical first-bad boundaries, and is excluded from the comparison because " +
    esc(keywords.oracle_first_bad.warning) +
    " The answer-term posterior control has <strong>" +
    esc(keywords.aggregate.oracle_posterior_control.avg_steps) +
    "</strong> mean builds across <strong>" +
    esc(keywords.aggregate.oracle_posterior_control.count) +
    "/10</strong> completed rows; " +
    esc(keywords.oracle_posterior_control.warning);
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
        '<td class="num oracle-diagnostic" title="Diagnostic only; leaked terms with normal calibrated-posterior selection.">' +
        diagnosticCellText(row.oracle_posterior_control) +
        "</td>" +
        '<td class="num oracle-diagnostic" title="Diagnostic only; exact first-bad patch fingerprint plus runner-backed parent proof.">' +
        diagnosticCellText(row.oracle_patch_proof) +
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

function diagnosticCellText(result) {
  if (result.state === "completed") return esc(result.steps);
  if (result.state === "running") return "running (" + esc(result.steps) + ")";
  if (result.state === "stopped") return "stopped (" + esc(result.steps) + ")";
  if (result.state === "unresolved") return "unresolved (" + esc(result.skips) + " skips)";
  return "-";
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
