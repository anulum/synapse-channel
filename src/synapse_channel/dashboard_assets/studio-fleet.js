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
  let current = null;
  let page = 0;
  const pageSize = 50;
  const unknown = (value) => value === null || value === undefined ? "unknown" : String(value);
  function node(tag, text) {
    const result = document.createElement(tag);
    result.textContent = text;
    return result;
  }
  function unavailable(message) {
    current = null;
    page = 0;
    status.textContent = message;
    root.replaceChildren();
  }
  function render(data) {
    if (!data || data.version !== 1 || data.advisory !== true ||
        typeof data.source_id !== "string" || !data.snapshot ||
        !Array.isArray(data.snapshot.peers) || !Array.isArray(data.snapshot.tasks)) {
      unavailable("incompatible mirror");
      return;
    }
    current = data;
    const snapshot = data.snapshot;
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
        const details = document.createElement("details");
        details.append(node("summary", data.source_id + " / " + task.task_id + " · " +
          task.status + (task.board_conflict ? " · unresolved conflict" : "")));
        details.addEventListener("toggle", () => {
          if (details.open && details.childElementCount === 1) {
            const evidence = node("pre", JSON.stringify(task, null, 2));
            evidence.style.whiteSpace = "pre-wrap";
            evidence.style.overflowWrap = "anywhere";
            details.append(evidence);
          }
        });
        root.append(details);
      }
    }
    if (total > pageSize) {
      const previous = node("button", "Previous mirror rows");
      const next = node("button", "Next mirror rows");
      previous.disabled = page === 0;
      next.disabled = end === total;
      previous.addEventListener("click", () => { page -= 1; render(current); });
      next.addEventListener("click", () => { page += 1; render(current); });
      root.append(previous, next);
    }
  }
  async function refresh() {
    const controller = new AbortController();
    const deadline = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/fleet-observed.json", {
        cache: "no-store",
        headers: window.SynapseStudioAccess ? window.SynapseStudioAccess.authHeaders() : {},
        signal: controller.signal,
      });
      if (!response.ok) {
        const states = { 404: "not configured", 401: "locked", 403: "locked",
          409: "incompatible mirror", 503: "mirror unavailable or invalid" };
        unavailable(states[response.status] || "mirror unavailable");
        return;
      }
      const data = await response.json();
      // Unchanged exports retain keyboard focus and expanded evidence.
      if (JSON.stringify(data) !== JSON.stringify(current)) render(data);
    } catch (_error) {
      unavailable("mirror unavailable");
    } finally {
      window.clearTimeout(deadline);
    }
  }
  async function loop() {
    await refresh();
    window.setTimeout(loop, 5000);
  }
  window.SynapseStudioFleet = Object.freeze({ render, refresh });
  void loop();
})();
