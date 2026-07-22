<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Cockpit user guide

The React cockpit is SYNAPSE CHANNEL's dense, read-mostly operator interface.
It combines live fleet state, the durable event tail, communication views,
claims, tasks, risk, audit evidence, and causality in one local-first page.
Operator controls are absent unless the dashboard is explicitly armed and the
browser principal has the matching capability.

The cockpit is experimental in the current pre-1.0 line. Pin the package
version when you depend on a specific layout or browser contract.

## Start the cockpit from a source checkout

Start a hub first; the [quick start](quickstart.md) covers the basic hub and
participant flow. In a second terminal, build the static cockpit:

```bash
cd clients/cockpit
npm ci
npm run build
cd ../..
```

Then start the loopback dashboard and point it at the build:

```bash
synapse dashboard \
  --port 8765 \
  --cockpit-dist clients/cockpit/dist
```

Open `http://127.0.0.1:8765/cockpit/`.

Add the durable hub database when you want the event log, reliability,
metrics, audit, causality, sessions, waits, and time-travel feeds:

```bash
synapse dashboard \
  --port 8765 \
  --cockpit-dist clients/cockpit/dist \
  --feeds-db ./hub.db
```

The dashboard remains useful without `--feeds-db`. Optional panels say that
their feed is not configured; they do not turn missing evidence into a zero.
Use `--feeds-db-key-file` when the selected hub store is encrypted.

## Unlock a protected dashboard

A loopback read-only dashboard can run without a browser bearer. A supplied
`--dashboard-token`, a browser-principal policy, or a non-loopback bind protects
the feeds. The cockpit shell then opens an unlock screen.

Paste the bearer into that screen. The cockpit keeps it in the current tab's
session storage. It does not put the bearer in the URL, local storage, logs,
cached application shell, or rendered page.

For a shared workstation or several operator identities, prefer an owner-only
`--dashboard-access-file`. It maps separate token files to viewer, operator,
and admin presentation roles. See
[Dashboard browser principals](cli.md#dashboard-browser-principals) for the
policy schema and file-mode requirements.

!!! warning

    Do not paste a bearer into a URL, chat message, command argument recorded
    by shell history, screenshot, or documentation. Prefer token files with
    owner-only permissions.

## Read the HUD

The top strip answers whether the page is current before it shows detail:

- **live / stale** and the timestamp state when data last arrived;
- headline fleet counters with deltas;
- the current browser role and capability posture;
- an identity focus lens that narrows claims and tasks;
- compact/cozy density and dark/light theme controls;
- the command palette button.

Click a headline counter to filter the signal log to its underlying event
kinds. Clear the focus lens before concluding that a board or claim list is
complete.

Press `Ctrl+K` or `Command+K` to open the command palette. Viewer principals
receive navigation and inspection commands only. Operator and admin principals
receive the governed write commands currently enabled by the server.

## Use the activity spine

The spine plots discrete retained events in presence, claims, task, and risk
lanes. It is not a smoothed activity estimate.

- Drag across the spine to select a time window.
- Use the keyboard on the spine to adjust the window.
- Clear the window from the inspector when you want the full retained tail.
- Hover or focus an event mark to inspect its identity and timestamp.

The selected window filters the signal log and fleet communication views.

Above those two views, the event-coverage strip states the source, retained
count, 250-event client cap, and available sequence/time range. “Retained
window at cap” means only that the client window reached its bound; it does not
claim that the server log is complete or that a specific event was dropped.

## Follow shared selection and filters

The context bar directly below the HUD shows the active investigation state.
It can contain a shared selection, the identity focus lens, and a brushed time
window. Remove one chip to widen only that dimension, or use **clear all** to
return to the unfiltered retained view in one step.

The shared selection supports agents, projects, tasks, directed routes, and
durable hub-event sequences. It follows the same entity across the activity
spine, fleet views, signal log, roster, claims, task board, and risk rail where
that surface has direct retained evidence. A panel that cannot prove a match
does not manufacture one. Event-sequence selection is therefore available only
for hub-attested events, not client-derived display rows.

Safe selection state is encoded in bounded query fields: `agent`, `project`,
`task`, a route pair in `from` and `to`, or `event`. Copying the address,
reloading it, or using browser Back and Forward restores that selection. The
focus lens and time-window chips remain distinct filters, so clearing a shared
selection does not silently discard either filter.

## Inspect fleet communication

Open the **fleet** inspector tab and choose one of three views:

- **web** groups identities by project and shows directed traffic;
- **matrix** uses sender rows and recipient columns for exact route volume;
- **projects** summarises inbound traffic, outbound traffic, identities, and
  claims by project.

The web emphasises a small set of priority routes for quick selection. The
matrix remains the precise long-tail view. Select a node, project, link, or
matrix cell to open its evidence detail.

The active inspector panel, fleet mode, and shared selection live in bounded
URL query fields. Copy the address to reopen that workspace; browser Back and
Forward restore earlier panel and selection changes. Signal-log filters remain
in the URL hash, so workspace navigation and log queries can be shared together.

A selected link shows retained pairwise messages with delivery outcomes.
When the dashboard is armed and the principal has message capability, the
detail can send attributed operator commentary about a selected message.
Commentary is not recipient acknowledgement or task-ownership evidence, and it
does not alter transport acknowledgement state.

## Triage the attention queue

Open **attention** for one live, read-only queue of branch conflicts, unread
dead letters, failed or deferred routes, stale claims, missing waiters, blocked
tasks, pending relay approvals, and coordination waits. Filter the queue by
critical or warning evidence, then open the exact agent, task, or route named by
a row.

The order is deterministic, not an opaque score: critical rows precede warning
rows, evidence kinds have a documented fixed rank, older available timestamps
come first within a kind, and stable ids break ties. A row navigates to evidence;
it does not acknowledge a peer, grant authority, approve a relay, or mutate hub
state.

## Work with claims and tasks

- Click a roster identity to open its claims, paths, unread dead-letter facts,
  and recent events.
- Click a task card to inspect its owner, dependencies, readiness, claims, and
  history.
- Use drawer actions to filter the log or trace task causality.
- Treat branch conflicts as advisory evidence derived from declared claims;
  the dashboard does not run Git to refine them.
- Check board truncation text before treating visible task rows as the whole
  plan.

The risk rail separates server-provided risk, dead letters, waits, pending
approvals, and client-side repetition heuristics. A heuristic is labelled as
such and is not an authorisation decision.

## Filter, group, and export the signal log

The signal log supports text search, event-kind filters, newest/oldest order,
task grouping, pause, raw event detail, and export of the visible evidence.
Its filter query lives in the URL hash, so you can copy a filtered-log address.
The exported JSON states its query, provenance, time window, and count.

When the durable event feed is available, history mode can open retained
sequence windows and compare two pinned windows. An imported cockpit export is
labelled as a post-mortem so it cannot pose as live data.

## Use time travel safely

The time-travel bar reconstructs claims and the task board at a selected durable
sequence. While it is armed:

- claims, tasks, and topology are historical;
- the activity spine, signal log, and roster remain live;
- presence remains live because presence is not reconstructed from the journal;
- a prominent label states the selected sequence and time.

Use **back to now** before acting on current fleet state.

## Read metrics, audit, and causality

- **metrics** reports event-log counts and bounded trailing windows. It is not
  the hub process-metrics endpoint.
- **audit** keeps universal receipts and operator actions as separate feeds so
  a requested action cannot masquerade as a completed receipt.
- **causality** traces recorded causes or effects from an event sequence or task.
  A task hop from the log opens this panel with the task already selected.
- **topology** joins identities and held tasks and adds imported federation
  posture when configured.

Each optional feed reports connecting, live, stale last-good, absent, or failed
state independently.

## Send governed actions

Add `--operator` to arm browser writes. For several browser principals, use the
owner-only access policy described in the CLI reference:

```bash
synapse dashboard \
  --port 8765 \
  --cockpit-dist clients/cockpit/dist \
  --feeds-db ./hub.db \
  --operator \
  --dashboard-access-file ~/.config/synapse/dashboard-access.json
```

Use the compatibility `--dashboard-token` and `--operator-name` pair only when
a single principal is sufficient and you can supply the token without recording
the secret in shell history.

Armed routes can send a message, declare a task, and update a task. Every
request still passes browser capability checks, HTTP validation, rate limits,
the hub ACL, task validation, and durable audit. Read the returned outcome:
`delivered`, `undelivered`, `accepted`, `denied`, `rejected`, `rate-limited`,
and `unreachable` have different meanings. HTTP success alone is not proof of
delivery.

## Phone and installed-app use

Under 640 pixels the deck becomes a segmented view: signals, claims, board,
roster, and reliability. The activity spine stays visible. The built cockpit
also ships an installable PWA shell.

The service worker caches only token-free application assets. Authorised
requests and JSON feeds bypass the cache, so an offline phone shows stale or
unavailable state instead of old fleet data presented as current.

Mobile operating systems suspend background tabs. The installed cockpit is an
operator view, not a permanent waiter or push receiver.

## Troubleshooting

### The cockpit says “Waiting for the hub”

Check the hub URI passed to `synapse dashboard`, then run:

```bash
synapse health
synapse who
```

The static shell can load while the dashboard cannot reach the hub.

### Optional panels say the feed is not served

Restart the dashboard with `--feeds-db` pointing at the hub event store. Use
the matching `--feeds-db-key-file` for an encrypted store.

### The unlock screen returns after loading

The bearer was refused or the principal policy changed. Obtain the current
bearer from the local operator through a secure channel. A `401` deliberately
clears the live presentation and session credential.

### Write controls are absent

Both conditions must hold: the dashboard runs with `--operator`, and the
authenticated principal has the exact write capability. A role label alone
does not grant authority.

### The page is stale

Keep the stale state visible while checking the dashboard process, hub health,
and network path. Do not infer current fleet safety from last-good rows.

## Security boundaries

- Loopback is the default bind.
- Non-loopback exposure requires `--allow-non-loopback`, deliberate host
  admission, authentication, and trusted network controls.
- The cockpit performs no direct database write.
- Browser mutations go through the dashboard relay and then the hub's normal
  validation, ACL, and audit path.
- Observed peer state is advisory and cannot grant local claim authority.
- The page loads no remote font, script, style, catalogue, or telemetry service.

For deployment details, read [Deployment](deployment.md),
[Identity and ACL](identity-and-acl.md), and
[At-rest encryption](at-rest-encryption.md).
