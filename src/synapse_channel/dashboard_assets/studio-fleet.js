// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — optional read-only Fleet mirror panel
"use strict";

(function () {
  const status = document.getElementById("cc-fleet-status");
  const root = document.getElementById("cc-fleet-mirror");
  if (!status || !root) return;
  status.tabIndex = -1;
  let current = null;
  let activeRequest = null;
  let page = 0;
  const opened = new Set();
  const pageSize = 50;
  const unknown = (value) => value === null || value === undefined ? "unknown" : String(value);
  function node(tag, text) {
    const result = document.createElement(tag);
    result.textContent = text;
    return result;
  }
  function unavailable(message) {
    const restoreFocus = root.contains(document.activeElement);
    current = null;
    page = 0;
    opened.clear();
    status.textContent = message;
    root.replaceChildren();
    if (restoreFocus) status.focus({ preventScroll: true });
  }
  function render(data, navigation = null) {
    if (!data || data.version !== 1 || data.advisory !== true ||
        typeof data.source_id !== "string" || !data.snapshot ||
        !Array.isArray(data.snapshot.peers) || !Array.isArray(data.snapshot.tasks)) {
      unavailable("incompatible mirror");
      return;
    }
    const active = root.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = active && active.dataset.mirrorTask;
    const focusNavigation = navigation || (active && active.dataset.mirrorNavigation);
    const sameSource = current && current.source_id === data.source_id;
    for (const details of root.querySelectorAll("details")) {
      if (details.open) opened.add(details.dataset.mirrorTask);
      else opened.delete(details.dataset.mirrorTask);
    }
    if (!sameSource) {
      page = 0;
      opened.clear();
    }
    const snapshot = data.snapshot;
    const taskKeys = snapshot.tasks.map(task => JSON.stringify([data.source_id, task.task_id]));
    const retained = new Set(taskKeys);
    for (const key of opened) if (!retained.has(key)) opened.delete(key);
    if (!navigation && sameSource && focusKey && retained.has(focusKey)) {
      page = Math.floor((snapshot.peers.length + taskKeys.indexOf(focusKey)) / pageSize);
    }
    current = data;
    const total = snapshot.peers.length + snapshot.tasks.length;
    page = Math.min(page, Math.max(0, Math.ceil(total / pageSize) - 1));
    status.textContent = data.source_id + " · advisory · exported at " + unknown(data.exported_at) +
      " · rendered at " + unknown(snapshot.generated_at) + " (not peer freshness)";
    root.replaceChildren();
    const start = page * pageSize;
    const end = Math.min(total, start + pageSize);
    root.append(node("p", total ? "Rows " + (start + 1) + "–" + end + " of " + total :
      "Complete export contains no mirrored rows"));
    for (let index = start; index < end; index += 1) {
      if (index < snapshot.peers.length) {
        const peer = snapshot.peers[index];
        const row = node("p", data.source_id + " / " + peer.peer_id +
          " · cursor " + peer.cursor + " · events " + peer.events +
          " · status at " + unknown(peer.status_written_at) +
          " · last success " + unknown(peer.last_success_at) +
          " · failures " + unknown(peer.consecutive_failures) +
          " · caught up " + unknown(peer.caught_up) +
          " · drain limit " + unknown(peer.budget_exhausted_reason));
        row.style.overflowWrap = "anywhere";
        root.append(row);
      } else {
        const task = snapshot.tasks[index - snapshot.peers.length];
        const key = taskKeys[index - snapshot.peers.length];
        const details = document.createElement("details");
        details.dataset.mirrorTask = key;
        const summary = node("summary", data.source_id + " / " + task.task_id + " · " +
          task.status + (task.board_conflict ? " · unresolved conflict" : ""));
        summary.dataset.mirrorTask = key;
        details.append(summary);
        function showEvidence() {
          if (details.open && details.childElementCount === 1) {
            const evidence = node("pre", JSON.stringify(task, null, 2));
            evidence.style.whiteSpace = "pre-wrap";
            evidence.style.overflowWrap = "anywhere";
            details.append(evidence);
          }
        }
        details.addEventListener("toggle", showEvidence);
        details.open = opened.has(key);
        showEvidence();
        root.append(details);
      }
    }
    if (total > pageSize) {
      const previous = node("button", "Previous mirror rows");
      const next = node("button", "Next mirror rows");
      previous.disabled = page === 0;
      next.disabled = end === total;
      previous.dataset.mirrorNavigation = "previous";
      next.dataset.mirrorNavigation = "next";
      previous.addEventListener("click", () => { page -= 1; render(current, "previous"); });
      next.addEventListener("click", () => { page += 1; render(current, "next"); });
      root.append(previous, next);
    }
    if (active) {
      const summaries = Array.from(root.querySelectorAll("summary"));
      const buttons = Array.from(root.querySelectorAll("button")).filter(button => !button.disabled);
      const target = sameSource && focusKey
        ? summaries.find(summary => summary.dataset.mirrorTask === focusKey)
        : sameSource && focusNavigation
          ? buttons.find(button => button.dataset.mirrorNavigation === focusNavigation) || buttons[0]
          : null;
      (target || status).focus({ preventScroll: true });
    }
  }
  async function refresh() {
    const controller = new AbortController();
    const previous = activeRequest;
    activeRequest = controller;
    if (previous) previous.abort();
    const deadline = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/fleet-observed.json", {
        cache: "no-store",
        headers: window.SynapseStudioAccess ? window.SynapseStudioAccess.authHeaders() : {},
        signal: controller.signal,
      });
      if (activeRequest !== controller || controller.signal.aborted) return;
      if (!response.ok) {
        const states = { 404: "not configured", 401: "locked", 403: "locked",
          409: "incompatible mirror", 503: "mirror unavailable or invalid" };
        unavailable(states[response.status] || "mirror unavailable");
        return;
      }
      const data = await response.json();
      if (activeRequest !== controller || controller.signal.aborted) return;
      // Unchanged exports retain keyboard focus and expanded evidence.
      if (JSON.stringify(data) !== JSON.stringify(current)) render(data);
    } catch (_error) {
      if (activeRequest === controller) unavailable("mirror unavailable");
    } finally {
      window.clearTimeout(deadline);
      if (activeRequest === controller) activeRequest = null;
    }
  }
  async function loop() {
    await refresh();
    window.setTimeout(loop, 5000);
  }
  window.SynapseStudioFleet = Object.freeze({ render, refresh });
  void loop();
})();
