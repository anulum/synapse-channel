<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE·CHANNEL cockpit

A read-mostly, real-time operator cockpit with four opt-in governed write routes for
the coordination hub, built as a static React + TypeScript SPA (Vite). It is a
*client* — like `clients/go`,
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

The shell constrains its grid column to the viewport. The HUD wraps controls
onto additional rows when needed and grows to contain them; the deck does not
widen to the header's intrinsic content width. The production-browser layout
gate checks panel and header-control bounds at 1100, 1280, 1440 and 1920 pixels
across all five interface languages and both display densities.

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
  Roster inspection is also a native button: Tab to the identity and activate
  with Enter or Space. The named modal detail focuses its close button, keeps
  Tab navigation inside, and makes the background inert. Escape, close, or
  backdrop dismissal restores the opener (or its surviving focusable container).
  Live data updates do not reset the selected control inside an open detail.
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
- **Audit** — two independent, store-attested cursor feeds: universal receipts
  (kind, status, subject, actor) and governed operator-relay history (action,
  outcome, subject, operator). Each states live, absent, failed, or stale
  last-good provenance independently and retains a bounded newest-first window.
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
| `/receipts.json?since=SEQ&limit=N` | 2 s incremental | universal receipts projected from receipt-bearing durable events (optional) |
| `/operator-actions.json?since=SEQ&limit=N` | 2 s incremental | governed operator-relay audit history from durable `operator_relay` events (optional) |
| `POST /message` | on send | governed operator chat relay; `undelivered` never reads as "sent" |
| `POST /message/respond` | on response | semantic reply with `message_seq`, `to`, `status`, and optional `note` |
| `POST /task` | on declaration | governed task declaration with `id`, `title`, and `depends_on` |
| `POST /task/update` | on update | governed task status and/or progress-note update |

The multiplexed `/live.ndjson` transport owns each HTTP attempt separately.
Before reconnect backoff it aborts the request, cancels the response reader and
releases its lock, including invalid frames, sequence gaps and consumer frame
callback failures. Unsupported and failed HTTP responses are closed too.
`stop()` initiates asynchronous teardown and prevents new attempts; it does not
wait for the server to observe closure. Late responses cannot restore live state.

Optional endpoints answer `404` on dashboards that do not serve them; the
corresponding panel states that plainly and activates the moment the surface
ships (`synapse dashboard --feeds-db PATH` serves the store-backed feeds).
All four write routes are absent unless the dashboard runs `--operator` and
require a recognised bearer with the corresponding capability. Each returns the
`{action, status, detail, ok}` outcome document; the cockpit reports the hub's
decision and never treats HTTP `200` alone as acceptance. Task update IDs are
suggested from the live board, while explicit IDs remain available.
The version-one `/dashboard-access.json` descriptor advertises `read` and three
write capabilities: `message_send` covers both message routes, `task_declare`
covers `/task`, and `task_update` covers `/task/update`. Viewer credentials do
not permit writes; operator/admin roles still require operator mode. The HTTP
server and hub enforce permissions independently of these presentation hints.
The snapshot's `state.pending_relay_approvals` (hubs ≥ 0.98.5) lists relays
awaiting their second operator; the risk rail names each one, and a hub
without the field simply shows no section.
Spine and log events prefer the hub-attested tail (true seq + ts, provenance
labelled "hub event log"); while it is absent they fall back to diffing
consecutive snapshot fetches — real transitions, quantised to the poll
cadence, labelled "observed transitions". The two sources never mix.

### Dashboard bearer

Dashboard reads require a bearer, including on loopback and in read-only mode.
Without a caller-supplied token or access-policy file, the dashboard generates
a token and prints it at startup. With `--dashboard-token`, use the token you
supplied; with `--dashboard-access-file`, use the credential assigned to your
principal. The validated React shell at `/cockpit/` loads without a bearer so
it can show its unlock form; live feeds remain protected. Paste the appropriate
bearer there. The cockpit retains it only in this tab's `sessionStorage` and
sends `Authorization: Bearer …` on every snapshot, optional feed, history,
proof, causality, and operator request. A `401` clears the credential and the
entire live presentation before showing the veil again.

Never put a dashboard bearer in a URL. The cockpit accepts no query-token form,
does not write the bearer to `localStorage`, rendered HTML, logs, built assets,
or Cache Storage, and the service worker bypasses every request carrying an
`Authorization` header.

## Getting started

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
npm run e2e        # production build against a real local hub/dashboard (Chromium)
npm run preview    # serves the PRODUCTION build on :8772 with the same proxy
```

For an isolated browser run, set `SYNAPSE_COCKPIT_E2E_DIST` to an absolute
production-build directory. The harness otherwise uses `clients/cockpit/dist`.
For example, after building that directory, run
`SYNAPSE_COCKPIT_E2E_DIST=/path/to/build npx playwright test e2e/layout.spec.ts`.

The pure data logic — snapshot parsing, the freshness contract, polling stores,
transition derivation, and every panel's data shaping — is held to full line
and branch coverage. The behavioural component layer uses jsdom and
Testing Library for the palette, drawers, boards, rail sections, toasts, and
views. Playwright then drives the production bundle through the real Python hub
and dashboard boundary: wrong/correct bearer handling, authenticated operator
messaging, dependent-task declaration and update, lock-on-`401`,
store-backed receipt/operator-audit rendering, URL/storage/cache discipline,
and axe-core scans in both themes at desktop and phone widths.

`.github/workflows/clients-cockpit.yml` runs that whole lane whenever cockpit,
dashboard-auth, route, lockfile, or workflow code changes. CI installs Chromium
only and retains a failure trace containing a disposable test bearer, never a
repository or deployment credential. Root CI and preflight also run
`tools/check_cockpit_ci.py --check`, which freezes the workflow contract and
verifies npm v3 package/lock alignment plus registry integrity metadata.

## Installing on a phone (PWA)

The built cockpit is an installable PWA. A phone on the tailnet opens
`http://<hub-tailnet-ip>:<dashboard-port>/cockpit/`; the token-free shell opens
first and asks for the bearer in its unlock
veil. Then install it:

- **Android / Chromium**: the cockpit shows an "add to home screen" chip
  when the browser fires its install prompt; one tap hands over to the
  browser dialog.
- **iOS Safari**: there is no prompt event — use **Share → Add to Home
  Screen**.

The service worker (`public/sw.js`) caches the **token-free app shell only** —
navigations are network-first with a cached fallback, hashed assets are
cache-first, and any request carrying `Authorization` bypasses it. The data
feeds (`*.json`) are **never cached**: stale
coordination data presented as current is worse than a spinner, so an
unreachable hub surfaces through the HUD beacon's honest `stale HH:MM:SS`
state (amber-bordered at phone width) instead of silently served old JSON.
Under 640px the deck becomes a segmented single-column view (signals ·
claims · board · roster · reliability) with 44px touch targets; the spine
stays at every width and yields vertical panning to the page on touch.

Honest scope (Tier 1): read-mostly observation plus the four explicit,
foreground operator routes above. Every action remains subject to dashboard
gating and the hub's validation, authorisation, rate limit, and audit decision.
No push, no background wake — mobile OSes suspend the tab, so "the phone stays
live on the bus" is deliberately not promised here.

## Serving the built cockpit

### Local host-session observation

The roster workspace includes a **Host sessions** view. It reads
`/host-sessions.json`, not the cost/turn feed `/sessions.json`. Collection starts
only after an authenticated request with an explicit host observation grant.
Without configuration, the host endpoints return `404`; a valid dashboard
credential without a host grant receives `403`. Operator/admin roles do not
implicitly grant access. `/dashboard-access.json` remains unchanged.

Create an owner-only JSON file (mode `0600`) with principal IDs from the
dashboard access policy. The generated/supplied single-token mode uses the
principal ID `compatibility`:

```json
{"version":1,"observers":{"compatibility":{"paths":false,"context":false}}}
```

Pass its path as `synapse dashboard --host-sessions-access-file PATH` alongside
the cockpit build configuration below. Grants reload on each request; removing
an observer revokes access. Invalid or inaccessible policy fails closed. The
separate `/host-sessions-access.json` descriptor reports implemented read grants
only. No stop, restart or wake endpoints are added.

`paths` permits working-directory observation. `context` permits bounded reads
of descriptor **pathnames**, looking for a unique open Codex rollout UUID under
the current user's `.codex/sessions/`. No transcript bodies, terminal contents,
process argv or process environments are read. Missing, closed or conflicting
context evidence stays unknown; process names are provider candidates, not
proof that a particular executable or model is running.
Each optional value carries a literal evidence status: `not_requested`,
`observed`, `unavailable`, `denied`, `conflicting`, `partial` or `unsupported`.
Only `observed` carries a value. An incomplete descriptor scan never establishes
a unique context ID; a denied filesystem read is distinct from an absent grant.
These statuses appear in both the terminal and the host-session detail view.
For a non-default installation, set `--host-sessions-context-root PATH` on the
dashboard or `--context-root PATH` on the local monitor. This selects the allowed
pathname root; it does not grant disclosure. The local monitor still requires
`--context`, and the dashboard still requires the principal's context grant.

Each row shows two distinct ages. **Observation age** is how old the displayed
scan is. **Runtime** is `observed_at - started_at`, where `started_at` is the
kernel start time derived from the boot time in `/proc/stat` plus the process
start ticks; it is accurate to about one second and is displayed as
`<days>d HH:MM:SS` or `HH:MM:SS`. Runtime proves neither activity nor responsiveness. When the
boot reference cannot be read, the row says `runtime unknown` and its
`started_at_status` is `unavailable`. A granted, observed
context ID has a copy button using the browser clipboard API. Missing clipboard
support or denied permission is reported without claiming success. Revocation or
expiry removes the control; copies already requested by the user cannot be
recalled from the operating-system clipboard.

The collector uses Linux procfs and metadata-only tmux formats. It inspects
same-user process metadata, retaining provider-name candidates and descendants
of observed tmux panes. A PID/start-ticks/boot reference distinguishes process
lifetimes. tmux identity is labelled as a session assertion, never action
authority. Attached/detached does not establish desktop-window visibility.
Every ancestor used for a pane join is revalidated. If linked tmux sessions
report different metadata for the same pane-root PID, the monitor reports a
partial tmux observation and withholds the ambiguous identity and pane binding.
Coordination presence, waiter names and unexpired claims remain separate facts;
the terminal's standalone mode has no hub observation.

Terminal observation uses the same schema:

```bash
synapse pid-monitor --json
synapse pid-monitor --pid 1234 --paths --samples 10
synapse pid-monitor --dashboard-port 8765 --token-file /path/to/dashboard.token --json
```

Replace `1234` with an intended local PID. The connected mode reads the shared
loopback HTTP cache; independent standalone scans have distinct observation
IDs. `--tmux-socket` selects an existing local socket without starting a server.
The snapshot cache is memory-only, keyed by disclosure grants and refreshed at
most once per second. The UI polls every two seconds while mounted, expires
stale rows and clears detail on access failure. It never stores host metadata
in browser storage. Bounded or unavailable scans do not prove process absence.
The panel follows the cockpit's English, Slovak, German, Spanish and French
language selection without resetting its filter. Wire status values and exact
identifiers remain literal evidence.

This view does not provide a durable process ledger, automatic recovery,
desktop-window mapping, provider readiness classification or process control.

### Usage

From this client directory, build and serve the bundle beside the real feeds:

```bash
npm run build
synapse dashboard --port 8765 --cockpit-dist "$PWD/dist"
```

Open `http://127.0.0.1:8765/cockpit/` and use the unlock form. Add `--operator`
only when you intend to enable the governed write routes.

| Page | Purpose | Access |
| --- | --- | --- |
| `/cockpit/` | Rich React client, when `--cockpit-dist` is configured | Public validated static shell; bearer-authenticated feeds |
| `/` or `/studio/command` | Packaged read-only Studio command centre | Bearer required for page and feeds |
| `/studio` | Static design-system reference, not live telemetry | Bearer required |
| `/classic` or `/index.html` | Legacy server-rendered dashboard | Bearer required |

The packaged pages do not provide the React unlock form. Ordinary browser
navigation cannot supply an Authorization header; use the React client for
the interactive unlock workflow. A client that opens packaged pages must
provide the bearer header explicitly. Do not put credentials into URLs.

`npm run build` emits a self-contained static bundle in `dist/` with relative
asset paths (`base: "./"`), so it can be served from any path on the dashboard
origin without a rebuild. The intended production shape is the dashboard HTTP
server serving `dist/` next to its JSON endpoints: the SPA's relative fetches
then hit the real surfaces with no proxy, and the cockpit inherits the
dashboard's loopback-by-default bind and token posture.
