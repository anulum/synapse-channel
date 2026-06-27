# Glossary

The vocabulary SYNAPSE uses, in one place. Terms cross-reference each other in **bold**.

### Hub

The single `SynapseHub` process. It owns all shared state — the roster, **claim**s, the
**blackboard**, **capability** cards, chat, and the **event log** — and relays every message to
every connected client. It is the authoritative source of truth; there is one per channel.

### Agent

Any client connected to the **hub** under a name: a coding agent, a human at a `syn` prompt, a
**worker**, a **supervisor**, or a presence holder. An agent's name may be a bare project
(`quantum`) or a `project/id` **seat**.

### Worker

An **agent** that answers chat on the channel through a model backend (an OpenAI-compatible
endpoint, a local Ollama server, or a deterministic rule fallback). It advertises a
**capability** card and throttles its own replies.

### Supervisor

An LLM-free **agent** that watches the **blackboard** and re-offers a **task** that has stalled,
so work does not stick on an idle claim.

### Claim

A lease an **agent** takes on a unit of work before doing it, carrying a **scope**, a
**status**, an **epoch**, and an optional **checkpoint**. The **hub** refuses a claim whose
**scope** overlaps a live one, so two agents never work the same files.

### Lease

The time-bounded nature of a **claim**: it has a TTL, can be renewed, and is released
explicitly or by an auto-release rule (for example a git hook on commit). A lapsed lease frees
the work for another agent.

### Scope

The set of path globs a **claim** covers. Scopes are opaque strings the hub compares only for
glob **overlap** — the hub never reads a filesystem, so a scope coordinates intent, not disk
access.

### Epoch

A monotonically increasing number on a **claim** that makes reconnect-safe idempotency
possible: a stale, replayed operation carrying an old epoch is ignored, so a redelivered claim
or release does not double-apply.

### Checkpoint

A small resume marker an **agent** attaches to its **claim** to record progress, so a returning
agent (or one reading `synapse state`) can see where the work was left and continue rather than
restart.

### Blackboard

The shared task plan: declared **task**s, their **status**, dependencies, the set that is
**ready** (unblocked), and recent progress notes. The hub bounds retained
progress globally, per author, and per task id. Printed by `synapse board`.

### Task

A unit of declared work on the **blackboard** with an id, a title, a typed **status**
lifecycle, and optional dependencies. A task marked done unblocks its dependents.

### Capability (card)

A **worker**'s self-description — its **task class**es, model, and a short description —
advertised to the channel and printed by `synapse manifest`, used to route a request to a
fitting worker.

### Task class

A routing label a **worker** advertises (for example `chat`, `reason`, `heavy`). A request is
matched to a worker by class; a `tiered` worker uses the class to pick a cheap or a heavy
backend.

### Presence

The fact of being on the **hub**'s live roster. A presence holder is a long-lived `listen`
connection that keeps an identity reachable and the durable feed flowing even when the agent
itself is between turns. Printed by `synapse who`.

### Wake / waiter

A **waiter** is a one-shot `synapse wait` an **agent** arms in the background; it blocks until a
message that should **wake** it arrives, then exits and re-invokes the agent. This is the
event-driven alternative to polling.

### Directed-only

A waiter mode (`--directed-only`) that wakes only on a message addressed to the agent (or a
group glob it is in), a CEO message, or a `--priority` message — suppressing routine broadcasts
to `all`. The **inbox** still receives everything; directed-only governs only what *wakes* you.

### Takeover

A re-arming **waiter** reclaiming its own name, evicting a stale ghost connection holding it,
instead of failing with a name conflict.

### Broadcast / directed message

A **broadcast** targets `all`; a **directed** message names a recipient — an **agent**, a
comma-list, or a group glob (`project/*`). A **seat** is reached by its `project/seat` name; the
bare `project` reaches a sole agent's inbox and, when armed bare, wakes it.

### Relay (log)

A compact NDJSON mirror of the channel the **hub** can write with `--relay-log`, for a
file-based observer. `synapse relay` decodes it back to readable lines.

### Ingest

Streaming durable events from the **hub**'s **event log** since a sequence cursor
(`synapse ingest`) — the read side a persistent-memory adapter consumes.

### Event log

The durable, append-only SQLite-WAL record of everything authoritative (claims, releases, plan
writes, findings), enabled with `synapse hub --db …`. It is replayed on restart to resume state,
applies the same blackboard retention and finding-quota counters, and is the spine for
**ingest**.

### Temporal event-log query

Read-only reconstruction over the **event log** using `synapse event-query`: task
timelines, task state at a sequence or timestamp, path-touch windows, and
historical claim conflicts. Prototype Datalog-like and Cypher-like query aliases
normalize into the same read-only event-log query model.

### Replayable postmortem

Read-only Markdown or JSON reconstruction over the **event log** using
`synapse postmortem ./synapse.db TASK-1`. It lists the task timeline, owners,
release events, assessment evidence, reconstructed path-overlap conflicts, and
candidate unanswered messages that mention the task id.

### Policy engine

Planned local-first decision layer for evaluating release receipts and event-log
evidence against configured rules such as required tests, strict type checking,
owner approval, evidence freshness, generated artifact parity, and
no-merge-without-receipt. The design is advisory by default and does not merge
code.

### Paranoid mode

Design target for one future operator switch that enables strict local settings
and reports missing hardening hooks. It is not implemented as a CLI flag yet; it
defines token-required access, loopback-first binds, metrics/A2A auth,
owner-only state files, bounded retention, durable event logs, release receipts,
and gaps such as encryption, signed events, per-agent identity, ACLs, private
channels, and exposed deployment threat modelling.

### At-rest encryption

Design target for optional encryption of local storage surfaces such as SQLite
event stores, WAL and SHM sidecars, relay logs, A2A state files, cursor files,
archive reports, temporary files, and backups. The design covers key storage,
key derivation, key rotation, backup recovery, and lost-key recovery boundaries;
it is not implemented yet.

### Reliability memory

Evidence-only owner summaries over the **event log** using
`synapse reliability ./synapse.db`. The report counts stale claims, declared
failed-check evidence, broken handoff candidates, and conflict pairs as audit
signals, not scores.

### TTL advice

Read-only adaptive lease TTL advice over the **event log** using
`synapse ttl-advice ./synapse.db`. It derives completed-task duration samples and
live-claim load, then reports advisory defaults without changing hub settings;
manual TTL values remain authoritative.

### Predictive stall detection

The local `synapse supervisor` policy that combines a fixed idle ceiling with
completed-task progress cadence from the current board. It can re-offer stalled
plan tasks earlier on boards with enough fast history, but remains an advisory
heuristic over board activity, not proof that a worker failed.

### Handoff

One **agent** passing an in-progress **claim** (with its **checkpoint**) to another atomically,
so work moves between agents without dropping the lease.
