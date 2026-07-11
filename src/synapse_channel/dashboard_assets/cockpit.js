// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — fleet nerve-center cockpit (live read-only client)
"use strict";

(function () {
  const cfg = window.__SYN_COCKPIT__ || {
    refreshSeconds: 5,
    snapshotUrl: "/snapshot.json",
    receiptsUrl: "/receipts.json",
  };
  const REFRESH_MS = Math.max(1, Number(cfg.refreshSeconds) || 5) * 1000;
  const SNAPSHOT_URL = cfg.snapshotUrl || "/snapshot.json";
  const RECEIPTS_URL = cfg.receiptsUrl || "/receipts.json";
  const TOKEN_KEY = "synapse-dashboard-token";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => {
    const node = document.createElement("div");
    node.textContent = value == null ? "" : String(value);
    return node.innerHTML;
  };
  const project = (name) => {
    const text = String(name || "");
    const slash = text.indexOf("/");
    return slash === -1 ? text : text.slice(0, slash);
  };
  const baseName = (name) => String(name || "").replace(/-rx$/, "");

  function authHeaders() {
    const token = sessionStorage.getItem(TOKEN_KEY);
    return token ? { Authorization: "Bearer " + token } : {};
  }

  async function fetchSnapshot() {
    const res = await fetch(SNAPSHOT_URL, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (res.status === 401) {
      throw Object.assign(new Error("unauthorized"), { unauthorized: true });
    }
    if (!res.ok) {
      throw new Error("hub snapshot unavailable (" + res.status + ")");
    }
    return res.json();
  }

  async function fetchReceipts() {
    const res = await fetch(RECEIPTS_URL, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (res.status === 401) {
      throw Object.assign(new Error("unauthorized"), { unauthorized: true });
    }
    if (res.status === 404 || res.status === 503) {
      return null;
    }
    if (!res.ok) {
      throw new Error("receipt feed unavailable (" + res.status + ")");
    }
    return res.json();
  }

  // ---------- HUD ----------
  function renderHud(snap) {
    const online = (snap.online_agents || []).filter((a) => !String(a).endsWith("-rx"));
    const fleet = snap.fleet || {};
    const claims = fleet.claims || {};
    const conflicts = (fleet.branch_conflicts || []).length;
    const missing = ((fleet.agents || {}).missing_waiters || []).length;
    const ready = ((fleet.task_graph || {}).ready || []).length;
    const vitals = [
      { n: online.length, l: "agents", cls: "vital--good" },
      { n: claims.active || 0, l: "claims", cls: "vital--warn" },
      { n: claims.stale || 0, l: "stale", cls: (claims.stale ? "vital--bad" : "") },
      { n: conflicts, l: "conflicts", cls: (conflicts ? "vital--bad" : "") },
      { n: missing, l: "no waiter", cls: (missing ? "vital--warn" : "") },
      { n: ready, l: "ready", cls: "" },
    ];
    $("vitals").innerHTML = vitals
      .map(
        (v) =>
          `<div class="vital ${v.cls}"><span class="vital__n">${esc(v.n)}</span>` +
          `<span class="vital__l">${esc(v.l)}</span></div>`
      )
      .join("");
  }

  // ---------- fleet graph (signature) ----------
  function clusters(snap) {
    const fleet = snap.fleet || {};
    const agents = fleet.agents || {};
    const live = new Set((agents.live || []).map(baseName));
    const missing = new Set(agents.missing_waiters || []);
    const groups = {};
    for (const raw of snap.online_agents || []) {
      const name = String(raw);
      const proj = project(name) || "(none)";
      const g = (groups[proj] = groups[proj] || { project: proj, live: 0, waiters: 0, missing: 0 });
      if (name.endsWith("-rx")) g.waiters += 1;
      else g.live += 1;
      if (live.has(baseName(name)) && missing.has(name)) g.missing += 1;
    }
    for (const m of agents.missing_waiters || []) {
      const proj = project(m) || "(none)";
      const g = (groups[proj] = groups[proj] || { project: proj, live: 0, waiters: 0, missing: 0 });
      g.missing += 1;
    }
    return Object.values(groups).sort((a, b) => b.live + b.waiters - (a.live + a.waiters));
  }

  function clusterClass(g) {
    if (g.missing > 0) return "cluster--bad";
    if (g.live === 0) return "cluster--warn";
    return "cluster--good";
  }

  function renderFleet(snap) {
    const groups = clusters(snap);
    const claimCount = ((snap.fleet || {}).claims || {}).active || 0;
    const svg = $("fleet-svg");
    const w = svg.clientWidth || 900;
    const h = 460;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 70;
    const parts = [];
    parts.push(`<circle class="ring" cx="${cx}" cy="${cy}" r="${radius}"></circle>`);
    parts.push(`<circle class="pulse-ring" cx="${cx}" cy="${cy}" r="26"></circle>`);

    const n = groups.length || 1;
    const placed = groups.map((g, i) => {
      const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
      return { g, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
    });
    for (const p of placed) {
      const isClaim = claimCount > 0 && p.g.live > 0;
      parts.push(
        `<path class="edge ${isClaim ? "edge--claim" : ""}" d="M ${cx} ${cy} Q ${(cx + p.x) / 2} ${
          (cy + p.y) / 2 - 24
        } ${p.x} ${p.y}"></path>`
      );
    }
    for (const p of placed) {
      const total = p.g.live + p.g.waiters;
      const r = Math.max(22, Math.min(40, 18 + total * 2));
      parts.push(
        `<g class="cluster ${clusterClass(p.g)}">` +
          `<circle class="cluster__disc" cx="${p.x}" cy="${p.y}" r="${r}"></circle>` +
          `<text class="cluster__name" x="${p.x}" y="${p.y - 2}">${esc(short(p.g.project))}</text>` +
          `<text class="cluster__meta" x="${p.x}" y="${p.y + 11}">${p.g.live}● ${p.g.waiters}○${
            p.g.missing ? " ⚠" + p.g.missing : ""
          }</text>` +
          `</g>`
      );
    }
    parts.push(
      `<circle class="node-core" cx="${cx}" cy="${cy}" r="22"></circle>` +
        `<text class="cluster__name" x="${cx}" y="${cy - 1}" style="fill:#0e1116;font-weight:700">HUB</text>` +
        `<text class="cluster__meta" x="${cx}" y="${cy + 11}" style="fill:#0e1116">${
          claimCount
        } claims</text>`
    );
    svg.innerHTML = parts.join("");
    const fleet = $("fleet");
    fleet.classList.remove("is-fresh");
    void fleet.offsetWidth; // restart the pulse animation
    fleet.classList.add("is-fresh");
  }

  function short(text) {
    const value = String(text);
    return value.length > 16 ? value.slice(0, 15) + "…" : value;
  }

  // ---------- panels ----------
  function tickClass(status) {
    const s = String(status || "").toLowerCase();
    if (s.includes("done") || s.includes("approved")) return "row__tick--good";
    if (s.includes("block") || s.includes("fail") || s.includes("reject") || s.includes("stale"))
      return "row__tick--bad";
    return "row__tick--warn";
  }

  function renderFeed(snap) {
    const progress = ((snap.board || {}).progress || []).slice(-40).reverse();
    if (!progress.length) {
      $("feed").innerHTML = '<div class="empty">No progress notes yet.</div>';
      return;
    }
    $("feed").innerHTML = progress
      .map((note) => {
        const kind = note.kind || "note";
        return (
          `<div class="row"><div class="row__tick ${tickClass(kind)}"></div>` +
          `<div class="row__main"><div class="row__sub">` +
          `<span class="stream__who">${esc(baseName(note.author))}</span> ` +
          `<span class="stream__kind">[${esc(kind)}]</span> ` +
          `${esc(note.task_id || "")}</div>` +
          `<div class="row__title" style="font-weight:400">${esc(note.text || "")}</div>` +
          `</div></div>`
        );
      })
      .join("");
  }

  function fallbackReceipts(snap) {
    return ((snap.fleet || {}).receipts || []).map((r) => ({
      kind: "claim",
      subject: r.task_id || "",
      actor: r.author || "",
      status: r.epistemic_status || r.confidence || "recorded",
      summary: r.text || "",
    }));
  }

  function renderReceipts(receiptDoc, snap) {
    const source = receiptDoc && Array.isArray(receiptDoc.receipts)
      ? receiptDoc.receipts
      : fallbackReceipts(snap);
    const receipts = source.slice(-12).reverse();
    $("receipts-count").textContent = receipts.length;
    if (!receipts.length) {
      $("receipts").innerHTML = '<div class="empty">No receipts.</div>';
      return;
    }
    $("receipts").innerHTML = receipts
      .map((r) => {
        const status = r.status || r.epistemic_status || r.confidence || "recorded";
        const title = r.subject || r.task_id || r.author || r.kind || "receipt";
        const kind = r.kind ? " [" + r.kind + "]" : "";
        return (
          `<div class="row"><div class="row__tick ${tickClass(status)}"></div>` +
          `<div class="row__main"><div class="row__title">${esc(title)}${esc(kind)}` +
          `</div><div class="row__sub">${esc(status)} ${esc((r.summary || r.text || "").slice(0, 120))}</div></div></div>`
        );
      })
      .join("");
  }

  const LANES = [
    { key: "ready", title: "Ready", match: (s) => s === "ready" || s === "open" },
    { key: "progress", title: "Claimed", match: (s) => s.includes("progress") || s === "claimed" },
    { key: "blocked", title: "Blocked", match: (s) => s.includes("block") },
    { key: "done", title: "Done", match: (s) => s === "done" || s === "cancelled" },
  ];

  function cardClass(status) {
    const s = String(status).toLowerCase();
    if (s === "done") return "card--good";
    if (s.includes("block")) return "card--bad";
    if (s.includes("progress") || s === "claimed") return "card--warn";
    return "";
  }

  function renderBoard(snap) {
    const tasks = ((snap.board || {}).tasks || []).filter((t) => t && t.task_id);
    $("board-count").textContent = tasks.length;
    if (!tasks.length) {
      $("lanes").innerHTML = '<div class="empty">No board tasks.</div>';
      return;
    }
    const buckets = LANES.map(() => []);
    let other = [];
    for (const task of tasks) {
      const status = String(task.status || "").toLowerCase();
      const index = LANES.findIndex((lane) => lane.match(status));
      if (index === -1) other.push(task);
      else buckets[index].push(task);
    }
    buckets[1] = buckets[1].concat(other); // unknown statuses sit with active work
    $("lanes").innerHTML = LANES.map((lane, i) => {
      const cards = buckets[i]
        .map(
          (t) =>
            `<div class="card ${cardClass(t.status)}"><div class="card__id">${esc(t.task_id)}</div>` +
            `<div class="card__title">${esc(t.title || "")}</div></div>`
        )
        .join("");
      return (
        `<div class="lane"><div class="lane__head"><span>${esc(lane.title)}</span>` +
        `<span>${buckets[i].length}</span></div>${cards || '<div class="empty">—</div>'}</div>`
      );
    }).join("");
  }

  function renderClaims(snap) {
    const claims = ((snap.fleet || {}).claims || {});
    const active = claims.active_claims || [];
    const stale = new Set((claims.stale_claims || []).map((c) => c.task_id));
    $("claims-count").textContent = active.length;
    if (!active.length) {
      $("claims").innerHTML = '<div class="empty">No active claims.</div>';
      return;
    }
    $("claims").innerHTML = active
      .map((c) => {
        const paths = (c.paths || []).join(", ");
        const bad = stale.has(c.task_id);
        return (
          `<div class="row"><div class="row__tick ${bad ? "row__tick--bad" : "row__tick--good"}"></div>` +
          `<div class="row__main"><div class="row__title">${esc(c.task_id)} ` +
          `<span class="tag ${bad ? "tag--stale" : ""}">${esc(baseName(c.owner))}</span></div>` +
          `<div class="row__sub">${esc(paths || "—")}</div></div></div>`
        );
      })
      .join("");
  }

  function renderManifest(snap) {
    const cards = snap.manifest || [];
    $("manifest-count").textContent = cards.length;
    if (!cards.length) {
      $("manifest").innerHTML = '<div class="empty">No advertised capabilities.</div>';
      return;
    }
    $("manifest").innerHTML = cards
      .map((c) => {
        const classes = (c.task_classes || []).join(", ");
        return (
          `<div class="row"><div class="row__main"><div class="row__title">${esc(c.agent || "")}</div>` +
          `<div class="row__sub">${esc(classes)}</div></div></div>`
        );
      })
      .join("");
  }

  function renderRisk(snap) {
    const panel = window.SynapseRiskPanel;
    if (panel && typeof panel.render === "function") panel.render(snap);
  }

  // ---------- beacon / loop ----------
  function setBeacon(state, label) {
    const beacon = $("beacon");
    beacon.className = "beacon beacon--" + state;
    $("beacon-label").textContent = label;
  }

  function stamp(snap) {
    const at = (snap.fleet || {}).generated_at || (snap.state || {}).generated_at;
    if (!at) return "";
    const d = new Date(at * 1000);
    return d.toLocaleTimeString();
  }

  async function tick() {
    try {
      const snap = await fetchSnapshot();
      const receiptDoc = await fetchReceipts();
      renderHud(snap);
      renderFleet(snap);
      renderRisk(snap);
      renderFeed(snap);
      renderReceipts(receiptDoc, snap);
      renderBoard(snap);
      renderClaims(snap);
      renderManifest(snap);
      $("veil").classList.remove("is-open");
      setBeacon("live", "live · " + stamp(snap));
    } catch (err) {
      if (err && err.unauthorized) {
        setBeacon("down", "auth required");
        $("veil").classList.add("is-open");
      } else {
        setBeacon("down", "reconnecting");
      }
    }
  }

  function loop() {
    if (!document.hidden) tick();
    window.setTimeout(loop, REFRESH_MS);
  }

  function wireAuth() {
    const submit = $("veil-submit");
    if (!submit) return;
    submit.addEventListener("click", () => {
      const value = $("veil-input").value.trim();
      if (value) sessionStorage.setItem(TOKEN_KEY, value);
      $("veil").classList.remove("is-open");
      tick();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireAuth();
    setBeacon("stale", "connecting");
    tick();
    window.setTimeout(loop, REFRESH_MS);
  });
})();
