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

The [wire and delivery compatibility guide](protocol-compatibility-plan.md)
describes current feature floors and upgrade boundaries. Version-three delivery
is an additional session-bound profile; it does not turn the version-two mailbox
ACK guarantees below into task-outcome evidence.

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
checks constants, cited test paths and model invariant identifiers: it fails
if a normative constant here disagrees with the implementation, if a `Pinned by`
test file is missing, or if a model invariant is undocumented. Those checks do
not prove that every prose assertion or runtime boundary is exercised.

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
checkpoint a lease that has since been renewed or moved. An operation that
supplies no epoch is checked only against the owner name. The epoch fences it
only when the hub requires fencing (INV-EF-4).

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

### INV-EF-4 — strict fencing requires the epoch (opt-in)

**Normative.** A hub started with `--require-fencing-epoch`
(`require_fencing_epoch=True`) MUST refuse, before any state change, a
`release`, `update_task`, `handoff` or `save_checkpoint` that supplies no
`epoch`. INV-EF-2 then covers every lease mutation, including one from a writer
that names no epoch at all. The shipped client MUST send the epoch of its own
current grant (`claim_granted` or `handoff_granted`) when the caller names none.
`expected_version` (INV-EF-3) stays optional even under this flag. Neither
check can fence a process that writes repository files directly.

**Implementation.** `core/handlers/leasing.py:_refuse_unfenced`;
`client/agent_dispatch.py` (the `lease_epochs` memory) and
`client/agent_outbound_tasks.py`.

**Pinned by.** `tests/test_fencing_epoch.py`: on real hubs, an epoch-less
release from a superseded writer succeeds by default and is refused under the
flag, and the shipped client passes the flag.

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
`claim`) MUST be admitted without a quota charge, even at the live-claim cap,
subject to the normal scope-conflict checks. It MUST set expiry to the current
hub time plus the newly clamped TTL and stamp a fresh epoch. Requesting a shorter
TTL can shorten the previous expiry; renewal does not promise a non-decreasing
deadline. Renewal MUST NOT create a second hold on the task.

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
— `deepcopy` → `mutate(candidate)` → context-preserving executor Future →
`state.publish_from(candidate)`.

**Pinned by.** `tests/test_claim_journal_atomicity.py`,
`tests/test_state_actor_shutdown.py`. `[model]` (the model
exercises the state-transition semantics; atomicity against a real journal is
pinned by the cited suite.)

### INV-JA-2 — a failed append publishes nothing

**Normative.** If the journal append raises, the candidate MUST be discarded and
the live state left unchanged: no grant, no wait, no checkpoint side effect. The
synchronous path MUST roll the touched task back to its pre-mutation snapshot.
A lease that expires during journal I/O MUST remain expired. Its retained
checkpoint MUST come from the captured snapshot, never a failed provisional
checkpoint mutation.

**Implementation.** `core/state_transaction.py` (actor error path;
`durable_state_transaction` restore-on-`BaseException`).

**Pinned by.** `tests/test_claim_journal_atomicity.py`,
`tests/test_state_actor_shutdown.py`.

### INV-JA-3 — cancellation waits for the authoritative outcome

**Normative.** A cancellation arriving while an append is in flight MUST wait for
the worker's authoritative commit before propagating, and MUST NOT close the
journal around an in-flight append or leave a committed event whose live state
was discarded. Repeated caller cancellation and normal event-loop shutdown
MUST preserve this ordering. An append failure MUST discard the candidate
before releasing the actor lock.

**Implementation.** `core/state_transaction.py` (retain the executor Future
independently of runner Tasks; shield and drain its authoritative outcome).

**Pinned by.** `tests/test_claim_journal_atomicity.py`,
`tests/test_claim_grant_recovery.py`, `tests/test_state_actor_shutdown.py`.

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

### Pinned installation artifacts and sealed bytes

Trusted supervisors may use `capture_protected_install_artifact` to read one
independently enrolled absolute path. Every opened ancestor and the regular
artifact MUST be owned by UID and GID zero, without group/other write permission.
Traversal MUST refuse symlinks. Private artifacts MUST also refuse group/other
access. The complete bytes MUST match the enrolled lowercase SHA-256 within a
positive individual limit of at most 64 MiB. Returned content is immutable and
omitted from its value's representation; private bytes MUST stay out of logs.
This is capture-time evidence, not continued custody of the source pathname.

`capture_protected_install_artifact_set` requires an independently retained
complete declared path set and private subset. Missing, extra or duplicate pins
MUST refuse. The aggregate limit is positive and at most 1 GiB. Capture returns
the whole set in canonical path order, or raises without publishing a partial
set. It does not infer the manifest from the caller's pins or discover the
application's actual interpreter, loader, import, configuration and trust closure.

`retain_sealed_installation_artifacts` performs that capture before publishing
a read-only path-to-descriptor mapping. Linux memory files carry close-on-exec
and seals against content writes, growth, shrinkage and changes to the seal set.
Their shared inode seals also apply through duplicated descriptors. Consumers
MUST borrow descriptors only within the context and MUST NOT close or replace
them. The context closes every owned descriptor on normal or exceptional exit;
a failed or partial memory-file write MUST NOT publish a descriptor set.
Unsupported sealing profiles MUST refuse before accessing installation bytes.

Captured content and memory-file pages coexist during preparation, so operators
must budget for both copies and allocator overhead. Seals protect byte integrity,
not confidentiality or process isolation. Explicit descriptor handover, forked
or duplicated descriptors can retain access beyond the original context; their
custody and closure belong to the supervisor's process lifecycle. No executable
launch, UID separation, namespace/syscall policy, authority grant, secret
provisioning or complete installed-runtime qualification follows from these APIs.

The Linux profiles in `tests/fixtures/protected_install_artifacts_linux.py`,
`protected_installation_set_linux.py` and `protected_sealed_installation_linux.py`
exercise actual root-owned public OS bytes, kernel seals, explicit child handover,
close-on-exec and resource-limit cleanup. Portable owning modules test enrollment
shape and modeled support guards. Public OS examples are mechanics fixtures;
they do not qualify a complete application installation or private credentials.

#### Native installation artifact APIs

::: synapse_channel.core.protected_write_install_artifacts.ProtectedInstallArtifact

::: synapse_channel.core.protected_write_install_artifacts.capture_protected_install_artifact

::: synapse_channel.core.protected_write_installation_set.ProtectedInstallArtifactPin

::: synapse_channel.core.protected_write_installation_set.capture_protected_install_artifact_set

::: synapse_channel.core.protected_write_sealed_installation.retain_sealed_installation_artifacts

### Durable protected execution evidence

The library execution driver requires native Linux `openat2`; it does not use
portable path-resolution fallbacks. It durably consumes the exact prepared begin once,
before dispatching its declared file operations. Replayed or conflicting
consumption performs no filesystem operation. Each started step has one ordered
result bound to its predecessor and immutable observation bytes. A crash or
failure leaves the retained frontier uncertain; inspection cannot authorize a
retry, rollback, cleanup or settlement.

`inspect_protected_execution_frontier` reads that exact completed prefix and its
first missing, started, failed or unknown step. Undeclared keys and records beyond
the frontier refuse. Observation verification additionally checks closed canonical
bytes, exact operation and postcondition bindings, enrolled roots and physical
identity continuity, including root aliases, creation, rename and absence.
Final-state comparison is a separate sequential descriptor check, requiring
independent writer quiescence and namespace custody before settlement.

`ProtectedWriterSequence` retains a private credential-bound file and serializes
allocations across threads and processes. A value is persisted before return;
restart reopens the same inode and cannot reset its floor. Missing, replaced,
damaged or exhausted state refuses. The operator must enroll creation for a new
writer key/incarnation rather than recreate a lost counter for an old credential.
Its closed version-two record checksums the credential binding and entire
counter. A torn or damaged record refuses before issuing another value; it
cannot fall back to an older value or automatically repair/reset state.
This requires a filesystem honoring `fsync` and independent file custody;
the checksum does not authenticate a maliciously rewritten valid record.
A damaged counter permanently refuses that incarnation, including after reopen.
Recovery means independent enrollment of a new key/incarnation and fresh counter
location, retaining the damaged record; it never means repairing or resetting
the old credential's floor.

The disposable Linux x86-64 writer may install the irreversible descriptor filter
after closing unapproved descriptors and before target access. It denies native
ancillary-message, io_uring and `pidfd_getfd` entrypoints, synchronizes existing threads and is
inherited by descendants; incompatible ABIs refuse or terminate. Existing outside
holders and pre-existing kernel polling rings remain deployment obligations.
These APIs are library mechanisms, without a new wire handler or installed
supervisor. The native profiles in the
[contributor guide](https://github.com/anulum/synapse-channel/blob/main/CONTRIBUTING.md) exercise
actual kernel and storage behavior; they do not prove isolated installation.

::: synapse_channel.core.protected_write_execution_journal

::: synapse_channel.core.protected_write_execution_observations

::: synapse_channel.core.protected_write_sequence

::: synapse_channel.core.protected_write_descriptor_filter

### Retained filesystem observations

`inspect_protected_file` observes a path relative to an enrolled borrowed root
descriptor. `hold_protected_path` retains the exact observed root, ancestors
and optional target without creating or truncating anything. Both accept the
keyword-only `require_openat2`, defaulting to `False` for their existing POSIX
diagnostic contract. When `True`, inspection MUST verify each opened ancestor
and leaf through Linux `openat2` before reading target bytes. Retention MUST
repeat that restricted identity check during revalidation. Unsupported kernels
and userspaces MUST refuse instead of using a weaker opener.

The native profile requires 64-bit `x86_64` or `aarch64` Linux. Resolution uses
`RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV`, compares parent and
leaf device/inode identities, and refuses a present leaf when absence was
expected. Descriptor inputs must be exact nonnegative Python integers in the
C `int` range, preventing ABI truncation to another open descriptor. Every
temporary kernel reference is closed on success and refusal.
Borrowed descriptors remain owned by the caller. This profile does not mount,
create, remove, chmod or write a target.

`ProtectedPathDescriptors.read_bytes()` returns immutable bytes only for an
existing, single-link regular file matching the enrolled size, digest, identity
and mode, under the explicit byte budget. It uses `pread`, preserving the
caller's file offset. It then revalidates the retained chain and content before
returning. A closed context, absent or directory target, changed attachment,
ownership, bytes, observed metadata or exhausted budget MUST refuse. A detached
external root pathname cannot redirect the captured data to a lookalike root.

Compatibility: existing function arguments and the descriptor value's existing
positional field order are preserved; its new internal kernel option is
keyword-only. Capturing bytes adds a final full revalidation read and retains
up to the selected content budget in memory. Operators must select budgets
appropriate to their process resources. No published performance guarantee is
made for this newly exposed primitive.

These values and file descriptors do not grant write or read authority. Current
admission/custody, authenticated service ordering, complete domain validation
and operating-system isolation of bypass writers remain separate requirements.
The checks narrow observation races; they cannot atomically exclude an
unconfined process from changing multi-component filesystem state.

Portable API contracts are exercised in `tests/test_protected_write_descriptors.py`,
`tests/test_protected_write_inspection.py`, `tests/test_protected_write_openat2.py`
and the preparation integration tests. Actual Linux syscall, procfs, inotify
and independent-mutator cases are collected explicitly from
`tests/fixtures/protected_paths_linux.py`; every Linux CI Python lane runs that
complete profile together with default collection. The coverage lane measures
their union in the same invocation before evaluating the existing floor.

#### Native retained-path APIs

::: synapse_channel.core.protected_write_openat2.verify_protected_openat2_path

::: synapse_channel.core.protected_write_inspection.inspect_protected_file

::: synapse_channel.core.protected_write_descriptors.hold_protected_path

::: synapse_channel.core.protected_write_descriptors.ProtectedPathDescriptors

### Durable protected-session enrollment

Protected-session enrollment belongs to the existing `SynapseHub` state,
`SerializedStateMutationActor` and `EventStore`. Issue and revoke MUST append
with `durable=True` before publishing their candidate. A failed append MUST
publish nothing; cancellation follows the actor's authoritative-outcome rule.
Replay reconstructs the complete issue/revoke prefix before protected work.
Missing protected-write configuration, a filtered prefix, unsupported schemas,
unknown protected events, reused identifiers and revocation without a live
predecessor MUST refuse reconstruction.

The closed `synapse-protected-write.enrollment.v1` issue schema retains the
session's authority, principal, target, revision, transaction, proposal digest,
verbs, expiry, writer incarnation, key ID and key fingerprint. Revocation
retains its schema and exact session ID. Neither event carries secret bytes.
Session IDs, key IDs and domain-separated key fingerprints cannot be reused,
even after revocation. A principal with an unrevoked writer incarnation cannot
receive a second one. Expiry withdraws admission but does not replace explicit
revocation of the old incarnation.

`ProtectedSessionScope` validates metadata without constructing a credential.
`ProtectedEnrollmentRecord` adds retained key identity and revocation.
`ProtectedSessionEnrollment` binds that scope to the actual operator-held key.
Projection requires an unexpired, unrevoked record, a finite trusted time and
the matching unrevoked single-principal key. Missing or changed retained keys
produce no usable enrollment.

`ProtectedEnrollmentControl.issue_from_metadata` accepts only the scope fields
and `key_id`, with sorted unique verbs and an explicit `writer_incarnation`
field. It binds them to an already retained key indexed by session ID. The
caller MUST authenticate the operator before invoking issue or revoke. This
primitive does not expose an operator listener or establish account/key
isolation. An admission handler MUST use the current projection and recheck
it under the authority actor before mutation.

Migration: `ProtectedSessionEnrollment` preserves its twelve original
positional parameters. `writer_incarnation` is keyword-only. Signed `begin`
and `settle` now require a nonempty server-side incarnation equal to their
existing body field. Callers enabling those verbs must bind the enrollment at
issue time. Other verbs retain their existing scope checks. No wire field or
protocol version changes in this delivery.

The authority implementation is Python. Native clients continue to use the
existing signed envelope; no client-side journal or parallel grant registry is
introduced. Behavioral evidence is in
`tests/test_protected_write_enrollment_control.py`,
`tests/test_protected_write_enrollment_journal.py`,
`tests/test_protected_write_session_scope.py` and
`tests/test_protected_write_session_auth.py`. These exercise actual SQLite
reopen/replay, issue races, failed appends, signed admission and durable revoke.

#### Native enrollment APIs

::: synapse_channel.core.protected_write_session_scope.ProtectedSessionScope

::: synapse_channel.core.protected_write_session_auth.ProtectedSessionEnrollment

::: synapse_channel.core.protected_write_enrollment_journal.ProtectedEnrollmentRecord

::: synapse_channel.core.protected_write_enrollment_control.ProtectedEnrollmentControl

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
restart as evidence and nothing more. Operator-relay and identity-pin-reclaim
audits likewise MUST NOT create or alter a lease. A validated pending inbound
operator-relay audit MAY restore only the separate two-person approval ledger as
specified by INV-RR-4.

**Implementation.** `core/journal.py:replay` (no registry branch for the denial
kinds; they fall through untouched).

**Pinned by.** `tests/test_claim_denial_evidence.py`, `tests/test_journal.py`.

### INV-RR-4 — pending relay quorum restores without restoring approval

**Normative.** A journal-backed two-person relay ledger MUST restore a pending
first principal only from a complete inbound `pending` audit. A terminal audit
for the same action/namespace/task MUST clear it. Outbound, malformed, or
contradictory evidence MUST NOT create or retain a quorum, and replay MUST NOT
restore an approved verdict. A second principal still MUST pass current peer,
scope, and namespace-ownership authorization before the action applies.

**Implementation.** `core/journal.py:replay` folds operator-relay audit rows
through `core/operator_relay_approval.py:RelayApprovalLedger.restore_audit`;
`core/hub_state_seed.py` publishes the bounded reconstructed ledger.

**Pinned by.** `tests/test_operator_relay_approval.py`,
`tests/test_hub_state_seed.py`, `tests/test_hub_operator_relay.py`.

## 6. Crash, reconnect, and partition semantics

### INV-CR-1 — a retried mutation applies once

**Normative.** On a journal-backed hub, a reconnecting agent that carries an
`idem_key` on a covered mutation MUST have its authoritative events and exact
response committed in one FULL-durability SQLite transaction. Covered mutations
include the claim family, resource offers, guard-denial evidence, and task-board
declare, update, and progress writes, accepted memory writes, and authorized
single-person or two-person operator-relay verdicts. The operation key MUST be
namespaced by the authenticated sender and normalized message type. An identical retry MUST replay
the stored response without a second mutation, including after restart. Reusing
that operation key with a different canonical request digest MUST return a stable
value-free `idempotency_conflict` and apply nothing. Operation rows are retained
indefinitely in this release. Keyed and unkeyed writes to the same in-memory
subject MUST share one serialized candidate-publication boundary, so a write
cannot be lost behind an in-flight durable commit. If cancellation arrives after
commit, every live in-memory projection covered by that commit MUST publish before
cancellation propagates. Unkeyed operations remain at-least-once; a hub without a
journal provides process-local response suppression only. Epoch/version checks
coordinate protocol clients but do not fence a process that writes repository
files directly.

**Implementation.** `core/atomic_operations.py`,
`core/persistence.py:EventStore.commit_operation`,
`core/state_transaction.py:SerializedStateMutationActor.run_atomic`, and
`core/hub_ledger_guard.py`; the operator-relay handler re-runs current transport
authorization before replay, and the typed ledger client plus `synapse task`
commands expose stable keys without generating replacements across manual retries.

**Pinned by.** `tests/test_atomic_operations.py`,
`tests/test_atomic_operation_handlers.py`,
`tests/test_atomic_operation_killpoints.py`, `tests/test_idempotency.py`, and
`tests/test_hub_ledger_guard.py`, `tests/test_hub_operator_relay.py`.

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

**Normative.** When the configured assertion feed reports two hubs owning a
namespace (a known partition), the ownership resolver MUST contest and refuse every grant rather than allow a
conflicting one. An ungoverned namespace (no assigned owner) MUST also grant
nothing. (Cross-host safety is stated fully in §9.)

**Implementation.** `core/namespace_ownership.py:resolve` (`CONTESTED` and
`UNGOVERNED` outcomes, deny-by-default).

**Pinned by.** `tests/test_namespace_ownership.py`,
`tests/test_hub_claim_forwarding.py`.

### INV-CR-5 — a disconnected holder keeps its claims only for the lease window

**Normative.** A claim MUST survive its holder's disconnect. While the holder has
been offline for less than the hub's lease window (`lease_offline_ttl`, default
3600 s, the window name-ownership leases use), a conflicting claim MUST be refused.
The state snapshot MUST mark the claim with `holder_online: false` and
`holder_offline_seconds`. Once the holder has been offline for the whole window,
the next claim attempt on the hub MUST release all of that holder's claims first.
Each release MUST be journalled as an ordinary release, so replay agrees, and
MUST be announced with `released_by: "hub:holder-offline"`. A holder that
reconnects inside the window resets it. After a hub restart the window runs from
hub start. The lease TTL remains the upper bound.

**Implementation.** `core/claim_holder_presence.py`, called from
`handlers/leasing.py:handle_claim` and `handlers/multihub_claim.py`; departures from
`hub_connection.py:unregister`; state marks in `handlers/snapshots.py`.

**Pinned by.** `tests/test_claim_holder_presence.py`.

## 7. Per-verb delivery guarantees

Delivery guarantees differ by verb, on purpose, and the difference is normative.

### INV-DG-1 — chat is at-least-once; a named retry is accepted once

**Normative.** Chat delivery is at-least-once. The hub MUST NOT consume `idem_key`
for chat. A sender that may retry SHOULD carry a printable `client_msg_id`; the
hub MUST echo it on every copy. Once a copy from a directly connected sender has
reached a live recipient (or a peer hub's forward outbox), the hub remembers its
`(sender, client_msg_id)` in process memory for at most 24 hours and 4096 pairs.
A later chat with that pair and the same content MUST NOT be stored, journalled or
delivered again: the hub answers the sender with a `system` frame carrying
`duplicate: true`, the `client_msg_id` and the first copy's `msg_id` (and `seq`).
The same pair with different content MUST be refused. A retry of a chat that
reached nobody is a redelivery attempt and MUST be routed again. A chat without a
`client_msg_id` keeps plain at-least-once delivery. Receivers SHOULD still
deduplicate by `(sender, client_msg_id)`: a retry after a hub restart or outside
the retention, and copies arriving by other paths, are not covered. `client_msg_id` MUST
NOT be treated as authentication.

**Implementation.** `core/handlers/messaging.py` (`_normalize_client_msg_id`,
`route_chat`); `core/chat_dedupe.py`; `CHAT` is absent from
`hub_ledger_guard.py:_MUTATING_TYPES`.

**Pinned by.** `tests/test_hub_core_chat.py`, `tests/test_chat_dedupe.py`.

### INV-DG-2 — keyed coordination verbs are apply-once

**Normative.** `claim`, `release`, `task_update`, `handoff`, `checkpoint`,
`guard_denial`, resource mutations, `ledger_task`, `ledger_task_update`, and
`ledger_progress` carrying an `idem_key` MUST obey the journal-backed atomic
apply-once boundary in INV-CR-1. Their transport may still be retried; their
committed effect and canonical response cannot separate. Unkeyed requests remain
at-least-once.

**Implementation.** `core/hub_ledger_guard.py:_MUTATING_TYPES`, the covered
handlers, and `core/persistence.py:EventStore.commit_operation`.

**Pinned by.** `tests/test_atomic_operation_handlers.py` and
`tests/test_atomic_operation_killpoints.py`.

### INV-DG-3 — a directed message dead-letters rather than vanishing

**Normative.** A directed message that matches no consume-live recipient MUST
be recorded in the bounded dead-letter diagnostic ledger. With a durable journal,
its chat event MUST remain available for replay while retained. A reconnecting
mailbox receiver MUST be able to request the retained directed backlog it missed,
bounded by a monotonic
receiver watermark it advances by acknowledging (`ack`) or by a registration
`since_seq`. Presence MUST NOT be promoted to a positive delivery verdict, and
the pending count is a transport fact, not proof a model read the message.
Without a journal, restart persistence and durable mailbox watermarks are absent.
Compaction and configured history/identity bounds still limit recovery; the
diagnostic target count itself is process-local and bounded, not a durable queue.

**Implementation.** `core/handlers/messaging.py` (dead-letter record),
`core/mailbox_pending.py:MailboxPendingTracker` (monotonic watermark, replay,
restart projection).

**Pinned by.** `tests/test_hub_mailbox_pending.py`, `tests/test_hub_core_chat.py`.

### INV-DG-4 — durable audit kinds have an explicit receipt disposition

**Normative.** Every declared durable `EventKind` MUST be classified as either
receipt-bearing or intentionally non-receipt, and the two sets MUST be disjoint. A
claim denial, guard denial, dead-letter escalation, identity-pin reclaim or
identity-key enrolment MUST project its existing bounded audit payload through
the universal receipt read side with the original sequence and timestamp.
Projection MUST NOT invent
success, model action, or an undisclosed raw task/path value. Ordinary state,
chat, memory, and idempotency events remain explicitly non-receipt rather than
silently falling out of the feed.

Identity-change receipts preserve `approved`, `not_applied`, `denied` and
`applied` as separate states. An approval never proves application. Storage
refusal details contain only authored text; diagnostics remain in server logs.

**Implementation.** `core/universal_receipts.py` defines the exhaustive
`UNIVERSAL_RECEIPT_EVENT_KINDS` / `NON_RECEIPT_EVENT_KINDS` disposition and the
audit projections; `dashboard_store_feeds.py:build_receipts_feed` and
`core/event_query.py` consume the shared receipt-bearing set.

**Pinned by.** `tests/test_universal_receipts.py`,
`tests/test_cli_event_query.py`, `tests/test_dashboard_store_feeds_activity.py`
and `tests/test_identity_persistence_refusal_runtime.py`.

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

**Normative.** Under the default frame-authentication profile, a signed frame carries a timestamp
that MUST fall inside the skew budget: no older than
`DEFAULT_MESSAGE_AUTH_WINDOW_SECONDS` in the past and no more than
`DEFAULT_MESSAGE_AUTH_FUTURE_SKEW_SECONDS` in the future, relative to the
verifying hub's clock. A frame outside the window MUST be rejected as `EXPIRED`.
This is the `-10 s / +1 s` budget: a generous allowance for a slow producer and a
tight allowance for a fast one, since a future-dated frame is the more
suspicious.

**Operations.** Every host that produces or verifies signed frames MUST run and
monitor a clock-synchronization service (for example chrony,
`systemd-timesyncd`, or another NTP implementation). The operational target MUST
be tighter than the asymmetric `-10 s / +1 s` acceptance window, especially the
one-second future allowance; the protocol budget is a rejection boundary, not a
substitute for synchronized clocks. An embedded replay cache can set a different
past/future window; an Ed25519 trust bundle uses its supplied cache. Record those
effective values rather than infer them from the defaults. An operator that cannot prove a host's
clock is synchronized SHOULD stop signed traffic from that host until the
offset is corrected. Apply the same discipline to every hub in a multi-host or
federated deployment because verification is relative to the receiving hub's
clock.

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
(and, for observation, watch peers). Ownership maps must agree between hubs;
the watch feed cannot detect a conflicting peer that has never been observed.
A deployment that needs cross-host mutual
exclusion MUST configure ownership; it MUST NOT assume the single-hub invariants
of §1 span an unconfigured federation.

**Implementation.** `core/namespace_ownership.py` (`UNGOVERNED` fails closed for
*forwarding*, but a default hub does no forwarding and grants locally),
`core/multihub_watch.py` (watch runs only when a peer is named explicitly),
`core/name_ownership.py` (the lease table is single-hub, in-memory by design).

**Pinned by.** `tests/test_hub_claim_forwarding.py`, `tests/test_name_ownership.py`.

### INV-MH-5 — one federated event identity binds one content fingerprint

**Normative.** For an observed peer log, `(hub_id, seq)` MUST bind the authoring
hub, sequence, exact timestamp, event kind, and complete payload to one canonical
SHA-256 fingerprint. An exact duplicate MUST be idempotent. Different content at
one identity MUST raise a typed integrity failure before any event, observed view,
cursor, protocol metadata, or clock metadata from that batch becomes live. The peer
MUST remain quarantined across reconnect and durable restart until an explicit,
audited recovery names a new log generation or accepted checkpoint. Evidence MUST
contain bounded metadata and both fingerprints, never the event payload. Timestamp,
arrival order, authentication, or lexical digest order MUST NOT select a winner.
New events in an exclusive cursor response MUST be contiguous; an unseen sequence at
or below the cursor, a gap, or rollback MUST fail before publication.

**Implementation.** `core/multihub_equivocation.py` (canonical fingerprint,
typed failures, batch validation), `core/multihub_follower.py` (candidate publish,
quarantine, explicit recovery), `core/journal.py` (durable bounded evidence),
`core/multihub_watch.py` (standing-watch refusal).

**Pinned by.** `tests/test_multihub_equivocation.py`,
`tests/test_multihub_merge.py`, `tests/test_multihub_follower.py`,
`tests/test_multihub_watch.py`, `tests/test_journal.py`.

### INV-MH-6 — divergent observed task heads stay visibly unresolved

**Normative.** For every observed task id, later same-hub sequence records and
valid content-bound cross-hub parent edges MUST remove only their proven
ancestors from the candidate heads. When at least two remaining heads have
different canonical task-record fingerprints, the projection MUST expose one
unresolved, payload-free conflict object containing each contender's hub,
sequence, timestamp, record fingerprint, event fingerprint, and bounded parent
status. Equal heads MUST converge without a conflict. The object MUST NOT expose
the losing task payload, grant authority, infer concurrency from a missing edge,
or apply an automatic resolution policy. Timestamp order chooses only the
display among unresolved heads.

**Implementation.** `core/multihub_fold.py` (`ObservedBoardConflict`, verified
head fold), `cli_multihub.py` (bounded text and JSON projection).

**Pinned by.** `tests/test_multihub_fold.py`,
`tests/test_multihub_follower.py`, `tests/test_cli_multihub.py`.

### INV-MH-7 — task parent edges are content-bound and same-task

**Normative.** An additive task `causal_parent` MUST contain exactly a non-empty
bounded hub id, positive sequence, and lowercase SHA-256 fingerprint of the
complete parent event. The hub MUST reject malformed references before changing
the local board or journal. The observed fold MUST accept the edge only when the
named `(hub_id, seq)` event exists, its canonical complete-event fingerprint
matches, and both events name the same task. A missing, mismatched, wrong-task,
or self-parent reference MUST suppress no contender. Parent metadata MUST remain
outside the compatible projected task record. One accepted edge proves only the
recorded observation relation it names; absence of an edge MUST NOT be called
concurrency.

Malformed-parent refusals MUST be private to the requesting connection and
MUST NOT consume a durable operation key. Only explicitly typed, deliberately
authored validation reasons may be echoed. Unexpected parser faults MUST return
`Causal parent validation failed; task was not changed.` without exception text;
diagnostics remain in server logs. A corrected write MAY reuse its key.

**Implementation.** `core/task_causality.py` (bounded wire value and task/event
separation), `core/handlers/planning.py` (ingress and atomic journal binding),
`core/multihub_fold.py` (content verification and ancestry).

**Pinned by.** `tests/test_task_causality.py`,
`tests/test_atomic_operation_handlers.py`, `tests/test_multihub_fold.py`,
`tests/test_client_ledger_envelopes.py`, `tests/test_cli_tasks.py`.
The private refusal, same-key recovery, replay and restart journey is pinned by
`tests/test_planning_causal_refusal_runtime.py`.

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
| `WIRE_PROTOCOL_VERSION` | `7` | `core/protocol.py` |
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
documented here. That guard checks identifier coverage and constants, not the
semantic meaning of every sentence or every production boundary.
