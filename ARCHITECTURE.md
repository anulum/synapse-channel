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
workers neither collide nor duplicate effort. Around that core the package has
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
| Hub composition | `core/hub`, `core/handlers/*`, `core/hub_connection`, `core/hub_ingress`, `core/hub_frame_gates`, `core/hub_exposure`, `core/hub_http`, `core/hub_identity_gate` | The routing core plus its seams: per-verb handlers, connection admission, frame gating and rate limits, the exposure guard, HTTP read surfaces, the identity gate. |
| Client | `client/agent` with its dispatch and outbound mixins, `client/launcher`, `client/llm_worker`, `client/supervisor` | The reusable async agent and its coordination verbs, one-command startup, an on-channel LLM worker, an LLM-free watcher that re-offers stalled tasks. |
| Identity and ownership | `core/auth`, `core/message_auth`, `core/identity_keys`, `machine_identity`, `core/identity_pins`, `core/name_ownership`, `core/acl*`, `core/policy_engine`, `core/trust_graph` | Shared-token admission, per-message Ed25519 authentication, the ownership lease, trust-on-first-use key pinning, deny-by-default ACL evaluation with opt-in enforcement, cross-agent trust edges. |
| Durability and storage | `core/persistence`, `core/journal`, `core/idempotency`, `core/compaction`, `core/at_rest*`, `core/persistence_sqlcipher`, `core/merkle`, `core/receipts`, `core/universal_receipts` | The append-only SQLite event store (WAL), journal replay on restart, idempotent re-applied mutations, log compaction, at-rest payload encryption (file key, escrow, TPM 2.0, PKCS#11, cloud-HSM attestation), whole-database SQLCipher, Merkle audit paths, signed receipts. |
| Messaging reliability | `ack`, `mailbox_cursor`, `core/delivery_receipts`, `core/pending_receipts`, `core/dead_letters`, `core/dead_letter_*` | Directed-message acknowledgement, durable mailbox replay across reconnects, delivery receipts, dead-letter capture with escalation and forwarding. |
| Federation and multi-hub | `core/multihub_merge`, `core/multihub_fold`, `core/multihub_follower`, `core/multihub_watch`, `core/federation*`, `core/causality*` | Read-only followers over the event-store seam, a conflict-free grow-only union of several hubs' logs, deny-by-default federation policy composed with mTLS, causality queries over the merged order. |
| Interop and surfaces | `cli_*` (the `synapse` command), `a2a_*`, `mcp/`, `dashboard_*`, `agent_tmux`, `codex_tmux`, `git/`, `cli_arm*` | Some ninety CLI verb modules, the Agent-to-Agent HTTP bridge, the MCP server, the operator dashboard and cockpit, tmux drivers for terminal fleets, git claim helpers, the permanent systemd waiter. |
| Workers and routing | `client/chat_backends`, `client/routing`, `core/semantic_routing`, `participants/`, `core/workflow*` | Pluggable reply backends, task-class routing to tiered backends, multi-participant deliberation, declarative workflows. |
| Observability and operations | `core/metrics`, `observability_textfile`, `otel_export`, `core/postmortem`, `core/dark_seat`, `core/stall`, `reap`, `update_check`, `benchmark/` | Prometheus metrics, node-exporter textfiles, OpenTelemetry export, post-mortem bundles, dark-seat alerts, stalled-task detection, committed benchmarks. |

## Coordination model

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
