# Wire protocol

Every message is a JSON envelope with a small, fixed shape: `sender`, `target`,
`type`, `payload`, and a `timestamp`; hub-originated messages also carry a
`hub_id`. The `type` field selects the message; the values, grouped by concern,
are below.

A state-mutating message may carry an `idem_key` so a retry after a reconnect is
applied once. On a secured hub, the first message of a connection must carry a
`token`.

The hub advertises its wire-protocol version in the `welcome` handshake as
`protocol_version` (an integer; the current wire is version `2`), and it is also
reported by `/health` as `protocol_version`. It is decoupled from the package
version on purpose — a patch or feature release that leaves the wire shapes
unchanged does not bump it, so it is a stable compatibility signal a client can
read on connect rather than a release counter. Version `2` added the client → hub
`ack` verb and the deferred delivery receipt it drives (see
[Directed delivery and the mailbox](#directed-delivery-and-the-mailbox)); a client
emits an `ack` only when the peer advertises version `2` or newer, and a hub that
predates the verb is never sent it, which is what keeps the addition
backward-compatible. A client that predates the field, or a hub that does, reads it
as absent. Version-skewed peers are accepted rather than rejected: consumers
negotiate to the lowest common wire version, warn the operator when the peer is
older, newer, or did not advertise a usable version, and gate optional features
against that effective version.

The [per-message authentication runtime](per-message-authentication.md) keeps
the same envelope shape and adds an `auth` object for selected mutating frames
after WebSocket connect authentication. It is opt-in: `--message-auth-key`
configures sender-bound HMAC keys, and `--require-message-auth` enforces signed
claims, releases, task updates, handoffs, checkpoints, and resource offers.

Embedded hubs may instead verify the `signature` object defined by the
[signed-events runtime](signed-events-mtls.md) against an
`EventSignatureTrustBundle`. The packaged hub CLI does not load that bundle;
native `--tls-certfile --tls-keyfile` is server TLS and does not by itself
enable signed events or mutual TLS.

The [identity and ACL runtime](identity-and-acl.md) keeps protocol messages as
ordinary envelopes. Signed registration fields bind a connection name to a
machine key or operator trust bundle, and opt-in ACL evaluation refuses
unauthorised mutating frames before state changes. These additive fields and
checks do not replace the connect token or change the default local wire flow.

The planned [signed capability cards design](signed-capability-cards.md) keeps
`advertise` and `manifest_request` as ordinary discovery messages while adding a
future card-signature profile for tamper evidence. It is not implemented yet and
does not turn capability cards into authorization or executable trust.

The planned
[differential-privacy blackboard design](differential-privacy-blackboard.md)
keeps `ledger_task`, `ledger_progress`, and `board_request` as ordinary local
messages while defining future redacted or noisy projections for shared reports.
It is not implemented yet and does not anonymize raw event logs.

The [agent trust graph](agent-trust-graph.md) (`synapse trust-graph`) keeps
the wire protocol unchanged. It reads existing event-log records and release
receipts as graph evidence for routing review, entirely on the read side; it
does not add agent grades to protocol envelopes.

## Agent → hub

- **Presence and chat:** `chat`, `heartbeat` (sent automatically by clients).
- **Directed delivery:** `ack` (acknowledges a replayed directed message by its
  durable `seq` so the hub can confirm a deferred receipt to its sender; see
  [Directed delivery and the mailbox](#directed-delivery-and-the-mailbox)).
- **Claims and leases:** `claim`, `release`, `task_update`, `handoff`,
  `checkpoint`, `wait_request`.
- **Resources:** `resource`.
- **Shared blackboard:** `ledger_task`, `ledger_task_update`, `ledger_progress`,
  `board_request`.
- **Capabilities:** `advertise`, `manifest_request`.
- **Queries:** `state_request`, `who_request`, `history_request`,
  `resume_request`.

An `advertise` message may include `contracts`, either as a list of contract
objects or as a task-class keyed mapping. The hub normalizes valid entries into
the manifest shape:

- `task_class`: the routing class the contract describes.
- `input_schema` and `output_schema`: JSON-object mappings, usually JSON Schema
  fragments, describing accepted input and produced output.
- `preconditions` and `postconditions`: optional lists of declarative checks.

Malformed contract entries are ignored rather than rejecting the advertisement.
Capability contracts are discovery metadata for routing, dashboards, A2A Agent
Card metadata, and human review; they do not execute checks, authorize callers,
or certify external conformance. The signed capability cards design defines a
future tamper-evidence profile for these advertisements without changing the
current wire format.

## Hub → agent

- **Session:** `welcome`, `presence_update`, `name_conflict`, `auth_denied`,
  `error`, `system`.
- **Claims and leases:** `claim_granted` / `claim_denied`,
  `release_granted` / `release_denied`, `task_updated`,
  `handoff_granted` / `handoff_denied`, `checkpoint_saved` / `checkpoint_denied`,
  `wait_granted` / `wait_denied`.
- **Resources:** `resource_offered`.
- **Shared blackboard:** `ledger_task_posted`, `ledger_task_updated`,
  `ledger_progress_posted`, `board_snapshot`.
- **Capabilities:** `capability_advertised`, `manifest_snapshot`.
- **Queries:** `state_snapshot`, `who_snapshot`, `history_snapshot`,
  `resume_snapshot`.

The envelope builders and the message-type constants live in
`synapse_channel.core.protocol`; the working agreement is in the repository's
`TEAM_PROTOCOL.md`.

## Directed delivery and the mailbox

A `chat` addressed to a `target` — one name, a `project/*` group glob, or a
`project/role` a holder answers to — uses the compatibility broadcast flow by
default, so each client filters for the messages meant for it. With
`--private-directed-messages` (forced by `--team-secure`), the hub instead sends
the frame only to recipients, their `-rx` sidecars, and identities holding the
ACL `observe` grant. The durable journal and configured relay log still retain
the message for audit and replay.

**Immediate receipts.** A `chat` sent with `receipt_requested: true` gets a private
`delivery_receipt` back: `delivered: true` with the matched `recipients` when a live
connection matched the target, or `delivered: false` when none did. A directed
message that matched no live connection is a *dead letter* — durable in the journal
and feed, but woken by nobody at send time.

**Reconnect replay (the mailbox).** A client that missed directed messages while
offline can ask for them on reconnect. On its *registration* heartbeat it sets:

- `mailbox: true` — request a replay of the directed backlog.
- `since_seq` — the last durable journal `seq` it has already processed; the hub
  replays only chat after it. A missing or malformed value degrades to `0` (the
  whole retained window).
- `mailbox_for` (optional) — the identity whose backlog to replay, when it differs
  from the connection name. A wake-listener connects under a receive-only `-rx` name
  but waits on its bare identity, so it names that identity here; absent or blank,
  the hub replays for the connection name. Roles are always read from the connection.

The hub re-sends each missed directed message as an ordinary `chat` frame marked
`replayed: true` and stamped with its durable `seq`. A client dedups on `seq`, not
`msg_id` — the per-hub `msg_id` counter resets on restart while `seq` never repeats.
Broadcasts are never replayed, and a hub with no durable journal replays nothing.

**Deferred receipts.** When a `receipt_requested` directed message dead-lettered,
the hub remembers it in a bounded pending-receipt store keyed by its `seq`. When the
recipient reconnects, drains the replayed message, and sends `ack: {seq}`, the hub
re-checks that the sender is a genuine recipient of the original target and then
sends the *original* sender a second `delivery_receipt` marked
`delivered: true, deferred: true` — closing the gap where the sender was told "not
delivered" and never learnt the message arrived. A spoofed ack from a client the
message was not addressed to neither fabricates a receipt nor drops the pending one.
The `ack` verb arrived at wire version `2`; a client emits it only when the hub
advertises that version or newer.

**Durable receipt ledger.** A hub with a SQLite journal records the delivery-receipt
lifecycle as audit-only events:
`delivery_receipt_requested`, `delivery_receipt_immediate`,
`delivery_receipt_deferred`, and `delivery_receipt_expired`. On restart, unsettled
immediate failures re-seed the bounded pending-receipt store, so a later mailbox
`ack` can still journal the deferred verdict even if the original sender is offline.
Operators can query the ledger with `synapse event-query <db> "receipts <agent>"`.

## Release receipts

A successful `release` may carry closeout evidence. The hub echoes a
machine-readable `receipt` object on `release_granted` with these fields:

- `task_id`, `owner`, and `released`.
- Repeated evidence lists: `evidence`, `artifacts`, `known_failures`,
  `changed_files`, `generated_artifacts`, and `approvals`.
- Optional `confidence` and `freshness_seconds`.

When any evidence field is present, the hub also records a compact
`ledger_progress_posted` assessment note for the same task, so `synapse board`
shows the release closeout alongside the task plan. The hub records the submitted
evidence; policy decisions about whether that evidence is sufficient remain
outside the wire protocol.

## Decoder hardening

Inbound hub and A2A JSON frames use `loads_bounded()` from
`synapse_channel.core.protocol`. The helper scans raw text for array/object
nesting before calling `json.loads`, so a malformed or deeply nested frame fails
as a normal JSON decode error instead of recursing through the interpreter.

`tools/fuzz_protocol_decode.py` is the local decoder hardening evidence harness.
Run `PYTHONPATH=src python tools/fuzz_protocol_decode.py --smoke` for the
deterministic seed corpus, or install Atheris and run
`PYTHONPATH=src python tools/fuzz_protocol_decode.py` for an open-ended fuzzing
session. This is not an external protocol-conformance certification; it is local
coverage for malformed bytes, malformed JSON, quoted bracket runs, valid nested
JSON, and depth-limit rejection.
