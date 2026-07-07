<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE·CHANNEL cockpit

A read-only, real-time operator cockpit for the coordination hub, built as a
static React + TypeScript SPA (Vite). It is a *client* — like `clients/go`,
`clients/js`, and `clients/vscode` — so the Python core stays an untouched,
no-telemetry neutral substrate. The cockpit renders what the hub recorded — it
never invents, smooths, or extrapolates state, and an empty surface is shown
as empty.

The design is a control-room instrument, not a SaaS analytics page: a lifted
graphite palette, Space Grotesk + JetBrains Mono (self-hosted woff2 — the
cockpit contacts no external origin and renders identically offline), a small
semantic signal set redundantly encoded (colour + glyph + position, never
colour alone), and one dominant live instrument — the **activity spine**, a
discrete event-driven oscilloscope of observed coordination transitions.

## Layout

- **HUD** — mark, the KPIs (agents online / claims held / observed
  transitions per minute / risk signals, each with a redundant delta —
  clicking one drills the signal log to its event kinds), the **focus
  lens** (name an identity and the claims board and task board narrow to
  its orbit, persistently, with a lens chip on every narrowed panel), the
  **density** and **theme** toggles (compact row rhythm; a WCAG-AA
  warm-paper light variant — stored choice wins, the OS preference decides
  otherwise), liveness beacon + freshness stamp.
- **Activity spine** — four lanes (presence, claims, task, risk) of discrete
  impulses at true timestamps against an amber now-edge, with a semantic
  colour legend. The risk lane is deliberately quiet; a deflection there is
  the alarm. The spine is a query surface: **drag** (or the arrow keys) to
  brush a time window that filters the signal log — brackets resize it,
  Escape clears it — and **hover** an impulse to name it. A log row that
  names a task is a hop straight into the causality inspector.
- **Federation row** — hub identity, imported peerings with lifecycle dots,
  and partition honesty: a contested namespace renders as a loud alert,
  because the hub refuses claims there until the split heals.
- **Time-travel bar** — arm it and scrub the durable log by sequence: the
  claims board, task board, and topology render the moment `/state-at.json`
  reconstructs (leases judged at that moment's own clock), amber-bordered
  and labelled while armed. The spine, log, and roster stay live — presence
  is not journalled, and the two truths never blend.
- **Toasts** — transitions said once: a task newly blocked, a new advisory
  conflict, a dead letter appearing or deepening, the risk rail crossing
  amber → red, a task newly done. Computed from live facts only; the first
  poll emits nothing; click or eight seconds dismisses.
- **Detail drawers** — click a roster row or a board card: everything the
  fleet knows about that name (claims with paths, the identity's unread
  dead-letter mailbox, dependency verdicts, history in the window), with
  actions that only steer other panels — filter the log, trace causality.
- **Deck** — the fleet roster (waker-missing presence honesty included) over
  the reliability EVIDENCE panel; the claims board (per-path detail, ticking
  lease countdowns, loud branch-conflict banner) over the inspector tabs; the
  task board (hub-verdicted dependency chips, done tasks listing the
  dependents they unblocked, a text + bucket-chip query with honest
  shown-of-total counts, and a Markdown **report** export that states its
  scope in the header); the risk rail — the hub's signals, then the
  hub-recorded **dead letters** (targets whose messages nobody reads), then
  client-side **repetition heuristics** (claim churn, repeating lease expiry)
  in their own clearly-labelled section — over the findings stream.

## Inspector tabs

- **Signal log** — the event stream with a query surface: text search, kind
  filters (the HUD's KPI tiles drill straight into them), newest/oldest
  order, a task-grouped compact view, pause-with-new-count, a raw-JSON
  expansion per hub event, and an **export** button that downloads exactly
  the shown window as a self-describing JSON document (provenance, query,
  window, and count stated inside the document). The query lives in the URL
  hash, so a filtered view is a shareable address. On hub provenance a
  **history** mode scrubs the whole durable log by sequence — any position
  renders its attested 200-event window in the same table, a **pin A /
  compare** pair diffs two windows (per-kind deltas, actors that appeared
  or went quiet, each window's own observed rate), and an **open** button
  loads a downloaded export back in as a **post-mortem** — same table,
  same filters, no hub required, with the document's provenance and stamp
  in a banner so replayed evidence never poses as live.
- **Topology** — a deterministic bipartite graph of who holds what: agents in
  one column, held tasks in the other, one line per claim (stale amber,
  conflict red), dashed ties for advisory conflicts, idle agents as a stated
  count. Below it, the **federation band**: this hub and its imported peer
  domains, edges coloured by the lifecycle state the durable store proves.
- **Metrics** — the log's pulse from the store-attested metrics feed: whole-
  log coverage, per-kind counts as plain horizontal bars, and the trailing
  windows the server measures against the log's own final timestamp.
- **Causality** — recorded causes/effects traces for a sequence or task, with
  per-hub clustering; log rows and task chips hop straight into it.

The layout is responsive: under 1100px the deck folds to two columns, under
720px to a single scrolling column re-ordered by triage priority (risk rail
first), with the spine kept at every width.

## Data contract

| Endpoint | Cadence | Serves |
|---|---|---|
| `/snapshot.json` | 2 s poll | fleet, claims, task graph, risk, board — the primary feed |
| `/reliability.json` | 15 s poll | the `synapse reliability --json` report (optional) |
| `/causality.json?seq=N\|task=ID&direction=causes\|effects` | on demand | a `synapse causality --json` trace (optional) |
| `/federation.json` | 20 s poll | federation posture (proposed contract; optional) |
| `/events.json?since=SEQ\|latest&limit=N` | 2 s incremental | the hub-attested event log (optional; also drives history mode) |
| `/metrics.json` | 30 s poll | store-attested log metrics: totals, per-kind, trailing windows (optional) |
| `/state-at.json?seq=N` | on scrub | claims + board reconstructed as of seq N by bounded replay (optional) |
| `/merkle-proof.json?seq=N` | on verify | RFC 6962 inclusion proof for one event; the root is recomputed in the browser (optional) |
| `/sessions.json` | 30 s poll | per-session cost/turn/token telemetry with task attribution (optional) |
| `/waits.json` | 15 s poll | tasks standing behind unmet dependencies — the pending decision queue (optional) |
| `/health-anomalies.json` | 30 s poll | the hub's causal-graph anomaly report: orphaned / dangling / stale (optional) |
| `POST /message` | on send | the ONE write: an operator chat relay — 404 unless the dashboard runs `--operator`; answers the `{action, status, detail, ok}` outcome document, and the palette states each status as a fact (`undelivered` never reads as "sent") |

Optional endpoints answer `404` on dashboards that do not serve them; the
corresponding panel states that plainly and activates the moment the surface
ships (`synapse dashboard --feeds-db PATH` serves the store-backed feeds).
The snapshot's `state.pending_relay_approvals` (hubs ≥ 0.98.5) lists relays
awaiting their second operator; the risk rail names each one, and a hub
without the field simply shows no section.
Spine and log events prefer the hub-attested tail (true seq + ts, provenance
labelled "hub event log"); while it is absent they fall back to diffing
consecutive snapshot fetches — real transitions, quantised to the poll
cadence, labelled "observed transitions". The two sources never mix.

## Develop

```bash
npm install
npm run dev        # http://127.0.0.1:8770 — proxies the JSON endpoints to :8765
```

Point the proxy at a running dashboard:

```bash
synapse dashboard --port 8765      # in the repo venv, against a live hub
SYNAPSE_DASHBOARD_ORIGIN=http://127.0.0.1:8765 npm run dev
```

The snapshot feed (`src/lib/snapshot.ts`) polls on a fixed cadence and honours
a freshness contract: once a snapshot is older than the stale threshold, the
beacon says so rather than presenting old numbers as current. With no hub
attached every panel waits honestly and the spine baseline stays flat.

## Build and test

```bash
npm run build      # strict typecheck (app + node configs), then vite build -> dist/
npm run typecheck  # strict type check only
npm test           # vitest unit suite
npm run coverage   # vitest with full-coverage thresholds on src/lib
npm run preview    # serves the PRODUCTION build on :8772 with the same proxy
```

The testable surface is the pure data logic — snapshot parsing, the freshness
contract, the polling stores, transition derivation, and every panel's data
shaping — held to full line and branch coverage — plus the behavioural
component layer: jsdom + testing-library drive the palette, drawers, boards,
rail sections, toasts, and views through their real interactions (the
canvas-drawing spine and the store-owning app shell stay on build + visual
review). Accessibility is scanned with axe-core against the live dashboard
in both themes at desktop and phone widths; the shipped surface measures
zero violations, and informational text never rides the faint decorative
tier or a whole-row opacity.

## Installing on a phone (PWA)

The built cockpit is an installable PWA. A phone on the tailnet opens
`http://<hub-tailnet-ip>:<dashboard-port>/cockpit/` (for a tokened dashboard
use the `?token=` query form — a plain navigation cannot send an
Authorization header), and installs it:

- **Android / Chromium**: the cockpit shows an "add to home screen" chip
  when the browser fires its install prompt; one tap hands over to the
  browser dialog.
- **iOS Safari**: there is no prompt event — use **Share → Add to Home
  Screen**.

The service worker (`public/sw.js`) caches the **app shell only** —
navigations are network-first with a cached fallback, hashed assets are
cache-first. The data feeds (`*.json`) are **never cached**: stale
coordination data presented as current is worse than a spinner, so an
unreachable hub surfaces through the HUD beacon's honest `stale HH:MM:SS`
state (amber-bordered at phone width) instead of silently served old JSON.
Under 640px the deck becomes a segmented single-column view (signals ·
claims · board · roster · reliability) with 44px touch targets; the spine
stays at every width and yields vertical panning to the page on touch.

Honest scope (Tier 1): read-mostly observation. No push, no background
wake — mobile OSes suspend the tab, so "the phone stays live on the bus"
is deliberately not promised here.

## Serving the built cockpit

`npm run build` emits a self-contained static bundle in `dist/` with relative
asset paths (`base: "./"`), so it can be served from any path on the dashboard
origin without a rebuild. The intended production shape is the dashboard HTTP
server serving `dist/` next to its JSON endpoints: the SPA's relative fetches
then hit the real surfaces with no proxy, and the cockpit inherits the
dashboard's loopback-by-default bind and token posture.
