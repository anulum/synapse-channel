<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Invariant conformance

SYNAPSE CHANNEL maintains a machine-checked conformance registry for six
coordination boundaries. It is a proof map, not a certification badge: each row
names its exact normative invariants, implementation files, executable evidence,
scope, and residual limitations.

The current programme status is **partial**. Two bounded guarantees conform and
four remain partial. The generated
[`invariant_conformance.json`](https://github.com/anulum/synapse-channel/blob/main/docs/_generated/invariant_conformance.json)
is suitable for automation; `tools/invariant_conformance.toml` is its canonical
source.

| Boundary | Status | Current truth |
| --- | --- | --- |
| Single authority | Conformant | One hub authority domain prevents overlapping ownership; federation does not merge authority. |
| Immediate-effect fencing | Partial | Hub-mediated epochs and versions fence stale mutations, but direct external effects are outside that boundary. |
| Atomic operation truth | Partial | Claim-family operations commit before publication and apply once; this is not yet universal. |
| Content-bound global event identity | Conformant | A federated identity binds one fingerprint; equivocation quarantines before publication. |
| Causal conflict handling | Partial | Content-bound, same-task parent edges suppress proven ancestors; unresolved heads remain payload-free conflicts without automatic resolution. |
| Evidence completeness | Partial | Defined journal, receipt, AEF, quarantine and scoped execution observations exist; external business effects and universal evidence remain unproven. |

## Trusted core and dependency direction

The guarantees above depend on a small trusted core, `synapse_channel.core`: it
owns identity, authority, claims, the operation life cycle and the durable
record. Each boundary's `sources` list in `tools/invariant_conformance.toml`
names the exact modules whose correctness that guarantee depends on. Vendor
integrations, adapters, protocol bridges (A2A, MCP), the dashboard and the CLI
are extensible edges: they translate, report observations and present evidence,
and they build on the core, never the reverse.

`tools/check_core_dependency_direction.py` enforces the direction
mechanically. Every module below `src/synapse_channel/core` may import from
`synapse_channel` only `synapse_channel.core` itself and the package version
(`from synapse_channel import __version__`). Absolute, relative,
`TYPE_CHECKING` and literal `importlib.import_module` / `__import__` imports are
checked; an import whose module name is computed at run time cannot be read
statically. The check runs as a pre-commit and pre-push hook, and the
repository test suite runs it against the shipped tree.

### Modules the authority and truth guarantees depend on

The boundary `sources` above cover the distributed guarantees. Authority and
outcome truth also depend on the modules below. A defect in any of them can
admit a wrong identity, grant an action that policy denies, lose or forge a
durable record, or misreport an outcome. The list names where the guarantee is
implemented; it does not certify those modules.
`tests/test_check_core_dependency_direction.py` checks that every path in this
list exists.

| Guarantee | Modules |
| --- | --- |
| Connection identity | `src/synapse_channel/core/auth.py`, `src/synapse_channel/core/identity_binding.py`, `src/synapse_channel/core/identity_keys.py`, `src/synapse_channel/core/identity_pins.py`, `src/synapse_channel/core/identity_enrollments.py`, `src/synapse_channel/core/handlers/identity_enrollments.py`, `src/synapse_channel/core/hub_identity_gate.py`, `src/synapse_channel/core/unbound_identity_guard.py`, `src/synapse_channel/core/peer_identity.py` |
| Message authenticity | `src/synapse_channel/core/message_auth.py`, `src/synapse_channel/core/message_auth_durable.py` |
| Authority over actions | `src/synapse_channel/core/acl.py`, `src/synapse_channel/core/acl_enforcement.py`, `src/synapse_channel/core/verb_access.py`, `src/synapse_channel/core/role_grants.py`, `src/synapse_channel/core/name_ownership.py`, `src/synapse_channel/core/namespace_ownership.py`, `src/synapse_channel/core/hub_frame_gates.py` |
| Claims and leases | `src/synapse_channel/core/state.py`, `src/synapse_channel/core/state_transaction.py`, `src/synapse_channel/core/state_leases.py`, `src/synapse_channel/core/state_scopes.py`, `src/synapse_channel/core/scoping.py`, `src/synapse_channel/core/path_identity.py`, `src/synapse_channel/core/handlers/leasing.py`, `src/synapse_channel/core/handlers/leasing_mutations.py` |
| Operation life cycle and retries | `src/synapse_channel/core/lifecycle.py`, `src/synapse_channel/core/ledger.py`, `src/synapse_channel/core/atomic_operations.py`, `src/synapse_channel/core/idempotency.py`, `src/synapse_channel/core/hub_ledger_guard.py`, `src/synapse_channel/core/chat_dedupe.py`, `src/synapse_channel/core/release_confirmation.py` |
| Durable record | `src/synapse_channel/core/persistence.py`, `src/synapse_channel/core/persistence_events.py`, `src/synapse_channel/core/persistence_event_authentication.py`, `src/synapse_channel/core/persistence_operation_commit.py`, `src/synapse_channel/core/persistence_operations.py`, `src/synapse_channel/core/journal.py`, `src/synapse_channel/core/journal_appliers.py`, `src/synapse_channel/core/replay.py`, `src/synapse_channel/core/event_row_mac.py`, `src/synapse_channel/core/event_row_recovery.py`, `src/synapse_channel/core/hub_journal_recovery_gate.py` |
| Outcome truth | `src/synapse_channel/execution_outcome.py`, `src/synapse_channel/a2a_outcome.py`, `src/synapse_channel/mcp/outcome.py`, `src/synapse_channel/worker_outcome.py`, `src/synapse_channel/core/receipts.py`, `src/synapse_channel/core/receipt_signing.py` |

Some guarantees depend on modules outside the core today. The outcome modules
above live in the package root and in `synapse_channel.mcp`. The boundary
sources also name `src/synapse_channel/cli_tasks.py`,
`src/synapse_channel/client/agent_outbound_ledger.py`,
`src/synapse_channel/client/agent_outbound_memory.py`,
`src/synapse_channel/cli_multihub.py` and
`src/synapse_channel/dashboard_store_feeds.py`. These are edges on which a
guarantee rests, not trusted core. Moving or narrowing them is open work.

### Trust model per profile

What the guarantees assume depends on the hub profile. A profile states what the
hub enforces; it does not sandbox an agent.

| Profile | Agents assumed | What the hub enforces | What it does not establish |
| --- | --- | --- | --- |
| Loopback, single owner (default) | Cooperative coding agents of one owner | Connect token when configured; claims, leases, fencing and the durable record as above | Which name a token holder registers as; any isolation between agents |
| [`--team-secure`](team-secure.md) | Several agents, any of which may misbehave | Proven connection identity, granted role claims, audience-routed directed messages, fenced lease mutations | Filesystem or process isolation; per-message HMAC and ACL enforcement unless also configured |
| [`--paranoid`](paranoid-mode.md) / [`--secure`](secure-mode.md) | Network-exposed hub | Additionally a durable log, per-message authentication, ACL enforcement and native WSS; `--secure` adds flood limits | Isolation of agents from each other's files, or a sandbox |
| Several tenants on one hub | Mutually distrusting organisations | Not a supported profile | Tenant isolation is not claimed; use separate hubs |

An agent that keeps a credential for a resource can still act on it after its
authority is withdrawn. Enforcement at the source needs an executor that holds
the access, not a hub profile.

`python tools/invariant_conformance.py --check` validates the schema, exact six-row
set, cited invariant identifiers, public evidence paths, and generated output.
This freshness check runs before commit and in reserved release preflight.
It does not execute the cited tests or qualify a new source or package artifact.

[Execution outcomes](execution-outcomes.md) record A2A/MCP exchanges, wake
transport and worker process observations independently of external effects,
stop requests, confirmed stops and undo. Their explicit unknown state requires
inquiry or operator recovery; it does not authorise mutation replay. These
adapter observations extend the evidence map without making its status universal.
The row statuses describe the registry's named guarantees and limitations;
qualification of a build also needs its exact source/package identity and
recorded results for the applicable evidence profiles.
`--enforce` is intentionally red until every row is truthfully conformant; it
prints every incomplete boundary and exits non-zero.

Ordinary line or branch coverage cannot close a boundary by itself. The registry
links state-machine exploration, kill-point fault injection, multi-process races,
restart and concurrency probes, adversarial inputs, and receipt/replay tests where
those modes apply. A status changes only when both the normative guarantee and
its hostile executable evidence change together.

The single-authority race evidence includes two independent Python client
processes released through an on-disk barrier against one live hub. Concurrent
takeovers must produce exactly one 4010-superseded process, one surviving
process, and one final socket-to-identity binding.

Partition evidence is similarly explicit. A contested namespace refuses local
grants; partition and verified-heal transitions are durable; failed or partial
poll rounds retain suspicion; restart restores the last proven contest; and an
observed release heals only after a successful round. These tests prove safe
partition containment and recovery, not causal ordering of the display fold.

The observed task-board fold also retains the latest snapshot from each authoring
hub. If those complete records diverge, JSON and text surfaces expose one bounded,
payload-free conflict object with each contender's hub, sequence, timestamp, and
record fingerprint. Equal snapshots converge without a conflict, and later equal
snapshots clear the divergence. This makes disagreement visible without leaking the
losing task record or choosing an authoritative winner. It is not a claim that the
snapshots were concurrent. Task events may carry one content-bound parent;
only a verified same-task complete-event fingerprint proves that ancestry.
Malformed local parents are privately refused before any mutation or retry-key
result. Malformed observed parents remain invalid and suppress nothing. A single
optional parent is not a vector clock, and missing edges do not prove concurrency,
so the boundary remains partial.

The randomized state machine is complemented by
`python tools/exhaustive_coordination_model.py --depth 4`. That deterministic
bounded explorer enumerates every trace over thirteen real claim, overlap,
cross-worktree, renewal, handoff, release, stale-fence, update, and expiry
actions. It drives `SynapseState` directly and checks the named mutual-exclusion,
epoch, lease, version, and handoff invariants after every transition. Its result
is bounded evidence, not a claim about unbounded state space.
