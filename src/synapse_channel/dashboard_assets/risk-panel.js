// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — bounded cockpit risk and safe-work guidance renderer
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (value) => {
    const node = document.createElement("div");
    node.textContent = value == null ? "" : String(value);
    return node.innerHTML;
  };
  const score = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const level = (value) => (["red", "amber", "green"].includes(value) ? value : "green");

  function postmortemHref(taskId) {
    const id = String(taskId || "").trim();
    return !id || id === "(unnamed)" ? "" : "/postmortem.json?task=" + encodeURIComponent(id);
  }

  function postmortemLink(taskId) {
    const href = postmortemHref(taskId);
    if (!href) return "";
    return (
      `<a class="risk__postmortem" href="${href}" target="_blank" rel="noopener noreferrer">` +
      "postmortem</a>"
    );
  }

  function signalRow(signal) {
    signal = signal && typeof signal === "object" ? signal : {};
    const severity = level(signal.level) === "red" ? "red" : "amber";
    const link = signal.category === "branch_conflict" ? "" : postmortemLink(signal.subject);
    return (
      `<div class="row risk__signal risk__signal--${severity}">` +
      '<div class="row__main"><div class="row__title">' +
      `${esc(signal.subject)} <span class="tag">${esc(signal.category)}</span>${link}</div>` +
      `<div class="row__sub">${esc(signal.detail)}</div></div></div>`
    );
  }

  function routeChips(candidates) {
    if (!candidates.length) return "";
    return candidates
      .filter((candidate) => candidate && typeof candidate === "object")
      .map(
        (candidate) => {
          const reasons = Array.isArray(candidate.reasons) ? candidate.reasons : [];
          return (
            `<span class="chip" title="${esc(reasons.join(", "))}">` +
            `${esc(candidate.agent)} · ${score(candidate.score)}</span>`
          );
        },
      )
      .join(" ");
  }

  function resourceChips(candidates) {
    if (!candidates.length) return "";
    return candidates
      .filter((candidate) => candidate && typeof candidate === "object")
      .map(
        (candidate) => {
          const reasons = Array.isArray(candidate.reasons) ? candidate.reasons : [];
          return (
            `<span class="chip chip--resource" title="${esc(reasons.join(", "))}">` +
            `${esc(candidate.resource_kind)}/${esc(candidate.resource_name)} · ` +
            `${esc(candidate.agent)} · cap ${score(candidate.capacity)}</span>`
          );
        },
      )
      .join(" ");
  }

  function hint(label, candidates, fallback, render) {
    const body = render(candidates);
    return (
      '<div class="risk__hint"><span class="risk__hint-label">' +
      `${label}</span><div class="risk__hint-body">` +
      (body || `<span class="risk__fallback">${esc(fallback || "no local match")}</span>`) +
      "</div></div>"
    );
  }

  function guidanceCard(task) {
    task = task && typeof task === "object" ? task : {};
    const routes = Array.isArray(task.route_candidates) ? task.route_candidates : [];
    const resources = Array.isArray(task.resource_bids) ? task.resource_bids : [];
    return (
      '<article class="risk__guidance-card"><div class="risk__guidance-head">' +
      `<strong>${esc(task.task_id)}</strong>${postmortemLink(task.task_id)}</div>` +
      hint("Route", routes, task.route_fallback, routeChips) +
      hint("Resources", resources, task.resource_fallback, resourceChips) +
      "</article>"
    );
  }

  function compactTask(task) {
    task = task && typeof task === "object" ? task : {};
    const href = postmortemHref(task.task_id);
    const title = [task.route_fallback, task.resource_fallback].filter(Boolean).join("; ");
    if (!href) return `<span class="chip">${esc(task.task_id)}</span>`;
    return (
      `<a class="chip risk__task-link" href="${href}" target="_blank" ` +
      `rel="noopener noreferrer" title="${esc(title)}">${esc(task.task_id)}</a>`
    );
  }

  function safeWork(risk) {
    const safe = Array.isArray(risk.safe_next_work) ? risk.safe_next_work : [];
    const guidance = risk.guidance || {};
    const tasks = Array.isArray(guidance.tasks) ? guidance.tasks : [];
    if (!safe.length && !tasks.length) return "";
    const richTasks = tasks.filter((task) => {
      const routes = task && Array.isArray(task.route_candidates) && task.route_candidates.length;
      const resources = task && Array.isArray(task.resource_bids) && task.resource_bids.length;
      return Boolean(routes || resources);
    });
    const compactTasks = tasks.filter((task) => !richTasks.includes(task));
    const cards = tasks.length
      ? richTasks.map(guidanceCard).join("") +
        (compactTasks.length
          ? `<div class="risk__compact">${compactTasks.map(compactTask).join(" ")}</div>`
          : "")
      : safe.map((taskId) => `<span class="chip">${esc(taskId)}</span>`).join(" ");
    const omitted = Math.max(0, Number(guidance.omitted_tasks) || 0);
    const omittedNote = omitted
      ? `<p class="risk__bounded">${omitted} additional ready task(s) omitted by the bounded view.</p>`
      : "";
    const trust = guidance.trust_boundary
      ? `<p class="risk__trust">${esc(guidance.trust_boundary)}</p>`
      : "";
    return (
      '<div class="risk__safe"><div class="risk__safe-head">Safe next work</div>' +
      `<div class="risk__guidance">${cards}</div>${omittedNote}${trust}</div>`
    );
  }

  function render(snapshot) {
    const risk = snapshot.risk || {};
    const verdictLevel = level(risk.level);
    const verdict = $("risk-verdict");
    const root = $("risk");
    if (!verdict || !root) return;
    verdict.textContent = verdictLevel.toUpperCase();
    verdict.className = "risk__verdict risk__verdict--" + verdictLevel;
    const signals = Array.isArray(risk.signals) ? risk.signals : [];
    const parts = [signals.map(signalRow).join(""), safeWork(risk)];
    if (!signals.length && !parts[1]) {
      parts.push('<div class="empty">All clear — no risks, no ready work waiting.</div>');
    }
    root.innerHTML = parts.join("");
  }

  window.SynapseRiskPanel = Object.freeze({ render });
})();
