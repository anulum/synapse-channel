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

The Python dashboard observer accepts decompressed hub messages up to 8 MiB.
This finite receive bound allows a growing task board to pass the WebSocket
library's former 1 MiB default while matching the cockpit live-frame ceiling.
A larger response closes the connection with code `1009`; split or reduce the
requested data before retrying. If the retained event log advances while roster,
claims, and board remain unavailable, check the dashboard's client version and
receive diagnostics. Unavailable snapshot counts do not prove an empty fleet.

## Open the cockpit

The package carries a build of the cockpit, and the cockpit is the dashboard's
front door. Start a hub first (the [quick start](quickstart.md) covers the hub
and participants), then the dashboard:

```bash
synapse dashboard --port 8765
# front door (cockpit): http://127.0.0.1:8765/
# capabilities JSON: http://127.0.0.1:8765/capabilities.json
# not served without --feeds-db: reliability, events, metrics, state-at, merkle-proof, health-anomalies, causality, findings, sessions, waits, operator-actions, receipts, postmortem, operations
# not served without --federation-store: federation
# not served without --attention-store: attention
# not served without --scope-groups: group scopes
# dashboard bearer: ~/.local/state/synapse/dashboard/bearer.token (written for this start; a restart writes a new one)
```

Open `http://127.0.0.1:8765/`. The browser is sent on to `/cockpit/`, keeping
any query such as `?lang=sk`, where the unlock screen asks for the bearer. Read
it from the file the start names: the start writes a generated bearer there,
owner-only, and never prints it. The directory is `$XDG_STATE_HOME/synapse/dashboard/`
(`~/.local/state/...` without it); the start refuses when that directory is a
link or open to other users. With `--dashboard-token` the bearer is the one you
gave; with `--dashboard-access-file` the start names the policy, whose token
files hold the bearers.

The start also names each optional surface it does not serve, with the flag
that turns it on. `/capabilities.json` says the same to a client, behind the
same bearer: every optional surface with its path, its flag, and whether this
start serves it. The setup assistant (**setup** in the HUD) shows that list
under "What this dashboard serves". A surface asked for without its store
answers `404` with `<name> feed not configured; start the dashboard with <flag>`;
the list and that answer come from one table, so they agree.

## Start the cockpit from a source checkout

A development build of the client replaces the packaged one with
`--cockpit-dist`. In a second terminal, build the static cockpit:

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

Open `http://127.0.0.1:8765/`, which opens this build.

Add the durable hub database when you want the event log, reliability,
metrics, audit, causality, sessions, waits, findings, and time-travel feeds:

```bash
synapse dashboard \
  --port 8765 \
  --cockpit-dist clients/cockpit/dist \
  --feeds-db ./hub.db
```

The dashboard remains useful without `--feeds-db`. Optional panels say that
their feed is not configured; they do not turn missing evidence into a zero.
Use `--feeds-db-key-file` when the selected hub store is encrypted.

Configured durable feeds return HTTP 503 with
`dashboard feed unavailable; check server diagnostics` when their store or
response cannot be read or encoded. The browser receives no internal path,
SQLite error or parser traceback. Check the dashboard server logs and restore
the selected store; failed responses are not cached, so the next request can
observe recovery without restarting the dashboard. A missing feed configuration
still returns 404 naming its enabling flag, and malformed queries retain 400.
The events, receipts and operator-actions channels in `/live.ndjson` use the
same fixed failure detail instead of presenting unavailable evidence as empty.

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
- `eventTailParser` defensively narrows the untrusted durable cursor document;
  `eventProjection` maps stored facts onto cockpit lanes without changing hub
  sequences or timestamps; `eventsTailSource` owns authenticated bootstrap,
  compatibility backfill, incremental polling, provenance, timers, and abort
  cleanup. The stable `eventsTail` facade preserves parser, projection, source,
  and type imports used by history and live-frame consumers.
- `auditFeedParser` strictly validates complete receipt and governed-action
  cursor pages for both HTTP and multiplexed live-frame consumers;
  `auditFeedStore` owns authenticated cursor advancement, retained last-good
  evidence, absence reset, sequence de-duplication, subscriptions, timers, and
  abort cleanup. The stable `auditFeeds` facade preserves existing component,
  projection, evidence, and feed-hook imports.
- `incidentDraftStore` owns strict schema-v1 parsing and fail-closed browser
  persistence; `incidentEvidence` owns typed evidence identity and bounded
  draft mutations; `incidentExport` owns the explicitly non-authoritative
  outward document and filename; immutable limits and types live in
  `incidentWorkspaceTypes`. The stable `incidentWorkspace` facade preserves the
  guided workspace component contract.
- `operatorActionValidation` owns strict governed-outcome decoding, dependency
  normalisation, and task-form admission; `operatorActionTransport` owns
  authenticated POST payloads and authority-preserving HTTP/result mapping;
  immutable input and result types live in `operatorActionTypes`. The stable
  `operatorActions` facade preserves task, response, and palette consumers.

Each owner has a dedicated behavioural hook or projection test. The wired
`App` and feed tests still exercise authentication, real endpoint adapters,
URL restoration, multiplexed history, polling recovery, replay, incident
evidence, command entry, and capability removal through public surfaces. This
separation is architectural: it must not change exact wire values,
browser-principal enforcement, startup ordering, or the evidence boundary
between retained live state and reconstruction.

The inspector also defines the cockpit's JavaScript delivery boundary. The
default signal log and attention queue ship in the entry chunk because they are
the immediate operator surfaces. Fleet, topology, metrics, audit, operations,
incident, and causality are separate feature chunks requested only when their
tab becomes active. A localised live status occupies the existing tabpanel while a deferred
chunk loads; the surrounding panel error boundary remains responsible for a
failed import. This is behavioural code splitting, not a warning override: the
recorded comparison measured an entry of 468.48 kB minified / 137.26 kB gzip
against 530.11 kB / 151.60 kB gzip before that change, without raising the
configured chunk-size threshold. These are historical build measurements;
measure the exact production artifact before claiming its current size.

Tab focus and panel data routing are separate contracts. Arrow keys, Home, and
End update the controlled tab and move focus without importing panel code;
task trace hops update the shared selection and causality prefill. The panel
router then adapts honest absent/connecting defaults and preserves the same
lazy production chunks.

## Unlock a protected dashboard

Every dashboard live/page read requires a bearer, including read-only loopback
operation. The CLI generates and prints one when no token or principal policy
is supplied. The validated React shell is the static exception: it loads before
authentication and displays an unlock screen; its feeds remain protected.

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
  detected**, or **poll fallback**, and **observed · polled** in a peer scope,
  which polls the observed hub's snapshot by design;
- headline fleet counters with deltas;
- the current browser role and capability posture;
- an identity focus lens that narrows claims and tasks;
- a scope selector that narrows the cockpit to one project (see
  [Narrow the cockpit to one project](#narrow-the-cockpit-to-one-project));
- compact/cozy density and dark/light theme controls;
- the command palette, contextual guide, local setup assistant, and
  interface-language controls.

Click a headline counter to filter the signal log to its underlying event
kinds. Clear the focus lens before concluding that a board or claim list is
complete.

Press `Ctrl+K` or `Command+K` to open the command palette. Viewer principals
receive navigation and inspection commands only. Operator and admin principals
receive the governed write commands currently enabled by the server.

### Counter glossary

Every counter below is computed once, on the hub's whole board, in the
snapshot's `headline` block (`/snapshot.json`, `/studio.json`, the live
stream, the classic page and the Go client all carry it). No surface counts
again, so one name shows one number everywhere. The HUD shows **agents
online** (`agents_live`), **claims held** (`claims_active`) and **red risk
signals** (`risk_signals_red`); the task board's head shows `tasks_blocked`;
Studio's header shows ready/blocked, active/stale and all/red risk signals.
**obs / min** is not a fleet counter: it is the event rate this browser
received in the last minute.

| Name | Definition |
| --- | --- |
| `agents_live` | identities connected now, not counting waiter sidecars or presence holders |
| `waiters_missing` | live identities with no waiter sidecar |
| `claims_active` | claims whose lease expires after the snapshot's time |
| `claims_stale` | claims whose lease expired at or before it and that the hub still lists |
| `tasks_open` | tasks whose status is not `done` or `cancelled` |
| `tasks_in_progress` | tasks whose status is `in_progress` |
| `tasks_ready` | the hub's ready list: status `open` and every prerequisite closed |
| `tasks_blocked` | open tasks that cannot start: `tasks_blocked_marked` plus `tasks_blocked_waiting` |
| `tasks_blocked_marked` | open tasks whose status is `blocked` |
| `tasks_blocked_waiting` | tasks whose status is `open` and whose prerequisite is not closed or is not on the board (a task in progress has started and is not counted) |
| `tasks_closed` | tasks whose status is `done` or `cancelled`, counted even when the bounded board does not send them |
| `risk_signals` | every risk signal, amber and red |
| `risk_signals_red` | red signals: an unsafe condition such as a stale lease or a branch conflict |
| `risk_signals_amber` | amber signals: impeded progress |
| `branch_conflicts` | pairs of live git-scoped claims on different branches towards one base whose paths overlap |
| `peers_reachable`, `peers_total` | observed peer hubs that answered, and that are configured |
| `seats_listening` | seats a socket receives messages for now: the identity itself or its waiter sidecar |
| `seats_active_not_listening` | seats with no such socket whose record shows work: a held claim at any age, or a claim stamped or a note authored in the last 900 seconds |
| `seats_silent` | seats with neither: the owner of a listed claim whose lease has run out and whose stamp is older than the window |
| `seats_deaf` | seats the hub judges deaf: connected under their own name, with no live waiter and no reaction within the hub's window (90 seconds by default), so a directed message to them draws a warning to its sender |
| `presence_holders` | connections that declared the wake capability `passive` and are not waiter sidecars (`synapse listen`, a `synapse-presence@` unit): they receive and never act, so they are neither agents nor seats; the roster's header counts them and no row names them |

A closed task (`done` or `cancelled`) sits in the board's "done" bucket. The
in-product guide carries the same definitions under "Headline counters".

### Seat states

The roster, the agent drawer, Studio's agents panel and the classic page show
each seat's state from the snapshot's seat projection (`fleet.seats`), and
never call a seat offline. **listening** says messages arrive, not that the
seat will answer: the hub keeps no proof of a reaction in the snapshot.
**active, not listening** is a seat at work by the durable record with nothing
receiving its messages, for example one that claims through a short connection
and listens through a waiter that has stopped. **silent** is a seat with
neither: in practice the owner of a listed claim whose lease has run out and
whose stamp is older than the window. The seats are the connected identities (a
waiter stands for its owner), the owners of listed claims and the authors of
notes inside the window; a note older than the window names no seat. In a peer
scope every seat is **observed**: the peer's log names it, and nothing here can
show whether a socket receives its messages, so no other state is claimed and
the headline's seat counters count none of them.
`last_seen` is not used: the hub stamps it on keepalive heartbeats, so it
proves a socket, not work.

A socket is not a reaction. The hub keeps its own proof: a seat is proven live
when a waiter of its own was seen within 20 seconds, or when the seat itself
connected or sent anything other than a keepalive within the hub's reaction
window (90 seconds by default). A connected seat with neither is **deaf**, the word `synapse who`
prints, and the hub warns anyone who sends it a directed message. The roster
tags such a seat "deaf", the agent drawer says when it last reacted on the hub's
clock, and Studio's agents panel and the classic page add the CLI's mark (for
example "deaf ~5h"). A seat the hub does not judge (one that is not connected
under its own name, or a hub that tracks no reactions) carries no mark: none is
invented. Presence daemons are seats: on the live hub they declare the same
wake capability as a working agent and no role, so the hub has no fact that
tells them apart, and they read deaf as the hub sees them. The window is 900 seconds, measured on the hub's
snapshot clock.

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

Choose **setup** in the HUD, or open it from the in-product guide. The
assistant is read-only unless the dashboard serves setup to your login (see
[Apply a setup plan from the cockpit](#apply-a-setup-plan-from-the-cockpit)).
It does not execute a shell, start or change a service, create a token, inspect
the host filesystem, or enable a network bind.

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
    permission in the terminal before running it.

## Apply a setup plan from the cockpit

When the dashboard serves setup (`synapse dashboard --operator --setup-apply
--setup-receipts DIR`, see [the command line](cli.md)) and your login holds
`setup_plan` and `setup_apply` (only an access file's admins do), the assistant
adds an **Apply** step before **Verify**. Its header then says it applies only what you
confirm. A peer scope never offers the step.

1. **Preview the plan.** The dashboard plans the profile its preflight names
   with the command line's own planner. The step lists each planned change,
   its disruption and whether it can be undone, and says "the hub (process N)
   is restarted" for a restart. A plan with a change the dashboard cannot make
   here offers no confirmation.
2. **Confirm and apply.** Nothing is sent until you tick the confirmation. The
   dashboard then applies the plan once, through the command line's executor,
   and answers with its receipt.
3. **Read the receipt back.** The step reads the apply's receipt from the
   dashboard's receipt chain (`/setup/receipts.json`) before it says anything:
   - **applied** only when the chain holds an `applied` receipt whose every
     change is `satisfied` afterwards;
   - **nothing changed** when the host already held the profile;
   - **refused** for a refusal, with the chain's own receipt and reason;
   - **unknown** otherwise, including when the apply got no answer and the
     chain shows nothing.

A plan applies once. Confirming the same plan again is refused by the
dashboard (`plan_replayed`) and shown as refused; preview a new plan instead.
Leaving the step forgets the plan, and the dashboard lets it expire.

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
return to the unfiltered retained view in one step. Each chip is named by what
it shows, then by what pressing it does (`focus api: clear the focus filter`).

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

## Narrow the cockpit to one project

The **scope** selector in the HUD narrows the cockpit to one project's part of
the fleet. It offers the whole hub, the work and names without a project
(**no project**), and each namespace the snapshot reports. Each choice shows
the hub's task and agent counts for it, closed tasks included, so a choice
never reads as a bare zero. Choosing a scope writes `scope=project:<namespace>`
or `scope=none` to the address; the whole hub leaves no `scope` field. Reloading
the page, copying the address, and browser Back and Forward restore the scope.
A dashboard started with `--scope-groups FILE` (see the
[CLI reference](cli.md)) lists its declared groups under **Declared groups**,
each with its counts. Choosing one writes `scope=group:<name>`, and the cockpit
shows the records of every namespace the group names.

While a scope is active, the selector is marked, and the cockpit asks the
dashboard for the scope on the snapshot, the live stream and the five feeds
that take it: events, receipts, findings, waits and attention. Each panel's
header then states what the panel shows:

- `scope: project:<namespace>` on a panel whose data the scope narrows: the
  signal log, claims, findings, topology, fleet roster, task board, risk rail,
  attention queue and fleet views;
- `scope: whole hub` on a panel whose feed the scope does not narrow:
  reliability, metrics, causality and host sessions;
- `scope: whole hub` on claims, task board and topology while time travel shows
  a past state, because the reconstruction is not narrowed; the replay
  workbench says the same;
- the audit panel narrows its receipts and keeps operator actions whole, and
  its tag's title says so.

A scope is not the shared selection's `project` field. A selection highlights
one project's records among all of them; a scope asks the dashboard for that
project's records only. A scope is a lens, not an access boundary: it grants
nothing and hides nothing the hub sends this principal. The rules that place
each record in a scope are in the [CLI reference](cli.md).

### Look at an observed peer hub

A dashboard started with `--observed-peer HUB=URI` lists each observed hub in
the selector, after the namespaces, under **Observed hubs (advisory)**.
Choosing one, `scope=peer:<HUB>`, shows that hub's folded state alone: its
board, its claims and the claim owners it saw. Nothing of this hub enters the
view, and the view grants nothing:

- a band under the HUD says the deck is advisory and observed from that hub,
  whether the last pull reached it, its cursor and lag, and that writes are off;
- the claims, board, topology, roster and risk rail say
  `advisory · observed from <HUB>`;
- the panels this hub's feeds fill (signal log, findings, attention, fleet
  views, metrics, audit, causality, reliability, host sessions) say
  `not observed` and show why instead of this hub's rows; the cockpit pulls
  none of them in a peer scope, nor the live stream, and polls the peer's
  snapshot alone;
- every write control is absent, whatever role this principal holds; this is
  the scope's view, not an access change, so no access notice appears;
- time travel and the federation row are hidden, because both read this hub.

A hub this dashboard does not observe, and one whose last pull failed, show no
row and say why. Choosing any other scope restores this hub's view and its
feeds.

### Look at the Fleet mirror

A dashboard started with `--fleet-observed-file EXPORT` and
`--fleet-observed-access-file GRANTS` serves Fleet's version-1 export to the
principals on the observer list. The cockpit reads
`/fleet-observed-access.json` on login and on each access revision; on a `200`
that says `observe`, the selector offers **Fleet mirror (advisory)** under
**Fleet**. Any other answer offers nothing and says nothing, because a dashboard
without the mirror is the usual case. No second credential is asked: the
cockpit reads the mirror with the login's own bearer.

Choosing it, `scope=fleet`, shows Fleet's export alone, read every 10 seconds;
the cockpit reads no other feed of this hub and no live stream:

- a band under the HUD says `advisory · Fleet mirror <source> · exported <age>
  ago · not this hub's state · writes off`, marks an export older than 5 minutes
  `stale`, says how many rows did not read and were left out, and opens the
  health of each hub Fleet drains: cursor, events, failures, last success,
  caught up, and the drain limit it hit;
- the board lists Fleet's tasks with their status, who claims each and the hub
  that holds the claim (`claim hub`), a `conflict` badge with its evidence, and
  the row's origin; the claims group the claimed tasks by that hub; both say
  `advisory · Fleet mirror <source>`;
- every other panel (roster, host sessions, reliability, signal log, findings,
  attention, fleet views, topology, metrics, audit, operations, incident,
  causality, risk) says `not observed` and why: the export carries no agents,
  events or messages;
- every write control is absent, time travel and the federation row are hidden,
  and the HUD shows no key figures of this hub.

An answer of `409` says the export's version is not supported; `503` says the
mirror is unavailable and keeps the last good read, marked stale. A `403` (the
grant was removed, or it changed between the read and the answer) withdraws the
scope: the cockpit returns to the whole hub and says the grant ended. A `404`
returns to the whole hub too.

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
critical, warning or information evidence, then open the exact agent, task, or route named by
a row.

With `--feeds-db`, the queue also reads the newest 200 declared operations from
`/operations.json`, every 30 seconds, and adds two kinds of row:
- **operation refused:** one per operation whose state is `refused: <answer>`.
  The row names the hub's answer exactly, such as `refused: not_approved`.
- **operation silent:** one per operation whose state is `attempt open, silent`.
  The row says that an open attempt ends only by its report, an observed effect
  or a seal, and never calls it ended.

A verified operation, or one in any other state, gives no row. **open
operation** opens the operations tab on that operation's chain. An operation
older than the newest 200 is not looked at here; the operations tab pages back
to it.

With `synapse dashboard --attention-store /path/to/queue.sqlite3`, the same
panel also shows owner-local approval, delivery, recovery and quota alerts
from [`synapse attention`](attention.md). Their snooze and resolution state is
shared with the CLI. A missing observer, absent feed or failed feed is shown
explicitly; none is interpreted as a quiet queue. The browser reads a
bearer-gated, bounded projection and never receives task bodies or account
labels from this feed. Use the CLI to change the local alert state.

The order is deterministic, not an opaque score: critical rows precede warning
rows, evidence kinds have a documented fixed rank, older available timestamps
come first within a kind, and stable ids break ties. A row navigates to evidence;
it does not acknowledge a peer, grant authority, approve a relay, or mutate hub
state.

## Work with claims and tasks

- Click a roster identity to open its claims, paths, unread dead-letter facts,
  and recent events. Its tag states the seat: none when listening,
  "active, not listening" or "silent" otherwise (see "Seat states"). The
  button is named by what the row shows, then the whole identity
  (`api harbour: inspect agent harbour/api`), so speech input can name it by
  the words on screen.
- Click a task card to inspect its owner, dependencies, readiness, claims, and
  history.
- Use drawer actions to filter the log or trace task causality.
- Treat branch conflicts as advisory evidence derived from declared claims;
  the dashboard does not run Git to refine them.
- Check board truncation text before treating visible task rows as the whole
  plan. The snapshot carries open work in full and counts closed tasks without
  loading them, so the board states "N closed not loaded"; the exact counts by
  status are in the snapshot's `bounds` block. `/snapshot.json?closed=1`
  returns the closed tasks as well.
- Narrow the fleet to one project with a scope (`?scope=project:<namespace>`,
  `?scope=group:<name>` for a group the dashboard declares, or
  `?scope=none` for work and names without a project, on `/snapshot.json`,
  `/studio.json` and the live stream). A scope is a lens, not an access
  boundary: it grants nothing and hides nothing the hub sends this principal.
  Its rules and its `scope` block are in the [CLI reference](cli.md). The
  event, receipt, finding, wait and attention feeds take the same scope and
  keep its rows; every other feed says that it was not scoped. The HUD's
  scope selector sets it for the whole cockpit (see
  [Narrow the cockpit to one project](#narrow-the-cockpit-to-one-project)).

The risk rail separates server-provided risk, dead letters, local fleet-health
counts, waits, pending approvals, and client-side repetition heuristics. The
fleet-health row is a retained-log summary, not an identity score; a heuristic
is labelled as such and is not an authorisation decision.

## Filter, group, and export the signal log

The signal log supports text search, event-kind filters, newest/oldest order,
task grouping, pause, raw event detail, and export of the visible evidence.
Its filter query lives in the URL hash, so you can copy a filtered-log address.
The exported JSON states its query, provenance, time window, and count.

Each row keeps the hub's sequence and time, and names its lane, kind and actor.
The actor and the line are the hub's own words for the event: `/events.json`
carries an `actor` and a `summary` for every kind the journal names, read from
the fields each event's writer records. The actor shows "—" where the record
names none, as for a release, whose record holds only the task id. The lane is
the cockpit's. A claim, a task update and a release ride the claims lane. A
recorded finding rides the task lane, with the author the hub attested. An
operator relay rides the task lane, with its operator and its status. A relay
the operator-actions feed reads as refused rides the risk lane instead: one
with no recorded status that was neither applied nor left pending. A kind of
event the cockpit has no lane for is shown as chat on the presence lane, with
the hub's words. Without them, as from an older dashboard or an imported
export, the cockpit words the kinds it knows itself and shows any other kind
by its name.

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

The roster's separately granted **Host sessions** view is Linux metadata
observation through `/host-sessions.json`. It is distinct from the
cost/turn/token feed `/sessions.json`. An operator/admin role alone does not
grant disclosure; configure the owner-only host-observation policy described in
[the client guide](https://github.com/anulum/synapse-channel/blob/main/clients/cockpit/README.md#local-host-session-observation).
PID/start-time and tmux assertions do not prove provider responsiveness or
authorise a process action. This view does not report per-process CPU, RAM,
swap or GPU usage, and adds no stop, restart or remote-compute control. Those
counters appear only in a local `synapse pid-monitor` run, for the user's own
processes.

- **metrics** reports event-log counts and bounded trailing windows. It is not
  the hub process-metrics endpoint.
- **audit** keeps universal receipts and operator actions as separate feeds so
  a requested action cannot masquerade as a completed receipt. Select a row to
  open its evidence drawer. The drawer marks action/receipt evidence as paired
  only when both records share the same exact durable event sequence and the
  receipt is the dashboard's operator-relay receipt; actor, task, or timestamp
  similarity is never used as an inferred join. Partial evidence remains
  visibly partial and any event hop opens that exact retained sequence.
- **operations** lists the governed operations the hub journalled, from
  `/operations.json` (it needs `--feeds-db`), grouped by state, newest
  declaration first, one page of 50 at a time. Choose one to read its chain
  link by link: A, the declared intent with its digest checked; B, each approval
  note on that digest; C, each of the hub's answers and each seal; D, each
  executor's report and each observer's reading. Every link shows its journal
  sequence, its time, its sender and the fields its row holds, and nothing
  else. **open evidence** fetches that row's inclusion proof and recomputes the
  root in the browser. The state words and the hub's answer names, such as
  `refused: not_approved` or `outcome reported, unknown`, are the operation
  report's own and stay untranslated in every language. A state the cockpit
  does not know is shown as the hub gave it and marked. An executor's report is
  never shown as a success, an attempt past its deadline is never shown as
  ended, a field the row does not hold reads "not recorded", and custody reads
  as the feed states it. A scope narrows the list; one chosen chain is shown
  whole, and the tab says so. The chosen operation is part of the address
  (`?panel=operations&operation=<id>`), so a link opens that chain, and Back and
  Forward return to it.
- **The approval view.** For an operation that awaits approval, the chain
  shows the intent the declaration kept, field by field in canonical order, as
  the dashboard encoded it. Beside it are the digest the declaration recorded
  and the digest the dashboard recomputed. When they are equal, the view shows
  the requester's own words in a box marked as not checked, and the two
  `synapse approvals decide` commands for that digest (approve, reject), each
  with a copy button. The commands carry a placeholder approver and never a
  token. When the login holds `task_update`, the view also offers **Decide
  here**: the person's reason (at most 4096 bytes, refused rather than
  trimmed), approve or reject, and a confirmation that names the digest. The
  dashboard records the note under the login's relay identity, after its own
  checks (`POST /approval/decide` in the CLI reference), and the view shows
  what it answered: "Recorded" with the dashboard's words, or why nothing was
  recorded. After a recorded decision the chain is read again, so the new
  approval link and the new state show as the store holds them. The view never
  says that the operation may now run. When the digests differ, the view says
  that the intent must not be approved, and it offers neither the commands nor
  the decision. The hub judges an approval when an attempt is asked, and only
  an approver of the declarer's project who is not the declarer counts.
- **causality** traces recorded causes or effects from an event sequence or task.
  A task hop from the log opens this panel with the task already selected.
- **topology** joins identities and held tasks and adds imported federation
  posture when configured.

The **Findings** panel lists the findings agents recorded with the hub, newest
first, from `/findings.json`, so it needs `--feeds-db`; without it the panel
says the feed is not served. Each row shows the hub-attested author and
project, the subkind, the claim status and the freshness. Only a freshness of
`verified-at-source` (in green) means the producer re-checked the reference at
its source in that session; a retracted or superseded finding is struck
through and labelled. The count reads "N of M" when older findings stay in the
log.

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

Service workers require a
[secure browser context](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register).
Use trusted HTTPS for a phone reaching the tailnet endpoint, through an
operator-configured gateway and admitted dashboard host. Plain HTTP on a remote
Tailscale IP does not supply this browser requirement. The HTTP loopback
exception applies to a browser on that same host.

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
