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
  transitions per minute / risk signals, each with a redundant delta),
  liveness beacon + freshness stamp.
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
- **Deck** — the fleet roster (waker-missing presence honesty included) over
  the reliability EVIDENCE panel; the claims board (per-path detail, ticking
  lease countdowns, loud branch-conflict banner) over the signal log /
  causality inspector tabs; the task board (hub-verdicted dependency chips,
  done tasks listing the dependents they unblocked); the risk rail over the
  findings stream.

## Data contract

| Endpoint | Cadence | Serves |
|---|---|---|
| `/snapshot.json` | 2 s poll | fleet, claims, task graph, risk, board — the primary feed |
| `/reliability.json` | 15 s poll | the `synapse reliability --json` report (optional) |
| `/causality.json?seq=N\|task=ID&direction=causes\|effects` | on demand | a `synapse causality --json` trace (optional) |
| `/federation.json` | 20 s poll | federation posture (proposed contract; optional) |

Optional endpoints answer `404` on dashboards that do not serve them; the
corresponding panel states that plainly and activates the moment the surface
ships. Spine events are derived client-side by diffing consecutive snapshot
fetches — real transitions, quantised to the poll cadence; a hub-attested
event feed (true seq + ts) replaces the derivation when the server exposes one.

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
shaping — held to full line and branch coverage. React view components are
exercised by the build and by visual review.

## Serving the built cockpit

`npm run build` emits a self-contained static bundle in `dist/` with relative
asset paths (`base: "./"`), so it can be served from any path on the dashboard
origin without a rebuild. The intended production shape is the dashboard HTTP
server serving `dist/` next to its JSON endpoints: the SPA's relative fetches
then hit the real surfaces with no proxy, and the cockpit inherits the
dashboard's loopback-by-default bind and token posture.
