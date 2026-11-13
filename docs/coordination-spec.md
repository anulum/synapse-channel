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

### Which requests the epoch fences, and why the others are outside it

*Informative.* An epoch belongs to a lease, so only requests that act on an
existing lease can carry one:

- `release`, `task_update`, `handoff` and `checkpoint` (INV-EF-2, and
  INV-EF-4 under `--require-fencing-epoch`);
- a renewal that names `renew_epoch`. The hub refuses it unless the caller
  still owns the lease, the epoch is the lease's current epoch or the anchor of
  its uninterrupted same-owner, same-scope ownership (`ownership_epoch`), no
  protected custody is outstanding on the task, and the request repeats the
  original paths
  (`core/state.py:SynapseState.renew_claim`). A plain re-`claim` by the holder
  is checked by owner name and stamps a new epoch.

A new `claim` creates the epoch, so it has none to present. The other mutating
requests do not act on a lease, and an epoch would have to invent one. Each
family relies on its own retry and conflict rule instead, as listed in the
[delivery matrix](delivery-matrix.md):

- the progress ledger, findings, recall log, resources and native message
  records use `idem_key` apply-once;
- delivery steps bind `request_id`, `idempotency_key` and `mutation_id` to one
  delivery record;
- attachments bind the content digest, the `upload_id` and each chunk's
  `offset`;
- identity rotation and pin reclaim name `expected_key_id`;
- a spend request uses an idempotency key scoped to caller, pool, window,
  seat, task and operation;
- a claim forwarded to another hub is applied under that hub's own `claim`
  rule;
- `guard_denial` only records evidence and changes no lease.

None of these rules fences a process that writes repository files directly.

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

### Native process and mount observations

These Linux APIs are components for an independently supervised writer. They do
not install accounts, a supervisor or a recovery service.

`capture_protected_cgroup_controls` traverses retained directory descriptors to
the actual cgroup v2 root. It captures control ownership and ordinary-domain
type; `verify_protected_cgroup_controls` refuses non-root or writable migration
and delegation controls. Parsed bytes alone establish representation, not
kernel origin or physical custody.

`capture_protected_writer_subtree` binds the current process membership to the
retained subtree inode. `inspect_protected_writer_subtree` samples recursive
population and freeze state without signalling. `stop_protected_writer_subtree`
requires the independently enrolled identity and a finite deadline, writes
`cgroup.kill`, then waits for observed recursive emptiness. The caller MUST
exclude new admission and migration throughout stop and recovery. An empty
sample or a missing leader PID alone MUST NOT release recovery custody.

Kernel capture reads one process-status sample. It cross-checks the real,
effective and saved IDs and supplementary groups against current process
credentials, and retains filesystem IDs, capabilities, no-new-privileges and
seccomp state from that sample. It does not detect later capability or
filesystem-ID changes; the caller must keep credential state fixed throughout.
Descriptor, subtree, namespace and mount-table comparisons refuse changes
observed across their capture. Verification requires the exact independently
enrolled profile, root-owned migration controls, a read-only base and the exact
private data-only writable mounts. It does not authenticate a caller-constructed
observation or independently certify the installation and syscall programme.

`inspect_protected_writer_mount_targets` requires exact retained mountpoint
descriptors, private leaf ownership and root-owned non-writable ancestors.
`apply_protected_writer_mount_restrictions` MUST run only inside an already
separate, single-threaded trusted root-stage child. It recursively makes the
base and submounts private/read-only, then clears read-only only for the exact
enrolled data mounts while setting nosuid/nodev/noexec. It refuses the retained
host namespace and has no legacy remount fallback. A partial failure MUST abort
the child; borrowed descriptors remain owned by the supervisor. The result is
root-stage observation, never proof of the later dedicated writer's admission.

The explicit Linux profiles require procfs, cgroup v2, a systemd user session
and Bubblewrap. Owned transient groups and private namespaces test real
population, termination, descriptor races and mount changes. Controlled
malformed files are bound only in disposable private namespaces to exercise
capture refusals; those files are not represented as genuine kernel evidence.
Successful component tests do not qualify a complete installed authority.

#### Native process and mount APIs

::: synapse_channel.core.protected_write_cgroup_parsing

::: synapse_channel.core.protected_write_cgroup_controls

::: synapse_channel.core.protected_write_subtree

::: synapse_channel.core.protected_write_subtree_stop

::: synapse_channel.core.protected_write_mounts

::: synapse_channel.core.protected_write_kernel_boundary

::: synapse_channel.core.protected_write_mount_attributes

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
actual kernel and storage behaviour; they do not prove isolated installation.

::: synapse_channel.core.protected_write_execution_journal

::: synapse_channel.core.protected_write_execution_observations

::: synapse_channel.core.protected_write_sequence

::: synapse_channel.core.protected_write_descriptor_filter

### Kernel-checked execution composition

`execute_kernel_checked_protected_write` binds the prepared admission to the
existing durable driver, a concrete signed current-status client and the
enrolled immutable execution-evidence store. Evidence operation limits MUST
equal the prepared limits. The copied root-descriptor mapping MUST contain
exactly the enrolled root names; each fresh capture MUST match the prepared
device/inode identities and the independently enrolled kernel policy.

The composition supplies fresh actual capture at every driver isolation check,
including before consumption and immediately before an effect. It does not
accept a caller-provided positive isolation predicate. The driver retains its
one-time consumption, ordered step history, bounded evidence readback and
uncertain-frontier semantics. Borrowed descriptors and stores remain caller
owned on both success and refusal.

The caller MUST run this API only inside an independently qualified trusted
writer launch. The expected kernel policy, authority endpoint and verification
key, persistent sequence source, execution journal, root and subtree
descriptors MUST be enrolled independently of the writer's own declarations.
Source integrity, separate principals, syscall confinement, descriptor custody
and exclusion of outside writable holders remain launch obligations. Signed
current status is a point-in-time read, not revocation fencing. This API
installs no writer or service and grants no settlement or recovery permission.

When configured with `MessageAuthKey`, `ProtectedWriteCurrentStatus` retains a
symmetric HMAC-SHA256 key for the authority response, held by the trusted writer. A
verified response does not establish non-forgery against that writer. Request
binding, freshness and replay checks remain necessary, but they do not turn
shared-key authentication into independent authority attestation.

An explicitly enrolled `EventSignatureKey` selects public-key verification
through the existing Ed25519 event-signature verifier instead. The public-key
profile requires the current domain-separated signature version and MUST refuse
legacy domain-less version-one event signatures. The authority uses
`sign_event_frame`; the writer supplies the public verification entry and
its response replay cache, without receiving the authority's private key. The
result still MUST match the original request digest, authority, continuity,
session, enrolment revision and exact begun reservation. A wrong key, sender,
expired or revoked key, replay or other signature profile refuses the read;
verification never falls back to HMAC. This profile requires the `encryption`
extra and changes neither request authentication nor the seven-verb state
machine. Independent key provenance, signer/process isolation and physical
writer custody MUST still be qualified separately.

The [Linux profiles](https://github.com/anulum/synapse-channel/blob/main/CONTRIBUTING.md#kernel-checked-execution-and-authority-ipc-profiles)
exercise pre-effect refusal, signed status under the descriptor filter, durable
enrolment revocation, delayed-begin refusal and owned process cancellation.
Their component results do not certify an installed writer profile.

::: synapse_channel.core.protected_write_kernel_execution

### Protected frontend control channel

An optional private Unix channel separates the protected WebSocket frontend
from the existing authority ingress. `ProtectedWriteControlServer` opens one
logical `ProtectedWriteRequestSession` per admitted connection, using the same
live enrolment registry, durable nonce ledger, seven handlers and mutation
actor as direct `ProtectedWriteTransport` connections. It creates no second hub,
state or journal. `ProtectedWriteControlGateway` forwards the client's original
UTF-8 request text; the authority independently decodes it, reconstructs its
canonical form and verifies the existing client proof before dispatch. A
frontend-supplied parsed object or authentication assertion grants nothing.

The supervisor independently enrols each process pair's actual Linux PID,
UID and GID, authority continuity and frontend incarnation. Both endpoints
check `SO_PEERCRED` before exchanging commands. It must also retain process
lifetime, the endpoint's complete ancestry and ACLs, and descriptor custody;
these library checks alone do not exclude PID reuse or qualify separate roles.
Operator enrolment issue/revoke control stays on its existing authority-owned local
Unix listener, with the original operator's kernel credentials. It is never
relayed through this frontend. Ordinary hub frames, unsigned client intent and
unknown operation families are refused by the protected channel.

The private `synapse.protected.frontend-control.v1` envelope is length-prefixed
with a four-byte big-endian byte count. Its closed fields are `schema`, `kind`,
`authority_continuity`, `frontend_incarnation`, `connection_id`, `sequence` and
`data`. The authority mints a fresh 256-bit `connection_id` in a `hello` at
sequence zero. Each `request` and its `result` use the next positive integer
sequence on that same connection. A bound `refused` carries empty data and
terminates the channel; no exception text is forwarded. Unknown fields,
duplicate keys, nonfinite or out-of-profile numbers, lone surrogates, foreign
bindings and wrong sequences refuse before dispatch. Embedded request text is
not normalised by the frontend. Existing protected request limits and numeric
rules apply again at authority admission, including strict number spellings.

`ProtectedWriteControlPolicy` requires explicit positive byte, string, depth,
node, deadline, connection and aggregate application payload ceilings. The
server refuses a policy whose decoded string budget is smaller than the
authority's original request/result byte limit, or whose envelope cannot hold
six times that limit plus its exact fixed fields and maximum-correlation
overhead. This conservative bound covers ASCII escape expansion before any
handler can run. Unexpected result encoding or delivery failure after handler
invocation aborts the channel as an unknown outcome; it is never a `refused`
message. Direct transport likewise reports post-invocation handler/result or
enrolment-recheck failures as unavailable (1011), rather than request refusal
(1008). A refusal qualifies only before invoking a handler, or when an
authority-owned `ProtectedWriteRefusalError` proves that the requested mutation
never committed. Recovery marks its synchronous checks and I/O-free response
builders with `protected_write_pre_commit`; a failed journal builder rolls
back before that refusal propagates. This adapter does not enclose awaits or
storage completion. Generic handler, journal and post-handler failures remain
unknown. A frontend cannot supply a refusal marker or a pre-commit assertion.
After sending a request, the client classifies malformed, foreign or
misdirected private replies as unknown too. Only a complete `refused` envelope
with the expected authority/process binding, connection and sequence proves
a known refusal; a generic decoding error does not.
A refusal concerns this invocation only. It never clears an earlier unknown
outcome for the same operation identity; inspect the original durable record.
Only dispatch can propagate the explicit pre-commit refusal. Post-dispatch
validation always stays unknown, including a refusal marker raised by a
registry callback after the requested mutation committed.

The aggregate reservation includes one maximum envelope in each direction per
connection; kernel and WebSocket buffers remain separate listener admission
obligations. There is one pending request per connection and no pending queue.
These are policy ceilings independent of hardware; host admission may only
lower them. Frontend listeners must separately bound external connection
capacity, frame size, receive queue, handshake and close deadlines.

The authority selects the connection and correlation of every response. The
client adapter refuses foreign or stale results. End clients still verify the
authority signature and exact request binding: a compromised frontend can
observe, drop, delay or misdeliver plaintext, so availability and confidentiality
remain frontend-trusted. Private routing metadata alone cannot certify what a
compromised process sends on an external socket.

Use `ProtectedWriteControlServer.serve` with an independently prebound native
socket that is not yet listening, and `open_protected_write_control` for an
owned client context. Listening starts inside the authority so the connecting
frontend observes that process's kernel credentials. Server
shutdown stops accepts, invalidates admission, cancels and drains its owned
requests before caller-owned stores close. Cancellation or a missing response
can leave an admitted mutation durable; inspect its original operation identity
instead of retrying it. Closure is terminal for these objects. This component
launches no actor and offers no replacement or restart API. Before any later
replacement launch, a supervisor must separately prove the previous actor has
ended; control-session mechanics do not satisfy that requirement.

The [native frontend tests](https://github.com/anulum/synapse-channel/blob/main/CONTRIBUTING.md#protected-frontend-control-tests)
exercise process transport and durable authority behaviour. Same-UID component
tests do not establish installed multi-role isolation, signer custody, an
ordinary coordination frontend split or whole-runtime qualification.

::: synapse_channel.core.protected_write_request_session

::: synapse_channel.core.protected_write_refusal

::: synapse_channel.core.protected_write_control_policy

::: synapse_channel.core.protected_write_control_wire

::: synapse_channel.core.protected_write_control_server

::: synapse_channel.core.protected_write_control_client

::: synapse_channel.core.protected_write_control_gateway

### Finite runtime qualification evidence

The source-checkout `tools.protected_runtime_probe` workflow prepares and checks
one finite disposable guest attempt. It is separate from the installed library
entry point above. Its public preparation and analysis commands expose their
arguments without starting a guest:

```bash
python -m tools.protected_runtime_probe.prepare --help
python -m tools.protected_runtime_probe.instrumentation_analysis --help
```

Preparation binds the source capsule, dependency bytes, bootstrap and a fresh
per-attempt guest UUID. The independently reviewed launcher, guest identity,
readonly seed, private disks, evidence return and resource envelope MUST also
match before execution. A prepared packet or a printed guest-side command is
not launch authority. Refusal or failed cleanup MUST preserve uncertainty and
held inputs; it grants no automatic retry, repair or cleanup permission.

| Evidence surface | What it checks | What it does not establish |
| --- | --- | --- |
| Default host-tools prerequisite packet | Its enrolled native prerequisites and finite role-boundary observations | A captured complete runtime or an installed service |
| Captured-runtime packet | Exact captured executable, import closure and package/wheel provenance | Permission to substitute host Python or admit a different source |
| Instrumented captured-runtime attempt | Six process contexts bound to exact enrolled collector and output bytes | An uninstrumented resource/performance result or whole-runtime acceptance |
| Ordinary recording analysis | Report, capsule, sealed dataset and current-source associations before combining private working copies | A protected-operation grant, physical recovery custody or deployment authority |
| Windows/macOS portability artifact | Actual branch observations bound to the successful producer, platform and committed source | Linux peer credentials, native role isolation or whole-scope qualification |

Recording changes the controller's inherited file-size limit and the authority's
shutdown profile; the report MUST keep those differences with the result.
Instrumentation storage exhaustion is an instrumentation error, not a product
result. Bootstrap admission, readonly recapture, collector startup and final
sealing/publication are explicitly outside the recorded coverage scope.
Registered sources and sources with executed arcs MUST remain distinguishable.

Linux indexed CI retains actual probe child observations separately from the
mandatory kernel-writer directories, then binds the combined data in its shard
artifact. The complete application coverage gate applies to `src/synapse_channel`;
it is not a coverage qualification of the probe tools. Portability artifacts
likewise carry observation data without claiming whole-scope coverage. A consumer
MUST check the producer result, source hashes, configuration, platform and data
digest before using either kind of observation; another head or platform is not
an interchangeable result.

The [finite qualification procedure](https://github.com/anulum/synapse-channel/blob/main/CONTRIBUTING.md#finite-linux-runtime-qualification)
defines prerequisites, collection and failure retention. These finite results
do not establish an installed supervisor, writer, recovery service or authorised
read service, complete consumer migration, settlement custody, revocation fencing
or independent action-control admission.

### Known-state recovery inputs and evidence

`inspect_protected_recovery_snapshot` compares every enrolled path with the
exact retained execution prefix. An uncertain step may match its known before
or after state; an undeclared physical effect, partial bytes, changed inode or
unmodelled parent refuses. Root aliases and planned absent children retain their
checked physical ancestry. File and evidence reads have independent aggregate
budgets. Collection is sequential: the supervisor MUST establish and retain old
writer quiescence and filesystem custody before inspection. A matching after
state proves the declared postcondition, not which writer produced it.

`collect_protected_recovery_evidence` stores a closed canonical parent/begin-bound
packet in the enrolled immutable evidence store. `read_protected_recovery_evidence`
reads it without consulting live files and compares the original admission,
begin, execution-start hash, frontier and every completed-prefix digest with the
retained journal. Later journal progress invalidates an older packet. A retained
packet remains historical if current files later change; its digest is not a
current-custody or quiescence receipt.

`prepare_protected_recovery_candidate` checks a fresh-claim child proposal against
those retained observations, the parent's enrolled project/domain, explicit
per-path effects, parent bindings and bounded immutable content. Unknown names
cannot acquire permission from a prefix. It performs no I/O and grants no
admission, begin or custody transfer.

Inside the authority actor, `verify_protected_recovery_admission_inputs` requires
the exact current blocked parent, unchanged candidate bytes and authority domain.
It is one check inside a separate recovery verifier; physical custody MUST still
be established independently. `ProtectedWriteRecoverHandler` rechecks authenticated
service enrollment and a distinct current writer, then uses the sole durable
actor for request-key conflict handling and atomic recovery finalization. Its
operator-enrolled recovery verifier is mandatory. Refusal preserves parent
custody and journals; reusing an admission key produces a signed, request-bound
conflict before the physical verifier runs.

The handler is an explicit embedding API. These mechanisms register no default
listener, install no recovery service and do not make an uncertain old writer
safe to resume. The [native profiles](https://github.com/anulum/synapse-channel/blob/main/CONTRIBUTING.md#known-state-recovery-profiles)
exercise actual kernel/storage behaviour and the existing authenticated loopback
transport; their result is not installed-role or independent physical-custody
qualification.

::: synapse_channel.core.protected_write_recovery_snapshot

::: synapse_channel.core.protected_write_recovery_evidence

::: synapse_channel.core.protected_write_recovery_candidate

::: synapse_channel.core.protected_write_recovery_admission_inputs

::: synapse_channel.core.protected_write_recover_handler

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
[The delivery matrix](delivery-matrix.md) lists every request type with its
delivery, retry key, clock and durable record.

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
Mail addressed to a role MUST be pending once, in the role's mailbox
`role:<project>/<role>`, and remain replayable to a later holder until a holder
acknowledges it; mail addressed to a name and a role is pending in both.
Without a journal, restart persistence and durable mailbox watermarks are absent.
Compaction and configured history/identity bounds still limit recovery; the
diagnostic target count itself is process-local and bounded, not a durable queue.

**Implementation.** `core/handlers/messaging.py` (dead-letter record),
`core/mailbox_pending.py:MailboxPendingTracker` (monotonic watermark, replay,
restart projection), `core/role_mailbox.py` (role mailbox keys and the
personal/role split).

**Pinned by.** `tests/test_hub_mailbox_pending.py`, `tests/test_hub_core_chat.py`,
`tests/test_hub_role_mailbox.py`, `tests/test_role_mailbox.py`.

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

### INV-CK-3 — only the authoritative hub's clock sets and ends a lease

**Normative.** The hub that owns a task's namespace (INV-MH-1) MUST stamp a
lease's start and expiry with its own wall clock and judge expiry against that
same clock. No time field of a claim frame (its `timestamp`, a supplied
`claimed_at`, `lease_expires_at` or `lease_duration_seconds`) may move the lease;
only `ttl_seconds`, clamped by INV-LL-3, sets its length. A clock difference
between a client and the hub therefore cannot shorten or lengthen a lease, and
leases need no client skew leeway. A client that times its own lease, such as
`synapse lock`, MUST time it by the relative `lease_duration_seconds` on its own
monotonic clock, measured from before its request and less a stop allowance
(`STOP_ALLOWANCE`, 5 s), never by the hub's absolute timestamps.

**Residual: the hub's own clock.** A step of the hub's wall clock moves every
lease with it. A forward step ends leases early: another agent, or a new
process of the same name, may then claim the task. Epoch fencing (INV-EF-2,
required under `--require-fencing-epoch`) stays the primary safety: the earlier
holder's release, update, handoff or checkpoint carries the old epoch and is
refused. A backward step lengthens leases and never frees one; a renewal keeps
the lease's duration. A renewal first expires stale leases at its own instant,
so a lease that expired with nothing else happening in between cannot be
renewed back to life. The hub's host MUST therefore run the same clock
synchronisation INV-CK-2 requires and SHOULD slew rather than step its clock.
Displays that compare a lease's `lease_expires_at` with the viewer's clock
(`synapse locks`, the dashboard) are diagnostic and may be off by that
viewer's skew. A lease is coordination, not a filesystem lock.

**Implementation.** `core/state.py` (`claim`, `renew_claim`, `_expire_claims`
use the hub clock), `core/handlers/leasing.py` (the grant reports
`lease_duration_seconds`), `cli_lock_supervision.py` (`lease_duration` and the
monotonic renewal deadline).

**Pinned by.** `tests/test_lease_clock_skew.py`, `tests/test_cli_lock_supervision.py`,
`tests/test_fencing_epoch.py`.

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

## 10. Operation attempts

An executor asks the hub to open an attempt of a declared operation before its
effect. These invariants hold for every executor that asks; one that never asks
is not stopped by them.

### INV-OA-1 — one journalled answer per request, from a closed order

**Normative.** A well-formed `operation_attempt` request on a journal-backed hub
MUST receive exactly one answer: the first of `not_claim_holder`,
`stale_epoch`, `unknown`, `not_approved`, `approval_stale`, `approval_expired`,
`approver_unauthorised`, `operation_closed`, `attempt_exists`,
`earlier_attempt_unreconciled` and `approved_fresh` that applies. The answer's
`operation_attempt_answered` row and the stored answer MUST be committed in one
FULL-durability transaction under the request's key, refusals included. A repeat
with the same key MUST return the stored answer unchanged and journal nothing; a
changed request under that key MUST be `idempotency_conflict`. A request without
a key, or otherwise malformed, MUST be answered `invalid_request` and journal
nothing.

**Implementation.** `core/operation_attempts.py:decide`,
`core/handlers/operation_attempts.py`, and the atomic path of INV-CR-1.

**Pinned by.** `tests/test_operation_attempts.py`,
`tests/test_hub_operation_attempts.py`.

### INV-OA-2 — nothing an answer reads is written between the reading and the answer

**Normative.** The hub MUST read the claim, the declared operation and its
attempts, the approval notes and the verdict records, decide, and commit the
answer while holding its single mutation lock. Claims, progress notes and
operation records MUST be written under that same lock, so none of them can be
journalled between a request's reading and its answer.

**Implementation.** `core/handlers/operation_attempts.py` through
`run_atomic_operation`; `core/handlers/operation_records.py` through
`state_mutations.run_subject`; the lock order is stated in both modules.

**Pinned by.** `tests/test_hub_operation_attempts.py`
(`test_a_verification_never_lands_between_the_reading_and_the_answer`).

### INV-OA-3 — one approval admits one attempt, and one attempt is open at a time

**Normative.** `approved_fresh` MUST open the attempt and record the consumed
approval's journal sequence in the same transaction. A later request on that
approval MUST be `attempt_exists`, also after the attempt has ended, and so MUST
every request while an attempt of the operation is open, under any approval.

**Implementation.** `core/operation_attempt_ledger.py:AttemptLedger.view`.

**Pinned by.** `tests/test_operation_attempt_ledger.py`,
`tests/test_hub_operation_attempts.py`.

### INV-OA-4 — the approval is judged when it is used

**Normative.** At each request the hub MUST evaluate the verdict gate on the
approval as it stands now, compare the intent's deadline with its own wall
clock, and refuse an approver that is the intent's actor or of another project.
A switched-off gate, an approval that came through a bypassed gate, and a wall
clock earlier than the declaration MUST give `unknown`. No other loss of the
hub's clock is detected; the host keeps its clock synchronised (INV-CK-3).

**Implementation.** `core/operation_attempt_facts.py:current_approval`,
`core/operation_attempts.py:decide`.

**Pinned by.** `tests/test_operation_attempt_facts.py`,
`tests/test_hub_operation_attempts.py`.

### INV-OA-5 — an attempt ends only by its executor, by its verified effect or by a seal

**Normative.** An open attempt MUST end only when its own executor publishes an
outcome of `not_started`, `confirmed_done` or `confirmed_failed`, when an
identity other than the declarer publishes a `verified` verification, which
also closes the operation, or when the hub keeps a seal of it (INV-OA-6). An
outcome from any other identity MUST end nothing. After an attempt ends, no
attempt MUST open until an identity other than the declarer publishes a
verification whose effect is `observed` or `absent`. The declarer's own
verifications MUST count for neither. The ledger MUST be rebuilt from the
journal at start without consulting any clock.

**Implementation.** `core/operation_attempt_ledger.py`, fed by
`core/handlers/operation_records.py`.

**Pinned by.** `tests/test_operation_attempt_ledger.py`,
`tests/test_hub_operation_attempts.py`, `tests/test_operation_seals.py`.

### INV-OA-6 — a seal names exactly one open attempt's sealed marker and reconciles nothing

**Normative.** The hub MUST keep a `seal` record only from an identity that
holds `operation-seal` on the task, which the handler enforces also on a hub
without an ACL policy, where no identity holds it. The named attempt MUST be an
open attempt of the named operation, otherwise the record MUST be refused
`attempt_unknown` or `attempt_ended`. The hub MUST recompute, in the stated
object format, the id of the `sealed` marker blob of that operation, attempt
and declared intent digest, and MUST refuse as `seal_not_observed` a record
whose object or ref differs, whose observation is not `landed` or
`landed_earlier`, or whose operation was declared without an intent. A kept
seal MUST end the attempt in the same `operation_sealed` journal row and
counts towards the operation's record limit. It MUST NOT reconcile the
attempt: INV-OA-5's verification by another identity still decides when a new
attempt may open. An outcome the executor publishes after the seal MUST end
nothing, and a repeat of the sealed attempt's request MUST return its stored
answer with the attempt state `sealed`.

**Implementation.** `core/operation_seals.py`,
`core/operation_records.py:seal_refusal`,
`core/handlers/operation_records.py`,
`core/operation_attempt_ledger.py:AttemptLedger.note_seal`.

**Pinned by.** `tests/test_operation_seals.py`,
`tests/test_hub_operation_seals.py`.

### INV-OA-7 — an operation's attempts are read only by holders on the task

**Normative.** An `operation_state` request MUST be answered only to an
identity that holds `operation-evidence` or `operation-seal` on the named task,
which the handler enforces also on a hub without an ACL policy. Any other
identity MUST receive `not_authorised` and no attempt, state or closure. An
operation not declared on the named task MUST be answered `unknown_operation`.
The answer MUST list each attempt's id, executor and state and whether the
operation is closed, and MUST journal nothing.

**Implementation.** `core/handlers/operation_state.py`,
`core/operation_attempt_ledger.py:AttemptLedger.summary`.

**Pinned by.** `tests/test_hub_operation_seals.py`.

### INV-OA-8 — the seal comes before its record, and only an operator removes it

**Normative.** `synapse operation seal` MUST read the attempt's state from the
hub, write the `sealed` marker and read it back before it sends the record, and
MUST send no record unless the read-back answer is `landed` or
`landed_earlier`; a seal in place without a record is therefore possible and
safe, a record without a seal is not. `synapse operation unmark` MUST remove a
marker only when the hub reports the operation closed, and only while the
marker holds this attempt's `applied` blob, by a compare-and-delete on that
exact object; it MUST NOT remove a `sealed` marker.

**Implementation.** `cli_operation_seal.py`,
`git/attempt_marker_changes.py:remove_applied_marker`.

**Pinned by.** `tests/test_cli_operation_seal.py`,
`tests/test_git_attempt_marker_changes.py`.

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
| `DEFAULT_MAX_MSG_BYTES` | `1048576` (1 MiB, largest inbound frame) | `core/hub_defaults.py` |
| `MAX_JSON_DEPTH` | `64` (deepest JSON nesting a frame may have) | `core/protocol.py` |
| `DEFAULT_MAX_CLIENTS` | `256` | `core/hub_defaults.py` |
| `DEFAULT_MAX_CONNECTIONS_PER_HOST` | `32` | `core/hub_defaults.py` |
| `IJSON_MAX_INTEGER` | `9007199254740991` (largest integer a signed frame may hold) | `core/aef_canonical.py` |
| `SESSION_NONCE_BYTES` | `18` (random bytes in a connection's session nonce) | `core/frame_signing.py` |
| `BAD_SIGNATURE_LIMIT` | `5` (refused frames that close a signing connection) | `core/frame_signature_gate.py` |
| `RECHECK_INTERVAL_SECONDS` | `5.0` (signing-key re-check period) | `core/frame_signature_gate.py` |

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
