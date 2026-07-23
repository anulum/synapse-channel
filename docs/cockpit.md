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

Whole-log reliability and causal-health reports are intentionally progressive:
the exact event tail and lightweight operational feeds start first, then the
two expensive reports run one at a time and refresh on a two-minute cadence.
The dashboard server also shares concurrent identical report builds across
browser tabs and isolates whole-log projection CPU from interactive requests.
State-at responses use a short, bounded cache by exact query, so repeated tabs
do not reconstruct the same moment independently. These controls change only
when evidence is computed, never the evidence or its durable sequence.

The causal-health response also carries a **local fleet health** summary for the
risk rail. It reports counts for retained-log claim contention, leases expired
by the log's final timestamp, receipt-proven dead letters and later recoveries,
and escalation events. Messages without a requested receipt appear only once
their target crosses the durable escalation threshold; the report does not
invent per-message evidence. It is deliberately content-minimized: it contains
no agent identity, task id, path, message, note, or raw payload. Its sequence
range and retained-event count state the evidence boundary. It is computed on
demand from the selected local store, is not persisted as a second report, and
performs no network or telemetry operation.

Current dashboards carry the four high-frequency cockpit channels over one
authenticated, versioned NDJSON connection at `/live.ndjson`: the fleet
snapshot, durable events, universal receipts, and governed operator actions.
Lower-frequency reports retain their deliberately slower independent cadence.
An older dashboard that answers 404 activates the legacy polling path. A
transient disconnect first receives bounded reconnect backoff; only an outage
longer than six seconds starts polling fallback. When the stream returns, its
snapshot and durable cursor bootstrap replace the fallback without discarding
the last known presentation. Unchanged snapshots travel as small freshness
heartbeats instead of retransmitting the complete fleet document.

Each HTTP stream still obtains its hub snapshot through the configured
dashboard participant identity. Reconnects and access changes can overlap at
the HTTP layer, so the server serializes that short-lived hub fetch per
dashboard instance. It keeps one stable roster identity and respects the hub's
single-owner name invariant without dropping either browser stream.

## Cockpit client architecture

The browser shell has explicit responsibility boundaries. `App.tsx` performs
only the top-level access, workspace, feed, and panel composition. Stateful
behaviour that used to share that root component has its own lifecycle owner:

- `useCockpitReplay` owns delayed state-at requests, stale-response rejection,
  replay slots, and live/history switching;
- `useCockpitPreferences` owns the focus lens, density, theme, and the
  shareable signal-log hash, including their existing storage boundaries;
- `useCockpitOverlays` owns command and guide entry, setup and detail overlays,
  trace requests, focus restoration, and fail-closed capability downgrades;
- `useCockpitToasts` compares only consecutive live fleet facts, deduplicates
  transition notices, and clears timers when the shell locks or unmounts;
- `useCockpitViewModel` derives the immutable panel projections. Replay can
  replace claims and tasks, but the roster and other non-journalled evidence
  remain live;
- `useCockpitFeeds` owns only the authenticated multiplexed transport, its
  bounded compatibility polling path, and the live event-source lifecycle;
- `useCockpitAuxiliaryFeeds` owns the slower reliability, federation, metrics,
  sessions, waits, and anomaly report stores. Its idempotent start signal and
  bounded timers keep whole-log reports behind exact history without allowing
  a hung history endpoint to starve them;
- `cockpitLiveFrames` projects untrusted channel envelopes into snapshot,
  event, receipt, and operator-action states without React side effects, while
  `cockpitKpis` owns headline values, deltas, and the local freshness stamp.
- `SignalLog` composes only the log controls and workspace strips;
  `useSignalLogWorkspace` owns live-view freezing, history scrub/pin/compare,
  and validated post-mortem file lifecycle; `SignalLogRows` owns the bounded
  flat and compact evidence views, raw payload expansion, exact navigation,
  and client-side Merkle-verification verdicts.
- `ActivitySpine` composes the accessible canvas peer, lane labels, legend,
  and tooltip; `useActivitySpine` owns live-source, animation, theme/resize,
  hover, pointer-drag, and keyboard-brush lifecycle; `activitySpineCanvas`
  performs the pure 75-second pixel projection and retained-event cutoff.
- `InspectorTabs` owns only accessible tab chrome; `useInspectorNavigation`
  owns roving focus and task-to-causality trace hops; `InspectorPanel` owns
  lazy panel routing, per-panel evidence defaults, and selection fallbacks.
- `communicationEvidence` normalises retained chat and receipt evidence;
  `communicationModel` derives metadata-only nodes, routes, and project traffic;
  `conversationDetail` admits body text only for an explicitly selected pair;
  `communicationLayout` owns deterministic web geometry and matrix bounds. The
  stable `communications` facade keeps existing consumers independent of these
  internal owners.
- `snapshotParser` owns defensive narrowing and safe defaults for untrusted
  fleet documents; `snapshotStore` owns authenticated polling, last-good-state
  retention, freshness transitions, subscriptions, timers, and abort cleanup.
  The stable `snapshot` facade preserves the existing feed and parser imports.

Each owner has a dedicated behavioural hook or projection test. The wired
`App` and feed tests still exercise authentication, real endpoint adapters,
URL restoration, multiplexed history, polling recovery, replay, incident
evidence, command entry, and capability removal through public surfaces. This
separation is architectural: it must not change exact wire values,
browser-principal enforcement, startup ordering, or the evidence boundary
between retained live state and reconstruction.

The inspector also defines the cockpit's JavaScript delivery boundary. The
default signal log and attention queue ship in the entry chunk because they are
the immediate operator surfaces. Fleet, topology, metrics, audit, incident, and
causality are separate feature chunks requested only when their tab becomes
active. A localised live status occupies the existing tabpanel while a deferred
chunk loads; the surrounding panel error boundary remains responsible for a
failed import. This is behavioural code splitting, not a warning override: the
production entry is 468.48 kB minified / 137.26 kB gzip, down from 530.11 kB /
151.60 kB gzip, and no configured chunk-size threshold was raised.

Tab focus and panel data routing are separate contracts. Arrow keys, Home, and
End update the controlled tab and move focus without importing panel code;
task trace hops update the shared selection and causality prefill. The panel
router then adapts honest absent/connecting defaults and preserves the same
lazy production chunks.

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
- the current transport posture: **stream**, **reconnecting**, **gap
  detected**, or **poll fallback**;
- headline fleet counters with deltas;
- the current browser role and capability posture;
- an identity focus lens that narrows claims and tasks;
- compact/cozy density and dark/light theme controls;
- the command palette, contextual guide, local setup assistant, and
  interface-language controls.

Click a headline counter to filter the signal log to its underlying event
kinds. Clear the focus lens before concluding that a board or claim list is
complete.

Press `Ctrl+K` or `Command+K` to open the command palette. Viewer principals
receive navigation and inspection commands only. Operator and admin principals
receive the governed write commands currently enabled by the server.

## Change language and use the in-product guide

The cockpit ships English, Slovak, German, Spanish, and French catalogues in the application
bundle. It does not download translations at runtime. The initial language
comes from the first valid source in this order:

1. the explicit `lang` URL parameter;
2. the operator's saved `cockpit-locale` preference;
3. the first supported browser language;
4. English.

Choose **EN**, **SK**, **DE**, **ES**, or **FR** in the HUD. The choice updates `lang` without
removing the active panel, selection, replay, or communication filters, so a
shared URL reopens the same workspace in the chosen language. The HTML `lang`
attribute changes with it for assistive technology. A missing future catalogue
entry falls back to English, and catalogue-parity tests stop incomplete
releases.

Press `?` or choose **guide** in the HUD to open the local guide. Its first
topic explains the currently active inspector panel; the remaining topics
cover evidence limits, governed actions, shortcuts, accessibility, and common
access or freshness failures. Search runs only over text already loaded in the
browser. It sends no query or usage telemetry. `Escape`, the close control, or
the shaded veil closes the guide, and focus returns to its HUD trigger.

Translation applies only to explanatory interface copy. SYNAPSE identities,
project and task IDs, event kinds, wire fields, capability names, hashes,
sequence numbers, CLI flags, paths, URLs, and protocol outcome tokens remain
literal evidence. For example, `accepted`, `delivered`, `undelivered`,
`rejected`, `rate-limited`, `unreachable`, `stream`, `poll fallback`, and `gap
detected` retain their exact spelling in Slovak, German, Spanish, and French modes.

The same boundary covers the complete fleet communication workbench. Its five
view controls, filters, graph and table descriptions, selected-entity details,
evidence-chain explanations, and operator-response controls follow the chosen
locale. Identity strings, project names, message bodies, sequence numbers,
delivery and semantic-response statuses, and server-authored outcome detail are
not translated. A locale change therefore improves navigation and explanation
without rewriting the evidence being inspected or the values sent back to the
hub.

## Prepare a local setup without browser-side mutation

Choose **setup** in the HUD, or open it from the in-product guide. The first F8
assistant is deliberately read-only. It does not execute a shell, start or
change a service, create a token, inspect the host filesystem, or enable a
network bind.

The assistant has four steps:

1. **Preflight** classifies only evidence the loaded browser can prove as
   installed, configured, absent, or unverifiable. It does not infer package or
   service-manager state from a working page.
2. **Profile** fixes the hub and dashboard host to `127.0.0.1`, validates two
   different non-privileged ports, and optionally adds inert durable-evidence
   or protected-access placeholders.
3. **Commands** previews one hub command and one dashboard command. Copying
   requires an explicit click and remains disabled for a command that fails the
   local safety gate.
4. **Verify** separates current hub snapshot evidence, dashboard access,
   loopback origin, live transport, and optional feed availability.

The assistant accepts no bearer, key, credential, or real secret-path input.
Its default previews contain neither inline secret flags nor secret-bearing
paths. When you opt into durable evidence or protected dashboard access, the
preview uses the literal placeholders `<HUB_DB_PATH>` and
`<OWNER_ONLY_ACCESS_POLICY_PATH>`; replace them only in your terminal. Setup
state stays in React memory and does not enter the URL, logs, telemetry, local
storage, or session-storage preferences.

!!! note

    A copied preview is still only a plan. Review each placeholder and file
    permission in the terminal before running it. Capability-bound apply
    actions require a separate backend contract and are not part of this
    assistant.

## Use the activity spine

The spine plots discrete retained events in presence, claims, task, and risk
lanes. It is not a smoothed activity estimate.

- Drag across the spine to select a time window.
- Use the keyboard on the spine to adjust the window.
- Clear the window from the inspector when you want the full retained tail.
- Hover or focus an event mark to inspect its identity and timestamp.

The selected window filters the signal log and fleet communication views.

The canvas has two explicit internal boundaries. Its lifecycle owner converts
source events and operator input into immutable frame state; the renderer owns
pixels only and returns the still-retained events after the 75-second cutoff.
Theme or canvas-size changes redraw immediately even under reduced motion, while
the signal-log table remains the accessible textual peer for every impulse.

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

Open the **fleet** inspector tab and choose one of five views:

- **web** groups identities by project and shows directed traffic;
- **matrix** uses sender rows and recipient columns for exact route volume;
- **projects** summarises inbound traffic, outbound traffic, identities, and
  claims by project;
- **timeline** separates retained message/receipt, claim, waiter, and task
  events into time-aligned lanes;
- **flow** aggregates retained message traffic from source projects to target
  projects and reports current claim ownership or contention beside it.

The web emphasises a small set of priority routes for quick selection. The
matrix remains the precise long-tail view. Select a node, project, link, or
matrix cell to open its evidence detail.

Use **identity or project** and **delivery health** above the visual to narrow
web, matrix, and project projections without changing the retained evidence
window. Text matching is case-insensitive and the result counter states both
visible and total route counts. The health choices distinguish delivered,
deferred, failed, and unknown receipt posture; unknown does not mean failed.
Filters pause in timeline and flow because those modes include claim, waiter,
task, and exact-event evidence that is not a communication edge. A selected
route remains pinned in its detail pane when a new filter hides it, and the
pane states that condition instead of silently changing the selection.

The communication query is bounded and encoded as `comm`; non-default health
is encoded as `delivery`. Both fields exist only while the fleet inspector is
active, survive reload and browser history, and are safe to share with the
rest of the workspace URL. They never contain message bodies or credentials.

Every timeline mark is a durable event sequence. Its table peer exposes the
same lane, sequence, time, actor/project, and label, and selecting either form
updates the shared event selection. The project-flow lines are retained message
aggregates, not inferred delivery paths. Each row links to the newest exact
message sequence supporting that aggregate; delivery still comes only from the
separate receipt evidence. Both modes obey the brushed time window and state
when the visual is bounded or contains no eligible retained evidence.

On first contact, current dashboards return the bounded recent event window and
its exact cursor inside the authenticated live stream, so essential timeline
evidence does not wait behind the heavier audit feeds. The cockpit retains its
legacy two-request polling bootstrap for older dashboard servers and follows
the same durable sequence contract in both transport modes.

The active inspector panel, fleet mode, and shared selection live in bounded
URL query fields. Copy the address to reopen that workspace; browser Back and
Forward restore earlier panel and selection changes. Signal-log filters remain
in the URL hash, so workspace navigation and log queries can be shared together.

The live transport envelope has its own connection id and strictly increasing
frame sequence. A missing or out-of-order frame is shown as **gap detected**;
the client reconnects and requests a fresh bounded history instead of treating
the held view as complete. **Poll fallback** is not a failure label: it means
the same authenticated HTTP feeds are preserving compatibility with an older
server or a sustained stream outage. The freshness beacon remains the source
for whether the currently displayed snapshot is live or stale.

A selected link shows retained pairwise messages with delivery outcomes.
Selecting one message opens an exact three-stage evidence chain: the durable
chat sequence, its sequence-correlated transport receipt (or an explicit
unknown state), and semantic responses whose `response_to_seq` names that
exact message. Exact-event buttons jump back to the retained event rather than
matching by actor, body, or timestamp. “No response retained” is deliberately
not presented as proof that the recipient did not act.

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

The risk rail separates server-provided risk, dead letters, local fleet-health
counts, waits, pending approvals, and client-side repetition heuristics. The
fleet-health row is a retained-log summary, not an identity score; a heuristic
is labelled as such and is not an authorisation decision.

## Filter, group, and export the signal log

The signal log supports text search, event-kind filters, newest/oldest order,
task grouping, pause, raw event detail, and export of the visible evidence.
Its filter query lives in the URL hash, so you can copy a filtered-log address.
The exported JSON states its query, provenance, time window, and count.

These capabilities cross explicit implementation boundaries without crossing
their evidence boundary. Workspace state selects live, history, or imported
evidence; the row renderer receives the already-filtered immutable set and owns
only its bounded presentation and per-row proof verdicts. Closing history or
unmounting the log cancels a pending debounced scrub, so a stale request cannot
revive a workspace the operator already left.

When the durable state-at feed is available, history mode can reconstruct one
selected sequence and comparison mode can pin two sequences, A and B. An
imported cockpit export is labelled as a post-mortem so it cannot pose as live
data.

## Use time travel safely

The replay workbench reconstructs claims and the task board at a selected
durable sequence. Choose **history** for one B position or **compare** for an
A-to-B evidence delta. The current replay state is encoded in the URL as
`replay=history&at=…` or `replay=compare&a=…&b=…`, so reload, Back, Forward,
and a copied workspace address preserve the investigation.

While replay is armed:

- claims and task-board cards show reconstructed B state;
- the activity spine, signal log, topology, and roster remain live;
- presence remains live because presence is not reconstructed from the journal;
- a prominent **HISTORY** or **COMPARE** label states the exact position and
  makes the live/historical boundary explicit;
- dragging a sequence control replaces the current browser-history entry,
  while entering or leaving replay creates a navigable history step;
- the comparison list counts added, removed, and changed claim/task evidence;
- an **exact event** hop appears only when a matching durable transition event
  is inside the retained A-to-B window. Otherwise the row says that the
  transition event is outside the retained window.

Use **live** before acting on current fleet state. If the state-at endpoint is
not configured, the workbench states that reconstruction is unavailable; it
does not silently substitute the current snapshot.

## Build a guided incident record

Open **incident** to turn the current investigation into a bounded local draft.
The three-step workspace separates the observable scope, explicitly selected
evidence, and operator notes:

1. **scope** names the observed problem and keeps the working hypothesis
   visibly provisional;
2. **evidence** adds only the current typed selection: an exact event sequence,
   task id, directed route, agent, or project;
3. **notes** records local commentary and exports a self-describing JSON
   document after the scope and evidence gates are satisfied.

The cockpit never fills the evidence cart from similar text, actors, times, or
task names. Each item records the replay mode and sequence position active when
the operator added it. Opening an item returns to its owning cockpit surface:
events to the signal log, tasks to causality, and routes, agents, or projects to
the fleet inspector.

The draft is autosaved in browser local storage under the authenticated
dashboard principal. It is not shared with another browser principal and it is
not sent to the hub. The selected incident step is URL-addressable as
`panel=incident&incident=scope|evidence|notes`; Back and Forward restore it
without putting draft text in the URL.

The JSON export states `local-operator-draft` provenance and explicitly says
that it is not a hub receipt or signed audit bundle. It contains exact typed
references and replay context, not embedded Merkle proofs or inferred event
relationships. Use the audit drawer and proof surfaces separately when a
signed or cryptographically committed record is required. Starting a new
incident requires an explicit confirmation and replaces only that principal's
local draft.

## Read metrics, audit, and causality

- **metrics** reports event-log counts and bounded trailing windows. It is not
  the hub process-metrics endpoint.
- **audit** keeps universal receipts and operator actions as separate feeds so
  a requested action cannot masquerade as a completed receipt. Select a row to
  open its evidence drawer. The drawer marks action/receipt evidence as paired
  only when both records share the same exact durable event sequence and the
  receipt is the dashboard's operator-relay receipt; actor, task, or timestamp
  similarity is never used as an inferred join. Partial evidence remains
  visibly partial and any event hop opens that exact retained sequence.
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
- Incident drafts stay in principal-scoped browser storage until the operator
  exports or explicitly replaces them; they are not hub records.
- Browser mutations go through the dashboard relay and then the hub's normal
  validation, ACL, and audit path.
- Observed peer state is advisory and cannot grant local claim authority.
- The page loads no remote font, script, style, catalogue, or telemetry service.

For deployment details, read [Deployment](deployment.md),
[Identity and ACL](identity-and-acl.md), and
[At-rest encryption](at-rest-encryption.md).
