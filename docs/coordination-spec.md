<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — normative coordination specification
-->

# Coordination specification

This document is the **normative** specification of the SYNAPSE CHANNEL
coordination core. Where [`coordination-model.md`](coordination-model.md)
describes *how the plane composes* and [`protocol.md`](protocol.md) fixes the
*wire shapes*, this document states the numbered **invariants** the single-hub
authority must uphold, the failure semantics around crash, reconnect, and
partition, the per-verb delivery guarantees, and the clock model — each mapped to
the executable test that pins it.

The scope is the authoritative coordination core: claims, leases, epoch fencing,
the durable journal, restart replay, directed delivery, and the boundary between
one hub's authority and a federation of observing hubs. It deliberately does not
restate the identity, ACL, encryption, or interop layers, which have their own
documents.

## Conventions

The key words **MUST**, **MUST NOT**, **SHOULD**, **MAY**, and **REQUIRED** are
used as in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119). Each invariant has
a stable identifier (for example `INV-ME-1`) that never changes meaning; a
retired invariant is struck through rather than renumbered, so external
references stay valid.

Every invariant carries:

- **Normative** — the rule, in RFC 2119 language.
- **Implementation** — the authoritative code site (`file:symbol`).
- **Pinned by** — the executable test file(s) that would fail if the rule broke.

A `[model]` tag on *Pinned by* means the invariant is additionally exercised by
the machine-checkable state model in `tests/test_coordination_spec_model.py`:
a Hypothesis `RuleBasedStateMachine` that drives the real `SynapseState` through
random claim/renew/release/handoff/update/checkpoint/expiry sequences and asserts
the tagged invariants after **every** step. The drift guard
`tests/test_coordination_spec.py`
binds this document to the code: it fails if a normative constant here disagrees
with the implementation, if a `Pinned by` test file is missing, or if a model
invariant is undocumented.

All times are wall-clock seconds. "Live" means a claim whose
`lease_expires_at` is strictly greater than the current time; a claim at or past
its expiry is not live.

## 1. Single-hub mutual exclusion

A single hub is the sole writer of claim authority. These invariants prevent two
agents from holding conflicting work at once.

### INV-ME-1 — one owner per task

**Normative.** At most one agent MUST hold a live claim on a given `task_id` at
any instant. A `claim` for a task already held live by a *different* agent MUST
be refused (reason code `LEASE_LIVE`), and the incumbent MUST be left untouched.

**Implementation.** `core/state.py:SynapseState.claim` — the live-owner block
refuses when `existing.owner != agent and existing.lease_expires_at > ts`.

**Pinned by.** `tests/test_state_claims.py`,
`tests/test_claim_denial_evidence.py`. `[model]`

### INV-ME-2 — file-scope overlap is refused (ancestry rule)

**Normative.** A claim declaring a file scope (`worktree` + `paths`) MUST be
refused (reason code `SCOPE_CONFLICT`) when any declared path overlaps a path in
another agent's live claim in the same worktree. Two paths overlap when they are
equal, when either is the worktree root (`""`), or when one is a directory
ancestor of the other. Overlap MUST be symmetric and reflexive.

The same guard applies to a `handoff` (INV-CR-2), so no lease-mutating verb can
leave two different owners holding overlapping live scopes; the registry
therefore never contains a different-owner conflicting live pair.

**Implementation.** `core/scoping.py:paths_overlap` (ancestry via
`startswith(prefix + "/")`), `core/state_scopes.py:find_scope_conflict`, refused
in `core/state.py:SynapseState._scope_conflict` on both the claim and handoff paths.

**Pinned by.** `tests/test_scoping.py`, `tests/test_state_scopes.py`,
`tests/test_state_scope_epoch.py`, `tests/test_handoff_scope_conflict.py`,
`tests/test_invariant_properties.py`. `[model]`

### INV-ME-3 — different worktrees never contend

**Normative.** Two claims in different worktrees MUST NOT conflict, whatever
their paths. Agents editing distinct checkouts are structurally independent.

**Implementation.** `core/scoping.py:scopes_conflict` returns `False` when
`worktree_a != worktree_b`.

**Pinned by.** `tests/test_scoping.py`, `tests/test_state_scope_epoch.py`,
`tests/test_invariant_properties.py`. `[model]`

### INV-ME-4 — a whole-worktree claim excludes every other claim there

**Normative.** An empty path set claims the whole worktree and MUST conflict with
any other live claim (empty or not) in that worktree.

**Implementation.** `core/scoping.py:scopes_conflict` (empty-set branch).

**Pinned by.** `tests/test_scoping.py`, `tests/test_state_scopes.py`,
`tests/test_invariant_properties.py`. `[model]`

### INV-ME-5 — per-principal live-claim cap

**Normative.** A single server-derived quota principal MUST NOT hold more than
`MAX_CLAIMS_PER_AGENT` live claims. Rotating an asserted agent name MUST NOT
multiply the budget: the cap is charged to the quota principal, not the
free-form name. A same-principal renewal is exempt (see INV-LL-4).

**Implementation.** `core/state.py:SynapseState.claim` (quota check via
`_claims_owned_by`); the same cap gates `handoff` onto the recipient.

**Pinned by.** `tests/test_state_quotas_leases.py`,
`tests/test_state_lifecycle_handoff.py`. `[model]`

## 2. Epoch fencing

Every lease carries a strictly-increasing generation (`epoch`) so a paused or
superseded owner cannot act on a lease that has moved on.

### INV-EF-1 — epochs are strictly increasing and unique

**Normative.** Every successful `claim` (new, renewal, or takeover) and every
`handoff` MUST stamp a fresh epoch strictly greater than every epoch issued
before it by that hub. Epochs MUST NOT be reused.

**Implementation.** `core/state.py:SynapseState._next_epoch` (monotonic
`_epoch_seq`), stamped at claim and handoff.

**Pinned by.** `tests/test_state_scope_epoch.py`, `tests/test_state_properties.py`.
`[model]`

### INV-EF-2 — a stale epoch is fenced out

**Normative.** When a `release`, `update_task`, `handoff`, or `save_checkpoint`
supplies an `epoch`, the operation MUST be refused unless it equals the claim's
current epoch. A superseded owner therefore cannot drop, mutate, hand off, or
checkpoint a lease that has since been renewed or moved.

**Implementation.** `core/state.py` — the `epoch is stale` guard in `release`,
`update_task`, `handoff`, and `save_checkpoint`.

**Pinned by.** `tests/test_state_scope_epoch.py`,
`tests/test_state_lifecycle_handoff.py`. `[model]`

### INV-EF-3 — optimistic-concurrency version guard

**Normative.** Each claim carries a monotonic `version`. When `update_task`
supplies `expected_version`, the update MUST be refused unless it matches the
current version, so a stale writer cannot clobber a newer value. A successful
mutation MUST increment `version`; a fresh claim or a handoff MUST reset it.

**Implementation.** `core/state.py:SynapseState.update_task` (version CAS and
bump); reset in the `TaskClaim` constructed by `claim`/`handoff`.

**Pinned by.** `tests/test_state_lifecycle_handoff.py`. `[model]`

## 3. Lease liveness

A lease is a liveness hint, not a durable reservation: a crashed agent MUST
eventually lose its claim.

### INV-LL-1 — a live claim's lease is always in the future

**Normative.** For every claim in the live registry, `lease_expires_at` MUST be
strictly greater than the time at which it is observed as live.

**Implementation.** `core/state.py` — leases are `ts + ttl`; expiry runs before
every read.

**Pinned by.** `tests/test_state_properties.py`, `tests/test_state_claims.py`.
`[model]`

### INV-LL-2 — expiry frees the task for takeover

**Normative.** Once a lease reaches or passes its expiry, a heartbeat, claim, or
snapshot at that time MUST drop it, and another agent MUST then be able to claim
the task. A retained checkpoint MUST survive the expiry for the next claimant.

**Implementation.** `core/state.py:SynapseState._expire_claims` (heap-driven,
epoch-fenced) and `expired_checkpoints`.

**Pinned by.** `tests/test_state_claims.py`, `tests/test_state_quotas_leases.py`.
`[model]`

### INV-LL-3 — every TTL is clamped into a bounded window

**Normative.** Every requested and default TTL MUST be clamped into
`[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]`. A non-finite (`inf`/`nan`) request
MUST fall back to the default rather than fail open into an unbounded lease.

**Implementation.** `core/state.py:_clamp_ttl`, fed by `safe_float(..., finite=True)`.

**Pinned by.** `tests/test_state_claims.py`, `tests/test_claim_ttl_coercion.py`.
`[model]`

### INV-LL-4 — same-owner renewal is free

**Normative.** An owner renewing its own live claim (a same-owner, same-principal
`claim`) MUST be admitted without a quota charge, even at the live-claim cap, and
MUST extend the lease and stamp a fresh epoch. Renewal MUST NOT create a second
hold on the task.

**Implementation.** `core/state.py:SynapseState.claim` (`same_principal` bypasses
the quota gate; the task's single entry is replaced in place).

**Pinned by.** `tests/test_state_quotas_leases.py`, `tests/test_state_claims.py`.
`[model]`

## 4. Journal-before-apply (durability)

With a durable journal (`--db`), a mutation's live effect MUST NOT become visible
before its authoritative event is committed.

### INV-JA-1 — publish only after commit

**Normative.** A durable claim-family mutation MUST apply to a private copy of
the state, append its event to the journal off the event loop, and publish the
new live state **only after** the append has committed. A concurrent reader MUST
never observe a provisional mutation whose event has not yet committed.

**Implementation.** `core/state_transaction.py:SerializedStateMutationActor.run`
— `deepcopy` → `mutate(candidate)` → shielded `to_thread(persist)` →
`state.publish_from(candidate)`.

**Pinned by.** `tests/test_claim_journal_atomicity.py`. `[model]` (the model
exercises the state-transition semantics; atomicity against a real journal is
pinned by the cited suite.)

### INV-JA-2 — a failed append publishes nothing

**Normative.** If the journal append raises, the candidate MUST be discarded and
the live state left unchanged: no grant, no wait, no checkpoint side effect. The
synchronous path MUST roll the touched task back to its pre-mutation snapshot.

**Implementation.** `core/state_transaction.py` (actor error path;
`durable_state_transaction` restore-on-`BaseException`).

**Pinned by.** `tests/test_claim_journal_atomicity.py`.

### INV-JA-3 — cancellation waits for the authoritative outcome

**Normative.** A cancellation arriving while an append is in flight MUST wait for
the worker's authoritative commit before propagating, and MUST NOT close the
journal around an in-flight append or leave a committed event whose live state
was discarded.

**Implementation.** `core/state_transaction.py` (`asyncio.shield` + `await
append` on `CancelledError`).

**Pinned by.** `tests/test_claim_journal_atomicity.py`,
`tests/test_claim_grant_recovery.py`.

### INV-JA-4 — durability is split honestly by workload

**Normative.** The lease/claim family (`claim`, `release`, `task_update`,
`handoff`, `checkpoint`, and durable audit events) MUST commit at
`synchronous=FULL` (durable across an OS crash). High-volume chat and progress
MAY commit at `synchronous=NORMAL` (durable across an application crash). The
store MUST run in WAL mode.

**Implementation.** `core/persistence.py:EventStore.append_batch` (per-write
`PRAGMA synchronous=FULL` for `durable=True`, restored to `NORMAL`); durability
flags set per record in `core/journal.py`.

**Pinned by.** `tests/test_journal.py`, `tests/test_hub_persistence.py`.

## 5. Restart replay

A hub restart MUST reconstruct authority from the log, and MUST NOT resurrect
state the live hub would have bounded away.

### INV-RR-1 — replay rebuilds live authority

**Normative.** On startup a journalled hub MUST rebuild its claims, task
lifecycle, checkpoints, blackboard, and idempotency guard by replaying the
event log, and MUST expire any lease already past its expiry at restart time.

**Implementation.** `core/hub.py` (`seed_hub_state`) over
`core/journal.py:replay`.

**Pinned by.** `tests/test_hub_persistence.py`, `tests/test_journal.py`.

### INV-RR-2 — bounded caps are re-applied on replay

**Normative.** Replay MUST re-apply the live bounds — blackboard note caps,
bounded chat history, and the mailbox identity cap — so a restart cannot
reconstruct an unbounded in-memory view from an append-only log.

**Implementation.** `core/journal.py:replay` → `core/ledger.py:restore_progress`
(three drop passes), bounded history seed, `mailbox_pending` LRU restore.

**Pinned by.** `tests/test_journal.py`, `tests/test_hub_state_seed.py`.

### INV-RR-3 — denial evidence is audit-only on replay

**Normative.** `claim_denial` and `guard_denial` events are durable audit
records only. During replay they MUST NOT create or alter any lease; they survive
restart as evidence and nothing more. The same holds for operator-relay and
identity-pin-reclaim audit events.

**Implementation.** `core/journal.py:replay` (no registry branch for the denial
kinds; they fall through untouched).

**Pinned by.** `tests/test_claim_denial_evidence.py`, `tests/test_journal.py`.

## 6. Crash, reconnect, and partition semantics

### INV-CR-1 — a retried mutation applies once

**Normative.** A reconnecting agent that carries an `idem_key` on a claim-family
verb MUST have the retry applied at most once: a duplicate key replays the first
response instead of mutating state again. The idempotency key MUST be namespaced
by sender and message type so it cannot suppress or leak across agents or verbs.
At-most-once MUST survive a hub restart (the key is journalled at FULL
durability).

**Implementation.** `core/hub_ledger_guard.py` (`_MUTATING_TYPES`,
`idempotency_key`, `maybe_replay_duplicate`), `core/idempotency.py`.

**Pinned by.** `tests/test_idempotency.py`, `tests/test_hub_ledger_guard.py`,
`tests/test_hub_persistence.py`.

### INV-CR-2 — handoff is atomic

**Normative.** A `handoff` MUST transfer ownership directly to the recipient with
no release/re-claim window in which a third agent could grab the task. The moved
claim MUST keep its scope, status, and checkpoint, MUST be stamped with a fresh
epoch (fencing the giver out), and MUST reset the version for the new owner. A
handoff MUST also honour file-scope mutual exclusion (INV-ME-2): it MUST be
refused when the moved scope collides with a live claim held by an agent *other
than the recipient*, so a transfer can never hand the recipient files a third
party still holds. The recipient's own overlapping claims never block the move.

**Implementation.** `core/state.py:SynapseState.handoff` (fresh epoch, version
reset, and the same `_scope_conflict` guard the claim path uses, evaluated
against the recipient).

**Pinned by.** `tests/test_state_lifecycle_handoff.py`,
`tests/test_handoff_scope_conflict.py`, `tests/test_state_properties.py`. `[model]`

### INV-CR-3 — a re-claimed task resumes from its checkpoint

**Normative.** A task taken over after its lease lapses MUST resume from its last
saved checkpoint rather than restarting; a normal `release` (task finished) MUST
drop the retained checkpoint so a later unrelated claim of the same id does not
resurrect stale resume state.

**Implementation.** `core/state.py` (`expired_checkpoints` carried across expiry;
cleared on `release`).

**Pinned by.** `tests/test_state_claims.py`, `tests/test_state_lifecycle_handoff.py`.
`[model]`

### INV-CR-4 — a contested namespace fails closed

**Normative.** When two hubs both assert ownership of a namespace (a partition),
the ownership resolver MUST contest and refuse every grant rather than allow a
conflicting one. An ungoverned namespace (no assigned owner) MUST also grant
nothing. (Cross-host safety is stated fully in §9.)

**Implementation.** `core/namespace_ownership.py:resolve` (`CONTESTED` and
`UNGOVERNED` outcomes, deny-by-default).

**Pinned by.** `tests/test_namespace_ownership.py`,
`tests/test_hub_claim_forwarding.py`.

## 7. Per-verb delivery guarantees

Delivery guarantees differ by verb, on purpose, and the difference is normative.

### INV-DG-1 — chat is at-least-once

**Normative.** Chat delivery is at-least-once. The hub MUST NOT consume `idem_key`
for chat and MUST NOT suppress a retry. A sender that may retry SHOULD carry a
printable `client_msg_id`; the hub MUST echo it on every copy so receivers can
deduplicate by `(sender, client_msg_id)`. `client_msg_id` MUST NOT be treated as
authentication.

**Implementation.** `core/handlers/messaging.py` (`_normalize_client_msg_id`);
`CHAT` is absent from `hub_ledger_guard.py:_MUTATING_TYPES`.

**Pinned by.** `tests/test_hub_core_chat.py`.

### INV-DG-2 — claim-family verbs are apply-once

**Normative.** `claim`, `release`, `task_update`, `handoff`, and `checkpoint`
carrying an `idem_key` MUST be applied at most once (see INV-CR-1). This is the
exactly-once-effect complement to chat's at-least-once transport.

**Implementation.** `core/hub_ledger_guard.py:_MUTATING_TYPES`.

**Pinned by.** `tests/test_hub_ledger_guard.py`, `tests/test_idempotency.py`.

### INV-DG-3 — a directed message dead-letters rather than vanishing

**Normative.** A directed message that matches no live recipient MUST be recorded
as a durable dead letter rather than silently dropped. A reconnecting recipient
MUST be able to replay the directed backlog it missed, bounded by a monotonic
receiver watermark it advances by acknowledging (`ack`) or by a registration
`since_seq`. Presence MUST NOT be promoted to a positive delivery verdict, and
the pending count is a transport fact, not proof a model read the message.

**Implementation.** `core/handlers/messaging.py` (dead-letter record),
`core/mailbox_pending.py:MailboxPendingTracker` (monotonic watermark, replay,
restart projection).

**Pinned by.** `tests/test_hub_mailbox_pending.py`, `tests/test_hub_core_chat.py`.

## 8. Hub and federation clock model

### INV-CK-1 — the hub clock is authoritative for ordering

**Normative.** For an inbound `chat`, the hub MUST overwrite the envelope
`timestamp` with its own wall clock; that hub stamp is the only value used to
order retained history and the dead-letter ledger. A finite client instant MAY be
kept as advisory `client_timestamp`; a non-finite or malformed client value MUST
be discarded. A Byzantine future or backdated client stamp therefore MUST NOT
poison ordering.

**Implementation.** `core/handlers/messaging.py` (`_stamp_chat_times`,
`_client_timestamp` — NaN/non-finite discard).

**Pinned by.** `tests/test_chat_timestamp_coercion.py`,
`tests/test_hub_core_chat.py`.

### INV-CK-2 — the signed-frame skew budget

**Normative.** A signed (per-message-authenticated) frame carries a timestamp
that MUST fall inside the skew budget: no older than
`DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS` in the past and no more than
`DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS` in the future, relative to the
verifying hub's clock. A frame outside the window MUST be rejected as `EXPIRED`.
This is the `-10 s / +1 s` budget: a generous allowance for a slow producer and a
tight allowance for a fast one, since a future-dated frame is the more
suspicious.

**Implementation.** `core/message_auth.py` (window constants; the past/future
comparison returning `VerificationResult.EXPIRED` on both the frame-auth and
event-signature paths).

**Pinned by.** `tests/test_message_auth.py`, `tests/test_hub_per_message_auth.py`.

## 9. Multi-host claim safety

Federation observes and merges append-only logs; it MUST NOT arbitrate authority.
The weaker cross-host behaviour is stated here honestly rather than implied to be
stronger than it is.

### INV-MH-1 — authority lives on exactly one hub and never merges

**Normative.** Leases, claims, and name ownership MUST live on exactly one hub. A
merge of federated logs MUST NOT transfer authority: there is no consensus round
and no quorum. Each project namespace MUST have at most one authoritative owning
hub, and only that hub grants claims inside it.

**Implementation.** `core/namespace_ownership.py`, `core/multihub_merge.py`
(grow-only union preserving each event's authoring hub).

**Pinned by.** `tests/test_hub_claim_ownership.py`,
`tests/test_namespace_ownership.py`.

### INV-MH-2 — a peer's claim is advisory only

**Normative.** A claim arriving from a peer's log MUST be recorded as observed
(advisory) state and MUST grant nothing locally. A real claim request MUST be
routed to the namespace's owning hub, never satisfied from the observed fold.

**Implementation.** `core/multihub_fold.py` (`ObservedClaim`, advisory-only),
`core/multihub_claim_transport.py` / `core/handlers/multihub_claim.py`
(forward-to-owner, fail-closed authorisation).

**Pinned by.** `tests/test_multihub_fold.py`, `tests/test_hub_claim_forwarding.py`.

### INV-MH-3 — cross-host routing is deny-by-default

**Normative.** Whether a hub forwards a claim to a peer, and whether it accepts a
forwarded claim, MUST be deny-by-default: the owning hub grants only after both
peer authorisation and a namespace-ownership check pass. A namespace absent from
the ownership map is ungoverned and MUST grant nothing.

**Implementation.** `core/handlers/multihub_claim.py`
(`_authorise_forwarded_claim`, `_owns_namespace`), `core/namespace_ownership.py`.

**Pinned by.** `tests/test_hub_claim_forwarding.py`,
`tests/test_namespace_ownership.py`.

### INV-MH-4 — two default hubs provide no cross-host mutual exclusion

**Normative.** Two hubs started with default settings — no configured namespace
ownership map and no `--multihub-watch` peer — provide **no** cross-host mutual
exclusion. Each such hub enforces only its own in-memory lease table; the same
`task_id` and file scope MAY be claimed independently on each. Cross-host claim
safety is present **only** when the operator has configured namespace ownership
(and, for observation, watch peers). A deployment that needs cross-host mutual
exclusion MUST configure ownership; it MUST NOT assume the single-hub invariants
of §1 span an unconfigured federation.

**Implementation.** `core/namespace_ownership.py` (`UNGOVERNED` fails closed for
*forwarding*, but a default hub does no forwarding and grants locally),
`core/multihub_watch.py` (watch runs only when a peer is named explicitly),
`core/name_ownership.py` (the lease table is single-hub, in-memory by design).

**Pinned by.** `tests/test_hub_claim_forwarding.py`, `tests/test_name_ownership.py`.

> **Note — the name-ownership lease is single-hub.** The `--lease-offline-ttl`
> ownership lease (close code `4016`, "name owned") protects a name across
> reconnects on **one** hub; it does not span hubs. Cross-hub name continuity is
> the separate trust-on-first-use key-pinning layer (close code `4013`), not this
> lease. Note that close code `4013` is overloaded across identity-binding
> failure, a genuine key change, and hub-at-capacity refusal; only `4016` is
> unambiguous.

## Normative constants

These values are part of the contract. The drift guard
`tests/test_coordination_spec.py`
fails if any row disagrees with the implementation.

| Constant | Value | Source |
| --- | --- | --- |
| `MINIMUM_TTL_SECONDS` | `30.0` | `core/state.py` |
| `MAXIMUM_TTL_SECONDS` | `2592000.0` (30 days) | `core/state.py` |
| default lease TTL | `3600.0` (1 hour) | `core/state.py:SynapseState` |
| `MAX_CLAIMS_PER_AGENT` | `128` | `core/state.py` |
| `MAX_DECLARED_PATHS` | `512` | `core/scoping.py` |
| `DEFAULT_WORKTREE` | `""` (root) | `core/scoping.py` |
| `DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS` | `10.0` (past) | `core/message_auth.py` |
| `DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS` | `1.0` (future) | `core/message_auth.py` |
| `DEFAULT_LEASE_OFFLINE_TTL` | `3600.0` | `core/name_ownership.py` |
| `DEFAULT_RESOURCE_TTL_SECONDS` | `300.0` | `core/state_resources.py` |
| `WIRE_PROTOCOL_VERSION` | `2` | `core/protocol.py` |
| `NAME_OWNED_CLOSE_CODE` | `4016` | `connect_failures.py` |

## The machine-checkable model

The claim/lease/fencing invariants above (`INV-ME-*`, `INV-EF-*`, `INV-LL-*`, and
the atomic-handoff and checkpoint-resume rules `INV-CR-2`/`INV-CR-3`) are checked
mechanically, not only by example. `tests/test_coordination_spec_model.py`
defines a Hypothesis `RuleBasedStateMachine` that:

1. drives a real `SynapseState` through randomised sequences of `claim`, renew,
   `release`, `handoff`, `update_task`, `save_checkpoint`, and time advancement;
2. maintains an independent shadow model of expected ownership, epoch, and
   expiry; and
3. asserts every tagged invariant as a Hypothesis `@invariant` after each step,
   plus a final "everything expires" sweep.

Hypothesis searches for a sequence that breaks an invariant and shrinks any
counterexample to a minimal reproducer. Each `@invariant` names the `INV-*`
identifier it enforces, and the drift guard asserts every enforced identifier is
documented here — so the model and this specification cannot drift apart.
