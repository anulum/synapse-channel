<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Agent Air Traffic Control architecture

Agent Air Traffic Control (ATC) is the umbrella that names what Synapse's shipped
parts already do together: give autonomous agents in one repository or a whole
ecosystem shared declared-work state, surface possible collisions, and make every
coordination decision inspectable after the fact. It is an architecture, not a new component — the
controller is the composition of the hub, claims, evidence, and read-side
reports, not a separate orchestrator that agents must obey.

This document maps the control loop end to end, names which pieces are
implemented today and which are design, and pins the boundary that keeps ATC a
coordination layer rather than a scheduler that owns the agents.

## The control loop

Air traffic control here means five repeating steps, each backed by a shipped or
designed Synapse surface:

1. **Separation** — refuse conflicting declared work scopes held by another owner. File-scope claims and
   semantic claims lease a unit of work (paths, symbols, APIs, tests, generated
   artefacts) with an epoch and a checkpoint, so a second conflicting claim is
   refused. Supported provider hooks can separately deny covered edits before
   mutation. Symbol scopes are synthetic source-file descendants enforced by the
   existing path ancestry rule; conservative tree-sitter Git-diff inference runs
   only on the client and widens incomplete evidence to the whole file.
   *(Shipped: `synapse git-claim`, semantic selectors and diff scopes, leases.)*
2. **Merge-risk radar** — surface impending collisions across branches before
   they land. Cross-branch claim overlap and historical path conflicts are
   computed read-only. *(Shipped: `synapse conflicts`, `synapse event-query
   "conflicts at seq|time"`.)*
3. **Evidence-gated completion** — record declared evidence alongside closeout.
   Release receipts carry bounded evidence, artefacts, known failures, and an
   epistemic status; an advisory policy check evaluates them. The multi-seat
   **default closeout story** is `synapse verify-release` → receipt JSON →
   `synapse release --receipt` (see [quick start](quickstart.md) and
   [recipes](recipes.md#evidence-gated-release-default-closeout)); bare release
   is the emergency exit. *(Shipped: release receipts, `synapse verify-release`,
   `synapse policy-check`, human-in-the-loop `synapse approval`.)*
4. **Post-incident replay** — reconstruct what happened from the durable log.
   Replayable postmortems and evidence-only reliability memory turn the event
   store into an audit trail. *(Shipped: `synapse postmortem`, `synapse
   reliability`, `synapse event-query`, `synapse accounting`.)*
5. **Memory** — export retained durable records for a persistent-memory consumer.
   *(Shipped seam: `synapse ingest --memory` prints `MEMORY_KINDS` records and
   can persist the last printed cursor. It does not acknowledge downstream
   ingestion or make a memory service learn. Compaction honours a caller-supplied
   floor; the operator must keep it at or below every consumer's durably ingested
   cursor. Neither command discovers consumers or verifies that progress.)*

Claims admit declared scopes; covered hooks and Git checks can separately refuse
covered mutations. Other verbs have their own effects and admission controls,
including identity/ACL and explicitly enabled policy gates. This architecture
adds no separate process that takes control of an agent.

## Controller surfaces

The "controller" is distributed across surfaces that already exist:

- **The hub** is the single source of truth for presence, claims, the plan,
  chat, capabilities and, when configured, the admitted durable journal.
- **Claims and leases** separate conflicting declared authority through scope,
  epoch and checkpoint; covered mutation hooks have their own boundaries.
- **The blackboard** holds the shared plan, progress, and handoffs.
- **Capability cards and routing** recommend which agent should take a task.
- **The operator reports and controls** (`conflicts`, `event-query`, `postmortem`,
  `reliability`, `accounting`, `dashboard`) are the radar and the flight
  recorder.
- **Receipts, policy-check, and approval** record and evaluate declared evidence;
  explicit policy and admission settings determine which checks gate an action.
- **The ingest seam** prints retained events for a consumer. Import into persistent
  memory and acknowledgement of that import remain the consumer's responsibility.

A live [dashboard](cockpit.md) renders who is online, what is claimed, what
conflicts and what is progressing. Inspection and enabled operator controls are
distinct: consult each control's effects and permissions before invoking it.

## Evidence-gated completion

The recommended closeout attaches a receipt to the owner's release. Receipts
are optional unless the operator enables a policy that requires them. A policy
check reports whether the
declared evidence satisfies a policy — required tests, strict typing, owner
approval, evidence freshness, claim coverage, generated-artefact parity, and
known-failure acknowledgement. The gate documents and surfaces; it does not
silently block a merge or rewrite history. A human-in-the-loop approval can hold
a policy-gated release for an explicit decision. This keeps the controller honest
about the difference between *claimed* and *verified*.

## Relationship to other designs

ATC is the composition layer over the rest of the design set: it uses
[identity and ACL](identity-and-acl.md) for who may act, [signed events and
mTLS](signed-events-mtls.md) and the [federated trust model](federated-trust-model.md)
for trust across hosts and domains, the [agent trust graph](agent-trust-graph.md)
for evidence aggregation, and [signed capability cards](signed-capability-cards.md)
for portable capability provenance. It introduces no new trust root and no new
authority; it only names and sequences what those surfaces already provide.

## Boundaries

Agent Air Traffic Control is an **architecture and a naming**, not a new runtime
component, and it makes no control claim beyond what its parts already enforce.

- It is **not a scheduler or orchestrator**: it does not assign, start, stop, or
  preempt agents. Claims coordinate declared scopes; host edits and external
  effects require the corresponding covered hooks and explicit execution controls.
- It does **not** add a new monolithic controller. The control loop is the
  existing distributed surfaces; there is no single process that owns the fleet.
- Its completion gate is **advisory**: receipts and policy-check document and
  surface evidence; they do not certify sufficiency, block merges, or replace
  code review.
- It does **not** weaken the local-first default: the loop can run on one local
  machine without a required cloud service. Local verbs still have their own
  mutation, persistence and permission boundaries.
- "Radar" and "flight recorder" are **audit signals**, not predictions of intent
  or proof that a merge is safe.

A Git claim timeout is an **unknown outcome** (exit `3`), not proof of denial. Use `--confirm-only` with the original identity and scope to verify an exact live lease without replaying a mutation. [Claim recovery](git-claims.md#claim-outcomes-and-recovery).
