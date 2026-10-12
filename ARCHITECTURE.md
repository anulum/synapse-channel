<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — architecture
-->

# Architecture

SYNAPSE CHANNEL is a local-first coordination bus for several agents working in
parallel on one codebase or one ecosystem of repositories. A single WebSocket
hub is the authoritative source of truth for presence, work claims, chat, task
status, the shared plan, agent capabilities, and resource offers, so concurrent
workers can see conflicting or duplicate declared work. Around that core the package has
grown deliberate layers — durable messaging, identity and ownership, federation
between hubs, interop bridges, and operator surfaces — each opt-in where it can
be, and each degrading with a stated warning when its optional dependency is
absent.

## Design principles

- **One authoritative hub per deployment.** All *authority* — who owns a name,
  who holds a claim, what state a task is in — lives on one single-writer
  instance, which keeps the routing logic deterministic and unit-testable.
  Multi-hub federation observes and merges append-only logs; it never
  arbitrates authority (see "Federation and multi-hub").
- **Core transport-light; extras arm features.** The core runtime dependency is
  `websockets`; everything else on the core paths is the standard library.
  Heavier features are gated behind optional extras (`encryption`, `sqlcipher`,
  `mcp`, `otel`, `wasm`, `tpm2`), and a missing extra degrades that feature
  with a stated warning instead of crashing an unrelated path.
- **Local-first, proportionate security.** The default posture is one operator
  on loopback. A bind off loopback without a connect token refuses to start
  (`guard_exposure` raises `InsecureBindError`); accepting that risk is an
  explicit operator flag. TLS, per-message authentication, signed events, ACL
  enforcement, and paranoid mode are opt-in layers bound to need.
- **A name has one owner.** Enforced by default, not opt-in: an ambient
  environment variable is never a silent identity source, a hub-side ownership
  lease survives reconnects, and installations with the `encryption` extra pin
  a zero-config machine key on first use (see "Identity and ownership").
- **Correctness before features.** Two layers are non-negotiable: durability (a
  hub restart must not wipe live leases) and reconnect-safety (a reconnected
  agent must not double-apply a claim, and a directed message is not consumed
  by a receiver that will never surface it).
- **Single responsibility per module.** The package is composed of a few
  hundred small modules, each owning one concern and carrying its own test
  surface.

## Module families

The package is `src/synapse_channel/`. The table names each family's
representative modules, not every file.

| Family | Representative modules | Responsibility |
| --- | --- | --- |
| Coordination state | `core/state*`, `core/scoping`, `core/lifecycle`, `core/deadlock`, `core/ledger`, `core/capability*`, `core/channels` | Transport-agnostic presence, scoped task-claim leases with epochs and checkpoints, typed task lifecycle, wait-for cycle refusal, the shared plan, capability cards, private channels. |
| Hub composition | `core/hub`, `core/handlers/*`, `core/hub_connection`, `core/hub_ingress`, `core/hub_frame_pipeline`, `core/hub_frame_registration`, `core/hub_frame_gates`, `core/hub_exposure`, `core/hub_http`, `core/hub_identity_gate` | The routing facade with explicit frame admission, registration effects, ordered gates and per-verb dispatch. |
| Client | `client/agent` with its dispatch and outbound mixins, `client/launcher`, `client/llm_worker`, `client/supervisor` | The reusable async agent and its coordination verbs, one-command startup, an on-channel LLM worker, an LLM-free watcher that re-offers stalled tasks. |
| Identity and ownership | `core/auth`, `core/message_auth`, `core/identity_keys`, `machine_identity`, `core/identity_pins`, `core/name_ownership`, `core/acl*`, `core/policy_engine`, `core/trust_graph` | Shared-token admission, per-message Ed25519 authentication, the ownership lease, trust-on-first-use key pinning, deny-by-default ACL evaluation with opt-in enforcement, cross-agent trust edges. |
| Durability and storage | `core/persistence`, `core/journal`, `core/idempotency`, `core/compaction`, `core/at_rest*`, `core/persistence_sqlcipher`, `core/merkle`, `core/receipts`, `core/universal_receipts` | The append-only SQLite event store (WAL), journal replay on restart, idempotent re-applied mutations, log compaction, at-rest payload encryption (file key, escrow, TPM 2.0, PKCS#11, cloud-HSM attestation), whole-database SQLCipher, Merkle audit paths, signed receipts. |
| Messaging reliability | `ack`, `mailbox_cursor`, `core/delivery_receipts`, `core/pending_receipts`, `core/dead_letters`, `core/dead_letter_*` | Directed-message acknowledgement, durable mailbox replay across reconnects, delivery receipts, dead-letter capture with escalation and forwarding. |
| Federation and multi-hub | `core/multihub_merge`, `core/multihub_fold`, `core/multihub_follower`, `core/multihub_watch`, `core/federation*`, `core/causality*` | Read-only followers over the event-store seam, a conflict-free grow-only union of several hubs' logs, deny-by-default federation policy composed with mTLS, causality queries over the merged order. |
| Interop and surfaces | `cli_*` (the `synapse` command), `a2a_*`, `mcp/`, `dashboard_*`, `agent_tmux`, `codex_tmux`, `git/`, `cli_arm*` | Some ninety CLI verb modules, the Agent-to-Agent HTTP bridge, the MCP server, the operator dashboard and cockpit, tmux drivers for terminal fleets, git claim helpers, the permanent systemd waiter. |
| Workers and routing | `client/chat_backends`, `client/routing`, `core/semantic_routing`, `participants/`, `core/workflow*` | Pluggable reply backends, task-class routing to tiered backends, multi-participant deliberation, declarative workflows. |
| Observability and operations | `core/metrics`, `observability_textfile`, `otel_export`, `core/postmortem`, `core/dark_seat`, `core/stall`, `reap`, `update_check`, `benchmark/` | Prometheus metrics, node-exporter textfiles, OpenTelemetry export, post-mortem bundles, dark-seat alerts, stalled-task detection, committed benchmarks. |

### HTTP and terminal boundary owners

`a2a_http.build_a2a_handler` binds an independent bridge to each server.
`a2a_http_handler` owns bounded body reads, response encoding and request
admission; `a2a_http_routes` retains the ordered discovery, task, message and
push-configuration routes. Capacity and accepted-socket deadlines remain with
the HTTP server.

Both HTTP servers share `prompt_bind_http`, which binds without a reverse name
lookup and ends each connection in order. `lingering_close` keeps a connection
whose request was not read to its end open just long enough to discard the
rest, within fixed bounds of time, bytes and count, so that the client keeps
the answer it was sent. One thread per server does this; no request thread and
no admission slot is held.

The dashboard handler retains host/access checks, operator writes and feed
routing. `dashboard_http_pages` prepares shells and snapshot pages;
`dashboard_http_live` emits bounded NDJSON frames and feed cursors;
`dashboard_handler_binding` creates each server's snapshot gate, caches and
host monitor. Snapshot collection remains late-bound to the facade.

`agent_tmux.inject_wake` binds the exact pane probes and registry writer to
`pane_wake`. Binding and idle checks precede mutation; a successful paste is
recorded before the second safety probe and observed submission. A failed paste
retains a pending, unstaged wake. An uncertain staged wake is never pasted again.
These Python ownership boundaries retain the existing HTTP, CLI and client
contracts; they add no vendor control commands or configuration authority.

## Coordination model

### Frame admission and serving lifetime

`SynapseHub.handle_message` coordinates envelope admission, connection
registration, safe logging and the ordered gate runner. Envelope decoding
refuses malformed JSON and non-object frames before host charging; object
frames, including heartbeats, consume host quota before their routing fields
are inspected. Registration verifies credentials and identity before removing
the token, resolving name ownership and registering delivery capabilities.
Typed callback records retain the owning hub's existing effects without giving
the registration module a back-reference to the hub.

After registration, gates run in this order: sender quota, per-message
authentication, journal recovery, federation, local ACL, namespace ownership
and operator-relay routing. A refusal stops the frame. An authorised
cross-domain frame is dispatched at the federation gate and ends the pipeline
before local gates; local frames reach duplicate-aware handler dispatch only
after every gate permits them. Hook lookup stays deferred until each gate runs.

Serving preserves separate startup checks, socket binding, readiness publication
and background-task ownership. Every started task is retained before another
is started. Shutdown joins those tasks before anchoring and closing the hub's
checkpoint, then clears its bound address and readiness. The supplied journal
and other caller-owned stores retain their existing ownership.

### Handler capabilities

`core.handler_context.HandlerContext` declares the state, journal and transport
capabilities shared by handlers. Each family declares the additional
collaborators it consumes, including capabilities needed by called handlers or
forwarding helpers. References are read-only where a handler does not replace
them; leasing retains its mutable wait graph and candidate-state contract.

These are internal static protocols defined under `TYPE_CHECKING`, without
runtime wrappers or new public exports. The dispatch registry retains a
concrete `SynapseHub` argument, so strict type checking proves that the hub
satisfies every registered family's requirements. Transport and mutation
methods remain late-bound on the hub for each invocation; overrides made after
composition still apply. The wire, CLI and public Python API are unchanged.

The pieces compose into one coordination plane:

1. **Plan.** Any agent declares work on the shared blackboard (`ledger`); the hub
   refuses dependency cycles, so the set of *ready* tasks is well-defined.
2. **Claim.** An agent leases a task by id, optionally declaring a file scope
   (`worktree` + `paths`); the hub refuses a claim whose files overlap another
   agent's live claim. Leases expire and carry an epoch.
3. **Work.** The owner updates status through a typed lifecycle and may save a
   durable checkpoint so the task can resume after a lease lapse.
4. **Hand off or recover.** The owner can hand a task to another online agent
   atomically (no release/re-claim race), and a supervisor re-offers tasks that
   stall; a re-claimed task resumes from its last checkpoint.
5. **Message.** Directed messages are journalled with a durable sequence; a
   mailbox client acknowledges what it accepts, replays what it missed while
   offline, and an unacknowledged message dead-letters rather than vanishing.
6. **Route.** Workers advertise capability cards; a request is classified into a
   task class and routed to the matching backend, reserving heavy models for the
   hard requests.

## Identity and ownership

Since 0.99, three properties are enforced by default so that a name has exactly
one owner — for the zero-config user, not only for hardened deployments:

1. **Ambient environment is never a silent identity source.** `SYN_IDENTITY` is
   honoured only when `SYN_PROJECT` is also set and agrees with its project
   segment — the pair a shell hook exports together is the operator opt-in. A
   lone or disagreeing `SYN_IDENTITY` (the borrowed-shell signature) is dropped
   and recorded as ignored; the command proceeds as the local identity and says
   so on stderr, or refuses when the local fallback also looks accidental.
2. **Hub-authoritative ownership lease.** A registration that declares
   `lease: true` on a free name is granted an opaque `owner_lease` token (the
   hub stores only its SHA-256 digest). While the lease is live — its holder
   connected, or offline for less than `--lease-offline-ttl` seconds — any
   claim on the name must present the token or it is refused with close code
   `4016` (name owned); a takeover flag does not override it.
3. **Trust on first use.** With the `encryption` extra installed, a client
   auto-provisions an Ed25519 machine key under the XDG data directory and
   proves it on connect; the hub pins name→key on first use and refuses a later
   mismatch with close code `4013` (identity pin mismatch) and a stated
   recovery path. A core-only installation warns once and retains the unsigned
   compatibility path.

Beyond that default posture, a deployment can opt into strict identity binding
with pre-provisioned keys, per-message authentication, signed events over mTLS,
and deny-by-default ACL enforcement (`--require-acl`). See
[`docs/identity-and-acl.md`](docs/identity-and-acl.md),
[`docs/per-message-authentication.md`](docs/per-message-authentication.md), and
[`docs/signed-events-mtls.md`](docs/signed-events-mtls.md).

## Durability

With `--db`, the hub is backed by an append-only SQLite event log in WAL mode.
Every claim, release, task update, handoff, checkpoint, resource offer, ledger
change, and chat message is recorded, and the hub rebuilds its state by replaying
the log on start-up. The guarantee is split honestly by workload: the lease/claim
path commits at `synchronous=FULL` (durable across an OS crash); the high-volume
chat and progress path commits at `synchronous=NORMAL` (durable across an
application crash). At-rest payload encryption and whole-database SQLCipher are
optional layers on the same store.

`EventStore` retains its public signatures and record types while table families
share one connection, lock and transaction owner:

| Responsibility | Owning modules |
| --- | --- |
| Connection custody and shared event state | `core/persistence_connection` |
| Append and AEF outbox | `core/persistence_events` |
| Bounded reads and forensic scans | `core/persistence_event_reads` |
| Row MACs and quarantine | `core/persistence_event_authentication` |
| Retention and vacuum | `core/persistence_event_maintenance` |
| Receipt aggregates and notification outbox | `core/persistence_receipts`, `core/persistence_receipt_codec` |
| Keyed operations and evidence outbox | `core/persistence_operations`, `core/persistence_operation_commit` |

Keyed mutation preparation happens before the writer lock. Replay and conflict
lookup precedes renewed predecessor and capacity checks. Mutation events, exact
response, compatibility replay and evidence intent share one `BEGIN IMMEDIATE`
boundary. Post-commit observers cannot roll back a durable result. Receipt chat,
request and notification transactions retain the same ownership boundary.
Public event and operation records remain defined in `core/persistence`.

Journal replay uses `core/journal_appliers`: protected custody restorers run
first, unsupported protected history is refused, and ordinary event families
update their existing projections. Expiry and reindexing remain at the replay
composition boundary.

`core/persistence_receipt_view` reads a receipt aggregate and its exact source
event through a read-only connection. It verifies message/state binding and
optional row authentication, uses live WAL snapshots and returns unavailable
for unknown or unverified facts. It never constructs a writable store or
migrates a schema. MAC enrollment probes require the writer's additive partial
index, rather than scanning history at each poll.
The next writable open of every existing store builds that index, and later
authenticated writes maintain it. Older versions ignore the additive index.
SQLite may create WAL/SHM
coordination sidecars beside an existing WAL database in a writable directory;
the database content and file mode remain unchanged. See
[`docs/api.md`](docs/api.md#read-only-delivery-receipts).

## Federation and multi-hub

Most coordination state merges cleanly across hubs; authority does not — and
the design keeps that split explicit rather than papering over it:

- **Observation merges.** A read-only follower polls a peer's event store over
  the same `read_since` seam the hub itself uses; `core/multihub_merge` unions
  several hubs' logs into a grow-only set in which every event keeps its
  authoring hub, and `core/multihub_fold` folds the merged order into an
  observed view. This is the conflict-free slice: append-only logs, grow-only
  union, deterministic merged order.
- **Authority never transfers by merge.** Leases, claims, and name ownership
  live on exactly one hub; a peer's claim arrives as advisory observed state
  and grants nothing locally. There is no consensus round and no quorum —
  by design, not by omission.
- **Pulls are deny-by-default.** Whether a follower may pull from a peer at all
  is gated by federation policy composed with mTLS peer verification.

The stated scale ceiling: one workstation up to a small fleet of hubs that
observe each other. Followers poll rather than stream, and a hub is one asyncio
process over SQLite, which bounds throughput. See
[`docs/multi-hub-sync.md`](docs/multi-hub-sync.md).

## What this is not

- **Not a consensus cluster.** There is no consensus protocol between hubs and
  no distributed lock manager; federation observes and merges append-only
  logs, and authority over a name or claim never moves by merge.
- **Not a PKI.** Default identity is trust on first use: the first key a name
  proves is pinned, and recovering from a lost key is an explicit operator
  action. Deployments that need pre-provisioned, centrally issued keys opt
  into strict identity binding instead.
- **Not a datacentre message bus.** Throughput is bounded by a single-writer
  asyncio process over SQLite; the design target is coordination correctness
  for a workstation or a small LAN fleet, not horizontal message throughput.

## Durable remote inbox reads

`core.handlers.inbox` serves identity-scoped, bounded journal pages through
an additive history query. Hub admission and mailbox ACLs precede this
reader; channel chat is excluded. `hub_inbox` validates replies and the
live hub binding. `hub_inbox_cursor` persists independent endpoint and
identity sequence cursors. `ergonomics_inbox` selects feed or hub reads,
and `mcp.hub_inbox` reuses the bridge connection. The local feed remains
the default. Replies expose source, availability and remaining pages to
control interfaces; the current dashboard does not configure per-client
inbox sources.


### Chat and lease handler stages

Chat admission keeps semantic validation, hub timestamps, retry handling and
durable quota in order before choosing channel, peer or local routing. Local
delivery is a separate component: it retains history before adding the durable
sequence, commits receipt-request state before fanout, classifies actual writes
and emits ordered feedback. Stale delivery receipts remain before an uncertain
fanout, and failed writes remain retryable.

Lease handlers separate candidate-state mutations from operation-draft
preparation. Their orchestration owns fencing, legacy journal recording,
publication and notification order. State and progress become visible only
after their authoritative append or operation commit succeeds. Journal recorder
lookup stays in the owning facade, preserving embedding overrides and fault
controls. A replay/conflict response never publishes a second mutation.
