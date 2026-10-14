# Glossary

The vocabulary SYNAPSE uses, in one place. Terms cross-reference each other in **bold**.

### Hub

A `SynapseHub` process owns its roster, **claim**s, **blackboard**, **capability**
cards, chat and configured **event log**. Delivery depends on message type,
target, channel membership and the configured private-directed profile.
Federated peers have their own state and explicit synchronization boundaries;
one hub is authoritative for its own admitted operations.

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
**scope** conflicts with a live scope held by another owner. Provider hooks and the staged Git check separately
enforce that authority on their documented mutation surfaces.

### Lease

The time-bounded nature of a **claim**: it has a TTL, can be renewed, and is released
explicitly or by an auto-release rule (for example a git hook on commit). A lapsed lease frees
the work for another agent.

### Scope

The declared literal file paths or directory prefixes a **claim** covers.
Legacy requests use literal
path comparison; Git-aware clients resolve local repository/path identities and
send versioned canonical identity data for the hub to validate. The hub does
not read the client's filesystem. Scope admission and configured mutation
guards coordinate declared authority; a claim does not isolate arbitrary disk
access.

### Epoch

A hub-issued, increasing lease generation. A mutation naming a stale epoch is
refused; the strict fencing profile also refuses missing epochs. Epoch checks
are separate from idempotency: supported operations with a retained idempotency
key can replay their exact committed response, while conflicting reuse is
refused. Neither mechanism fences an arbitrary external process or file write.

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

A **waiter** is a one-shot `synapse wait` an **agent** arms in the background.
It waits for a qualifying message and exits. A separately configured harness
must observe that exit and invoke the provider; waiting alone does not start
a model turn. The process and connection still consume resources.

An **active waker** is the supervised `agent-tmux` bridge installed by `synapse waker install`.
It converts an exact directed wake into a fixed routing prompt in an already bound provider
tmux pane. Its systemd service can recreate the bridge after exit or failure, subject
to its configured start budget and host health, while
the durable `inhibited` state lets the owning agent stop that exact bridge without stopping or
restarting the provider terminal.

### Directed-only

A waiter mode (`--directed-only`) that wakes only on a message addressed to the agent (or a
group glob it is in), a CEO message, or a `--priority` message — suppressing routine broadcasts
to `all`. The **inbox** still receives everything; directed-only governs only what *wakes* you.
The interactive `agent-tmux` pane bridge applies a stricter subset: exact identity,
role, or group targets only, never global priority or CEO broadcasts.

### Takeover

A re-arming **waiter** reclaiming its own name, evicting a stale ghost connection holding it,
instead of failing with a name conflict.

### Broadcast / directed message

A **broadcast** targets `all`; a **directed** message names a recipient — an **agent**, a
comma-list, or a group glob (`project/*`). A **seat** is reached by its `project/seat` name; the
bare `project` reaches a sole agent's inbox and, when armed bare, wakes it.

### Relay (log)

A compact NDJSON mirror of the channel the **hub** can write with `--relay-log`, for a
file-based observer. Version 2 retains structured payloads and auxiliary envelope
fields while remaining readable alongside legacy version-1 rows. `synapse relay`
decodes the feed back to readable lines.

### Ingest

Streaming durable events from the **hub**'s **event log** since a sequence cursor
(`synapse ingest`) — the read side a persistent-memory adapter consumes.

### Event log

The configured SQLite-WAL journal of admitted durable operations (including
claims, releases, plan writes and findings), enabled with `synapse hub --db …`.
Retained, valid records are replayed on restart under the configured integrity,
retention and lease rules. Compaction can remove eligible records; replay is
not a promise that every chat or external effect survives every failure. It
applies the blackboard retention and finding-quota counters and is the spine for
**ingest**. Accepted recall and finding writes carrying a caller-retained idempotency key commit
their event and exact response together; a failed finding append does not consume live quota.

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

Advisory local-first decision layer exposed by `synapse policy-check`. It
evaluates release receipts and event-log evidence against configured rules such
as declared tests, strict type checking, owner approval, evidence freshness,
generated-artifact parity, and no-merge-without-receipt. It reports a decision;
it does not merge code or become a blocking gate unless an operator wires it
into a hook or CI. Receipt rules inspect the supplied declarations; they do not
rerun the checks or independently authenticate an approval.

### Identity and ACL

Shipped identity-binding and authorisation controls. Installations with
`cryptography` can pin a name to a trust-on-first-use machine key; operator
profiles can require an enrolled Ed25519 identity bundle. Deny-by-default ACLs
can enforce allowed verbs and target patterns on mutating frames, including
mailbox and role-claim gates. Read-surface ACLs, automated credential lifecycle,
and full multi-tenant IAM remain outside the current runtime. Identity and ACL do
not replace per-message authentication or signed events.

### Signed capability cards

Shipped advisory runtime for tamper-evident capability advertisements. A signed
capability card binds canonical card JSON, an Ed25519 card signature, key id, agent
and project scope, manifest digest, sequence, validity window, expiry, and an
explicit verification result. A separate loaded trust bundle supplies rotation and
revocation state; changing its file does not hot-reload a running CLI hub.
Bounded in-memory history reports replay and capability downgrade.
Unsigned cards remain advisory discovery, and a valid result grants no authority.

### Paranoid mode

Shipped `synapse hub --paranoid` profile. It requires a connect token, durable
event log, HMAC per-message authentication, deny-by-default ACL enforcement,
native WSS, and metrics bearer auth when metrics are enabled. It reports controls
that the flag does not compose automatically, including identity binding,
at-rest encryption, private/E2E channels, mutual-TLS client verification, and
deployment evidence.

### At-rest encryption

Shipped opt-in protection for local storage. `--db-key-file` enables SQLCipher
page encryption for the live event store. Separate AES-256-GCM whole-file tools
can envelope operator-selected files such as relay logs, A2A state, cursors,
archives and backups; selecting a live DB key does not automatically encrypt
all these surfaces or every temporary file. Key
wrapping, rotation, backup recovery, escrow, and attestation tools are separate
operator workflows. At-rest encryption does not protect hub RAM or create
multi-tenant isolation.

### End-to-end encrypted channels

Shipped runtime for selected chat payloads. `send --encrypt-key-file` encrypts
the body at the endpoint and `listen --decrypt-key-file` decrypts it locally, so
the hub cannot read plaintext. Routing metadata remains visible, and key
discovery, managed rotation, non-chat payload profiles, and compromised-endpoint
protection remain outside the tranche.

### Private channels

Shipped audience-scoped routing for channel messages. The runtime enforces
membership on delivery and bounded history and filters relay/event-query
projections. Channel membership is process-local in the current tranche, and
private channels do not encrypt payloads or create cryptographic identity.

### Differential privacy blackboard

Design target for redacted and noisy shared blackboard projections in
multi-organisation collaboration. It defines sensitive progress note handling,
redaction policy, aggregation boundary, cohort threshold, field minimisation,
role-based view, differential privacy parameters epsilon and delta, privacy
budget, privacy ledger, and audit trail. It is not implemented yet and does not
anonymize raw logs.

### Signed events and mTLS

Library/runtime primitives for authenticating selected coordination events and
trusted multi-host peers. Signed events use Ed25519 over a canonical payload,
key id, signed sequence metadata, timestamp window, replay protection, and an
explicit verification result. Mutual-TLS contexts and operator-managed
certificate-pin bundles protect configured peer paths. The packaged hub CLI
loads a client CA through `--multihub-serving-policy` and requests optional
client certificates; its serving policy checks the configured peer proof.
It does not load an Ed25519 signed-event trust bundle, so the combined managed
operator profile remains staged.

### Per-message authentication

Shipped opt-in HMAC-SHA256 authentication for selected mutating WebSocket frames
after connect authentication. `--message-auth-key` configures sender-bound keys
and `--require-message-auth` enforces canonical frames, key ids, nonces,
timestamps, sequence metadata, and a bounded replay cache. It does not encrypt
payloads, replace TLS, or provide a managed key lifecycle.

### Reliability memory

Evidence-only owner summaries over the **event log** using
`synapse reliability ./synapse.db`. The report counts stale claims, declared
failed-check evidence, broken handoff candidates, and conflict pairs as audit
signals, not scores.

### Agent trust graph

Shipped read-side evidence graph exposed by `synapse trust-graph`. It projects
reliability signals, release receipts, handoff outcomes, conflict history,
provenance references, and event sequences into typed edges with optional decay
windows. Routing integration and owner annotations remain design targets. The
graph does not rank agents, assign trust grades, or authorise execution.

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

## Explicit advisory ranking

The separate opt-in `synapse reputation` command ranks recorded evidence for
human triage and lists source sequences. The default evidence graph remains
unranked. The projection does not authorise execution or change routing;
self-attested evidence and row authentication do not independently verify work.
See [advisory agent ranking](reputation.md) for counts, read-only snapshots,
existing key requirements, complete-read budgets and filters.
