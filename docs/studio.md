<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Studio

The Synapse dashboard is growing from a read-only cockpit into an operator **Studio**:
a control plane that answers, in one glance, *what is happening, what is at risk, what
is safe to do next, and who should do it.* It is built the way the rest of Synapse is —
local-first, dependency-light, offline-safe — and rides on the read model the dashboard
already serves.

This page tracks what has shipped. The full direction lives in the internal design
plan; what is public here is real and running.

## Design system

The Studio speaks an **instrument-panel** language rather than a marketing dashboard:
a deep ink base, one indigo-violet brand hue, and red/amber/green reserved
*exclusively* for verdicts so risk reads at a glance. It is a small set of CSS custom
properties and components in `dashboard_assets/studio.css`, served by the dashboard at
`/studio.css` — no build step, no external request, and it renders correctly with the
hub offline.

- **Palette** — ink-navy surfaces, an indigo-violet accent for links, focus, and the
  live pulse, and the three verdict colours used nowhere else.
- **Type** — three roles: a display face for labels and numerals, a body face for
  prose, and a monospace face that carries the data (task ids, leases, paths, hashes).
  A system-font stack ships today; self-hosted faces drop in behind the same variables.
- **Components** — panels and cards (elevation by tint and a hairline, never shadows),
  status dots, the verdict pill, mono data rows, the navigation rail, and an indigo
  focus ring. Motion is restrained and stilled under `prefers-reduced-motion`.

## The reference page

Run the dashboard and open `/studio` to see the design system exercised on one page —
every verdict tone, every status-dot state, the panels, cards, data rows, and
numerals. It renders no live data, so it works even when the hub is down, and it is the
visual reference the live command centre is assembled from.

```bash
synapse dashboard --port 8765
# then open http://127.0.0.1:8765/studio
```

## The Studio snapshot — `/studio.json`

The command centre reads one JSON contract, served live at `/studio.json`: a single risk
**verdict** (the reserved red/amber/green signal), a row of headline counters, and the
agents, claims, tasks, conflicts, and risk behind them. It is a pure projection of the
same read model the dashboard already exposes — `studio_snapshot.py` reshaping
`/snapshot.json` — so Studio adds no new hub call, only a curated command-centre view.
Every headline count is derived from the list it summarises, so the instrument and the
rows beneath it can never disagree. A partial payload from a degraded hub still projects
to a renderable snapshot rather than failing.

```bash
synapse dashboard --uri ws://127.0.0.1:8765
# then GET http://127.0.0.1:8765/studio.json
```

## What comes next

The command centre wires the live `/studio.json` read model into these components — the
risk verdict, the fleet, the safe-next-work queue, and the signature Coordination Clock
over leases and claims — followed by the workflow, trace, policy, routing, and channel
surfaces. The core read-only Studio stays free; an organisation-level workbench (saved
views, exports, multi-project, managed) is planned as a separate layer.
