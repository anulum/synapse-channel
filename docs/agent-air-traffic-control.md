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
parts already do together: keep many autonomous agents working in one repository
or a whole ecosystem without colliding, and make every coordination decision
inspectable after the fact. It is an architecture, not a new component — the
controller is the composition of the hub, claims, evidence, and read-side
reports, not a separate orchestrator that agents must obey.

This document maps the control loop end to end, names which pieces are
implemented today and which are design, and pins the boundary that keeps ATC a
coordination layer rather than a scheduler that owns the agents.

## The control loop

Air traffic control here means five repeating steps, each backed by a shipped or
designed Synapse surface:

1. **Separation** — keep two agents off the same work. File-scope claims and
   semantic claims lease a unit of work (paths, symbols, APIs, tests, generated
   artefacts) with an epoch and a checkpoint, so a second agent is refused before
   it edits. *(Shipped: `synapse git-claim`, semantic selectors, leases.)*
2. **Merge-risk radar** — surface impending collisions across branches before
   they land. Cross-branch claim overlap and historical path conflicts are
   computed read-only. *(Shipped: `synapse conflicts`, `synapse event-query
   "conflicts at seq|time"`.)*
3. **Evidence-gated completion** — let work close only against declared evidence.
   Release receipts carry bounded evidence, artefacts, known failures, and an
   epistemic status; an advisory policy check evaluates them. *(Shipped:
   release receipts, `synapse verify-release`, `synapse policy-check`,
   human-in-the-loop `synapse approval`.)*
4. **Post-incident replay** — reconstruct what happened from the durable log.
   Replayable postmortems and evidence-only reliability memory turn the event
   store into an audit trail. *(Shipped: `synapse postmortem`, `synapse
   reliability`, `synapse event-query`, `synapse accounting`.)*
5. **Memory** — feed the durable record to a persistent-memory layer so the
   fleet learns across sessions. *(Shipped seam: `synapse ingest` feeds the
   [REMANENTIA](https://github.com/anulum/synapse-channel) read-side via
   `MEMORY_KINDS` and a persisted cursor; compaction never prunes past an
   unconsumed cursor.)*

Each step is read-only or advisory except separation: only claims actually gate a
mutation. Everything else informs a human or a policy layer; nothing in ATC
seizes control of an agent.

## Controller surfaces

The "controller" is distributed across surfaces that already exist:

- **The hub** is the single source of truth for presence, claims, the plan,
  chat, capabilities, and the durable event log.
- **Claims and leases** provide separation with scope, epoch, and checkpoint.
- **The blackboard** holds the shared plan, progress, and handoffs.
- **Capability cards and routing** recommend which agent should take a task.
- **The read-side reports** (`conflicts`, `event-query`, `postmortem`,
  `reliability`, `accounting`, `dashboard`) are the radar and the flight
  recorder.
- **Receipts, policy-check, and approval** are the evidence gate.
- **The ingest seam** exports the record to persistent memory.

A live, read-only [fleet cockpit dashboard](cli.md) renders the current picture —
who is online, what is claimed, what conflicts, what is progressing — without
mutating anything.

## Evidence-gated completion

ATC's completion rule is deliberately advisory, not enforced: a task closes when
its owner releases it with a receipt, and a policy check reports whether the
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
  preempt agents. Agents coordinate voluntarily through the hub; only a claim
  gates a mutation, and only by refusing an overlapping claim.
- It does **not** add a new monolithic controller. The control loop is the
  existing distributed surfaces; there is no single process that owns the fleet.
- Its completion gate is **advisory**: receipts and policy-check document and
  surface evidence; they do not certify sufficiency, block merges, or replace
  code review.
- It does **not** weaken the local-first default: every ATC surface is read-only
  or advisory except claims, and the whole loop runs on one local machine with no
  required cloud service.
- "Radar" and "flight recorder" are **audit signals**, not predictions of intent
  or proof that a merge is safe.
