# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Studio command centre (Stage B): the live operator view
"""The Studio command centre — the live operator view (Stage B).

A single self-contained page, in the instrument-panel language of the A0 design system,
that reads the `/studio.json` projection and answers at a glance: what is the fleet's
verdict, who is live, what is claimed, what is ready or blocked, and what is at risk. Its
signature instrument is the **Coordination Clock** — a radial gauge where every claim is a
segment around the dial, coloured by lease health (green fresh, amber ageing, red stale),
with conflicts struck through on the rim and a slow radar sweep; the dial centre carries
the verdict and the live claim count.

The page shell is hub-independent: it loads with no hub running and shows an offline state,
then progressively fills in live data as it polls `/studio.json`. It honours
``prefers-reduced-motion`` — the radar sweep is stilled and the dial is paired with a claims
table so the same information is legible without animation. Vanilla HTML, CSS custom
properties from `studio.css`, and dependency-free ES — no build step, no external request.
"""

from __future__ import annotations

from synapse_channel.dashboard_studio import STUDIO_REFERENCE_PATH
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH

STUDIO_COMMAND_PATH = "/studio/command"
"""HTTP path the live Studio command centre is served at."""

DEFAULT_POLL_SECONDS = 5
"""How often the command centre re-reads the live snapshot."""

_STYLE = """
:root { --syn-clock-r: 150px; }
.cc-grid { display:grid; grid-template-columns: minmax(320px, 1fr) minmax(280px, 0.9fr);
  gap: var(--syn-sp-5); align-items: start; }
.cc-bar { display:flex; align-items:center; gap:var(--syn-sp-4); flex-wrap:wrap;
  margin: var(--syn-sp-4) 0 var(--syn-sp-5); }
.cc-stat { display:flex; flex-direction:column; line-height:1.1; }
.cc-stat b { font-family:var(--syn-font-mono); font-size:var(--syn-fs-data);
  color:var(--syn-text); font-weight:600; }
.cc-stat span { font-size:var(--syn-fs-label); color:var(--syn-muted);
  text-transform:uppercase; letter-spacing:.08em; }
.cc-clock-wrap { display:flex; flex-direction:column; align-items:center;
  gap:var(--syn-sp-4); }
.cc-clock { width:min(360px, 78vw); height:auto; }
.cc-clock-face { fill:var(--syn-surface); stroke:var(--syn-hairline); stroke-width:1; }
.cc-tick { stroke:var(--syn-hairline); stroke-width:1; }
.cc-seg { fill:none; stroke-width:9; stroke-linecap:round; }
.cc-seg--ok { stroke:var(--syn-green); }
.cc-seg--warn { stroke:var(--syn-amber); }
.cc-seg--bad { stroke:var(--syn-red); }
.cc-conflict { stroke:var(--syn-red); stroke-width:2.5; }
.cc-centre-verdict { font-family:var(--syn-font-display); font-size:18px; font-weight:600;
  text-anchor:middle; text-transform:uppercase; letter-spacing:.04em; }
.cc-centre-count { font-family:var(--syn-font-mono); font-size:34px; font-weight:600;
  fill:var(--syn-text); text-anchor:middle; }
.cc-centre-label { font-size:var(--syn-fs-label); fill:var(--syn-muted); text-anchor:middle;
  text-transform:uppercase; letter-spacing:.1em; }
.cc-sweep { transform-origin:center; animation: cc-spin 6s linear infinite; }
.cc-offline { color:var(--syn-amber); font-family:var(--syn-font-mono);
  font-size:var(--syn-fs-data); }
.cc-empty { color:var(--syn-muted); font-size:var(--syn-fs-body); padding:var(--syn-sp-2) 0; }
.cc-table { width:100%; border-collapse:collapse; font-size:var(--syn-fs-data); }
.cc-table th { text-align:left; color:var(--syn-muted); font-size:var(--syn-fs-label);
  text-transform:uppercase; letter-spacing:.08em; font-weight:500;
  border-bottom:1px solid var(--syn-hairline); padding:var(--syn-sp-1) var(--syn-sp-2); }
.cc-table td { font-family:var(--syn-font-mono); padding:var(--syn-sp-1) var(--syn-sp-2);
  border-bottom:1px solid var(--syn-hairline); }
.cc-fallback { display:none; }
@keyframes cc-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .cc-sweep { animation:none; display:none; }
  .cc-fallback { display:block; }
}
"""

_SCRIPT_TEMPLATE = """
const SNAPSHOT = "__SNAPSHOT__";
const POLL_MS = __POLL_MS__;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const TONE = { green: "ok", amber: "warn", red: "bad", unknown: "warn" };
const TAU = Math.PI * 2;
const CX = 180, CY = 180, R = 132;

function polar(angle, radius) {
  return [CX + radius * Math.cos(angle - Math.PI / 2), CY + radius * Math.sin(angle - Math.PI / 2)];
}
function arc(a0, a1, radius) {
  const [x0, y0] = polar(a0, radius), [x1, y1] = polar(a1, radius);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ` +
    `${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
function el(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (text != null) node.textContent = text;
  return node;
}

function drawClock(data) {
  const svg = document.getElementById("cc-clock");
  while (svg.lastChild) svg.removeChild(svg.lastChild);
  svg.appendChild(el("circle", { class: "cc-clock-face", cx: CX, cy: CY, r: R + 14 }));
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * TAU;
    const [x0, y0] = polar(a, R + 14), [x1, y1] = polar(a, R + 8);
    svg.appendChild(el("line", { class: "cc-tick", x1: x0, y1: y0, x2: x1, y2: y1 }));
  }
  const active = (data.claims && data.claims.active) || [];
  const stale = (data.claims && data.claims.stale) || [];
  const segments = active.map(() => "ok").concat(stale.map(() => "bad"));
  const conflicts = (data.conflicts || []).length;
  const gap = 0.10;
  const span = segments.length ? (TAU / segments.length) : 0;
  segments.forEach((tone, i) => {
    const a0 = i * span + gap / 2, a1 = (i + 1) * span - gap / 2;
    svg.appendChild(el("path", { class: "cc-seg cc-seg--" + tone, d: arc(a0, a1, R) }));
    if (i < conflicts) {
      const am = (a0 + a1) / 2;
      const [mx, my] = polar(am, R);
      svg.appendChild(el("circle", { class: "cc-conflict", cx: mx, cy: my, r: 5, fill: "none" }));
    }
  });
  if (!reduceMotion) {
    const sweep = el("g", { class: "cc-sweep" });
    const [sx, sy] = polar(0, R + 6);
    sweep.appendChild(el("line", { x1: CX, y1: CY, x2: sx, y2: sy,
      stroke: "var(--syn-brand)", "stroke-width": 1.5, opacity: 0.5 }));
    svg.appendChild(sweep);
  }
  const verdict = data.verdict || "unknown";
  const count = String(active.length);
  svg.appendChild(el("text", { class: "cc-centre-count", x: CX, y: CY + 2 }, count));
  svg.appendChild(el("text", { class: "cc-centre-label", x: CX, y: CY + 22 }, "active claims"));
  const v = el("text", { class: "cc-centre-verdict", x: CX, y: CY - 28 }, verdict);
  v.setAttribute("fill", "var(--syn-" + (verdict === "green" ? "green" :
    verdict === "red" ? "red" : verdict === "amber" ? "amber" : "muted") + ")");
  svg.appendChild(v);
}

function list(id, items, render) {
  const host = document.getElementById(id);
  host.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "cc-empty";
    empty.textContent = "none";
    host.appendChild(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "syn-row";
    row.innerHTML = render(item);
    host.appendChild(row);
  }
}

function text(value) {
  return String(value == null ? "" : value).replace(/[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function setStat(id, value) { document.getElementById(id).textContent = value; }

function render(data) {
  document.getElementById("cc-offline").hidden = true;
  const h = data.headline || {};
  const verdict = data.verdict || "unknown";
  const pill = document.getElementById("cc-verdict");
  pill.className = "syn-verdict syn-verdict--" + (TONE[verdict] === "ok" ? "green" :
    TONE[verdict] === "bad" ? "red" : "amber");
  pill.textContent = verdict;
  setStat("cc-agents", h.agents_live || 0);
  setStat("cc-claims", (h.claims_active || 0) + " / " + (h.claims_stale || 0));
  setStat("cc-tasks", (h.tasks_ready || 0) + " / " + (h.tasks_blocked || 0));
  setStat("cc-conflicts", h.branch_conflicts || 0);
  setStat("cc-signals", h.risk_signals || 0);
  drawClock(data);
  const agents = data.agents || {};
  const live = (agents.live || []).map((a) => ({ name: a, state: "ok" }))
    .concat((agents.missing_waiters || []).map((a) => ({ name: a, state: "bad" })));
  list("cc-agents-list", live, (a) =>
    `<span class="syn-dot syn-dot--${a.state}"></span><span>${text(a.name)}</span>`);
  const claims = data.claims || {};
  const allClaims = (claims.active || []).map((c) => ({ c, tone: "ok" }))
    .concat((claims.stale || []).map((c) => ({ c, tone: "bad" })));
  list("cc-claims-list", allClaims, (x) =>
    `<span class="syn-dot syn-dot--${x.tone}"></span>` +
    `<span>${text(x.c.owner || x.c.task_id || "claim")}</span>` +
    `<span style="margin-left:auto;color:var(--syn-muted)">${text(x.c.scope || "")}</span>`);
  const tasks = data.tasks || {};
  const taskRows = (tasks.ready || []).map((t) => ({ id: t, state: "ok" }))
    .concat((tasks.blocked || []).map((t) => ({ id: t.task_id || t, state: "warn" })));
  list("cc-tasks-list", taskRows, (t) =>
    `<span class="syn-dot syn-dot--${t.state}"></span><span>${text(t.id)}</span>`);
  const risk = data.risk || {};
  list("cc-risk-list", risk.signals || [], (s) =>
    `<span class="syn-dot syn-dot--${TONE[s.level] || "warn"}"></span>` +
    `<span>${text(s.subject)}</span>` +
    `<span style="margin-left:auto;color:var(--syn-muted)">${text(s.detail)}</span>`);
  const tbody = document.getElementById("cc-fallback-body");
  tbody.replaceChildren();
  for (const x of allClaims) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${text(x.c.owner || x.c.task_id || "claim")}</td>` +
      `<td>${text(x.c.scope || "")}</td><td>${x.tone === "ok" ? "active" : "stale"}</td>`;
    tbody.appendChild(tr);
  }
}

async function poll() {
  try {
    const res = await fetch(SNAPSHOT, { cache: "no-store" });
    if (!res.ok) throw new Error("hub " + res.status);
    render(await res.json());
  } catch (err) {
    const banner = document.getElementById("cc-offline");
    banner.hidden = false;
    banner.textContent = "hub unavailable — " + err.message;
  } finally {
    setTimeout(poll, POLL_MS);
  }
}
poll();
"""


def _script(*, snapshot_path: str, poll_seconds: int) -> str:
    """Return the command-centre script with the snapshot path and poll interval bound."""
    return _SCRIPT_TEMPLATE.replace("__SNAPSHOT__", snapshot_path).replace(
        "__POLL_MS__", str(poll_seconds * 1000)
    )


def render_studio_command_html(*, poll_seconds: int = DEFAULT_POLL_SECONDS) -> str:
    """Render the live Studio command centre page.

    The shell renders without the hub and shows an offline banner until the first
    ``/studio.json`` poll succeeds. The Coordination Clock and panels then fill in live and
    refresh every ``poll_seconds``. ``prefers-reduced-motion`` stills the radar sweep and
    reveals the claims-table fallback.
    """
    script = _script(snapshot_path=STUDIO_SNAPSHOT_PATH, poll_seconds=poll_seconds)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SYNAPSE Studio — command centre</title>
  <link rel="stylesheet" href="/studio.css">
  <style>{_STYLE}</style>
</head>
<body class="syn" style="margin:0;padding:var(--syn-sp-5)">
  <nav class="syn-nav" style="margin-bottom:var(--syn-sp-4)">
    <a href="{STUDIO_COMMAND_PATH}" aria-current="page">command</a>
    <a href="{STUDIO_REFERENCE_PATH}">design</a>
    <a href="#">workflow</a><a href="#">trace</a><a href="#">policy</a><a href="#">routing</a>
  </nav>
  <header style="display:flex;align-items:baseline;gap:var(--syn-sp-3)">
    <h1 style="font-family:var(--syn-font-display);font-size:var(--syn-fs-title);margin:0">
      Coordination command centre</h1>
    <span id="cc-offline" class="cc-offline" hidden>connecting…</span>
  </header>
  <div class="cc-bar">
    <span id="cc-verdict" class="syn-verdict syn-verdict--amber">unknown</span>
    <div class="cc-stat"><b id="cc-agents">0</b><span>live agents</span></div>
    <div class="cc-stat"><b id="cc-claims">0 / 0</b><span>claims active/stale</span></div>
    <div class="cc-stat"><b id="cc-tasks">0 / 0</b><span>tasks ready/blocked</span></div>
    <div class="cc-stat"><b id="cc-conflicts">0</b><span>conflicts</span></div>
    <div class="cc-stat"><b id="cc-signals">0</b><span>risk signals</span></div>
  </div>
  <div class="cc-grid">
    <section class="syn-panel cc-clock-wrap">
      <div class="syn-label">coordination clock</div>
      <svg id="cc-clock" class="cc-clock" viewBox="0 0 360 360"
        role="img" aria-label="Coordination clock: claims by lease health"></svg>
      <div class="cc-fallback" style="width:100%">
        <table class="cc-table">
          <thead><tr><th>owner</th><th>scope</th><th>state</th></tr></thead>
          <tbody id="cc-fallback-body"></tbody>
        </table>
      </div>
    </section>
    <div class="syn-stack" style="display:grid;gap:var(--syn-sp-4)">
      <section class="syn-panel">
        <div class="syn-label">agents</div>
        <div id="cc-agents-list" style="margin-top:var(--syn-sp-2)"></div>
      </section>
      <section class="syn-panel">
        <div class="syn-label">claims</div>
        <div id="cc-claims-list" style="margin-top:var(--syn-sp-2)"></div>
      </section>
      <section class="syn-panel">
        <div class="syn-label">tasks</div>
        <div id="cc-tasks-list" style="margin-top:var(--syn-sp-2)"></div>
      </section>
      <section class="syn-panel">
        <div class="syn-label">risk signals</div>
        <div id="cc-risk-list" style="margin-top:var(--syn-sp-2)"></div>
      </section>
    </div>
  </div>
  <script>{script}</script>
</body>
</html>
"""
