<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — agent trust graph design
-->

# Agent trust graph design

The agent trust graph is a design target for making reliability and routing
evidence easier to inspect without turning agents into hidden scores. It is not
implemented yet. Today, `synapse reliability` reports audit signals, and
`synapse route-task` returns advisory routing hints with explainable reasons.

The goal is a local evidence graph over existing event-log records: release
receipts, capability observations, claim outcomes, handoff outcomes, conflict
history, stale claims, and owner review notes. The graph should help operators
ask why a route was suggested or why a task needs review. It does not rank
agents, does not assign trust grades, does not authorize execution, does not
replace code review, and does not replace identity and ACL enforcement.

## Evidence model

The graph should use explicit evidence nodes and evidence edges rather than a
single reputation number:

- **Evidence node:** one observed fact from the local event log, such as a
  release receipt, reliability signal, capability observation, handoff outcome,
  claim, release, assessment note, or conflict record.
- **Evidence edge:** a typed relationship between an agent, task, capability,
  file scope, resource offer, receipt, or policy decision.
- **Provenance reference:** every node and edge must cite an event sequence,
  event kind, task id, author, timestamp, and source field when available.
- **Capability observation:** positive release-receipt evidence that a named
  agent completed work matching a capability card or route-task signal.
- **Negative evidence:** declared failed checks, stale claims, broken handoff
  candidates, unresolved owner review notes, or repeated conflict history.

The graph should preserve source detail. A route explanation that says
`observed:websocket` must still point to the release receipt and event sequence
that created the observation.

## Routing use

Graph evidence can improve routing only when it stays inspectable:

- A routing hint may cite matching capability cards, prior observed capability,
  recent clean release receipts, relevant handoff outcome, or conflict history.
- Each candidate should carry an explainable reason list and provenance
  references for each reason.
- A decay window should age out stale observations so old successes do not
  dominate new work indefinitely.
- Negative evidence should reduce confidence in the explanation or require
  owner review; it should not silently blacklist an agent.
- The policy engine may consume graph records as a policy input, but the graph
  itself should not decide whether work may merge or execute.

The first route-task integration should remain advisory. The output can say that
one candidate has fresher matching evidence than another, but it should not
produce an opaque agent grade or claim that the candidate is trustworthy.

## Review workflow

The operator-facing workflow should keep humans close to the evidence:

1. A routing or policy command asks for graph evidence for one task, agent,
   capability, file scope, or release receipt.
2. The report lists evidence nodes, evidence edges, reason codes, decay window
   effects, and provenance references.
3. Owner review can accept, ignore, or annotate the recommendation.
4. The annotation becomes another event-log fact, not a rewritten score.

This keeps the local-first tradeoff clear: the graph is useful because it is
small, local, replayable, and tied to exact events. It is not a central identity
provider or a social reputation service.

## Relationship to current surfaces

- [Coordination model](coordination-model.md) defines raw claims, board tasks,
  release receipts, route-task recommendations, reliability memory, and event
  replay. The graph should reuse those records.
- [Policy engine](policy-engine.md) can treat graph evidence as a policy input
  while keeping pass, warn, and fail decisions separate.
- [Identity and ACL](identity-and-acl.md) resolves who may act. The trust graph
  explains observed evidence; it does not replace identity and ACL.
- [Signed capability cards](signed-capability-cards.md) can make advertised
  capabilities tamper-evident. The graph can then distinguish declared
  capability from observed capability.
- [Differential-privacy blackboard](differential-privacy-blackboard.md) can
  shape shared reports when graph summaries leave the local operator boundary.

## Boundaries

This is a design target, not implemented yet. The agent trust graph does not
rank agents, does not assign trust grades, does not authorize execution, does
not reserve resources, does not replace code review, does not replace identity
and ACL, does not sandbox agents, and does not certify external providers.

The local-first tradeoff is interpretability over automation. Evidence should be
useful for routing review and policy input because it remains traceable to local
events, not because it hides judgement inside an opaque model.
