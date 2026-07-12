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
  Space Grotesk, Inter, and JetBrains Mono now ship as pinned, same-origin variable
  WOFF2 assets behind those role variables. Latin and Latin Extended cover product
  copy and Central-European names in 217,608 bytes; other scripts retain the system
  fallbacks. The browser makes no font request to an external origin.
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
# front door (command centre): http://127.0.0.1:8765/
# design reference:           http://127.0.0.1:8765/studio
# classic hub HTML:           http://127.0.0.1:8765/classic
```

## The Studio snapshot — `/studio.json`

The command centre reads one JSON contract, served live at `/studio.json`: a single risk
**verdict** (the reserved red/amber/green signal), a row of headline counters, the
agents, claims, task columns, conflicts, risk behind them, and a compact security-posture
section. It is a pure projection of the same read model the dashboard already exposes —
`studio_snapshot.py` reshaping `/snapshot.json` — so Studio adds no new hub call, only a
curated command-centre view. Every headline count is derived from the list it summarises,
so the instrument and the rows beneath it can never disagree. A partial payload from a
degraded hub still projects to a renderable snapshot rather than failing.

```bash
synapse dashboard --uri ws://127.0.0.1:8765
# then GET http://127.0.0.1:8765/studio.json
```

## The command centre — `/studio/command`

The live operator view, served at `/studio/command`, reads `/studio.json` and answers at a
glance what the fleet is doing and what is at risk. Its signature instrument is the
**Coordination Clock** — a radial gauge where every claim is a segment around the dial,
coloured by lease health (green fresh, amber ageing, red stale), with conflicts marked on
the rim and a slow radar sweep; the dial centre carries the verdict and the live claim
count. Around it sit the verdict pill, the headline counters, a board-column view, and
the agents, claims, tasks, and risk panels.

### Shared-plan columns

The board view joins the blackboard plan with live claim leases by task id. It preserves
the hub's exact values rather than inventing presentation-only lifecycle states:

- blackboard `open`, `in_progress`, `blocked`, `done`, and `cancelled`;
- claim `claimed`, `working`, `input_required`, `done`, and `failed`.

The visible columns are Open, Claimed, Working, Input required, Blocked, Closed, and a
fail-visible Other column for future additive values. Cards retain both raw statuses,
their source, readiness, unmet dependencies, owner, paths, and lease freshness. A live
claim without a blackboard row appears as an explicitly labelled ad-hoc claim; the view
does not fabricate a declaration. A bounded blackboard snapshot carries its truncation
and total counts so the UI cannot present a partial page as the whole plan. Its complete
ready-id set remains authoritative: dependencies whose task rows were omitted are shown
as unknown when necessary, never fabricated as proven blockers of a ready task.

The projection is read-only. It does not claim, assign, route, reserve, approve, release,
or update work, and the free Studio page renders no mutation controls. Evidence and
approval notes remain progress-ledger facts; they do not become a fictional "review"
status. `board-columns.js` creates DOM text nodes for task data, and the command shell,
feed polling, board renderer, and styles ship as separate focused package assets.

The page shell is hub-independent: it loads with no hub running, shows an offline state,
and fills in live as it polls. The persistent NavRail keeps the command view, reference
view, fleet, LiveFeed, and security posture one click away; the HeaderBar shows the live
hub id, version, verdict, and connection state from `/studio.json`. It honours
`prefers-reduced-motion` — the sweep stills and a claims table pairs the dial so the same
information is legible without animation. Vanilla HTML, the `studio.css` tokens, and
dependency-free ES — no build step, no external request.

### Browser roles and capabilities

Both Studio surfaces read the authenticated, server-authored
`/dashboard-access.json` descriptor. The dependency-free command centre remains
read-only and shows a neutral `role · principal` pill; malformed, unreachable,
or unauthenticated access fails visibly as `access unavailable`. It adds no
empty operator/admin controls.

The built React cockpit probes access before exposing its live shell. It uses
the descriptor's capability booleans—not the display role—to build the command
catalogue. Viewer DOM, search results, and keyboard selection contain no
message/task write entry. Operator and admin currently see exactly message,
task declare, and task update; admin has a distinct badge but no fabricated
admin action. A capability downgrade closes the palette/form, moves focus to
the command trigger, and announces that write controls were removed. The role
is never persisted as authority.

Conditional rendering grants nothing. The browser bearer is independently
resolved on every POST, then the dashboard applies the exact route capability,
JSON/media/size/rate gates, principal-specific relay identity, and the existing
hub ACL and durable audit. See [Dashboard browser principals](cli.md#dashboard-browser-principals)
for the private token-file policy and status-code contract.

The security-posture panel sits beside the Coordination Clock and summarises five shipped
safety surfaces: sandbox grants, ACL/role visibility, the dashboard exposure guard,
signed federation / peer observation, and receipt evidence. Rows are evidence-bound:
missing role bindings, peers, or receipts are shown as amber "not currently evidenced"
instead of being treated as configured. The panel is read-only; server-side ACL,
dashboard bind, federation, and sandbox enforcement remain in their existing modules.

The **observed peers (advisory)** panel projects dashboard `--observed-peer` rows (and
any FLEET-style advisory mirrors folded into the same snapshot field) into
`/studio.json` under `observed_fleet`: per-peer reachability, lag, clock skew, and
observed claim-owner counts. Unreachable peers are red; lagging peers amber; no
configured peers is an honest amber "not configured" state. Peer data never grants
local claim authority.

The LiveFeed panel tails `/events.json?since=SEQ&limit=N`, the durable event-store feed
served when the dashboard starts with `--feeds-db`. It starts at `since=latest`, then
polls forward by `next_cursor`, so it shows new recorded events without walking a large
history. If `--feeds-db` is absent, the panel says the event feed is not configured rather
than implying a quiet log.

```bash
synapse dashboard --port 8765 --feeds-db ./hub.db
# open http://127.0.0.1:8765/  (same shell as /studio/command)
```

The dashboard CLI prints the Studio URL first. Root `/` and `/studio/command`
serve the same command-centre shell; `/classic` keeps the pre-Studio hub HTML.

## What comes next

Beyond the command centre come the workflow, trace, policy, routing, and channel surfaces,
each reading the same kind of projection. The core read-only Studio stays free; an
organisation-level workbench (saved views, exports, multi-project, managed) is planned as a
separate layer.
