<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — differential privacy blackboard design
-->

# Differential privacy blackboard design

Differential-privacy blackboard controls are a design target for
multi-organisation blackboard views where one operator wants to share useful
coordination signals without exposing every raw task note, author pattern, or
release detail. They are not implemented yet. Today, the hub stores and serves
ordinary board tasks and progress notes according to local retention settings.

The goal is a projection layer over the existing blackboard and event log:
redact sensitive fields first, aggregate only where groups are large enough,
then add calibrated noise to selected counts before publishing a shared view.
This profile does not encrypt payloads, does not replace private channels, does
not replace end-to-end encrypted channels, does not anonymize raw logs, and does
not authorize board writes.

## Data scope

The first privacy profile should treat these blackboard surfaces separately:

- **Task records:** task id, title, description, status, dependencies, owner
  hints, timestamps, and release receipt references.
- **Progress notes:** ordinary status updates, assessments, handoff context,
  evidence references, and sensitive progress note bodies.
- **Release receipt summaries:** evidence names, artifacts, known failures,
  changed files, freshness, confidence, and advisory epistemic status.
- **Event-log projection:** reconstructed board history, postmortem timelines,
  reliability summaries, and temporal event-query results derived from durable
  events.

Raw local data stays local. A privacy view is a derived projection with an
explicit aggregation boundary: project, organisation, task cohort, time window,
agent cohort, or channel. Operators must know which boundary a report used
before they interpret its numbers.

## Redaction policy

Redaction must happen before aggregation or noise:

- **Field minimisation:** keep only fields needed for the shared view. A public
  planning view may need task status and dependency state, not full note bodies.
- **Sensitive progress note handling:** replace private note text with a marker,
  a reason code, or a channel/event reference rather than a paraphrase.
- **Path and artifact controls:** collapse paths to repository, directory, file
  type, or change category when exact paths would reveal confidential work.
- **Release receipt controls:** expose whether evidence exists and whether
  freshness was declared without copying private artifact names into a shared
  report.
- **Role-based view:** render different projections for operator, project
  member, external collaborator, auditor, and public roadmap audiences.

A redaction policy should be versioned and attached to the projection. A reader
should be able to see which fields were removed, generalised, or retained.

## Aggregation and noise

Differential privacy should apply only to selected aggregate reports. It should
not be used to disguise one raw progress note as safe to share.

The initial aggregate controls should include:

- **Cohort threshold:** refuse noisy aggregates when fewer than the configured
  number of tasks, agents, organisations, or events contributed.
- **Time bucketing:** group events by day, week, or release window rather than
  exposing exact timestamps.
- **Contribution bounding:** cap how many events one agent, task, or
  organisation can contribute to one aggregate.
- **Differential privacy:** add noise only to documented aggregate measures such
  as task counts, stale-task counts, completion counts, progress-note counts, or
  evidence-presence counts.
- **Epsilon and delta:** record the chosen epsilon, delta, mechanism, and random
  seed handling policy for each projection family.
- **Privacy budget:** spend from an operator-defined budget per project,
  organisation, audience, or time window before publishing another noisy view.
- **Noise budget:** record how much perturbation a projection family may apply
  before the report becomes too imprecise for operational decisions.

Noisy output must explain its precision limits. A dashboard or report should
label a value as noisy, rounded, suppressed, or exact so operators do not make
false scheduling decisions from a privacy-preserving count.

## Privacy ledger

Every privacy projection should write an audit trail separate from the raw board
event:

- Projection id, audience, source event-log range, aggregation boundary, and
  redaction policy version.
- Cohort threshold result and contribution bounds applied.
- Differential privacy mechanism, epsilon, delta, and privacy budget spent.
- Suppressed fields, suppressed cohorts, and noisy aggregate fields.
- Operator identity or future audit subject that requested the projection.

This **privacy ledger** is local evidence for postmortems and policy checks. It
does not reveal the raw hidden content, but it lets an operator reconstruct why
a shared report looked the way it did.

## Relationship to current surfaces

- [Coordination model](coordination-model.md) remains the source for raw local
  board tasks, progress, release receipts, postmortems, reliability summaries,
  and event-log projections.
- [Private channels](private-channels.md) decide who should receive selected
  messages. Differential privacy shapes aggregate or redacted views after data
  exists.
- [End-to-end encrypted channels](end-to-end-encrypted-channels.md) hide
  selected payload bodies from the hub. Differential privacy does not decrypt,
  encrypt, or protect plaintext once a participant sees it.
- [Identity and ACL](identity-and-acl.md) decides who may create board data or
  request scoped views. Differential privacy does not authorize board writes.
- [Policy engine](policy-engine.md) can later require projection metadata,
  privacy-budget evidence, or cohort thresholds before publishing shared
  reports.

## Migration path

The first implementation should be advisory and read-only:

1. Add dry-run reports that show which blackboard fields would be redacted and
   which aggregates would be suppressed by cohort threshold.
2. Add privacy-ledger records for generated projections without changing raw
   board storage.
3. Add noisy aggregate output for one bounded report family, such as public
   progress counts by week.
4. Add project-level configuration for redaction policy, aggregation boundary,
   epsilon, delta, privacy budget, and role-based view labels.
5. Enable enforcement only for explicit export or hosted dashboard surfaces, not
   for the default local board.

## Boundaries

This is a design target, not implemented yet. Differential-privacy blackboard
controls do not encrypt payloads, do not replace private channels, do not
replace end-to-end encrypted channels, do not anonymize raw logs, do not
authorize board writes, do not sandbox agents, and do not make small cohorts
safe to publish.

The local-first tradeoff is interpretability. Raw local coordination stays exact
for the operator, while shared projections may be redacted, suppressed, rounded,
or noisy. Operators need clear labels and an audit trail so privacy controls do
not turn into misleading operational metrics.
