# Wire protocol

Every message is a JSON envelope with a small, fixed shape: `sender`, `target`,
`type`, `payload`, and a `timestamp`; hub-originated messages also carry a
`hub_id`. The `type` field selects the message; the values, grouped by concern,
are below.

For inbound `chat` frames the hub overwrites `timestamp` with its own wall
clock. That hub stamp is the only value used to order retained chat history and
the dead-letter ledger. A finite client-supplied instant is kept only as
optional advisory metadata on `client_timestamp`; non-finite or malformed client
values are discarded. A Byzantine future or backdated client stamp therefore
cannot poison history or dead-letter ordering.

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

The [signed capability cards runtime](signed-capability-cards.md) keeps
`advertise` and `manifest_request` as ordinary discovery messages while optionally
adding a domain-separated Ed25519 signature and manifest digest. The hub binds the
signed project to the connected sender's namespace and projects an explicit
verification result. Verification remains advisory and does not turn capability
cards into authorization or executable trust.

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
- **Directed delivery:** `ack` (acknowledges a mailbox-accepted live or replayed
  directed message by its durable `seq`, optionally naming `mailbox_for`; see
  [Directed delivery and the mailbox](#directed-delivery-and-the-mailbox)).
- **Claims and leases:** `claim`, `release`, `task_update`, `handoff`,
  `checkpoint`, `wait_request`.
- **Resources:** `resource`.
- **Shared blackboard:** `ledger_task`, `ledger_task_update`, `ledger_progress`,
  `board_request`.
- **Capabilities:** `advertise`, `manifest_request`.
- **Queries:** `state_request`, `who_request`, `history_request`,
  `resume_request`.
- **Governed operator recovery:** `identity_pin_reclaim` removes one exact TOFU
  pin after the always-on ACL, requester-binding, owner-liveness, expected-key,
  and durable-audit gates pass. It is emitted only by an explicit operator
  command, never automatically by a client.

### Canonical claim-path identity

A `claim` may carry the additive `path_identity` object below. It does not bump
the wire version because it is optional, is omitted by legacy clients, and is
ignored by clients that only render the ordinary `worktree` and `paths` fields.

```json
{
  "version": 1,
  "worktree_path": "/canonical/repository",
  "worktree_object_id": "device:object",
  "filesystem_namespace": "sha256:opaque-host-namespace",
  "case_sensitive": true,
  "paths": [
    {
      "git_path": "src/auth.py",
      "filesystem_path": "src/auth.py",
      "object_id": "device:object",
      "object_scope": ""
    }
  ]
}
```

Each nested row aligns one-to-one with the claim's display `paths` and is
client-derived from the local Git index and filesystem. Comparison strings are
repository-relative Unicode NFC; case folding occurs only for an insensitive
worktree. Device/object values are compared only when both identities carry the
same opaque filesystem namespace and worktree-root object key; this prevents
coincident inode values on different hosts from aliasing. Empty object ids mean
the path does not yet exist. The optional `object_scope` is empty or absent for
the whole object and otherwise carries a canonical semantic descendant. A whole
object conflicts with every descendant, declaration ancestry conflicts, and
sibling declarations remain independent across hard-link aliases. Object
comparison is conflict-only: it can deny a competing hard-link claim, but cannot
widen edit authorization or auto-release because inode identity is not a
historical capability. The hub rejects an unsupported version, malformed values,
or any row that does not match its display path before changing state. It then
persists and replays the field in claim, handoff, journal, causality, conflict,
yield, and staged-check projections; Git-hook release matching can consume it
client-side. The hub never trusts the identity as authorization and never uses it
to access a local path.

When only one side supplies the field, the hub projects the legacy display
under the supplied filesystem policy. Two claims without the field retain the
version-2 literal-path behavior. This supports rolling upgrades but means a
fleet closes every alias gap only after all Git-aware claim producers upgrade.

### Durable claim-denial evidence

When a hub has a durable event journal, every refusal made by its authoritative
local claim application appends a `claim_denial` event with `durable=true`.
This includes a forwarded claim that the owning hub applies locally. For a
direct client, the append completes before the private `claim_denied` reply is
emitted. The reply carries the same stable
`reason_code`: `TASK_ID_REQUIRED`, `PATH_IDENTITY_INVALID`, `LEASE_LIVE`,
`SCOPE_CONFLICT`, `QUOTA_EXCEEDED`, or the conservative fallback
`CLAIM_DENIED`.

The evidence row is deliberately content-minimized. It contains the bounded
claimant identity plus its full digest, decision and reason, path count, and SHA-256 correlations for
the task id and declared scope. It never stores the request note, raw task id,
raw worktree or paths, Git metadata, message bodies, prompts, or file contents.
The event is audit-only during restart replay: it survives restart but cannot
create or alter a lease.

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
or certify external conformance. A signed advertisement may also carry
`manifest_digest` and a `signature` envelope containing version, algorithm, key id,
sequence, signing/expiry timestamps, card digest, and signature value. Both fields
are additive: unsigned clients retain their legacy outbound shape, while projected
cards expose `project`, `manifest_digest`, `signature`, and `verification`.

An `advertise` message may also carry the additive `persist`, `dispatchable`,
and `agent` fields. With `persist: true` the card becomes a persistent dispatch
registration: it survives the sender's disconnect and expires only when not
refreshed within 24 hours, so automated dispatch can discover a project seat
across reconnects. `dispatchable` (boolean, default `true`) opts the
registration in or out of automated dispatch. `agent` names the identity the
card belongs to; the hub honours it only when it equals the connection name or
the connection is that identity's `-rx` sidecar (a wake listener registering
its seat). Persistent registration requires a project-scoped seat identity
(`<project>/<seat>`); non-boolean flags, a foreign `agent`, or an unscoped
identity are refused with a private `error`. Projected cards in
`capability_advertised` and `manifest_snapshot` carry the additive
`persistent`/`dispatchable` keys, merged onto the agent's single manifest
entry; legacy consumers ignore them.

A `ledger_task` may carry the additive `project` string (a namespace scope for
the task; absent means unscoped, and a re-declaration with a conflicting
non-empty scope is refused) and both `ledger_task` and `ledger_task_update`
may carry the additive `expected_version` integer — a compare-and-set guard
refused with a private `error` unless the task's current monotonic `version`
matches (an absent task counts as version `0`). `ledger_task_update` may also
carry `project` to re-scope. Every accepted mutation increments `version`;
task projections in `ledger_task_posted`, `ledger_task_updated`, and
`board_snapshot` carry `project` and `version`. A non-integer
`expected_version` (booleans included) is refused as malformed.

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
- **Operational warnings:** `recipient_liveness_warning`,
  `dark_seat_alert`, `dead_letter_escalation`, `dead_letter_forwarding`.
- **Governed operator recovery:** `identity_pin_reclaim_result` is the private
  applied/refused verdict for an `identity_pin_reclaim` request.

A `dark_seat_alert` is a default-on hub broadcast for an identity that owns an
unexpired claim or is the `suggested_owner` of a non-terminal board task but has
no fresh exact-identity `-rx` waiter. The condition must persist for 30 seconds;
the hub then emits one alert per continuous episode with sorted `claims` and
`tasks`, `missing_for_seconds`, and an explicit permanent-arm `remedy`. Re-arming
the waiter or ending all owned work clears the episode, so a later regression can
alert again. This is an operator warning: it neither releases work nor changes
claim or blackboard authority.

The envelope builders and the message-type constants live in
`synapse_channel.core.protocol`; the working agreement is in the repository's
`TEAM_PROTOCOL.md`.

## Governed identity-pin reclaim

The request carries `pin_name`, `expected_key_id`, a non-empty `reason`, and an
optional boolean `break_glass`. The requesting socket is already bound to the
envelope `sender`; the hub additionally requires that sender to have proved a
TOFU pin or an operator-managed identity bundle and to hold the ACL permission
`identity-pin-reclaim` on target kind `agent` for `pin_name`. This grant is
always checked by the handler, even when the general ACL compatibility switch
is off. A durable event journal is mandatory.

Without `break_glass`, the target must have no live socket and any opt-in name
ownership lease must have lapsed under the hub's configured offline TTL. With
`break_glass: true`, the same ACL-authorised, exact-key request may revoke the
live socket and lease. The hub write-ahead records an `approved` audit event,
compare-and-swap removes only a pin still matching `expected_key_id`, then
records `applied`; a storage failure or race records `not_applied` and leaves no
false success verdict. An applied action is also broadcast as a body-free
`system` notice. Public key material and replacement key material never enter
the audit or the wire request.

The result carries `applied`, `pin_name`, `expected_key_id`, `break_glass`, an
actionable `payload`, and the applied `audit_seq` when successful. Reclaim only
removes the old binding: the next valid registration proof may establish a new
first-use pin. Because the verb is explicit operator control rather than an
automatically emitted compatibility feature, an older hub simply refuses the
unknown request; clients never send it during ordinary connect or messaging.

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
  advances that identity's receiver watermark and replays only chat after it. A
  missing or malformed value degrades to `0` (the whole retained window).
- `mailbox_for` (optional) — the identity whose backlog to replay, when it differs
  from the connection name. A wake-listener connects under a receive-only `-rx` name
  but waits on its bare identity, so it names that identity here; absent or blank,
  the hub replays for the connection name. Roles are always read from the connection.

The hub re-sends each missed directed message as an ordinary `chat` frame marked
`replayed: true` and stamped with its durable `seq`. A client dedups on `seq`, not
`msg_id` — the per-hub `msg_id` counter resets on restart while `seq` never repeats.
Broadcasts are never replayed, and a hub with no durable journal replays nothing.

**Receiver watermark and pending count.** A mailbox client sends `ack` for every
live or replayed chat admitted by its `mailbox_advance` gate. The frame carries
the durable `seq` and may carry `mailbox_for` so an `identity-rx` sidecar advances
the bare identity, not its connection name. The hub validates that the stored
chat was directed to that logical identity before advancing, and journals the
monotonic cursor as `mailbox_watermark`. A registration `since_seq` advances the
same cursor after the existing mailbox-identity authorisation check.

The additive `who_snapshot.mailbox_pending` field is a per-identity integer map
when the hub has a durable journal, or JSON `null` when the projection is
unavailable. A count is the matching directed chats after the receiver watermark;
it is what `synapse who`, `synapse status`, and `synapse doctor` render as
`N undelivered messages pending for <identity>`. This is deliberately a mailbox
transport fact: it does **not** claim that a model read, understood, or acted on
the message. The hub bounds this projection to 512 recently touched identities;
that cache bound does not delete journal events. The default `synapse who`
presentation independently shows the 20 largest positive counts plus complete
totals, while `--all-mailbox-pending` expands the retained map. Older clients
ignore the WHO field; older hubs ignore the additive ACK identity and keep their
receipt-only ACK behavior.

**Chat retry identity.** Chat delivery is explicitly **at least once**. The hub
does not consume `idem_key` for chat and does not suppress a retry. A sender that
may retry across reconnects instead supplies an optional printable
`client_msg_id` of at most 256 UTF-8 bytes. The hub echoes the normalized value on
live delivery, bounded history, the durable chat row, mailbox replay, immediate /
deferred receipt frames, and the durable receipt ledger. Receivers deduplicate on
`(sender, client_msg_id)`; `msg_id` and durable `seq` identify individual delivery
attempts and therefore differ across retries. Missing or invalid `client_msg_id`
keeps ordinary at-least-once behavior. The hub never treats this caller-chosen
identity as authentication or authorization.

**Durable ingress quotas.** Optional per-principal sliding-window bounds cap how
many chat events and serialized chat-frame bytes a server-derived quota principal may have
*accepted* inside a window. The hub charges the connect-token fingerprint (or the
open-host bucket), not the free-form sender name, so name rotation cannot multiply
the budget. A refusal is an `error` frame
(`Durable ingress quota exceeded (events|bytes|oversized|principal-capacity).`)
and happens *before*
bounded history or the durable journal grow; admitted chats still journal normally.
`--secure` enables the default window when the operator left the flags disabled.
The bounded principal table evicts only expired buckets; when every slot is active,
new principals fail closed instead of resetting another principal's quota.

**Consume-live immediate receipts.** For a directed chat, the hub partitions
socket-level matches using the same reaction-plus-waiter liveness policy exposed
by WHO. At least one consume-live match yields `delivered: true`. If sockets match
but every recipient is stale, the immediate receipt instead carries
`delivered: false`, `reason: "no_live_recipient"`, the complete
`matched_recipients` and `stale_recipients`, and `dead_lettered: true`; no socket
match uses `reason: "no_online_recipient"`. These fields are additive and do not
change the wire version. The chat stays durable and may still be queued to a stale
socket as a best effort, but transport presence is never promoted to a positive
delivery verdict. This is reachability/acceptance evidence, not proof that a model
understood or acted on the body. A hub with stale-recipient tracking explicitly
disabled keeps the compatibility behavior and treats its socket matches as live.

**Deferred receipts.** When a `receipt_requested` directed message dead-lettered,
the hub remembers it in a bounded pending-receipt store keyed by its `seq`. When the
recipient reconnects, drains the replayed message, and sends
`ack: {seq, mailbox_for?}`, the hub re-checks that the logical mailbox identity is
a genuine recipient of the original target and then
sends the *original* sender a second `delivery_receipt` marked
`delivered: true, deferred: true` when that sender is still live. This notification
is deliberately online-only: if the original sender is offline at ack time, the hub
does not mailbox-replay receipt frames on a later reconnect. The durable receipt
ledger below is the authoritative recovery path, queryable by sender identity after
restart. A spoofed ack from a client the message was not addressed to neither
fabricates a receipt nor drops the pending one.
The `ack` verb arrived at wire version `2`; a client emits it only when the hub
advertises that version or newer.

**Durable receipt ledger.** A hub with a SQLite journal records the delivery-receipt
lifecycle as audit-only events:
`delivery_receipt_requested`, `delivery_receipt_immediate`,
`delivery_receipt_deferred`, and `delivery_receipt_expired`. On restart, unsettled
immediate failures re-seed the bounded pending-receipt store, so a later mailbox
`ack` can still journal the deferred verdict even if the original sender is offline.
Operators can query the ledger with `synapse event-query <db> "receipts <agent>"`.
When supplied, `client_msg_id` is included in every receipt phase so a sender can
correlate the durable final verdict with its original retry identity.
Mailbox watermarks are separate `mailbox_watermark` events: losing the newest
normal-durability watermark in a power failure can cause safe replay/recount, not
loss of an unseen message body.

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
session. The read-only `fuzz.yml` workflow also runs weekly and on manual
dispatch. It gives each Hypothesis property 1,000 generated examples against the
actual `loads_bounded()` decoder and `EventStore` persistence path, including
reopen, cursor-walk, and deletion invariants. A discovered counterexample must
be promoted to a committed `@example` regression so it survives the ephemeral CI
Hypothesis database.

This is not an external protocol-conformance certification; it is automated
local property-based coverage for malformed bytes, malformed JSON, quoted
bracket runs, valid nested JSON, depth-limit rejection, and persistence
round-trips.
