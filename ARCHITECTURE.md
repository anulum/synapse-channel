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
parallel on one codebase. A single WebSocket hub is the authoritative source of
truth for presence, work claims, chat, task status, the shared plan, agent
capabilities, and resource offers, so concurrent workers neither collide nor
duplicate effort.

## Design principles

- **One authoritative hub.** All coordination state lives on one instance — no
  consensus protocol, no distributed locks, no CRDTs. A single-writer hub keeps
  the routing logic deterministic and unit-testable.
- **Transport-light.** One runtime dependency (`websockets`); everything else is
  the standard library.
- **Local-first, proportionate.** The default posture is one operator on
  loopback with no authentication. Heavier controls (a connect token, off-host
  exposure warnings) are opt-in and bound to need, not added by default.
- **Correctness before features.** Two layers are non-negotiable: durability (a
  hub restart must not wipe live leases) and reconnect-safety (a reconnected
  agent must not double-apply a claim).
- **Single responsibility per module.** Each file owns one concern; the package
  is composed of small, independently tested modules.

## Module map

The package is `src/synapse_channel/`. Modules group by concern:

### Coordination state (transport-agnostic)

| Module | Responsibility |
| --- | --- |
| `state` | Presence, scoped task-claim leases, epochs, optimistic-concurrency versions, checkpoints, and resource offers. |
| `scoping` | Worktree- and path-overlap detection that keeps two agents off the same files. |
| `lifecycle` | Typed task-status states and the legal transitions the hub enforces. |
| `deadlock` | Wait-for cycle detection so circular hold-and-wait claims are refused. |
| `ledger` | The shared blackboard: a declared task plan (with dependencies) and an append-only progress stream. |
| `capability` | Agent capability cards and the hub-aggregated manifest. |

### Wire and transport

| Module | Responsibility |
| --- | --- |
| `protocol` | The on-wire message envelope and the message-type constants. |
| `relay` | Lite/heavy codec and append-only NDJSON log helpers for file-based observers. |
| `hub` | The routing core: connections, names, history, broadcast, and the state machine. |
| `client` | The reusable async agent connection and the coordination verbs. |
| `auth` | Optional shared-secret connect authentication (proportionate, not an identity system). |

### Durability

| Module | Responsibility |
| --- | --- |
| `persistence` | Append-only SQLite event store (WAL) giving the hub a crash-durable spine. |
| `journal` | Records authoritative mutations as events and replays them to rebuild state on restart. |
| `idempotency` | Bounded LRU of applied-mutation responses, replayed on a repeated idempotency key. |
| `ratelimit` | Per-agent token-bucket limiter so one runaway agent cannot swamp the hub. |

### Workers and routing

| Module | Responsibility |
| --- | --- |
| `chat_backends` | Pluggable reply backends (OpenAI-compatible HTTP, rule-based). |
| `routing` | Classify a request into a task class and route it to a tiered backend. |
| `llm_worker` | An on-channel agent that answers addressed messages via a backend. |
| `supervisor` | LLM-free watcher that spots stalled plan tasks and re-offers them. |
| `launcher` | One-command local hub + worker startup. |
| `cli` | The unified `synapse` command. |

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
5. **Route.** Workers advertise capability cards; a request is classified into a
   task class and routed to the matching backend, reserving heavy models for the
   hard requests.

## Durability

With `--db`, the hub is backed by an append-only SQLite event log in WAL mode.
Every claim, release, task update, handoff, checkpoint, resource offer, ledger
change, and chat message is recorded, and the hub rebuilds its state by replaying
the log on start-up. The guarantee is split honestly by workload: the lease/claim
path commits at `synchronous=FULL` (durable across an OS crash); the high-volume
chat and progress path commits at `synchronous=NORMAL` (durable across an
application crash).

## What this is not

There is deliberately no internal consensus protocol, no distributed lock
manager, no CRDT, no graph database, and no cryptographic agent identity. The
single-writer hub on a local machine makes those unnecessary; adding them would
be complexity without a matching need.
