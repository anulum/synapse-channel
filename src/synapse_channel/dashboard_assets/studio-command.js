// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — Studio command-centre snapshot client and instruments
"use strict";

(function () {
  const configNode = document.getElementById("syn-studio-config");
  let config = {};
  if (configNode) {
    try {
      const parsed = JSON.parse(configNode.textContent || "");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) config = parsed;
    } catch (_error) {
      config = {};
    }
  }
  const snapshotUrl = String(config.snapshotUrl || "/studio.json");
  const pollMs = Math.max(1000, Number(config.pollMs) || 5000);
  const reduceMotion = typeof matchMedia === "function" &&
    matchMedia("(prefers-reduced-motion: reduce)").matches;
  const tones = Object.freeze({ green: "ok", amber: "warn", red: "bad", unknown: "warn" });
  const tau = Math.PI * 2;
  const centreX = 180;
  const centreY = 180;
  const radius = 132;

  function svgNode(tag, attributes, value) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const key in attributes) item.setAttribute(key, attributes[key]);
    if (value != null) item.textContent = String(value);
    return item;
  }

  function htmlNode(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value == null ? "" : value);
    return item;
  }

  function polar(angle, distance) {
    return [
      centreX + distance * Math.cos(angle - Math.PI / 2),
      centreY + distance * Math.sin(angle - Math.PI / 2),
    ];
  }

  function arc(start, end, distance) {
    const [x0, y0] = polar(start, distance);
    const [x1, y1] = polar(end, distance);
    const large = end - start > Math.PI ? 1 : 0;
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${distance} ${distance} 0 ${large} 1 ` +
      `${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  function drawClock(data) {
    const svg = document.getElementById("cc-clock");
    if (!svg) return;
    svg.replaceChildren();
    svg.appendChild(svgNode("circle", { class: "cc-clock-face", cx: centreX, cy: centreY, r: radius + 14 }));
    for (let index = 0; index < 12; index += 1) {
      const angle = (index / 12) * tau;
      const [x0, y0] = polar(angle, radius + 14);
      const [x1, y1] = polar(angle, radius + 8);
      svg.appendChild(svgNode("line", { class: "cc-tick", x1: x0, y1: y0, x2: x1, y2: y1 }));
    }
    const claims = data.claims || {};
    const active = Array.isArray(claims.active) ? claims.active : [];
    const stale = Array.isArray(claims.stale) ? claims.stale : [];
    const segments = active.map(() => "ok").concat(stale.map(() => "bad"));
    const conflicts = Array.isArray(data.conflicts) ? data.conflicts.length : 0;
    const gap = 0.1;
    const span = segments.length ? tau / segments.length : 0;
    segments.forEach((tone, index) => {
      const start = index * span + gap / 2;
      const end = (index + 1) * span - gap / 2;
      svg.appendChild(svgNode("path", { class: "cc-seg cc-seg--" + tone, d: arc(start, end, radius) }));
      if (index < conflicts) {
        const [x, y] = polar((start + end) / 2, radius);
        svg.appendChild(svgNode("circle", { class: "cc-conflict", cx: x, cy: y, r: 5, fill: "none" }));
      }
    });
    if (!reduceMotion) {
      const sweep = svgNode("g", { class: "cc-sweep" });
      const [x, y] = polar(0, radius + 6);
      sweep.appendChild(svgNode("line", {
        x1: centreX, y1: centreY, x2: x, y2: y,
        stroke: "var(--syn-brand)", "stroke-width": 1.5, opacity: 0.5,
      }));
      svg.appendChild(sweep);
    }
    const verdict = String(data.verdict || "unknown");
    svg.append(
      svgNode("text", { class: "cc-centre-count", x: centreX, y: centreY + 2 }, active.length),
      svgNode("text", { class: "cc-centre-label", x: centreX, y: centreY + 22 }, "active claims"),
    );
    const verdictNode = svgNode(
      "text", { class: "cc-centre-verdict", x: centreX, y: centreY - 28 }, verdict,
    );
    const color = verdict === "green" || verdict === "red" || verdict === "amber" ? verdict : "muted";
    verdictNode.setAttribute("fill", "var(--syn-" + color + ")");
    svg.appendChild(verdictNode);
  }

  function dot(tone) {
    return htmlNode("span", "syn-dot syn-dot--" + tone);
  }

  function value(valueText, className = "") {
    return htmlNode("span", className, valueText);
  }

  function list(id, items, renderRow) {
    const host = document.getElementById(id);
    if (!host) return;
    host.replaceChildren();
    if (!items.length) {
      host.appendChild(htmlNode("div", "cc-empty", "none"));
      return;
    }
    for (const item of items) {
      const row = htmlNode("div", "syn-row");
      row.append(...renderRow(item));
      host.appendChild(row);
    }
  }

  function setStat(id, stat) {
    const item = document.getElementById(id);
    if (item) item.textContent = String(stat);
  }

  function render(data) {
    const offline = document.getElementById("cc-offline");
    if (offline) offline.hidden = true;
    const connection = document.getElementById("cc-connection");
    if (connection) {
      connection.textContent = "connected";
      connection.className = "syn-verdict syn-verdict--green";
    }
    const hub = data.hub || {};
    const headline = data.headline || {};
    const verdict = String(data.verdict || "unknown");
    setStat("cc-hub", hub.id || "unknown");
    setStat("cc-version", hub.version || "unknown");
    const verdictPill = document.getElementById("cc-verdict");
    if (verdictPill) {
      verdictPill.className = "syn-verdict syn-verdict--" +
        (tones[verdict] === "ok" ? "green" : tones[verdict] === "bad" ? "red" : "amber");
      verdictPill.textContent = verdict;
    }
    setStat("cc-agents", headline.agents_live || 0);
    setStat("cc-claims", (headline.claims_active || 0) + " / " + (headline.claims_stale || 0));
    setStat("cc-tasks", (headline.tasks_ready || 0) + " / " + (headline.tasks_blocked || 0));
    setStat("cc-conflicts", headline.branch_conflicts || 0);
    setStat("cc-signals", headline.risk_signals || 0);
    const posture = data.security_posture || {};
    setStat("cc-posture", posture.level || "unknown");
    const observed = data.observed_fleet || {};
    const peersTotal = headline.peers_total || observed.peers_total || 0;
    const peersReachable = headline.peers_reachable || observed.peers_reachable || 0;
    setStat("cc-peers", peersTotal ? peersReachable + " / " + peersTotal : "—");
    drawClock(data);

    const agents = data.agents || {};
    const live = (agents.live || []).map((name) => ({ name, tone: "ok" }))
      .concat((agents.missing_waiters || []).map((name) => ({ name, tone: "bad" })));
    list("cc-agents-list", live, (agent) => [dot(agent.tone), value(agent.name)]);

    const claims = data.claims || {};
    const allClaims = (claims.active || []).map((claim) => ({ claim, tone: "ok" }))
      .concat((claims.stale || []).map((claim) => ({ claim, tone: "bad" })));
    list("cc-claims-list", allClaims, (entry) => [
      dot(entry.tone),
      value(entry.claim.owner || entry.claim.task_id || "claim"),
      value(entry.claim.scope || (entry.claim.paths || []).join(", "), "cc-push-right"),
    ]);

    const tasks = data.tasks || {};
    const taskRows = (tasks.ready || []).map((taskId) => ({ taskId, tone: "ok" }))
      .concat((tasks.blocked || []).map((task) => ({ taskId: task.task_id || task, tone: "warn" })));
    list("cc-tasks-list", taskRows, (task) => [dot(task.tone), value(task.taskId)]);
    if (window.SynapseBoardColumns) {
      window.SynapseBoardColumns.render(document.getElementById("cc-board-columns"), tasks.columns || {});
    }

    const risk = data.risk || {};
    list("cc-risk-list", risk.signals || [], (signal) => [
      dot(tones[signal.level] || "warn"), value(signal.subject), value(signal.detail, "cc-push-right"),
    ]);
    list("cc-posture-list", posture.rows || [], (row) => [
      dot(tones[row.level] || "warn"), value(row.surface), value(row.state, "cc-push-right"),
    ]);
    const peerRows = observed.peers && observed.peers.length
      ? observed.peers
      : [{ level: observed.level || "amber", hub_id: "local-only", detail: observed.detail || "no observed peers" }];
    list("cc-peers-list", peerRows, (row) => [
      dot(tones[row.level] || "warn"), value(row.hub_id), value(row.detail, "cc-push-right"),
    ]);

    const body = document.getElementById("cc-fallback-body");
    if (body) {
      body.replaceChildren();
      for (const entry of allClaims) {
        const row = htmlNode("tr");
        row.append(
          htmlNode("td", "", entry.claim.owner || entry.claim.task_id || "claim"),
          htmlNode("td", "", entry.claim.scope || (entry.claim.paths || []).join(", ")),
          htmlNode("td", "", entry.tone === "ok" ? "active" : "stale"),
        );
        body.appendChild(row);
      }
    }
  }

  async function poll() {
    try {
      const response = await fetch(snapshotUrl, { cache: "no-store" });
      if (!response.ok) throw new Error("hub " + response.status);
      render(await response.json());
    } catch (error) {
      const banner = document.getElementById("cc-offline");
      if (banner) {
        banner.hidden = false;
        banner.textContent = "hub unavailable — " + String(error && error.message || error);
      }
      const connection = document.getElementById("cc-connection");
      if (connection) {
        connection.textContent = "offline";
        connection.className = "syn-verdict syn-verdict--amber";
      }
    } finally {
      setTimeout(poll, pollMs);
    }
  }

  function start() {
    void poll();
    if (window.SynapseStudioFeeds) window.SynapseStudioFeeds.start(config);
  }

  window.SynapseStudioCommand = Object.freeze({ render, start });
  start();
})();
