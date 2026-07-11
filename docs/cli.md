# CLI reference

The `synapse` command exposes the following subcommands.

For end-to-end examples that combine these commands with existing agent tools,
see the [Integration demos](integration-demos.md).

Start-up is proportional to the command you run: the CLI imports only the
module family that owns the requested subcommand, so frequent short calls
(`synapse who`, `synapse send`, wake loops) skip the import cost of the rest
of the surface. `synapse --help`, `--version`, and unknown commands still load
everything, since they need the whole command table.

| Command | What it does |
| --- | --- |
| `synapse hub` | Run the coordination hub. |
| `synapse commands` | List every subcommand grouped by stability tier — the quickest map of the surface. |
| `synapse completions` | Print a static tab-completion script for bash, zsh, or fish, generated from the installed CLI. |
| `synapse demo` | Run a self-contained local coordination demo and print a success marker. |
| `synapse benchmark` | Benchmark the installed package (event store, relay encoding, live hub round-trips) and print a scorecard with honest host context; `--compare BASELINE.json` gates the run against a saved scorecard, exit `1` on regression; `--trend STORE.db` accumulates runs and renders per-metric sparkline trends (`--ascii` for a printable-ASCII trend block); `--alert` gates the run statistically against its own same-context history, exit `1` on drift. |
| `synapse quickstart-coding` | Create a coding-fleet workspace, run the no-collision demo, and print a success marker. |
| `synapse fleet-init` | Empty machine to working fleet in one command: doctor (`--fix`), persistent workspace scaffold, provider-seat probe, demo smoke, and a printed next-steps plan. |
| `synapse new coding-fleet` | Scaffold a runnable two-agent coding demo workspace. |
| `synapse health` | Probe the hub; exit `0` if reachable, `1` if not (wired as a container healthcheck). |
| `synapse worker` | Run a model worker that answers on the channel. |
| `synapse worker-session` | Run a provider command with `SYN_PROJECT`/`SYN_IDENTITY` set and a waiter armed around it. |
| `synapse team` | Launch a hub plus one or two local workers in one shot. |
| `synapse mcp` | Serve identity-safe coordination tools over stdio, including bounded local-feed inbox and live status; an omitted `--name` resolves from an agreeing environment or `<git-project>/mcp` (see [MCP server](mcp.md)). |
| `synapse mcp-tools` / `synapse mcp-call` | List and call allowlisted tools on an external MCP server (outbound). Stable taxonomy codes distinguish invalid config (exit `2`), deny-by-default access refusal (exit `3`), and tool failure (exit `1`). |
| `synapse sandbox` | Validate a capability manifest and pre-flight or run a `.wasm` tool against it (`validate`/`test`/`run`). |
| `synapse adapters` | Detect coding tools and wire them to the hub with a claim-aware adapter (`list`/`install`/`uninstall`). |
| `synapse a2a-card` | Print an Agent2Agent Agent Card projected from the live capability manifest. |
| `synapse a2a-conformance` | Print the local Agent2Agent conformance matrix. |
| `synapse a2a-serve` | Run the stdlib HTTP+JSON Agent2Agent bridge. |
| `synapse channel` | Manage private-channel membership and member-visible history; pair with `synapse send --channel`. |
| `synapse encrypt-key` | Generate and check at-rest encryption key files (needs the `encryption` extra to encrypt). |
| `synapse agent-tmux` | Wake an existing terminal-agent tmux session (Codex, Kimi, …) with a fixed safe prompt. |
| `synapse codex-tmux` | Codex-defaulted alias of `agent-tmux`. |
| `synapse dashboard` | Serve a loopback-only read-only live cockpit (fleet graph, board, claims, risk guidance, stream, receipts) over hub snapshots, plus `/snapshot.json`; `--feeds-db` adds durable store feeds including `/postmortem.json?task=ID` and `/receipts.json`, and `--observed-peer HUB=URI` adds advisory peer-hub rows. |
| `synapse route-task` | Recommend agents for a board task using local capability signals. |
| `synapse resource-bids` | Rank live resource offers for a board task without reserving capacity. |
| `synapse memory-recall` | Recall matching durable memory records from a local event store. |
| `synapse send` | Connect, send one message, optionally await replies, and exit. |
| `synapse wait` | Block until a message addressed to you arrives, then exit (a wake trigger). |
| `synapse listen` | Connect and stream channel messages until interrupted. |
| `synapse arm` | Keep a waiter armed, or use `arm install --identity NAME [--start]` to write a permanent Linux systemd user waiter with mailbox replay and `Restart=always`. |
| `synapse relay` | Decode and print a lite relay log a hub mirrored to a file. |
| `synapse ingest` | Stream durable event-store records since a sequence cursor. |
| `synapse event-query` | Query a hub SQLite event store for temporal task and coordination history. |
| `synapse multihub` | Observe or follow a peer hub's event log and print its board and claims (see [Multi-hub sync](multi-hub-sync.md)). |
| `synapse participant` | Probe or drive Participant Fabric providers: `list` reports each driver's readiness, `ask` runs one turn, `exchange` and `convene` run multi-party deliberations, `costs` reports per-session spend and telemetry from a hub event store. |
| `synapse federation` | Exchange, import, list, and revoke operator-confirmed peer-domain bundles (`offer`/`fetch`/`import`/`list`/`revoke`); fetch displays fingerprints and never imports. |
| `synapse compact` | Apply event-store retention and optionally write an HTML archive report. |
| `synapse postmortem` | Build a replayable task postmortem from a hub SQLite event store. |
| `synapse debug` | Fork a task's reconstructed state at a sequence point (read-only what-if). |
| `synapse reproduce` | Fingerprint a task's authoritative history into a deterministic digest. |
| `synapse causality` | Trace coordination causes, effects, or counterfactuals over the event log — federated across hubs with `--peer`; `contention` weighs overlapping live claims and advises who yields; `otel` exports the graph as OpenTelemetry spans; `health` flags orphaned claims, dangling dependencies, and stale claims. |
| `synapse merkle` | Commit the event log to a Merkle root, prove event inclusion, and generate the receipt-signing keypair (`keygen`). |
| `synapse reliability` | Build evidence-only reliability memory from a hub SQLite event store. |
| `synapse trust-graph` | Query the evidence trust graph (receipts, stale claims, conflicts) as text, JSON, or Graphviz DOT. |
| `synapse accounting` | Record and report opt-in model cost/token usage from a hub SQLite event store. |
| `synapse fleet-scorecard` | Compose causality spans, opt-in accounting, live-claim contention, reliability findings, and optional benchmark history into an owner-only JSON bundle or a two-signal OTLP/HTTP collector push. |
| `synapse approval` | Request, decide, and replay human-in-the-loop approval gates from a hub SQLite event store. |
| `synapse ttl-advice` | Build read-only lease TTL advice from a hub SQLite event store. |
| `synapse auto-action` | Introspect the opt-in auto-action reactor and manage the durable armed policy the orchestration loop reads (`show`/`arm`/`disarm`/`clear`). |
| `synapse board` | Print the shared task/progress blackboard. |
| `synapse supervisor` | Run an LLM-free supervisor that re-offers stalled tasks. |
| `synapse manifest` | Print the capability manifest of advertised agents. |
| `synapse directory` | Print a read-only capability directory from live agent cards (discovery only). |
| `synapse who` | List the agents currently online and hub-authoritative mailbox pending counts, optionally for one project or this identity with `--me`. The full-roster view shows the 20 largest positive mailboxes plus total identities/messages; `--all-mailbox-pending` (alias `--all`) expands every retained positive identity. `--observed-peer HUB=URI` appends advisory `observed@HUB` peer rows. |
| `synapse status` | Print a one-line hub summary (online agents, active claims, this identity's mailbox pending count) for shell prompts and tmux status bars, the counts as JSON with `--json`, or a refreshing operator dashboard with `--watch`; exit non-zero when the hub is down; `--observed-peer HUB=URI` appends advisory peer counters. |
| `synapse state` | Print active claims and their checkpoints (a resume view); `--observed-peer HUB=URI` appends advisory peer claims marked `observed@HUB`. |
| `synapse dead-letters` | Print directed messages the hub delivered to no consume-live recipient — no socket, or only stale sockets without a recent reaction/live waiter — worst first with the `syn inbox --as NAME` drain remedy. |
| `synapse approvals` | Print the relays awaiting a second operator under the two-person quorum — the pending set of the per-hub approval ledger (enforced but otherwise invisible), oldest first, naming each pending action and its first requester. Rides in the same state snapshot the dashboard and cockpit read. |
| `synapse doctor` | Check common coordination misconfigs plus this identity's hub mailbox pending count; exit non-zero on a failure. `--fix` auto-repairs a down default local hub or missing waiter by installing and starting the user services; `--json` emits the verdicts for CI health gates. |
| `synapse init` | Print or install the local user services (hub, waiter, presence) as systemd units. |
| `synapse install-shell-hook` | Install auto-arming shell integration into Bash, Zsh, and Fish (idempotent, guarded block). |
| `synapse shell-hook` | Print the shell code that auto-arms terminals and wraps agent commands, for manual sourcing. |
| `synapse git-init` | One-step claim-aware setup: install the hooks and write a `.synapse/` conventions guide. |
| `synapse git-claim` | Claim work scoped to the current git branch (see [Git-native claims](git-claims.md)). |
| `synapse git-hook` | Install post-commit/post-merge hooks that auto-release a commit's claims. |
| `synapse git-release` | Release the claims whose paths a commit or merge just touched. |
| `synapse conflicts` | Predict cross-branch merge conflicts between overlapping claims; exit non-zero on a hit. |
| `synapse cross-repo` | Scan a directory of repositories into a dependency graph (manifests/CODEOWNERS as edges), flag provably conflicting version pins, and join live claims onto it; with `--repo`, exit `1` when a connected repository holds a live claim; `--suggest-resolution` names each conflict's odd-one-out declaration; `--watch --notify-cmd` runs a sink command on coordination-fact transitions. |
| `synapse verify-release` | Run declared verification commands and write an observed release receipt JSON; `--merkle-db` commits the coordination log's Merkle root into it, `--signing-key` attests it with the hub key. |
| `synapse policy-check` | Evaluate a release receipt against a policy file; advisory by default, `--enforce` to gate, `--trusted-signing-key` verifies the commitment's hub signature. |
| `synapse identity` | Inventory and audit declared agent identities for enforcement-rollout blockers. |
| `synapse acl` | Shadow-mode (non-blocking) deny-by-default ACL evaluation of candidate accesses. |
| `synapse lock` | Hold a lease while running a command, to serialise it across agents. |
| `synapse release` | Manually drop a claim you own (e.g. an `--auto-release-on manual` claim). |
| `synapse task` | Declare and update the shared task plan. |
| `synapse workflow` | Validate and compile a declarative workflow into blackboard tasks (`validate`/`compile`/`plan`/`run`); `contention` weighs overlapping live claims involving the workflow's tasks. |

## First 60 seconds

The installed CLI has a source-checkout-free validation path:

```bash
python -m pip install synapse-channel
synapse commands   # a map of every subcommand, grouped by stability tier
synapse doctor
synapse demo
synapse quickstart-coding
```

`synapse commands` prints the whole surface grouped into its five stability tiers
(stable core, adapters, read-only analysis, advisory governance, experimental), so
you can find the daily-safe core without scrolling the flat `synapse --help` list.

`synapse doctor` reports local wiring issues, including identity, hub exposure,
root-filesystem pressure, hub reachability, the current identity's waiter, and
**directed messages nobody reads**: one reader often answers to several names
(a terminal identity, an agent identity, a role name like
`project/coordinator`), and a message addressed to a name whose inbox no
cursor drains and whose waiter is absent lands durably in the feed while
waking no one — the human ends up relaying, the exact failure the bus exists
to remove. The doctor names such addresses with their message counts and the
remedy (`syn inbox --as NAME`, repeatable; a standing set goes in the
comma-separated `$SYN_ALIASES`, and each name advances its own cursor so
draining a role never consumes another reader's delta). The primary `syn inbox`
read is exact-identity scoped too: it uses the full resolved identity and an
identity-specific cursor, so one terminal cannot display or consume another
terminal's directed mail. `syn inbox --project-wide` is the explicit opt-in to
the broader project feed, while `syn inbox --name PROJ/name` reads one exact
address. A bare `--as PROJ` remains an explicit project-wide alias. On
a fresh machine, a missing hub or waiter can be a warning before services are
installed. `synapse doctor --fix` repairs the safely repairable findings: when the
default local hub does not answer or the waiter is missing, it installs and starts
the local hub, presence, and wake services, then re-runs the checks so the exit
code reflects the repaired state. Findings the services cannot repair — identity,
exposure, disk pressure, or any non-default hub — are reported with a remedy but
never touched. `synapse doctor --json` emits every verdict plus the overall
health as one JSON document for CI health gates (it refuses the mutating and
checklist flags so stdout stays a single document); `synapse status --json`
does the same for the status counts, sized for monitoring scripts.
`synapse doctor --notify-cmd CMD` additionally pipes any warn/fail findings
to the sink command's stdin — one line each, remedy attached, hub URI in
`SYNAPSE_DOCTOR_URI` — turning the diagnostics into a proactive alert: a
healthy run sends nothing, under `--fix` the sink sees the state *after* the
repair, and it composes with `--json` (stdout stays one document). Same
contract as `cross-repo --notify-cmd`: split without a shell (wrap in
`sh -c '…'` for pipes), best-effort, a failing sink never changes the exit
code.
Federated deployments can opt into peer checks without inferring ambient trust
state: repeat `--federation-peer PEER=URI` for the hubs to probe, add
`--federation-cursor PEER=SEQ` for the local consumed cursor when known, and
optionally pass `--federation-store PATH` to inspect imported bundle expiry and
revocation state. The peer probe uses the multi-hub log request path and reports
reachability, cursor lag (`log_end_seq - cursor` when the peer supports it),
measured clock skew from the peer welcome timestamp, and TLS certificate expiry
warnings. `--federation-token TOKEN` is sent only on these peer probes;
`--federation-skew-warn-seconds` and `--federation-cert-warn-days` tune the
warning thresholds. Add `--federation-path PEER=MODE` to declare the network
shape for certificate-pinned federation checks. Supported modes are
`direct-mtls`, `tls-passthrough`, `tailnet`, and `tls-terminating-proxy`; the
terminating-proxy mode fails because the remote peer pins the proxy certificate
and hub-side client certificates do not reach the hub.
`synapse status --watch` refreshes the line every `--interval` seconds (default
2) as an operator dashboard: each refresh opens its own probe connection so a
hub restart shows as an honest offline line, a TTY rewrites the line in place
while piped output appends one line per refresh, and `--json --watch` streams
one JSON object per line (NDJSON). `--count N` stops after N refreshes;
Ctrl-C is the normal way to stop an unbounded watch and exits `0`. `synapse
demo` starts an ephemeral local hub, drives a planner/worker
flow, and is successful when it prints:

For post-release local fleet restarts, `synapse doctor --redeploy-checklist`
prints package, service, roster, durable-state, and git-hook checks. It does not
restart services by itself; it gives operators copyable verification commands
for the installed executable, `systemd --user` units, live roster, SQLite event
log, and claim-aware hooks.

```text
success: coordination demo completed
```

`synapse quickstart-coding` creates a temporary workspace, runs the packaged
two-agent coding demo, removes the temporary workspace after success, and is
successful when it prints:

```text
success: coding fleet demo completed
```

For a generated workspace with editable files and a runnable no-collision coding
scenario:

```bash
synapse new coding-fleet ./demo-fleet
cd ./demo-fleet
python run_demo.py
```

The generated demo succeeds when it prints `success: coding fleet demo completed`.

`synapse fleet-init [path]` is the one-command version of the whole first-run
sequence — empty machine to working fleet:

```bash
synapse fleet-init            # doctor, ./synapse-fleet scaffold, seat probe, demo smoke
synapse fleet-init --fix      # let the doctor stage repair the default hub and waiter
synapse fleet-init my-fleet --seat claude --seat codex   # plan exactly these seats
```

It runs the real `doctor` (a failing report is a printed remedy, not an abort),
scaffolds a persistent workspace (refused when non-empty unless `--force`),
probes every registered provider CLI without taking a turn, runs the packaged
no-collision demo (`--no-smoke` to skip), and prints a next-steps plan — waiter
arming, per-provider `worker-session` seat commands, `git-init`, dashboard —
with the workspace's project name filled in. It starts no daemon and adds
nothing the bundled commands do not already do.

## Fastest safe trial path

Use this order when moving from install validation into a real repository:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
synapse git-init --name trial-agent
synapse a2a-card --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
```

Run this in a disposable or already-versioned repository. `synapse git-init
--name trial-agent` installs hooks and writes the local `.synapse/` conventions
guide before agents edit files. The A2A bridge step is optional and local-only:
it validates the HTTP+JSON bridge shape for local tools, but it is not an
external conformance claim. Do not bind it off-loopback without bearer auth.

For a stricter local hub profile, use `synapse hub --paranoid --db <path>
--token-file <path>`. Paranoid hub mode requires a shared-secret token, durable
event log, and metrics bearer token when metrics are enabled; it disables metrics
query tokens and the insecure off-loopback override while printing the hardening
hooks that are still missing.

The loopback-only `--metrics-query-token-ok` compatibility flag is deprecated,
warns when parsed, and is scheduled for removal in 0.101.0. Send metrics tokens
in the `Authorization: Bearer` header instead; URL credentials can leak into
logs, shell/browser histories, and proxy records.

## Recovery: picking up after a restart

Nothing is lost when a terminal or session goes down — the feed, the plan, and the
event log are durable. On return, catch up everything for your repo regardless of
the instance id you now run as:

```bash
synapse relay ./feed.ndjson --project quantum --cursor ./quantum.cursor  # missed messages
synapse board                                                           # the current plan
synapse state --owner quantum                                           # your claims + resume checkpoints
synapse who --project quantum                                           # who is live now
synapse dashboard --port 8765                                           # local read-only HTML/JSON view
```

A lapsed claim keeps its checkpoint, so re-claiming the task resumes from it rather
than restarting.

`synapse who`, `synapse status`, `synapse state`, and `synapse dashboard` can add
an opt-in observed peer layer with repeatable `--observed-peer HUB=URI`. The peer
URI is pulled through the same multi-hub event-log request path as
`synapse multihub follow`, then folded locally and marked `observed@HUB` wherever
it appears. Observed rows are advisory: they never grant local claims, never
change the local hub's roster, and peer failures render as unreachable peer rows.
Use `--observed-token` for secured peer hubs, `--observed-pin HUB=sha256:<hex>`
to pin a self-signed `wss://` peer's certificate (a pin naming an unfetched hub
is refused), and `--observed-timeout` to bound
each peer pull. When a peer welcome frame carries a usable timestamp,
observed-peer output also carries local-minus-peer clock skew: `who` prints
`skew=+/-Ns`, `status` includes the largest absolute skew, and JSON uses
`clock_skew_seconds` / `observed_max_clock_skew_seconds`.

`synapse dashboard` binds to `127.0.0.1` by default and reads roster, state,
board, and manifest snapshots from the live hub. The state snapshot carries
`dead_letters` — directed chats that reached no live connection, per target
with counts, so a blackhole shows up on the page instead of being discovered
by a human relaying messages. A hub started with a
`dead_letter_escalation_threshold` turns that passive visibility into an active
signal: when a target's undelivered count reaches the threshold, and each further
multiple of it, the hub broadcasts a one-line `dead_letter_escalation` notice to
every connected socket and journals an audit event, so a growing blackhole is
surfaced without polling. It never re-delivers a message — the ledger keeps
counts and names, not bodies — so escalation points a human or an orchestrator at
the blackhole rather than silently re-sending. The default of `0` disables it,
leaving the ledger's visibility unchanged. It serves `/` for the browser
view and `/snapshot.json` for local tooling. The snapshot also includes a derived
`fleet` section for live agents, `-rx` waiters, missing waiters, active and stale
claims, a task-dependency graph from blackboard task edges, branch-conflict candidates
from live git-scoped claims, ready and blocked board tasks, release receipt
notes, and optional A2A task counts. It also carries the live hub's pinning tag —
`hub_version` (the package version) and `config_epoch` (a fingerprint of the hub's
configuration posture) — so a cockpit can badge which hub build and configuration
it is watching and notice a deploy or a config drift; the hub's own `/health`
endpoint reports the same two values. Pass `--a2a-state-file <path>` to
summarise a persisted `synapse a2a-serve --state-file <path>` store in that
section. The task-dependency graph is read-only and does not mutate the
blackboard. Dashboard branch conflicts use the same declared-claim metadata as
`synapse conflicts`; they do not run git or apply `--check-diff` refinement. Use
`--allow-non-loopback` only behind trusted local network controls because the
page exposes agent names, claim scopes, branch names, and task text. Pass
`--dashboard-token <token>` to require `Authorization: Bearer <token>` on `/`
and `/snapshot.json`; when `--allow-non-loopback` exposes the dashboard and no
token is supplied, Synapse generates and prints a startup token. Add
`--observed-peer HUB=URI` to include peer-hub rows in the browser and
`/snapshot.json`; those rows stay advisory and are labelled `observed@HUB`.
The snapshot's risk section also enriches at most 20 ready tasks with at most
three explainable `route-task` candidates and three `resource-bids` candidates
per task. These are the same deterministic local scorers as the CLI, remain
advisory-only, and never claim work, assign owners, reserve capacity, or grant
execution authority.

With `--feeds-db <hub.db>` (`--reliability-db` is the same flag's original
name) the dashboard serves twelve feeds off the **durable event store** —
available when the hub is down, real sequences and timestamps, behind the
same dashboard bearer token as every other path:

- `/reliability.json` — the same audit-signal report as `synapse
  reliability` (per-owner tallies and finding records anchored to event
  sequences, explicitly "audit signals, not scores"), which the cockpit's
  reliability panel consumes;
- `/events.json?since=SEQ&limit=N` — the raw event-log tail past a
  cursor, in the exact multihub snapshot shape (`events`, `next_cursor`,
  and `log_end_seq`),
  so a polling client walks the log forward without loss or duplication;
  `since=latest` starts at the log's end — the tail shortcut that spares
  a client the full history walk on a large log;
- `/causality.json?seq=N|task=ID&direction=causes|effects` — one
  causality query in the CLI's exact `--json` shape; `task=ID` resolves to
  the task's most recent recorded event, so a client can hop from a log
  row to its causal cone without knowing sequences (an unrecorded task is
  404, not an invented anchor). A `present: false` answer carries a
  `note` naming which absence it is: an event recorded but outside the
  coordination causal graph (chatter carries no causal edges), or no
  event at that sequence at all;
- `/postmortem.json?task=ID` — the same replayable task evidence as
  `synapse postmortem`, projected as JSON for cockpit links. The identifier is
  required and bounded before storage access; a task with no matching events
  returns `present: false` and an empty timeline rather than invented history;
- `/metrics.json` — store-attested log metrics for the cockpit's metrics
  panel: total and per-kind event counts plus the same split over
  trailing hour/day windows, measured against the log's own final
  timestamp (never the wall clock) so the document is deterministic over
  a given log. Honest scope stated in the document itself: these are
  *log* metrics; the live process registry (connection gauges, handler
  timings) is the hub's own `/metrics` endpoint and is deliberately not
  duplicated here.

- `/state-at.json?seq=N` — coordination state (claims + board) reconstructed as of event `seq` by bounded replay, in the live snapshot shape plus `as_of_seq` and `log_end_seq`; deterministic (judged at the bounded event's own timestamp), `seq` clamped into range, presence/roster omitted (not journalled).
- `/merkle-proof.json?seq=N` — an RFC 6962 Merkle inclusion proof for event `seq`, in the same shape `synapse debug merkle` emits, so a cockpit row's verify button can check the row against the attested tree root; a `seq` the committed log does not hold returns `{"present": false}` with a note, never a fabricated proof.
- `/health-anomalies.json` — the honest hub-side alert surface: the orphaned, dangling, and stale coordination anomalies the causality graph makes visible, in the same shape `synapse causality --health` emits, with an `anomaly_count` for a cockpit alerts badge. Fired alerts stay collector-side off `/metrics`; this is only what the durable log can prove.
- `/sessions.json` — the opt-in `session_metric` telemetry the fleet left in the log, in the same shape `synapse participants costs` renders: per-session token counts, cost, latency, and error/abstention rates, with `totals` aggregated across sessions. Every record carries the `seq` of the snapshot it was read from, so a cockpit joins a session's cost straight to its causal cone via `/causality.json`; each record's coordination `task_id` (from the note body) is the same join key `synapse participant costs` reports. A log with no session notes reports empty `sessions` and zeroed `totals`, never a fabricated cost.
- `/waits.json` — the pending coordination gates reconstructed from the plan: each non-terminal task blocked on a dependency that has not reached a terminal status, with `who` is waiting (the task's suggested owner, or whoever declared it), `on_what` dependency ids it is blocked on, and `since` when it was declared, plus a `wait_count`. This is the "what is the fleet stuck behind" panel. Transient socket waiters (a client's `-rx` connection) are not journalled and are omitted; this is only the coordination gates the durable plan can prove.
- `/operator-actions.json?since=SEQ&limit=N` — the governed operator-action history reconstructed from `operator_relay` audit events: direction, action, namespace, task, operator, origin/owner hubs, peer or local requester, status, reason, break-glass tag, detail, and real `seq`/`ts` join anchors. Ordinary releases without relay provenance are omitted.
- `/receipts.json?since=SEQ&limit=N` — the universal receipt feed projected from receipt-bearing durable events: release/claim evidence, delivery receipts, sandbox run attestations, approval/policy/verification notes, governed operator relays, cross-hub pointers, A2A validation notes, and postmortem notes in one shape (`seq`, `ts`, `receipt_id`, `kind`, `subject`, `actor`, `status`, `summary`, `source_event_kind`, `payload`). Ordinary events without receipt semantics are omitted.

Without the flag each endpoint answers 404 naming the remedy; an
unreadable store answers 503 rather than an empty document pretending the
log is clean. `--federation-store <federation.json>` adds
`/federation.json` — the imported peerings with provenance and the bundle
fingerprints operators compared in the exchange ceremony; namespace
outcomes are hub-runtime state no durable store carries, so that section
ships empty with the reason stated. `--cockpit-dist <dir>` serves a built
cockpit single-page app read-only under `/cockpit/` (paths escaping the
directory or with unrecognised suffixes are refused).

**Operator write-path (opt-in).** `--operator` arms three write routes so the
cockpit can act on the fleet rather than only observe it:

- `POST /message` `{"to": "<name|group|all>", "text": "..."}` — relay one chat
  message to the fleet.
- `POST /task` `{"id": "...", "title": "...", "depends_on": ["..."]?}` — declare
  a board task.
- `POST /task/update` `{"id": "...", "status": "..."?, "note": "..."?}` — change
  a task's status and/or append a progress note (at least one of the two).

The write-path is **off by default** — without the flag every route answers 404,
indistinguishable from an unknown path, and the dashboard stays a pure observer.
When armed, a write still requires the dashboard bearer token, is rate-limited,
and is sent under the identity `operator:<name>` (set with `--operator-name`),
never impersonating an agent. The relay reimplements neither authorisation nor
auditing: the hub applies its own ACL to the relayed frame and records it in the
durable log, so every operator action is authorised at the hub and shows up in
replay, `/state-at`, and the signal stream like any other frame. The response is
JSON — `200` when the hub delivered, dead-lettered, or applied it, `403` when the
ACL refuses it, `409` when the blackboard refuses the task on its own terms
(unknown id, empty title, dependency cycle, unknown status), and `503` when the
hub is unreachable.

**Public status-page posture:** the dashboard is read-only by default —
every endpoint answers `GET` only unless `--operator` is set (above), and
nothing on the read surface mutates the hub or the store — so a public
status page needs no special mode: expose
the dashboard deliberately with `--allow-non-loopback`, set
`--dashboard-token` (or accept the generated one), and point it at a
store copy with `--feeds-db` if you want it serving with the hub down.
What limits a *public* page is the data, not the verbs: the snapshot and
feeds carry agent names, task ids, and message subjects, so publish them
only where that operational metadata is fine to show. Every response also
carries browser-hardening headers (`X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and a same-origin
`Content-Security-Policy`), so the page cannot be framed and pulls no remote
resources.

## Identities and groups

An identity is a name; when several agents share a project they use composite
names `<project>/<agent>`, e.g. `quantum/claude-7f3a` and `quantum/codex-2b40`.
A `target` is then a name, a comma list, a **group glob** (`quantum/*` for every
agent on the project, `quantum/claude-*` for one role), or `all`. List who is live:

```bash
synapse who                       # agents online, with -rx waiter sidecars counted apart
synapse who --project quantum     # only quantum/... instances
synapse who --observed-peer east=ws://127.0.0.1:8877  # add advisory peer owners
synapse who --name quantum/codex-2b40 --me  # this identity plus its -rx waiter status
syn who --me                      # same check using the resolved syn identity
syn reap                          # list this identity's shell-hook waiter pidfile
syn reap --pid 1234               # clean up only that verified identity waiter PID
syn reap --stale                  # reap all verified waiters whose owner shell is dead
syn reap --stale --dry-run        # report the sweep verdicts without acting
syn locks                         # list this project's leases, scopes, ages, and release commands
syn ask <target> <message>        # send, require an online recipient, and wait for replies
syn inbox                         # exact resolved identity, with its own cursor
syn inbox --project-wide          # explicit project-wide feed and project cursor
syn inbox --name PROJ/name        # one exact address, with its own cursor
syn inbox --as PROJ/coordinator   # also drain a role name, under its own cursor (repeatable)
syn commit <paths> -m <message>   # hold the project git lease and commit only those paths
synapse send --target quantum/* "rebasing main now"   # the whole project team
```

`synapse who --me` queries as `<name>-who`, then reports `<name>` and
`<name>-rx`, so the check does not create the presence it describes. It keeps the
output honest: presence is not a wake loop, and a missing `-rx` waiter means
directed messages will not wake that terminal promptly.

`synapse wait --directed-only` suppresses *routine* broadcasts: it wakes on messages
that name you (or a group you are in), but still wakes on a **priority broadcast**
(`synapse send --priority`) and on any message from **`CEO`** — so an `all` that
genuinely matters reaches a quiet waiter promptly while peer chatter is left for the
next `synapse relay`/inbox read. Use `--priority` sparingly, for announcements that
must reach everyone immediately.

When several agents share a repo, serialise the operations that must not overlap —
above all commits — by wrapping them in a lease. The hub grants one live lease per
id, so the others wait their turn instead of clobbering each other:

```bash
synapse lock quantum:git -- git push          # holds quantum:git while pushing
synapse lock quantum:git --wait-timeout 0 -- git push   # fail fast if someone holds it
synapse lock quantum:git --release-timeout 10 -- git push  # hold the exit up to 10s for the release confirmation (slow links)
```

A lock is a named mutex keyed by its id: `quantum:git` and `physics:git` are
independent, so one repo's push-lock never blocks another's. The lease is held only
for the wrapped command and dropped when it exits. A claim that no commit or merge
will auto-release — a `git-claim --auto-release-on manual` — is dropped by its owner
with `synapse release <task> --name <owner>`.

Add receipt fields when the release is also the closeout record. The hub echoes
the receipt on `release_granted`; if any evidence field is present, it records the
same receipt as an `assessment` progress note on the board. Use `--receipt-json`
when another tool should consume the hub-confirmed receipt:

```bash
synapse release BUILD --name api-dev \
  --evidence "pytest tests/test_feature.py -q: passed" \
  --evidence "mypy src/synapse_channel/feature.py: passed" \
  --changed-file src/synapse_channel/feature.py \
  --generated-artifact docs/_generated/capability_manifest.json \
  --artifact coverage.xml \
  --approval "reviewed-by=owner" \
  --known-failure "none" \
  --confidence medium \
  --freshness-seconds 60 \
  --receipt-json
```

The receipt records the releasing owner's submitted evidence; it does not certify
that the evidence is complete or sufficient. It also includes advisory
`epistemic_status` and `epistemic_reasons` fields derived from the submitted
evidence, known failures, and `--freshness-seconds`: fresh positive evidence is
`supported`, positive evidence without freshness is `needs_freshness`, old
positive evidence is `stale`, declared known failures are `degraded`, and no
positive evidence is `unsupported`.

Use `synapse verify-release` when the closeout record must include observed
command execution instead of hand-written evidence. The command runs each
declared `--run` argv, records exit codes plus stdout/stderr SHA-256 digests,
hashes named `--artifact` files, captures Git `HEAD`, tree, and changed files,
and writes receipt JSON consumable by `synapse release --receipt`:

```bash
synapse verify-release BUILD --name api-dev \
  --run ".venv/bin/python -m pytest tests/test_feature.py -q" \
  --run ".venv/bin/python -m mypy --strict src/synapse_channel/feature.py" \
  --artifact coverage.xml \
  --merkle-db ~/synapse/hub.db \
  --output verified-release.json

synapse release BUILD --name api-dev \
  --receipt verified-release.json \
  --receipt-json
```

`--merkle-db` additionally commits the coordination log's RFC 6962 Merkle root
into the receipt, binding the release to the exact coordination history behind
it. Because the log is append-only, anyone can later re-verify the commitment —
`synapse policy-check --merkle-db` recomputes the committed log prefix and adds
a `merkle_commitment` decision that fails when that prefix was rewritten,
truncated, or renumbered since the receipt (and passes as the log grows):

```bash
synapse policy-check BUILD --policy ./policy.json \
  --receipt-json verified-release.json \
  --merkle-db ~/synapse/hub.db          # re-verify the committed log prefix
```

The bare commitment proves the log did not change; it cannot prove who attested
it — whoever holds the receipt file could have written any root into it.
`--signing-key` closes that gap: `synapse merkle keygen` creates the hub
deployment's Ed25519 receipt-signing keypair (private key `0600`, public half in
a distributable `PATH.pub`), `verify-release --signing-key` signs the commitment
into `verification.merkle_signature`, and `policy-check --trusted-signing-key`
(repeatable, one `.pub` per trusted hub) adds a `merkle_signature` decision. A
verifier holding only the receipt and the `.pub` file — no access to the live
log — learns which hub attested that exact log state; a tampered root, an
untrusted or transplanted key, and a signature with no commitment to cover all
fail, and only a receipt with no signature at all reads `not_applicable`:

```bash
synapse merkle keygen ~/synapse/hub-receipt.key       # once per hub deployment

synapse verify-release BUILD --name api-dev \
  --run ".venv/bin/python -m pytest -q" \
  --merkle-db ~/synapse/hub.db \
  --signing-key ~/synapse/hub-receipt.key \
  --output verified-release.json

synapse policy-check BUILD --policy ./policy.json \
  --receipt-json verified-release.json \
  --trusted-signing-key ~/synapse/hub-receipt.key.pub  # provenance, offline
```

The generated receipt is still advisory coordination evidence. A `supported`
status means the submitted checks produced fresh positive evidence; it does not
mean Synapse independently verified correctness, reviewed the commands, or
proved the artifacts sufficient.

`synapse git-claim` accepts the task id either positionally (`synapse git-claim
TASK-1 --paths src`) or as a named field (`synapse git-claim --task-id TASK-1
--paths src`) for generated argv. Use one form, not both. `synapse git-release`
is hook-invoked and does not take a task id; when a manual drop is needed, use
`synapse release <task> --name <owner>`.

`synapse git-claim` also accepts semantic selector flags for the same local
resolver exposed by `tools/semantic_claims.py`: `--module`, `--symbol`, `--api`,
`--source`, `--test`, `--generated`, and `--migration`. The command resolves the
selectors against the local git root, merges the derived source/test/generated
paths with any explicit `--paths`, and sends only canonical path scopes to the
hub. Symbol and API selectors use a synthetic descendant below their source;
other selectors and companion paths stay whole-file.

With the optional `semantic` extra, `--diff-base main` maps tracked working-tree
changes to the smallest named declaration in Python, JavaScript/JSX,
TypeScript/TSX, Rust, or Go. Add `--diff-head HEAD` for a committed comparison
and repeat `--diff-path src/pkg` to filter it. Every incomplete mapping widens to
the whole file. `python tools/semantic_diff_claims.py --base main --claim-args`
exposes the same diff-only planning surface. Add `--semantic-evidence-json
semantic-evidence.json` to either selector or diff claims to write receipt-ready
local evidence; no parser download or hub-side Git access occurs.

Claim paths are coordination scopes, not filesystem reads. Normal relative paths
such as `src/auth.py` stay narrow. Absolute paths and any path containing `..`
are treated as traversal-like declarations and widen to the whole worktree, so a
suspicious scope may block more work but cannot miss a conflict.

Use `syn locks` for the operator view before releasing or asking another owner to
release. It queries the live state snapshot as `<identity>-locks`, filters to the
resolved project by default, and prints the task id, holder, scope, age, remaining
lease time, checkpoint, git branch context, and the exact `synapse release ...`
command. `syn locks --all` removes the project filter; `syn locks --owner <name>`
shows one owner or project namespace; `syn locks --json` emits the same rows as
JSON.

Use `syn commit <paths> -m <message>` for the common commit workflow. It resolves
the current identity, acquires the `<project>:git` lease, runs `git add -A --`
only for the supplied paths, and runs `git commit -m <message> --` for the same
paths. Unrelated staged or modified files stay outside that commit. The command
rejects empty, absolute, parent-traversal, and `.git` paths before contacting the
hub.

## Getting woken on a message

A turn-based assistant cannot hold a socket between turns, so it learns of a
message only when it checks. `synapse wait` turns that into a push: it blocks on
the connection and exits the instant a message addressed to you arrives. Run it as
a background task — when it exits, the message has landed (and a harness that
re-invokes an agent on background completion wakes you). On wake, read the message,
act, and re-launch `synapse wait`. It costs nothing while it waits.

```bash
synapse wait --name api-dev-rx --for api-dev   # blocks; prints + exits on a message for api-dev
synapse wait --for api-dev --timeout 60        # give up after 60s (exit 2) instead of waiting forever
```

`synapse arm` states its binding out loud before it holds the socket: the first line
names exactly whose messages it wakes on (`waiting for messages to <identity>`), and
when the shell environment carries a different `SYN_IDENTITY` it says so on a second
line — an explicit `--name`/`--for` always wins, but that mismatch is the classic
sign of arming from a borrowed shell, so it surfaces immediately rather than after a
night of silently missed messages.

A waiter is deaf while it is disconnected — the gap between a dropped connection (or
a re-arm as a fresh process) and the next connect. A directed message that lands in
that gap is durable, but the waiter is not woken by it and it waits unread until an
unrelated wake happens to drain it. `synapse arm --mailbox` closes the gap: on each
connect the waiter asks the hub to replay the directed messages it missed and wakes
on them as it would on a live message. It resumes from a `since_seq` cursor kept per
identity under `~/synapse/mailbox-cursor/`, so a re-arm picks up where it left off
rather than being replayed — and woken by — the whole retained backlog again. It is
off by default; a plain `arm` is unchanged, and against a hub older than wire version
`2` the mailbox request is simply ignored.

```bash
synapse arm --name api-dev-rx --for api-dev --mailbox   # also wake on messages missed while offline
```

When the shell hook launches an interactive provider command, `worker-session`
automatically starts or attaches a persistent tmux session and keeps a directed
wake bridge alive. The user still types the provider command normally, for
example `codex` or `claude`; the provider process starts with `SYN_PROJECT` and
`SYN_IDENTITY` already set.

Use `synapse agent-tmux` as the manual diagnostic/admin surface for that tmux
wake path. It works for any terminal coding agent — Codex, Kimi K2, Claude Code —
selected with `--agent-command`. It starts or targets a named tmux session and
injects only a fixed instruction; the Synapse message body stays in the inbox and
the agent reads it itself. `synapse codex-tmux` is a Codex-defaulted alias kept
for backward compatibility (`--codex-command` instead of `--agent-command`).

```bash
# Generic form — choose the agent with --agent-command (defaults to codex):
synapse agent-tmux start  --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"
synapse agent-tmux wait   --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"
synapse agent-tmux status --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"

# Codex alias (equivalent to --agent-command codex):
synapse codex-tmux wait --identity api-dev/codex-main --session api-dev-codex --cwd "$PWD"
```

A terminal agent does not wake its own idle pane on a Synapse message: its
`synapse wait` is a foreground tool call whose turn ends, so the message lands in
the inbox but the pane never re-engages. `agent-tmux wait` is the external bridge
that closes that gap — it blocks on `synapse wait` for the identity and, on each
directed message, types the wake prompt into the pane and presses Enter.

`wait` types the fixed prompt and presses Enter as two steps separated by
`--submit-delay` seconds, because the agent UI ignores a submit key that arrives
in the same keystroke batch as the pasted line. It retries a failed
`synapse wait` with backoff instead of exiting, giving up only after
`--max-wait-failures` consecutive failures (unbounded by default), so a hub
restart does not permanently stop the waker.

When a broadcast (`--target all`, or a `--priority`/`CEO` message that reaches a
`--directed-only` waiter) wakes *every* terminal at the same instant, their agents
all re-invoke and call the model provider at once. That synchronised burst trips the
**provider's** request-rate limiter — not a synapse limit: Anthropic's API, for
instance, answers *"Server is temporarily limiting requests"* (a request-rate
throttle, distinct from your usage quota). `synapse wait --wake-jitter <seconds>`
(default 8) spreads the broadcast wakes over `0..jitter` so each agent reacts
without the stampede; a
one-to-one directed message has no herd and still wakes immediately. Set `0` to
disable for a latency-critical single-waiter setup.

The shell hook records its background waiter in an identity-scoped pidfile under
`$XDG_RUNTIME_DIR/synapse-shell` (or `/tmp/synapse-shell`). Use `syn reap` to list
the pidfile for the resolved identity. If the pidfile points at a dead PID,
`syn reap --pid <pid>` removes only that pidfile; if the PID is live, it sends
SIGTERM only after the command line verifies as this exact `synapse arm --name
<identity>-rx --for <project>` waiter. It refuses unrelated PIDs instead of
searching or pattern-killing.

`syn reap --stale` sweeps every pidfile in the runtime directory and reaps the
verified waiters whose owner is demonstrably dead — the recorded `--owner-pid`,
or the terminal PID embedded in a `…/terminal-<pid>` identity. A waiter whose
owner is alive, or that names no checkable owner, is kept; a live process whose
command line is not this Synapse waiter is reported and never signalled. Add
`--dry-run` to see the verdicts without acting.

```bash
synapse wait --for api-dev                  # default: broadcast wakes jitter 0–8s
synapse wait --for api-dev --wake-jitter 0  # disable the jitter
```

The same herd from the *sending* side: to push a fleet-wide update, do **not**
`--target all` a fleet of waiters at once — send directed, spaced a few seconds
apart, so the wakes (and re-invocations) do not stampede the provider.

## Messaging: broadcast, several, or one

Every message carries a `target`. The hub broadcasts each message to all
connected clients and records it in history and the relay log; the `target`
selects who it is *for*:

```bash
synapse send --target all "deploy is green"              # everyone (the default)
synapse send --target SCPN-CONTROL "kernel built, run the control tests"  # one agent
synapse send --target SCPN-CONTROL,REMANENTIA "you two: rebase on main"   # several
synapse send --require-recipient --target SCPN-CONTROL "ping"             # also print the positive receipt
```

If a one-shot send accidentally uses a waiter name such as `api-dev-rx`, the
command sends as `api-dev` instead. That keeps the persistent wake socket online
and avoids the hub's duplicate-name refusal for the short-lived sender.

Every directed `synapse send` asks the hub for a delivery receipt. The reply
includes `delivered`, `message_target`, `message_id`, consume-live `recipients`,
all socket-level `matched_recipients`, `stale_recipients`, a machine-readable
`reason`, and whether the hub `dead_lettered` it. A socket match counts as live
only when the recipient reacted within the configured liveness window or has a
fresh `-rx` waiter; otherwise the CLI prints `delivery failed: no live recipient
matched ...` and exits `1`, the same as an offline target. The message is still
journalled and best-effort routed to the stale socket, so a later mailbox replay
can settle its deferred receipt. `--require-recipient` additionally prints a
positive `delivered to ...` receipt and fails if an older hub returns no receipt;
without the flag, receiptless older hubs retain their historical success result.

For selected sensitive bodies, `synapse send --encrypt-key-file` replaces the
plain payload with an AES-256-GCM envelope whose authenticated data binds the
visible sender, target, channel, task id, and recipient set. The hub routes the
ciphertext and metadata; it does not receive the plaintext:

```bash
synapse send --target SCPN-CONTROL \
  --encrypt-key-file ./payload.key \
  --encrypt-key-id project-main-v1 \
  --encrypt-recipient SCPN-CONTROL \
  "private handoff note"
synapse listen --name SCPN-CONTROL --for SCPN-CONTROL \
  --decrypt-key-file ./payload.key
synapse channel key-check ./payload.key
```

The key file is a local 32-byte owner-only file. This first runtime tranche does
not discover, rotate, revoke, or escrow keys.

Private channels scope delivery without encrypting payloads:

```bash
synapse channel create ops --name alice
synapse channel join ops --name bob
synapse send --name alice --channel ops "operator note"
synapse channel history ops --name bob --limit 20
synapse relay ./feed.ndjson --channel ops
synapse relay ./feed.ndjson --public-only
synapse relay ./feed.ndjson --channel ops --channel-metadata
synapse event-query ./synapse.db "channel ops between seq 1 999999"
```

The hub retains a bounded live history per channel for current members and
journals channel chat with a visible channel id. Relay filtering can select one
private channel or the default public lane. Event-query channel results expose
metadata and payload byte length, not private payload bodies.

For the common question workflow, use `syn ask <target> <message>`. It resolves
the same identity as `syn say`, dispatches to `synapse send` with
`--wait-seconds 30 --require-recipient`, and prints replies during that wait
window. Override the window with `syn ask --wait 10 <target> <message>`. Use
`--no-require-recipient` for broadcasts or to tolerate a receiptless legacy hub;
a directed negative receipt from a current hub still exits non-zero.

A reader sees only the messages addressed to it with `--for`, which also drops
presence noise and other agents' cross-talk — a per-agent inbox. Because the
relay log is durable, an agent that was offline still catches up on its next read:

```bash
synapse relay ./feed.ndjson --for SCPN-CONTROL --cursor ./control.cursor
synapse listen --name SCPN-CONTROL --for SCPN-CONTROL    # live inbox
```

## Hub options

```bash
synapse hub --port 8876
synapse hub --port 8876 --db ./synapse.db          # crash-safe persistence
synapse hub --port 8876 --rate 5 --burst 20        # per-agent rate limiting
synapse hub --port 8876 --relay-log ./feed.ndjson  # mirror the channel to a file
synapse hub --max-clients 32 --max-msg-kb 256      # cap connections and frame size
synapse hub --max-connections-per-host 4           # cap simultaneous sockets from one host
synapse hub --max-progress-per-task 500            # cap retained board progress per task id
synapse hub --max-findings-per-agent 200           # cap durable findings admitted per agent
synapse hub --shutdown-close-timeout 5             # bound active socket close handshakes
synapse hub --tls-certfile ./hub.crt --tls-keyfile ./hub.key  # native wss://
synapse hub --host 0.0.0.0 --token-file ./tok      # token from a file, not argv (ps-safe)
synapse hub --host 0.0.0.0 --insecure-off-loopback # bind off-loopback WITHOUT a token (refused otherwise)
```

Binding a non-loopback host without a token (and, with `--metrics`, a metrics
token) is **refused** by default — the hub will not start exposed by accident;
`--insecure-off-loopback` downgrades that to a warning for a trusted private
network. `--max-connections-per-host` is a connection-count cap keyed by the
remote host; it is separate from `--host-rate`, which meters inbound frames from
that host. Native `wss://` uses `--tls-certfile` plus `--tls-keyfile`; it protects
the transport but does not replace `--token` for off-loopback binds. Supply the
token with `--token-file` or the `SYNAPSE_TOKEN`
environment variable rather than `--token`, which is visible in `ps`. The hub
drains on `SIGTERM`/`SIGINT`, so a container stop shuts it down cleanly. `synapse
health` is a liveness probe — exit `0` when the hub answers, `1` otherwise —
wired as the Docker `HEALTHCHECK`. `--shutdown-close-timeout` bounds the
WebSocket close handshake during stop; accepted mutations are durable at append
time when `--db` is enabled, not deferred to process exit:

Takeover and identity-conflict paths are logged for auditability without message
payloads: accepted takeovers, cooldown refusals, name conflicts, and name-switch
denials include the sender name, remote host, and close reason.

```bash
synapse health                       # exit 0 if the local hub is reachable
synapse health --uri ws://host:8876
```

`synapse health` prints nothing by design — it reports only through its exit code,
so an empty response is a pass, not a failure. Read `$?` (or rely on the container
healthcheck) rather than the output. When you want a human-readable account of what
is or is not wired — identity, hub exposure, waiters — run `synapse doctor` instead;
`health` answers "is the hub up?", `doctor` answers "is my setup right?".

## Selecting the hub

Every client command talks to `ws://localhost:8876` by default. To point the
whole CLI at another hub — a remote coordinator, or a second local hub on another
port — set `SYNAPSE_URI` once instead of passing `--uri` to each command:

```bash
export SYNAPSE_URI=ws://coordinator.internal:8876
synapse who            # queries the hub named by SYNAPSE_URI
synapse board          # so does every other command
```

An explicit `--uri` on a single command overrides the environment for that call,
and unsetting `SYNAPSE_URI` (or leaving it blank) restores the loopback default.
The companion `SYNAPSE_TOKEN` supplies the shared secret for a secured hub the
same way.

## Worker options

```bash
synapse worker --name FAST --provider ollama --model gemma3:4b
synapse worker --name OFFLINE --provider rule
synapse worker --name TIER --provider tiered --model small --heavy-model big
synapse worker --prefix remanentia/ --name FAST --provider rule
```

A `tiered` worker classifies each request and routes trivial requests to a cheap
rule path and hard requests to the heavy model.

`--prefix` is prepended to `--name` to form the identity the worker registers
under (here `remanentia/FAST`), so the same role can run under several projects on
one hub without a name clash. `synapse team --prefix remanentia/` namespaces a
whole team the same way; address a namespaced worker by its full identity, for
example `synapse send --target remanentia/FAST "status?"`.

## Observing

```bash
synapse listen --name USER
synapse board
synapse manifest
synapse directory --task-class chat --json
synapse route-task TASK-1 --limit 3 --event-store ./synapse.db --json
synapse resource-bids TASK-1 --resource-kind gpu --json
synapse memory-recall ./synapse.db "transport handoff" --json
synapse a2a-card --endpoint-url https://agent.example.com/a2a/v1
synapse a2a-conformance --json
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --bearer-auth --a2a-token "$A2A_TOKEN" --state-file ./a2a-state.json
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --task-timeout 300 --subscribe-timeout 1
synapse relay ./feed.ndjson --cursor ./feed.cursor
synapse compact ./synapse.db --all --max-checkpoints-per-task 3 --archive-report ./compact-report.html
synapse event-query ./synapse.db "task TASK-1 timeline"
synapse event-query ./synapse.db "conflicts at seq 120" --json
synapse event-query ./synapse.db "channel ops between seq 1 999999"
synapse event-query ./synapse.db "receipts ALICE" --json
synapse event-query ./synapse.db "universal-receipts all" --json
synapse event-query ./synapse.db 'timeline("TASK-1").'
synapse event-query ./synapse.db 'MATCH (task:TASK {id:"TASK-1"}) RETURN timeline'
synapse postmortem ./synapse.db TASK-1
synapse debug ./synapse.db --fork-at 142
synapse debug ./synapse.db --task TASK-1 --fork-at 142 --set status=blocked --json
synapse reproduce ./synapse.db TASK-1
synapse reproduce ./synapse.db TASK-1 --expect 9f2c… --json
synapse causality causes ./synapse.db 142
synapse causality effects ./synapse.db 118 --json
synapse causality counterfactual ./synapse.db 96
synapse causality contention ./synapse.db      # who should yield on overlapping live claims
synapse causality causes ./hub.db peer:96 --peer peer=./peer-hub.db   # federated, HUB:SEQ refs
synapse causality causes ./hub.db peer:96 --peer peer=./peer-hub.db --clock-skew peer=-6.4
synapse causality causes ./hub.db peer:96 --peer peer=./peer-hub.db --dot  # Graphviz, cluster per hub
synapse causality otel ./synapse.db --out spans.json                  # OpenTelemetry projection
synapse causality otel ./synapse.db --endpoint http://127.0.0.1:4318/v1/traces  # OTLP push [otel]
synapse causality otel ./synapse.db --out s.json --filter T1 --service-name hub-eu  # named tasks only
synapse causality health ./synapse.db                                 # orphaned/dangling/stale claims
synapse merkle root ./synapse.db
synapse merkle prove ./synapse.db 142 --json > proof.json
synapse merkle verify proof.json --expect 9f2c…
synapse merkle verify proof.json --json
synapse merkle keygen ~/synapse/hub-receipt.key   # receipt-signing keypair (private + PATH.pub)
synapse reliability ./synapse.db
synapse accounting record --name alpha --task TASK-1 --model claude-opus-4-8 --input-tokens 1200 --output-tokens 300
synapse accounting report ./synapse.db --pricing pricing.json --budget budget.json
synapse approval request --name dev --subject TASK-1 --reason "needs human sign-off"
synapse approval decide --name ceo --subject TASK-1 --approve --reason "ship it"
synapse approval status ./synapse.db --pending
synapse ttl-advice ./synapse.db
synapse ingest ./synapse.db --cursor ./ingest.cursor    # drain new events as JSON lines, resumable
synapse multihub observe --peer-db ./peer.db --json     # fold a peer hub's log offline
synapse multihub follow --peer-uri ws://peer:8876       # pull a peer's board over a connection
synapse multihub follow --peer-uri wss://peer:8877 --pin sha256:HEX  # pin a self-signed TLS peer
synapse supervisor --idle-seconds 300 --history-multiplier 3
```

`synapse manifest` prints live capability cards advertised by connected agents.
When a card carries capability contracts, the readable output includes the
contract count while JSON surfaces such as the MCP manifest resource, A2A Agent
Card metadata, and dashboard snapshot retain the full `contracts` entries with
`task_class`, `input_schema`, `output_schema`, `preconditions`, and
`postconditions`.

`synapse directory` builds a read-only capability directory from the live
manifest plus resource offers from the state snapshot. It supports `--agent`,
`--task-class`, `--skill`, `--resource-kind`, and `--json`. The directory is
discovery metadata only: it helps route or review work, but it does not reserve
capacity, authorize execution, or certify trust.

`synapse route-task TASK-1` fetches the board, manifest, and state snapshots and
returns advisory routing recommendations for that board task. The scorer is
local and deterministic: task-class matches rank first, skill matches and card
description overlap add explainable evidence, and cards with contracts carry a
small evidence bonus. `--event-store ./synapse.db` optionally adds observed
capability evidence from positive release-receipt assessment notes in the
durable log, preserving the source task id and event sequence for review.
`--include-zero` shows unmatched agents for diagnostics; no route recommendation
claims the task, changes `suggested_owner`, reserves capacity, grades an agent,
or certifies trust.

`synapse resource-bids TASK-1` fetches the same board, manifest, and state
snapshots, then ranks live resource offers for the board task. The scorer is
local and deterministic: requested resource kind, capacity, provider task-class
matches, skill tags, provider description overlap, resource kind/name overlap,
and matching metadata all appear as reason codes in the output. `--resource-kind`
filters offers before scoring, `--include-zero` keeps diagnostic candidates, and
`--json` prints the stable report. A resource bid is a marketplace-style
directory hint only: it does not reserve capacity, authorize execution, mutate
the board, or certify provider trust.

`synapse memory-recall DB QUERY` reads the same local SQLite event store and
returns deterministic recall hits over durable memory records. The projection
uses findings, checkpoints, and handoffs, ignores recall-query telemetry, and
keeps each hit tied to its source sequence, timestamp, event kind, source field,
task id, actor, evidence reference, score, and matched tokens. `--since-seq`
limits the read to records above a cursor, `--limit` bounds output, and `--json`
prints the stable machine-readable report. This is local event-log projection:
it does not create external embeddings, contact a service, certify truth, or
mutate hub state.

`synapse supervisor` watches the shared board and re-offers stalled plan tasks.
The fixed `--idle-seconds` threshold remains the operator ceiling. By default the
supervisor can lower the effective threshold when completed tasks on the board
show enough faster progress cadence; `--history-multiplier`,
`--min-history-samples`, and `--min-predictive-idle-seconds` tune that heuristic,
and `--no-predictive-stall` disables it. This is local board evidence, not a
claim that a worker process has failed.

`synapse compact` is an offline maintenance command for the SQLite event store
created by `synapse hub --db`. It needs either `--floor-seq <seq>` (the lowest
sequence every read-side consumer has ingested) or `--all` (only when the whole
log is settled). `--archive-report PATH` writes an owner-only static HTML report
from the pre-compaction event snapshot, then records the actual checkpoint and
finding removal counts from the compaction run. The report includes event-kind
counts, board tasks, release receipt notes, and a bounded coordination timeline;
`--archive-report-limit N` controls the row cap for bounded sections.

`synapse event-query` is a temporal event-log query command for the same SQLite
event store. It supports `task <id> timeline`, `task <id> at seq <n>`,
`task <id> at time <seconds>`, `path <path> between <start> <end>`,
`channel <id> between seq|time <start> <end>`, `conflicts at seq|time <n>`, and
`receipts <agent|target|all>` for the durable delivery-receipt ledger. Use
`universal-receipts <selector|all>` for the first-class receipt view across
claim/release evidence, delivery, sandbox-run, policy, approval, operator-relay,
cross-hub, A2A-validation, and postmortem receipt families.
Channel queries return metadata-only records so private-channel bodies are not
printed by this forensic path. Receipt queries return the requested, immediate,
deferred, and expired delivery-receipt audit events that involve the selected
participant. Universal receipt queries return the shared receipt shape with the
source event kind and real event-log sequence. It also accepts prototype aliases over the same
model: Datalog-like `timeline("TASK").`, `state("TASK", seq, 120).`,
`touches("src/auth.py", 0, 9999999999).`, `channel("ops", seq, 1, 99).`,
`receipts("AGENT").`, `universal_receipts("all").`,
`conflicts(seq, 120).`, plus
Cypher-like `MATCH (task:TASK {id:"TASK"}) RETURN timeline` and related
`AT`/`BETWEEN` forms. It is read-only forensic evidence: it reconstructs what
the event log said at a sequence or timestamp, but it does not contact the live
hub or certify that a merge is safe.

Queries read selectively rather than loading the whole store. Each query pushes
its sequence/time window and the event kinds it needs into SQLite, so a query
deserialises only candidate rows even as a dogfood event log grows large — the
result is identical to a full scan because the loaded window always contains
every event the query keeps. Memory is bounded by the query window, not the log
size: a point-in-time query (`at seq|time`) never loads events after its cutoff,
and a range query (`between`) loads only its window. `--limit N` additionally
caps the printed output to the most recent `N` records (and conflict pairs); it
bounds output, not the reconstruction, so task-state and conflict results stay
correct.

`synapse postmortem ./synapse.db TASK-1` builds a replayable postmortem from the
same event store. The Markdown or `--json` report lists the task timeline,
observed owners, release events, assessment evidence, reconstructed path-overlap
conflicts, and candidate unanswered messages that mention the task id. Candidate
unanswered messages are an audit signal only: the log proves the directed chat
and the absence of a later matching chat reply, not intent or off-channel
communication.

`synapse debug ./synapse.db --fork-at 142` forks a task's reconstructed state at
a sequence point. It folds the durable log back into the exact claim state the
task held as of that sequence — owner, status, declared paths, and the saved
resume checkpoint — then prints the resume manifest an agent would pick up if the
task were rewound there, alongside the events that really happened after the fork
point. The task is inferred from the snapshot at the sequence, or named with
`--task`; `--set FIELD=VALUE` overrides a resume field (`owner`, `note`,
`status`, `data_ref`, `worktree`, `checkpoint`) on the manifest only. It is a
read-only what-if: the hub runs no task, so nothing is executed or changed. Exit
status is `0` when the task held a live claim at the fork point, `1` when it did
not (released or never claimed — nothing to fork), and `2` on a bad argument or a
missing store.

`synapse reproduce ./synapse.db TASK-1` fingerprints a task's authoritative
history. Because hub state is a pure fold of an append-only log, a task's claim
snapshots and releases must replay to the same state on every machine; this
command canonicalises that slice and hashes it into a stable SHA-256 digest, so
two operators — or two federated hubs — holding the same history derive the same
digest. `--expect DIGEST` gates on a known-good value (exit `1` on mismatch, like
a release-receipt check); a task with no authoritative events exits `2`.

`synapse causality causes ./synapse.db 142` traces coordination causality over
the event log. It folds the durable log into a directed acyclic graph of three
recorded relations — a task's own lifecycle (claim before update before release),
a declared `depends_on` satisfied by the dependency's completion, and a release
that let a later, path-overlapping claim proceed — then answers against an event
sequence: `causes` returns the events upstream of it, `effects` the events it
enabled downstream, and `counterfactual` the downstream events whose recorded
cause traces back through it and so would lose all support if it were removed.
This is coordination causality inferred from recorded scheduling semantics, not
statistical causal discovery and not program-trace causality; every edge is
backed by a concrete event, and the counterfactual is a structural what-if over
the inferred graph, not a claim that the work would never have happened another
way. Exit status is `0` when the sequence names a coordination event, `1` when it
does not, and `2` on an unknown direction or a missing store.

`synapse causality contention ./synapse.db` takes no sequence: it finds every
pair of overlapping live claims — different owners, same worktree, path scopes
that intersect (an empty scope means the whole tree) — and weighs each contender
by what its task gates downstream: the distinct tasks causally reachable from
any of its recorded events, plus its declared dependents (transitively) that
have not completed. The contender whose task gates less is advised to yield;
on an equal count the later claim yields, so first-come precedence breaks the
tie. The advice is advisory only — no claim is preempted, nothing contacts a
live hub — and each recommendation prints with both standings and its reason
(`--json` for the machine shape). The exit code doubles as a collision signal:
`0` when no live claims overlap, `1` when at least one pair does, `2` on a
missing store.

A worked example. Alice claims `src/parser.py` for `parser-rework`, which three
declared tasks (`grammar-tests`, `error-recovery`, `docs-refresh`) depend on;
Bob later claims the whole `src/` scope for `lint-sweep`, on which only
`lint-config` depends. The scopes intersect in the same worktree, so the pair
contends — and because Alice's task gates three downstream tasks against Bob's
one, the advice names Bob's claim as the lighter one:

```console
$ synapse causality contention ./synapse.db
# Contention: 1 overlapping live claim pair(s)

## lint-sweep (bob) should yield to parser-rework (alice)
- reason: parser-rework blocks 3 downstream task(s) versus 1; the lighter claim yields
- keeps: parser-rework (alice, seq 7) blocks 3 downstream task(s): docs-refresh, error-recovery, grammar-tests
- yields: lint-sweep (bob, seq 8) blocks 1 downstream task(s): lint-config
- advisory only: no claim is preempted; coordinate the yield explicitly
$ echo $?
1
```

Had both tasks gated the same count, the later claim (Bob's, higher sequence)
would still be the one advised to yield — first-come precedence breaks ties.
Acting on the advice stays explicit: Bob releases his claim (or narrows its
paths so the scopes no longer intersect) and re-claims once Alice releases.

With `--peer HUB=PATH` (repeatable) the sequence queries trace causality
*across federated hubs*: the named hubs' logs merge in the deterministic
multi-hub order (timestamp, then hub id, then sequence — the same order the
multi-hub read side folds), every event keeps its global identity `hub:seq`,
and the three recorded relations are derived over that merged order. An edge
whose endpoints two different hubs authored is tagged `federation`, with the
recorded relation it derives from kept as its basis — a dependency completed
on one hub and claimed on another renders as `federation:dependency`. Events
are addressed as `HUB:SEQ`; a plain `SEQ` means the primary DB's hub, whose id
defaults to the DB file name and can be set with `--hub-id`. Honest scope:
within one hub, precedence is the hub's own monotonic sequence —
authoritative; across hubs there is no shared sequence, so the merged order
falls back to event timestamps, and a federation edge is clock-ordered
evidence, only as good as the hubs' clock agreement. Like the multi-hub fold,
the query observes and grants nothing.

When a live probe has measured clock skew for a hub, annotate the offline
causality report with `--clock-skew HUB=SECONDS`. Values are local-minus-peer
seconds; positive means the local clock was ahead of that hub. The default
warning threshold is 5 seconds and can be changed with `--skew-warn-seconds`.
Warnings appear as a `## Clock skew warnings` section in Markdown, a
`clock_skew` object in `--json`, and comments in `--dot`; they do not alter the
graph, they mark the timestamp evidence as outside the operator's bound.

A worked cross-hub example: `eu-hub` completes and releases
`schema-migration`; `us-hub` declares `api-rollout` depending on it and
claims it. The claim on `us-hub` traces its cause across the hub boundary to
the release on `eu-hub`:

```console
$ synapse causality causes ./eu-hub.db us-hub:2 --peer us-hub=./us-hub.db
# Federated causality (causes): us-hub:2

- Hubs: eu-hub, us-hub
- Event: us-hub:2 kind=claim task=api-rollout owner=us/codex-2b40 status=claimed
- Direct causes: 2
- Transitive: 5

## Direct causes
- [federation:dependency] eu-hub:4 kind=release — task api-rollout depends on schema-migration
- [lifecycle] us-hub:1 kind=ledger_task — ledger_task → claim

## Transitive
- eu-hub:1 kind=ledger_task task=schema-migration
- eu-hub:2 kind=claim task=schema-migration owner=eu/claude-11ab status=claimed
- eu-hub:3 kind=task_update task=schema-migration owner=eu/claude-11ab status=done
- eu-hub:4 kind=release task=schema-migration
- us-hub:1 kind=ledger_task task=api-rollout
```

With `--dot` the same federated answer renders as a Graphviz digraph: every
hub becomes a cluster, so an edge *inside* a cluster is same-hub causality
and an edge *crossing* cluster boundaries is a federation edge — drawn in
colour and labelled with its basis. The rendered edges are the query's
induced subgraph (also carried in the JSON as `edges`), so the picture shows
the whole causal neighbourhood, not just the one-hop links; the queried node
is double-bordered and a counterfactual's unsupported descendants are
dashed. Replayed against the same two stores (`dot -Tsvg` renders it):

```console
$ synapse causality causes ./eu-hub.db us-hub:2 --peer us-hub=./us-hub.db --dot
digraph federated_causality {
  ...
  subgraph cluster_0 {
    label="eu-hub";
    "eu-hub:4" [label="eu-hub:4\nrelease schema-migration", shape=box];
    ...
  }
  subgraph cluster_1 {
    label="us-hub";
    "us-hub:2" [label="us-hub:2\nclaim api-rollout", shape=box, peripheries=2];
    ...
  }
  "eu-hub:3" -> "eu-hub:4" [label="lifecycle"];
  "eu-hub:4" -> "us-hub:2" [label="federation:dependency", color=blue];
}
```

`contention` stays single-hub (`--peer` is refused there): yield advice
weighs one hub's *live* claims, and claims are never granted across hubs.
Exit codes are unchanged — `2` additionally covers a malformed `--peer`
spec, a duplicate hub id, or a reference naming an unmerged hub; `--dot`
without `--peer` is refused, and `--dot` excludes `--json`.

`synapse causality otel ./synapse.db --out spans.json` projects the whole
graph onto **OpenTelemetry spans**: one trace per task (root span covering the
task's recorded lifetime), one child span per coordination event, and — the
part that carries the causality — a span *link* on every event a recorded
`dependency` or `contention` edge enabled, pointing at the causing event's
span in the other task's trace. "This claim proceeded because that release
freed its paths" renders as a first-class link in any trace viewer. Ids are
deterministic SHA-256 derivations of the task id and event sequence, so
re-exporting the same log yields identical spans and cross-task links always
resolve. `--out FILE` writes the span records as JSON with no extra
dependency; `--endpoint URL` pushes real OTLP over HTTP to a collector's full
traces URL (typically `http://host:4318/v1/traces`) and needs the optional
extra: `pip install 'synapse-channel[otel]'`. Exactly one of the two is
required; a failed push exits `2` with the exporter's verdict rather than
pretending success, and taskless events are counted in the summary line, not
silently dropped. Replayed against a real hub log and a real OTLP collector
(`otel/opentelemetry-collector`, debug exporter):

```console
$ synapse causality otel ./synapse.db --endpoint http://127.0.0.1:4318/v1/traces
exported 5 span(s) across 1 trace(s) to http://127.0.0.1:4318/v1/traces
```

The collector's log confirms the batch (`service.name=synapse-channel`,
`spans: 5`). Timestamps are the hub's own event timestamps; the projection is
read-only and, like every causality mode, contacts no live hub.

Three flags shape the projection. `--service-name NAME` overrides the
`service.name` resource on the exported spans (default `synapse-channel`), so
several hubs exporting into one observability tenant stay distinguishable.
`--filter TASK_ID` (repeatable) projects only the named tasks' traces and
*refuses* a task the log does not record; links pointing at excluded tasks are
kept — the deterministic ids resolve against any export that carried the other
task — and the exclusions are counted in the summary, never silently truncated.
An event recording the task lifecycle's failure terminal (`failed`) projects
span status `ERROR` — as does a task root whose *final* recorded status is the
failure terminal — so failed coordination is visible at a glance in a trace
viewer; everything else stays `UNSET`, because the log records progress, not
success verdicts. Replayed against a real 333-task hub log:

```console
$ synapse causality otel ~/synapse/hub.db --out spans.json \
    --service-name workstation-hub --filter OUTPUT-INTEGRITY-REAL-SURFACE --max-nodes 0
exported 3 span(s) across 1 trace(s) to spans.json, 332 task(s) filtered out
```

`--watch` turns the one-shot export into live coordination observability:
the store is reread and the spans re-exported every `--interval` seconds
(default 2.0) until `--count` ticks ran (0 = until Ctrl-C, which stops the
watch cleanly). The deterministic ids make each re-export idempotent on the
collector side — a span received twice is stored once — so a fixed cadence
against a growing log simply fills in the new events. A failing tick stops
the watch with exit `2`, exactly as a single export fails visibly:

```console
$ synapse causality otel ~/synapse/hub.db --out spans.json --watch --count 2 --interval 1 --max-nodes 0
exported 2582 span(s) across 338 trace(s) to spans.json
exported 2582 span(s) across 338 trace(s) to spans.json
```

`synapse causality health ./synapse.db` walks each task's recorded lifecycle
in the same causal graph and flags three shapes that usually mean coordination
went wrong: **orphaned claims** (a claim is its task's last recorded event —
claimed, then silence), **dangling dependencies** (a declared `depends_on`
whose task never completed, using exactly the completion predicate the
dependency-edge derivation uses, so the two never disagree), and **stale
claims** (claimed, never released, silent longer than `--stale-after` seconds,
default 3600). Ages are measured against the log's own final timestamp — never
the wall clock — so the assessment is deterministic and replayable. Exit `1`
signals at least one anomaly, mirroring `contention`; every signal is an
operator hint derived from recorded events, not a verdict — an orphaned claim
may simply be an agent mid-work. Replayed against the real workstation hub log
(341 tasks), where it flagged 22 anomalies including a claim the session
itself was holding:

```console
$ synapse causality health ~/synapse/hub.db --max-nodes 0
# Causal health: 22 anomalies across 341 task(s)
...
## Orphaned claims (claimed, then silence)
- seq=4807 task=causality-health owner=SYNAPSE-CHANNEL/claude-2759-work silent 238s
...
## Dangling dependencies (declared, never completed)
- seq=1532 task=director-ai-calib-gateb depends on remanentia-ms1-query-stream, which never completed
```

`--since TS` bounds the scan to events with `ts >= TS` — the focus control
for a large log, mirroring the trust graph's `--since`. Honest scope: a task
whose entire recorded lifecycle predates the window is not assessed, and a
window-straddling task is judged on the window's evidence only.

With `--watch` the assessment becomes a standing monitor: every
`--interval` seconds (default 2, `--count` bounds the ticks) the store is
reread and re-assessed, the first tick prints the full report as the
baseline, and every later tick prints **only the transitions** — `+ fact`
for a new anomaly, `- fact` for a cleared one — so a steady fleet stays
quiet and the scrollback reads as a timeline of what went wrong and what
recovered. The facts carry each anomaly's identity (kind, anchoring
sequence, task, owner) and deliberately omit the ages, which grow every
tick; `--json` streams the full report as one NDJSON line per tick
instead. A failing tick stops the watch with exit `2`, and a bounded watch
exits with the last tick's anomaly signal. Replayed against the live
workstation hub log: the baseline reported 31 anomalies across 405 tasks
and the following ticks stayed silent, exit `1` — the anomalies persist.

```console
$ synapse causality health ~/synapse/hub.db --watch --count 2 --max-nodes 0
# Causal health: 31 anomalies across 405 task(s)
...
$ echo $?
1
```

`synapse merkle root ./synapse.db` commits the durable event log to a single
Merkle root: a 32-byte fingerprint of every event, so two operators — or two
federated hubs — holding the same log derive the same root and a mismatch proves
the logs differ (`--expect ROOT` gates on a trusted value, `--through SEQ` commits
only up to a sequence). `synapse merkle prove ./synapse.db 142` emits an `O(log
n)` inclusion proof — the sibling hashes that reconstruct the root from one
event's leaf — and `synapse merkle verify proof.json` checks such a proof offline
with no event store, the light-client verification a follower runs against a
trusted root (`--expect ROOT` also pins the root). The tree follows RFC 6962
(Certificate Transparency): leaves and interior nodes carry distinct
domain-separation prefixes, so a leaf hash cannot be forged as an interior node.
The commitment proves what the log contains — integrity and inclusion — not the
semantic correctness of the coordination it records. `root`/`prove` exit `2` on a
missing store and `prove` exits `1` when no event has that sequence; `verify`
exits `0` valid, `1` on a bad proof or root mismatch, `2` on an unreadable file.
`verify` reports through its exit code and a stderr line by default; pass `--json`
to get a `{"valid", "seq", "root"}` verdict on stdout instead (with a `reason` when
invalid), matching the `--json` stdout payload that `root` and `prove` already
carry.

`synapse merkle keygen PATH` creates the hub deployment's receipt-signing
keypair: an Ed25519 private key at `PATH` (owner-only `0600`, never overwritten)
and its distributable public half at `PATH.pub` — a small JSON document whose
`key_id` is a fingerprint derived from the key material. The private key signs
receipt commitments (`synapse verify-release --signing-key`); the `.pub` file is
what verifiers pass to `synapse policy-check --trusted-signing-key`, so a third
party can check which hub attested a receipt's log state without access to the
live log. `keygen` exits `2` when either file already exists.

`synapse reliability ./synapse.db` builds evidence-only reliability memory from
the same event store. It counts stale claims, declared failed-check evidence,
broken handoff candidates, and reconstructed conflict pairs per owner. The
output is audit signals, not scores: it does not rank agents, assign trust
grades, or prove intent.

`synapse trust-graph ./synapse.db` projects the same evidence as a queryable
graph: typed edges between agent and task nodes — `positive_receipt` (from
positive release receipts tied to a prior board task), `stale_claim`,
`declared_failed_check`, `broken_handoff_candidate`, and one agent-to-agent
`conflict_pair` edge per reconstructed conflict — each carrying the event-log
sequence, timestamp, and evidence fields that created it. Focus a review with
`--agent NAME`, `--task ID`, or `--since TS` (the decay window: evidence older
than the timestamp drops out of the view without being deleted from the log),
and choose the projection with `--json` or `--dot` (Graphviz; agents are
ellipses, tasks boxes, conflicts dashed undirected edges). Like `reliability`,
it is evidence with provenance, not scores. Against a store holding one
positive receipt, one degraded receipt, a stale claim, an overlapping claim
pair, and an expired handoff:

```text
$ synapse trust-graph ./synapse.db
Trust graph: evidence with event-log provenance, not scores; authorship is as recorded in the local log and is not cryptographically verified here
generated_from_seq=7 as_of=100.000
nodes=8 edges=6

alpha -[positive_receipt seq=2]-> ROUTING: release receipt: evidence=pytest tests/test_routing.py -q
alpha -[declared_failed_check seq=3]-> ROUTING: release receipt: known_failures=mypy failed; epistemic_status=degraded
beta -[stale_claim seq=4]-> STALE: lease expired at 20.000 by as_of 100.000
alpha -[conflict_pair seq=6]-> beta: OVERLAP-A@alpha overlaps OVERLAP-B@beta
gamma -[broken_handoff_candidate seq=7]-> HANDOFF-BROKEN: handoff recipient had no later task update/checkpoint/release before lease expiry 30.000
gamma -[stale_claim seq=7]-> HANDOFF-BROKEN: lease expired at 30.000 by as_of 100.000
```

Piping `--dot` into Graphviz (`synapse trust-graph ./synapse.db --dot | dot
-Tsvg -o trust.svg`) draws the same evidence for a review meeting; every edge
label keeps the `seq` pointer back to the log.

`synapse cross-repo ~/code` widens coordination from one repository to the
whole checkout tree. It scans every immediate subdirectory of the root for
dependency manifests — `pyproject.toml`, `Cargo.toml`, `package.json`,
`go.mod` — and CODEOWNERS files, and composes them into a graph: a
`dependency` edge where one repository's manifest names a package another
scanned repository declares (external dependencies create no edge), and a
`shared_owner` edge where two repositories cite the same CODEOWNERS handle. A
manifest that exists but cannot be parsed is reported as a problem, never
silently skipped (TOML manifests need Python 3.11+ or the `tomli` package).
With `--db` the live claims of a hub event log join the graph — a claim's
`worktree` is its repository — and with `--repo` the report focuses on one
repository and the exit code becomes a coordination signal: `1` when a live
claim exists in a repository connected to the focus by a dependency edge,
answering *"is anyone working right now in a repository mine depends on, or
one that depends on mine?"* before a cross-cutting change starts. Against a
tree where `consumer` declares a dependency on the package `provider`
publishes, with live claims in both:

```text
$ synapse cross-repo ./org --db ./synapse.db --repo consumer
Cross-repository dependency graph: declaration-level evidence, advisory only
root=./org repositories=3 edges=2 focus=consumer

consumer -[dependency]-> provider: consumer depends on provider-pkg (python) provided by provider
consumer -[shared_owner]-> provider: consumer and provider share owner(s) @org/platform

Live claims
provider [depends_on] PROV-1@agent-a seq=1 paths=src/thing.py
consumer [self] CONS-1@agent-b seq=2 paths=src/thing.py
```

The command exits `1` here because `provider` — a repository the focus
depends on — holds a live claim. `--json` emits the graph as data and
`--dot` a Graphviz digraph (the focus double-bordered, repositories holding
live claims labelled with their count, shared-owner edges dashed,
version-conflict edges red). Like every analysis surface it is
declaration-level, advisory evidence: it reads manifests and the log,
resolves nothing, and decides nothing.

The graph also flags declared version constraints that can never be
satisfied together. Every package two or more scanned repositories consume —
including external packages that create no dependency edge — is checked
pairwise, and a `version_conflict` edge appears when the constraints are
*provably* disjoint: no version lies in both declared ranges. When
`consumer` pins `httpx>=0.27` while `provider` pins `httpx<0.25`:

```text
$ synapse cross-repo ./org
Cross-repository dependency graph: declaration-level evidence, advisory only
root=org repositories=2 edges=3

consumer -[dependency]-> provider: consumer depends on provider-pkg (python) provided by provider
consumer -[shared_owner]-> provider: consumer and provider share owner(s) @org/platform
consumer -[version_conflict]-> provider: consumer pins httpx '>=0.27' but provider pins '<0.25' (python)
```

The comparison is deliberately conservative so a flagged conflict is always
defensible: it models PEP 440 specifier sets, Cargo requirements, and npm
semver ranges over plain numeric release versions, and anything outside
that bounded model — pre-release or epoch segments, unrecognised
operators — never claims a conflict. Direct URL references stay
uncompared with one honest exception: two references to the *same base
URL* pinned at two *hex revisions* of which neither prefixes the other are
provably two different commits and do conflict (identical revisions
overlap; a branch or tag revision is mutable and never supports a claim).
PEP 440 exclusions
(`!=`) are ignored, which can only suppress a claim, never invent one.
`go.mod` requirements are never compared: a Go requirement is a minimum
that minimal version selection reconciles, and a different major version is
a different module path. This stays declaration-level satisfiability — not
a resolver: no lockfiles, no transitive closure, no knowledge of which
versions are actually published.

`--suggest-resolution` turns each detected conflict into actionable advice:
for every provably conflicting package it intersects **all** consumers'
declared ranges (the same bounded interval model, so advice and detection
never disagree) and names which single repository's declaration is the
**odd one out** — the one whose removal leaves every other consumer a
common range, rendered so the operator sees what the rest already agree
on. When a concrete version inside that remainder is already named by one
of the remaining declarations (an inclusive `==`, `>=`, or `<=` bound),
the advice says so — a pin lifted from a manifest some consumer already
wrote, never invented, because the scanner reads manifests, not package
indexes, and cannot know what an index publishes. When no single
declaration is the outlier the constraints split into
mutually disjoint camps, and the advice says so instead of guessing;
declarations outside the bounded model are listed as unassessed, never
silently skipped. Advisory text only — nothing rewrites a manifest.
Replayed against the real GOTM checkout tree, where it named both sides of
a `cryptography` standoff and honestly refused the nine-way
`scpn-studio-platform` split:

```text
$ synapse cross-repo ~/code --suggest-resolution
...
### python cryptography
- DIRECTOR-AI declares '>=42,<49' (pyproject.toml)
- SCPN-PHASE-ORCHESTRATOR declares '>=49,<50' (pyproject.toml)
...
- ODD ONE OUT: DIRECTOR-AI ('>=42,<49') — the other declarations reconcile at >=49, <50; 49 would satisfy them all (a version SCPN-PHASE-ORCHESTRATOR already declares)
- ODD ONE OUT: SCPN-PHASE-ORCHESTRATOR ('>=49,<50') — the other declarations reconcile at >=42, <49; 42 would satisfy them all (a version DIRECTOR-AI already declares)

### python scpn-studio-platform
...
- no single declaration is the odd one out; the constraints split into mutually disjoint camps
```

Like `status`, the report can stand watch: `--watch` rescans the manifests
and rejoins the claims every `--interval` seconds (default 2, `--count`
bounds the refreshes, Ctrl-C stops cleanly). A TTY clears and redraws the
report in place; piped output appends each report behind a `---` divider,
and `--json --watch` streams one compact document per refresh (NDJSON).
The exit code reports the last refresh's `--repo` signal. `--dot` does not
combine with `--watch`.

With `--notify-cmd CMD` the watch also *tells* someone: whenever the
coordination facts — live claims joined to the graph and provable version
conflicts — change between two consecutive refreshes, CMD runs with the
delta on stdin (`+ fact` appeared, `- fact` cleared) and the scanned root
in `SYNAPSE_CROSS_REPO_ROOT`. It fires on transitions only: never on the
first refresh (the baseline) and never on a steady state, so a quiet fleet
stays quiet. The command is shlex-split and run without a shell (wrap in
`sh -c '…'` for pipes); a failing or hanging sink is reported on stderr
and never stops the watch. The sink is deliberately generic — a desktop
notifier, `synapse send` back onto the bus, or anything else — so the
scanner stays decoupled from any live hub. Validated end-to-end: a claim
seeded between ticks delivered this to a real sink script:

```text
+ claim repo-a LIVE-1@bob [self]
```

`synapse benchmark` measures the installed package on your machine and
prints a scorecard. The probes exercise real production surfaces — durable
event-store appends and a full journal replay against a temporary SQLite
file, the lite relay encoding over realistic envelopes, and `who`
request/response plus claim-to-grant round-trips over a real WebSocket
connection to an in-process hub on a loopback port. Each probe reports
throughput and p50/p95 per-operation latency, and the scorecard carries the
context that makes a number honest: package version, interpreter, CPU model
and governor, and the load averages before and after the run, plus an
explicit isolation label — a run of this command is shared-workstation
evidence for functional comparison and regression hunting, not an
isolated-core production benchmark. `--list` names the probes, `--probe`
selects a subset (repeatable), `--iterations` overrides the defaults,
`--json` emits the scorecard as data, and `--results FILE` also writes it to
disk. The committed reference numbers in [Benchmarks](benchmarks.md) come
from the deeper repository harnesses; this command is the quick scorecard
for *your* installed version.

A saved scorecard becomes a regression gate with `--compare BASELINE.json`:
the run is measured as usual, then every directional metric shared with the
baseline — throughput (`*_per_second`, higher is better) and latency
percentiles (`*_ms`, lower is better) — is checked for drift beyond
`--tolerance` (default 25%, sized for shared-workstation noise), and any
regression exits `1`. Ungated context metrics (byte ratios, rebuilt-claim
counts) never gate. A baseline recorded on a different CPU model is refused
outright — that comparison would measure hardware, not the package — while
softer drift (governor, interpreter, package version) is reported as loud
warnings. A real second run against a baseline saved minutes earlier on the
same loaded workstation:

```text
Baseline comparison (tolerance ±25%)
encode-lite/messages_per_second: 213,906.67 -> 282,884.31 (+32.2%, higher-is-better) ok
event-store-append/events_per_second: 217.89 -> 190.07 (-12.8%, higher-is-better) ok
event-store-append/p50_ms: 3.50 -> 3.50 (+0.0%, lower-is-better) ok
event-store-append/p95_ms: 10.66 -> 13.81 (+29.5%, lower-is-better) REGRESSION
1 regression beyond ±25% tolerance
```

That tail-latency trip is the isolation label speaking: on a shared
workstation p95 jumps with scheduler load, which is exactly why the default
tolerance is generous and why a CI gate should compare like against like —
record the baseline on the machine (and load profile) the gate runs on.
Under `--json` the emitted document gains a `comparison` object beside the
scorecard; `--compare` composes with `--results`, so a passing run can
become the next baseline.

Where `--compare` gates one run against one baseline, `--trend STORE.db`
watches the long arc: the finished scorecard is appended to a local SQLite
history and every stored run renders as per-metric sparkline trend lines —
first and latest values, the observed range, and the series shape — so a
slow regression no single gate trips stays visible. A change of CPU model,
governor, or package version between consecutive runs is annotated as an
explicit **context break** rather than silently connected; unlike
`--compare`, a differing CPU model is annotated, not refused, because a
history legitimately spans upgrades. The store is a plain SQLite file the
operator owns. Two real runs on the same workstation:

```text
$ synapse benchmark --probe encode-lite --trend bench-trend.db
...
Benchmark trend: 2 stored run(s)
encode-lite lite_to_raw_ratio: ▁█ 0.72 → 0.72 (min 0.72, max 0.72, 2 runs)
encode-lite messages_per_second: ▁█ 31,585.98 → 60,645.33 (min 31,585.98, max 60,645.33, 2 runs)
```

Under `--json` the document gains a `trend` object with the full stored
history and its context breaks; `--trend` composes with both `--results`
and `--compare`, and `--export-csv FILE` also writes the history as
long-format CSV (one row per stored metric value, the context columns on
every row) for spreadsheets and external monitors. For consoles and CI log viewers without UTF-8, `--ascii`
renders the same trend block in printable ASCII — the glyph ramp becomes
`._-=+*#%@` and the arrow and dash punctuation degrade to `->` and `--`
(it requires `--trend`; the stored history and the JSON document are
unchanged):

```text
$ synapse benchmark --probe encode-lite --trend bench-trend.db --ascii
...
Benchmark trend: 2 stored run(s)
encode-lite lite_to_raw_ratio: .@ 0.72 -> 0.72 (min 0.72, max 0.72, 2 runs)
encode-lite messages_per_second: .@ 31,585.98 -> 60,645.33 (min 31,585.98, max 60,645.33, 2 runs)
```

`--alert` turns that history into a **deterministic statistical gate**: the
latest value of every probe metric is measured in sigma distances from the
sample mean of its predecessors, and a value further out than
`--alert-sigma` (default 3) is a drift finding that exits `1`. Honesty
bounds the statistics the same way the rendering bounds the sparklines:
only the **trailing same-context segment** (same package version, CPU
model, and governor as the latest run — exactly the fields the context
breaks annotate) forms the population, a series with fewer than
`--alert-min-samples` same-context samples (default 5, floor 3) is
reported as *insufficient* and never silently gated, and a perfectly flat
baseline has no sigma, so any deviation from it is flagged as such.
Captured from a real run against a store seeded with a flat 100 msg/s
history:

```text
$ synapse benchmark --probe encode-lite --trend bench-trend.db --alert
...
Drift alert: 1 finding(s) across 1 gated series (5 same-context run(s), sigma 3, floor 5)
DRIFT encode-lite messages_per_second: 997,319.24 is off a flat baseline (baseline mean 100.00, std 0.00, 5 samples)
insufficient samples for encode-lite lite_to_raw_ratio: 1 of 5 same-context run(s) — not gated
$ echo $?
1
```

Under `--json` the document gains a `drift` object with the findings and
the insufficient series; `--alert` composes with `--compare` (either gate
failing exits `1`) and requires `--trend`.

`synapse accounting` records and reports opt-in model cost/token usage. Synapse
never calls a model provider and collects no telemetry, so token and cost figures
exist only when an agent or operator records them: `synapse accounting record`
posts one usage note (a `usage`-kind progress note carrying a canonical
`key=value` body) onto the shared ledger, and `synapse accounting report
./synapse.db` reads those notes back into per-agent and per-model totals. Pass
`--pricing pricing.json` (model → `{input_per_1k, output_per_1k}`) to estimate
cost from tokens, and `--budget budget.json` (agent → ceiling) for budget
evidence. Budgets are evidence, not an enforcement gate: the report states spend
against a ceiling, it does not block work. Non-Python clients can record usage by
posting the identical note body.

`synapse fleet-scorecard ./synapse.db --out fleet-scorecard.json` composes the
existing causality, accounting, contention, and reliability reports into one
portable schema. Add `--trend bench-trend.db` to include the complete benchmark
history and context breaks. The output is replaced atomically with owner-only
permissions because task identities and opt-in cost evidence can be sensitive.
No source report is weakened: accounting remains opt-in evidence, contention
remains advisory, reliability remains findings rather than scores, and benchmark
numbers remain host-dependent.

With the optional `otel` extra,
`synapse fleet-scorecard ./synapse.db --endpoint http://127.0.0.1:4318` pushes
the existing deterministic causality spans to `/v1/traces` and the scorecard
gauges to `/v1/metrics`. `--endpoint` is a collector base URL; embedded
credentials, query strings, fragments, and pre-appended signal paths are
refused. The JSON bundle carries full benchmark history; the metric plane sends
only the latest value and a same-package, same-host relative change, never a
fabricated historical backfill. The command reads local stores only; the two
collector posts are its sole network actions and are not transactional. If one
signal export fails, the command exits `2` even if the collector already accepted
the other, so rerun it after repairing the endpoint.

`synapse approval` runs a human-in-the-loop approval gate over the same ledger.
`synapse approval request --name <actor> --subject <id>` posts an
`approval`-kind note that puts the subject in `awaiting_approval`; `synapse
approval decide --subject <id> --approve|--reject [--reason ...]` records the
decision; and `synapse approval status ./synapse.db [--subject <id>] [--pending]
[--json]` replays the notes into the current decision state per subject (the
latest event wins, so a fresh request after a decision re-opens the gate). It is
advisory evidence and an audit trail, not a hard runtime gate — nothing blocks a
hub mutation. An approved subject can be cited in a release receipt via `synapse
release --approval "<id>: approved by <actor>"`.

`synapse ttl-advice ./synapse.db` builds read-only adaptive lease TTL advice from
the same event store. It derives completed-task duration samples, active
live-claim counts, and stale-claim counts, then prints an advisory default and
optional owner-specific rows when enough samples exist. The command does not
change the hub default, and explicit manual TTL values remain the control path.

`synapse auto-action` introspects the opt-in auto-action reactor — the layer that
turns the session advisor's per-round signals into automatic actions (compact a
filling context, write a log, hand a run over). The bare command previews the
*static model*: which advisory signal maps to which action, which signals
deliberately map to none (`over-budget` halts the loop; `approaching-rate-limit` is
steered by the router), and — given `--arm compact,log` or `--all` — how a
hypothetical armed set would read. It touches no files and fires nothing.

The reactor is armed in-process by an orchestration loop rather than by a
hub-side toggle, but which actions are armed is now a *durable policy* the loop can
read: `synapse auto-action arm compact,log` and `disarm log` add or remove actions,
`clear` disarms everything, and `show` renders the persisted posture. The policy is
a JSON file in the coordination home (`$SYN_HOME` or `~/synapse`, overridable with
`--store PATH`); an orchestration harness loads the same file to build its dispatch,
so the terminal and the loop agree on one persisted armed set. Persisting a policy
still fires nothing — an armed action fires at runtime only when its signal is
raised and a handler was supplied. Add `--json` to `show` or the bare command for
the machine-readable form.

`synapse multihub` reads a *peer* hub's event log rather than the local one.
`multihub observe --peer-db ./peer.db` folds a peer's log file offline into its
board and claims; `multihub follow --peer-uri ws://peer:8876` pulls the same
snapshot from a live peer over a connection (`--pin sha256:<hex>` accepts a
self-signed `wss://` peer by certificate pin). Both are read-only observations
tagged with a peer id and neither mutates the local hub. See
[Multi-hub sync](multi-hub-sync.md) for the federation and trust model.

The hub itself can run that follower standing, feeding partition detection:

```bash
synapse hub --port 8876 --hub-id syn-a \
  --namespace-owner MY-PROJECT=syn-a \
  --multihub-watch syn-b=ws://peer:8876 --multihub-watch-interval 30
```

`--namespace-owner NS=HUB_ID` (repeatable, requires `--hub-id`) declares the
single authoritative owner of each namespace, deny-by-default: an ungoverned
namespace grants nothing, a remote-owned claim is refused with the owner named.
Remote-owned claims with a configured owner route are forwarded to the owning hub;
the owner treats a retry of the same `(task_id, claimant)` as idempotent and
relays the existing lease without renewing or double-counting it. A forwarding
timeout is refused in place with `forward_error=timeout`, not hidden behind the
generic ownership refusal. `/metrics` exposes
`synapse_forwarded_claims_total`, `synapse_forwarded_claims_granted_total`,
`synapse_forwarded_claims_denied_total`, and
`synapse_forwarded_claim_timeouts_total`.
`--multihub-watch PEER=URI` (repeatable, requires `--namespace-owner`) polls
each named peer's event log on a bounded interval and folds the claims it
observes into the asserting-owners view (`--multihub-watch-pin PEER=sha256:<hex>`
pins a self-signed `wss://` watch peer's certificate, mirroring
`multihub follow --pin`; the peer must also be named by `--multihub-watch`) — a namespace a watched peer is seen
contesting resolves as *partitioned* and refuses to grant until the contest
clears. Naming a peer is the operator confirmation for the always-on outbound
connection; a failed poll keeps the last successful observation, so a link
outage never lets a contested namespace silently resume granting. Validated
live: a claim held on the watched peer flips the namespace to repeated
`partitioned` refusals, and the peer's release clears it on the next poll.

## Agent2Agent bridge

`synapse a2a-card` projects the live SYNAPSE capability manifest into an A2A
Agent Card. `synapse a2a-serve` runs the local HTTP+JSON bridge and keeps A2A at
the edge of the system; the hub remains WebSocket-native.
The bridge is an interop surface for A2A-shaped clients, not a replacement for
orchestration frameworks, coding agents, or the native SYNAPSE hub protocol.
`synapse a2a-conformance` prints the current support matrix against the A2A
1.0.0 operation model, including rows that remain externally gated.

Supported local subset:

- `GET /.well-known/agent-card.json` and `/agent-card.json` for discovery.
- `POST /message:send` to create a bridge task and forward text/data/file parts
  into SYNAPSE chat.
- `POST /message:stream` for an immediate Server-Sent Events task snapshot.
- `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}:cancel`, and
  `POST /tasks/{id}:subscribe` for bridge-local task lifecycle operations.
- `POST|GET|DELETE /tasks/{id}/pushNotificationConfigs[/config_id]` for stored
  push-notification configuration.
- `POST /rpc` for JSON-RPC 2.0 dispatch to the same operations.

Operational boundaries:

- Bearer auth is opt-in with `--bearer-auth --a2a-token "$A2A_TOKEN"` and applies
  to protected bridge routes. The public Agent Card remains public discovery.
- `--state-file` persists bridge tasks and push configs. Corrupt state files fail
  fast; non-terminal persisted tasks recover as failed on restart; failed writes
  roll back the in-memory task/config view.
- Terminal task states are immutable: cancel and late SYNAPSE replies do not
  reopen or rewrite completed, failed, canceled, or rejected tasks.
- `--task-timeout` marks open tasks failed when no correlated SYNAPSE reply arrives
  within the configured window.
- A2A task correlation uses structured SYNAPSE chat metadata (`a2aTaskId` and
  `a2aContextId`). Inline marker-looking text in a payload is preserved as reply
  text and is never trusted to select a task.
- `--subscribe-timeout` bounds one in-process subscription wait. Subscriptions
  emit bounded local replay frames for the current bridge process, then at most
  one queued update for that wait. Persisted task recovery restores task
  snapshots only; it does not rebuild durable subscription streams across
  restarts or bridge replicas.
- Caller-supplied `taskId` and `contextId` values are restricted to bridge-safe
  characters. Duplicate caller task ids are rejected.
- Webhook URLs must be HTTP(S), include a host, omit embedded credentials, and not
  target localhost, loopback, private, or link-local IP literals.

State-file durability matrix:

| Case | Behavior | Focused coverage |
| --- | --- | --- |
| Clean restart | Tasks and push configs reload from `--state-file`. | `test_task_store_persists_tasks_and_push_configs` |
| Corrupt JSON | Startup fails fast with `Invalid A2A state file`. | `test_task_store_reports_corrupt_state_file` |
| Atomic write | Writes go through an owner-only temp file, fsync the file, replace the state file, and best-effort fsync the parent directory. | `test_a2a_task_store_fsyncs_state_file_and_parent_directory` |
| Failed write | In-memory task/config changes roll back; the previous committed state file is left intact. | `test_a2a_task_store_keeps_committed_state_file_when_temp_write_fails` |
| Stale in-flight task | Persisted non-terminal tasks recover as failed on restart. | `test_state_file_recovery_fails_stale_working_tasks` |
| Push config recovery | Push configs persist, reload, list, get, delete, and roll back failed writes/deletes. | `test_a2a_task_store_push_config_get_list_delete_paths` |

Bounded local soak coverage:

| Path | Local evidence | Limit |
| --- | --- | --- |
| Network handler churn | Sixteen real localhost `POST /message:send` requests through one stdlib HTTP server persist and reload from a state file. | This is not a latency or throughput benchmark. |
| Persistence churn | The same run exercises repeated fsynced state writes under a fixed task cap. | It does not simulate power loss or filesystem faults beyond focused write-failure tests. |
| Webhook failure pressure | Twelve task completions continue while configured webhook deliveries raise timeout errors. | It uses an injected failing deliverer, not a remote receiver. |
| Subscriber fanout | Twelve concurrent subscribers receive the terminal update and the bridge clears subscriber queues. | It is bounded local thread pressure, not multi-process soak. |

Unsupported or externally gated:

- Independent evidence includes an official `a2a-sdk==1.1.0`
  discovery/send/get/list/cancel lifecycle and an official A2A TCK HTTP+JSON
  MUST run (55 passed, 5 structured-response failures, 175 skipped). This is
  partial validation, not certification or full conformance.
- Structured artifact/direct Message scenarios, an outbound external-server
  pass, remote public webhook and proxy/TLS receipts, durable replay, and
  operator deployment sign-off remain open.
- `synapse a2a-conformance` is the live local matrix for those supported,
  partial, unsupported, and external rows.
- Exposed A2A bridge deployments should follow the
  [A2A deployment threat model](a2a-deployment-threat-model.md).
- The bridge does not make SYNAPSE itself an A2A-native hub; it is a separate edge
  process translating between A2A-shaped HTTP operations and SYNAPSE chat/tasks.
- Subscription replay is local process memory. It is not a durable event log shared
  across bridge restarts or multiple bridge replicas, and terminal recovered
  tasks reject subscription with a problem response.

## Managing the task plan

`synapse task` lets a human drive the shared blackboard from the command line —
the persistent plan, not the live leases (claiming/holding a lease belongs to a
running agent, since a lease is released when its holder disconnects):

```bash
synapse task declare BUILD --title "Compile the package"
synapse task declare TEST --title "Run the suite" --depends-on BUILD
synapse board                                  # BUILD ready, TEST blocked on it
synapse task update BUILD --status done        # TEST now unblocks
synapse task progress TEST "started" --kind note
syn ack TEST --evidence "pytest tests/test_feature.py -q" --artifact coverage.xml
```

The hub bounds the in-memory blackboard and memory-admission surfaces with
operator-set limits: `--max-progress` for the total retained progress notes,
`--max-progress-per-author` for one author, `--max-progress-per-task` for one
task id, and `--max-findings-per-agent` for durable findings admitted by one
agent. These limits apply on live writes and on `--db` replay; the append-only
event log still retains accepted events until `synapse compact` removes safe
history.

`--board-task-cap N` bounds the tasks served per board snapshot — on a
long-running fleet the full board eventually outgrows a websocket frame
(observed around a thousand tasks). Under a cap every live task is kept
ahead of any terminal one, the newest `updated_at` wins inside each class
when trimming, and the reply carries `total_tasks` and `truncated` so a
consumer sees the bound instead of mistaking the page for the whole plan;
the `ready` id list always stays complete, because ids are cheap and the
task bodies are what outgrow the frame. The default serves the full board
unchanged, and the ledger itself is never trimmed — the cap bounds one
reply, not the plan.

`syn ack <task>` is the ergonomic closeout path for a completed board task. It
requires at least one `--evidence` or `--artifact` value, writes those values as an
`assessment` progress note from the resolved `syn` identity, waits for the hub's
progress confirmation, marks the task `done`, and waits for the task-update
confirmation before printing success. Use repeated flags when a task has several
proof points:

```bash
syn ack TEST \
  --evidence "pytest tests/test_feature.py -q" \
  --evidence "mypy src/synapse_channel/feature.py" \
  --artifact coverage.xml \
  --note "ready for release"
```

## Setup and terminal integration

These commands wire a machine and its terminals into the bus once, so agents
arm themselves and coding tools claim work without per-session setup. `init`
prints or installs the local user services; `install-shell-hook` adds an
auto-arming block to your shell rc (idempotent — re-running it replaces the
guarded block rather than appending); `shell-hook` prints that block for manual
sourcing; `arm` keeps a single waiter re-armed; and `adapters` wires detected
coding tools to the claim-aware hooks.

```bash
synapse init --project my-repo                          # print the hub/waiter/presence user units
synapse init --project my-repo --install-user-services  # install them under systemd --user
synapse arm install --identity my-repo/agent --start    # install/start only one permanent waiter (Linux)
synapse install-shell-hook                              # auto-arm Bash, Zsh, and Fish on new terminals
synapse install-shell-hook --shell fish                 # only the Fish integration
synapse shell-hook                                      # print the block instead of installing it
synapse completions bash > ~/.local/share/bash-completion/completions/synapse   # tab completion
synapse completions fish > ~/.config/fish/completions/synapse.fish              # (re-run after upgrades)
synapse arm --name my-repo --for "my-repo,my-repo/*"    # keep a waiter armed, re-arming after each wake
synapse arm --name my-repo --owner-pid $$               # leash the waiter: disarm when this shell exits
synapse worker-session --identity my-repo -- codex      # run a provider CLI with SYN_PROJECT/SYN_IDENTITY set
synapse adapters list                                   # detect coding tools and report adapter status
synapse adapters install --project my-repo              # write the claim-aware adapter into each tool
```

`arm install` requires an explicit identity and never silently uses ambient
`SYN_IDENTITY` for a background service. Add `--uri URI` for a remote hub and
`--token-file PATH` for a secured hub; raw `--token` and ambient
`SYNAPSE_TOKEN` are refused so a secret cannot be embedded in the unit. Without
`--start`, the command writes the template and prints exact systemctl follow-up
commands. It supports Linux systemd user services; native Windows installation
is not claimed, so use WSL with systemd enabled.

## Governance and integrity

Governance commands are advisory by default — they report, they do not block —
so a rollout can observe before it enforces. `identity audit` inventories
declared identities for enforcement-rollout blockers; `acl shadow` evaluates
candidate accesses deny-by-default without denying anything live; `policy-check`
scores a release receipt against a policy (`--enforce` to gate); and `federation`
manages operator-confirmed peer-domain bundles for cross-hub trust.

The federation exchange pair moves bundle *bytes* over the wire while the *trust
decision* stays out-of-band, the SSH-known-hosts ceremony: the offering operator
authors their domain's bundle material, serves it with
`synapse hub --federation-offer FILE`, and reads the fingerprint block
(`synapse federation offer FILE`); the fetching operator pulls it
(`synapse federation fetch`), sees the identical block, compares the bundle
fingerprint over an independent channel, and only then imports explicitly. A
fetch never imports, and the whole-bundle fingerprint changes when *any* policy
content is altered in path — namespaces and scope grants as much as keys and
pins.

Trust material is a lifecycle, not a one-off ceremony: `federation list`
shows each peering's age since its confirmed import and renders a peering
whose bundle expiry has passed as `[expired]`; with `--max-age DAYS` an
active peering imported longer ago than the threshold is flagged
`[stale: …]` and the command exits `1`, so a scheduled job can hold the
fleet to a re-ceremony cadence. The same policy applies at the cheapest
moment with `federation import --max-age DAYS`, which warns (the import
still succeeds — the operator is confirming it explicitly) when the
incoming bundle never expires or expires further out than the threshold.

`federation rotate` keeps a domain's **own** bundle fresh on the other side of
that lifecycle. It pushes the expiry to `--lifetime-days` out, unions any
`--add-signing-key`/`--add-pin` material with the existing sets — the grace
window: an old key stays valid until a later `--retire-signing-key`/`--retire-pin`
drops it, so a peer that has not re-fetched keeps verifying — and rewrites the
bundle in place, saving the prior one as `<bundle>.prev` (or `--backup PATH`).
Retiring material the bundle does not hold, or a non-positive lifetime, is refused
before anything is written. The rotation changes the fingerprint, so it is followed
by the same out-of-band ceremony before peers re-import. It mints no keys of its
own: the `--add-*` values are ids the domain generated and enrolled through its
existing signing-key and certificate tooling.

`federation relay` performs a governed operator action **on** a peer hub over the
same federation transport, rather than managing peerings. The first action is
`release` — an operator whose domain is granted the `release` verb in a namespace
the peer owns force-releases a stuck lease held on that peer, without a shell on
it. The peer authorises the relay deny-by-default (mutual TLS + the peering's
scope + it must own the namespace) and audits it with the verified peer, the
asserting operator, and the previous holder, so a lease revoked across hubs stays
attributable. `--local-id` must match a serving grant on the peer, `--operator`
records who asked (default: the OS user), and the exit code is `0` when the peer
applied the action, `1` when it refused it or there was nothing to release, `2`
when the relay never reached the peer — the fail-closed case — and `3` when the
peer recorded the relay pending a second operator's approval (see two-person
approval below). Only registered actions relay: an unknown action is refused,
never smuggled through the wire.

`--reason` records why the action was relayed, in the audit on both hubs; `--break-glass`
tags it a distinct emergency override. A hub started with reason-required receipts refuses
a relay that carries no reason, so a team or production deployment can hold every governed
cross-hub action to an auditable why.

A hub can also require **two-person approval**: an authorised relay is not applied on its
own but recorded pending, and carried out only when a second, *different* operator relays
the same action (same namespace and task). The first relay returns exit `3` and a "pending"
verdict; the same operator repeating it stays pending (no self-approval); a second operator
completes the quorum and the peer applies it. Both the pending request and the approval are
audited, so a governed cross-hub release under this policy names two distinct operators in
the log. The policy is a hub setting (`require_two_person_relay`), off by default; break-glass
does not bypass it — an emergency still needs a second operator.

The relay can also go **through** the operator's own hub instead of straight to the
peer: point `--peer` at your local hub, and if that hub is configured with a relay
route to the namespace's owner (a hub started with a relay-peer map, the operator-relay
counterpart of the claim-forwarding routes), it forwards the action to the owner on
your behalf and relays the verdict back — so the operator never needs the owning hub's
credentials, exactly as a claim routes through the claimant's hub. The originating hub
records an **outbound** audit event naming the requester and the destination owner, and
the owning hub records the **inbound** one when it applies the action, so a force-release
routed across hubs is attributable on **both** ends. A relay for a namespace the local
hub neither owns nor has a route to is refused fail-closed, never silently dropped.

```bash
synapse identity audit --identities ./identities.json          # audit declared identities for blockers
synapse identity audit --identities ./identities.json --json
synapse acl shadow --policy ./acl.json --requests ./requests.json   # non-blocking deny-by-default evaluation
synapse policy-check --policy ./policy.json --receipt-json ./receipt.json   # advisory; --enforce to gate
synapse federation offer ./my-domain.json                      # validate own material; print fingerprints
synapse hub --port 8876 --federation-offer ./my-domain.json    # serve it to peer operators (token-gated)
synapse federation rotate ./my-domain.json --lifetime-days 90 --add-signing-key ed25519:new  # fresh expiry + a new key kept alongside the old for a grace window; backs up the prior bundle
synapse federation rotate ./my-domain.json --retire-signing-key ed25519:old  # after the grace window, drop the superseded key
synapse federation fetch ws://peer-hub:8876 --out ./peer-domain.json  # pull + fingerprints; NEVER imports
synapse federation fetch wss://peer-hub:8876 --pin sha256:<hex> --out ./peer-domain.json
synapse federation import ./peer-domain.json --confirmed-by ceo --source ws://peer-hub:8876  # after comparing
synapse federation list --store ./federation.json              # imported peer domains, provenance, and age
synapse federation list --store ./federation.json --max-age 90 # flag active peerings imported >90 days ago; exit 1
synapse federation revoke example.org --store ./federation.json
synapse federation relay release --peer ws://peer-hub:8876 --namespace TEAM-X --task build-7  # force-release a stuck lease on a peer hub
synapse federation relay release --peer ws://peer-hub:8876 --namespace TEAM-X --task build-7 --reason "wedged by a crashed agent" --break-glass  # with an auditable reason, tagged break-glass
synapse encrypt-key generate ./synapse.key                     # write a fresh owner-only 32-byte key file
synapse encrypt-key generate --from-passphrase ./synapse.key   # derive the key from a prompted passphrase (scrypt) instead of random bytes
synapse encrypt-key generate --from-passphrase --scrypt-n 65536 ./synapse.key  # tune the scrypt cost (n a power of two; also --scrypt-r/--scrypt-p)
synapse encrypt-key generate-wrapped ./synapse.wrapped.key     # envelope-encrypted key whose passphrase can be rotated later
synapse encrypt-key rewrap ./synapse.wrapped.key               # rotate that passphrase without re-encrypting any data
synapse encrypt-key generate-wrapped-pkcs11 --token-label synapse ./synapse.hsm.key  # wrap the key on a PKCS#11 token (YubiKey/HSM)
synapse encrypt-key generate-wrapped-tpm2 ./synapse.tpm.key    # wrap the key with a TPM 2.0 device (RSA-OAEP)
synapse encrypt-key check ./synapse.key                        # verify its ownership, mode, and length
```

`--from-passphrase` derives the 32-byte key from a passphrase (prompted twice)
with scrypt, whose cost is tunable via `--scrypt-n` (a power of two), `--scrypt-r`,
and `--scrypt-p` for a security/performance trade-off. A fresh random salt is used
per derivation and discarded — the written file is a normal key of record that
must be protected exactly like a random one, and the passphrase alone cannot
reconstruct it. Prefer the default random key unless a passphrase source is
specifically wanted.

`generate-wrapped` writes a different kind of key file — **envelope encryption**: a
random data key does the bulk AES-GCM, and a key-encryption key derived from the
prompted passphrase wraps it with RFC 3394 AES-KW. The salt is kept, so `rewrap`
can rotate the passphrase (unwrap with the old, wrap with the new) **without
re-encrypting any data** — the data key underneath is unchanged, so ciphertext
sealed before the rotation still decrypts. This is the model a hardware key store
(TPM, YubiKey, cloud HSM) plugs into: only the key-encryption key moves into
hardware, the wrapped-file format and the data key stay the same. Both commands
take the same `--scrypt-*` cost flags.

`generate-wrapped-pkcs11` is that hardware step. It wraps the random data key with a
key-encryption key held on a **PKCS#11 token** — a YubiKey PIV, a cloud or network
HSM, or SoftHSM for testing — via RFC 3394 AES key wrap on the device, so the token
key never leaves the hardware. Point it at the token with `--pkcs11-module` (or the
`PKCS11_MODULE` environment variable) and `--token-label`; the key-encryption key is
generated on the token on first use (or `--no-create-kek` to require a pre-provisioned
one), and the PIN comes from `PKCS11_PIN` or an interactive prompt. Needs the optional
`python-pkcs11` dependency (`pip install synapse-channel[pkcs11]`). The written file
records only the token and key labels — never the PIN or the module path — so loading
it at startup re-opens the token to unwrap the data key.

`generate-wrapped-tpm2` is the same hardware step rooted in a **TPM 2.0** device. A
decrypt-only RSA-2048 key-encryption key is derived from the TPM's storage seed and a
fixed template — the identical key every time, so nothing needs to be persisted as a
handle — and wraps the random data key with RSA-OAEP; the RSA private key is generated
inside the TPM and never leaves it. Point it at the device with `--tcti` (or the
`TPM2_TCTI` environment variable), defaulting to the in-kernel resource manager
`device:/dev/tpmrm0`; a software TPM such as swtpm (`swtpm:host=127.0.0.1,port=2321`)
serves for testing. Needs the optional `tpm2-pytss` dependency (`pip install
synapse-channel[tpm2]`). The written file records only the template version — no device
path — so loading it re-derives the same key inside the TPM to unwrap the data key.
Clearing the TPM hierarchy destroys the key-encryption key and, with it, access to the
data key, which is the intended way to make the store unrecoverable.

## Experimental surfaces

Newer, advisory surfaces whose shape may still change before 1.0. `sandbox`
validates a capability manifest and pre-flights or runs a `.wasm` tool against
it (running needs the `wasm` extra); `workflow` validates a declarative workflow
and compiles it into the blackboard tasks the board would execute —
step-level `requires` predicates (`receipt`, `tests`, `policy`, `approval`,
`sandbox_run`, `mailbox`, `dead_letters`, or `claim`) hold a task until a
`--evidence` snapshot proves the required values, so a workflow can route on
attested evidence instead of status alone;
`workflow contention FILE DB` additionally joins the compiled task ids to the
durable log, running the same offline yield-advice analysis as
`synapse causality contention` but keeping only the overlapping live-claim
pairs a workflow task is party to (whether it keeps or yields); pairs outside
the workflow are counted in a trailing note instead of shown, the exit code
signals scoped collisions only (`0` none, `1` at least one, `2` on an invalid
workflow, missing store, or the `--max-nodes` ceiling); `participant`
is the operator surface over the Participant Fabric — `list` probes each
registered provider driver (claude, codex, kimi, ollama, ollama-api, grok)
without taking a turn, and `ask` runs exactly one turn against one provider and
prints the answer, or the full typed turn result with `--json`. Grok turns stay
refused while `GROK_SCHEMA_VERIFIED` is false (stream schema not yet captured from a real
binary on this host; prior June 2026 CLI reliability issues are no longer observed).
`--model`
is required for `ollama` and `ollama-api` (their drivers configure no default).

The deliberation subcommands drive the Fabric's multi-party layers from the same
surface, naming each seat as `PROVIDER[:MODEL]` (the model part may itself hold
colons, so `ollama:gemma3:1b` works). `exchange` runs an opener turn and then a
reactor turn that sees the opener's result only as fenced peer data; `convene`
fans a question out to a whole panel, runs the mode's cross-critique rounds
(`--mode` defaults to `auto`, choosing colloquy, roundtable, or symposium from
the panel size and whether `--moderator` was given), and in a symposium ends
with the moderator's synthesis. A repeated provider gets numbered seats
(`participant/claude`, `participant/claude-2`, …). Both print each turn as it is
produced, or the full typed transcript with `--json`, and exit `0` only when
every turn answered and the run completed — an unavailable seat, a degraded
turn, or a `--budget-usd` halt exits `1`; a refused configuration exits `2`.

`convene --dry-run` prints the plan without taking a single turn: the resolved
mode, its round count, and each seat's identity, readiness (health probes run —
they never cost a turn), planned turns, and estimated cost. Costs come from an
operator-supplied `--pricing` table (the same `model -> {input_per_1k,
output_per_1k}` JSON as `accounting report`) under printed per-turn token
assumptions (`--est-input-tokens`/`--est-output-tokens`, default 1000/500);
seats whose model has no price line are reported unpriced and excluded from the
total rather than counted as free. With `--budget-usd` the report states
whether the estimate fits. Exit `0` when every seat is ready, `1` when any is
unavailable — so the dry run doubles as a pre-flight gate — and `2` for a
refused configuration or an unreadable pricing file.

`costs` reads opt-in session telemetry back from a hub SQLite event store —
offline, no hub connection, like `accounting report`. Sessions that emitted
`session_metric` progress notes (the orchestration loop with `emit_metrics`, or
any caller of the emit helper) appear as their latest cumulative snapshot per
`(agent, session)`: turns, errors, abstentions, token pressure, metered spend,
mean latency, and the highest rate-limit utilisation seen, plus fleet totals.
When the emitting session names the coordination task it was advancing (a claim
or board `task_id`), that id rides each snapshot too, so a row can be read
against the work it was doing, not only its session.
Where `accounting report` answers what models cost, `participant costs` answers
how participant sessions are going and what they spent; both are descriptive
evidence, never an enforcement gate. Exits `0` for any produced report (even an
empty one) and `2` when the store is missing.

```bash
synapse sandbox validate ./manifest.json                # validate a capability manifest
synapse sandbox test ./tool.wasm --manifest ./manifest.json    # pre-flight without running
synapse sandbox run ./tool.wasm --manifest ./manifest.json --input ./in.json
synapse sandbox run ./tool.wasm --manifest ./manifest.json --approve --attest ~/synapse/audit.db  # run + record an audit attestation
synapse workflow validate ./workflow.json               # parse and validate
synapse workflow compile ./workflow.json --json         # compile into blackboard tasks
synapse workflow plan ./workflow.json --evidence ./evidence.json  # hold proof-carrying steps
synapse workflow contention ./workflow.json ~/synapse/hub.db   # yield advice scoped to this workflow
synapse participant list                                # readiness of every provider driver
synapse participant ask ollama "summarise this diff" --model llama3   # one turn, print the answer
synapse participant ask claude "review src/foo.py" --context "be terse" --json
synapse participant exchange "is this design sound?" claude codex   # opener + reviewing reactor
synapse participant convene "how should we cache this?" claude codex ollama:gemma3:1b \
    --mode symposium --moderator claude --budget-usd 2.50          # panel + moderated synthesis
synapse participant convene "…" claude codex --dry-run --pricing ./prices.json \
    --budget-usd 2.50                                   # plan + cost estimate, no turns taken
synapse participant costs ~/synapse/hub.db              # per-session spend and telemetry + totals
synapse participant costs ~/synapse/hub.db --json       # the same report, machine-readable
```

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
`manifest`, `release`, `a2a-card`, `a2a-serve`, and `task`.

Run any command with `--help` for its full set of options.
