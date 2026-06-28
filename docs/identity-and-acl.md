<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — identity and ACL design
-->

# Identity and ACL design

Per-agent identity and ACL enforcement are a design target for deployments that
need more than the current shared-token mode and caller-name model. They are not
implemented yet. Today, a secured hub admits a connection with a shared token and
then trusts the sender name on accepted frames. That remains proportionate for a
single-owner local hub, but it is not enough for exposed or multi-operator
coordination.

The goal is to bind every durable actor to an identity-bound credential, then
evaluate whether that identity may perform a requested action before the hub
mutates state or exposes scoped data. This does not replace per-message
authentication, does not replace signed events, and does not sandbox agents.

## Identity model

An identity record should separate the stable actor from the terminal, process,
or provider session currently using it:

- **Agent id:** the stable per-agent identity used as the audit subject on
  claims, releases, messages, progress notes, capability cards, and receipt
  evidence.
- **Seat id:** the local workstation, tmux session, service unit, or operator
  seat that launched the agent. A seat can run one or more agent ids, but it
  should not silently borrow another agent's credential.
- **Project namespace:** the project or organisation prefix, such as
  `SYNAPSE-CHANNEL`, that scopes identity names, claim paths, private channels,
  metrics views, and future federation policy.
- **Credential id:** the key, certificate, or token handle that proves the
  caller is allowed to act as the agent id.
- **Audit subject:** the canonical identity string recorded by the hub after
  verification, not a self-reported display name.

The first credential format should be local-first and operator-managed. A
credential can be a symmetric key, an asymmetric signing key, or a certificate
handle, but every option must support key id lookup, credential rotation,
revocation, owner recovery, and diagnostics that explain which credential failed
without leaking secret material.

## ACL model

ACLs should deny by default. An admitted identity may only perform the allowed
verb on a matching target pattern inside its project namespace. The initial
permission vocabulary should stay small and auditable:

| Permission | Scope |
| --- | --- |
| `message` | Send chat, direct messages, handoffs, and checkpoints to allowed targets. |
| `claim` | Create or update claims for allowed path target patterns. |
| `release` | Release claims and publish release receipts; this is the release permission. |
| `board` | Create and update blackboard tasks or findings. |
| `metrics` | Read live or historical operational metrics; this is the metrics permission. |
| `dashboard` | Read dashboard snapshots; this is the dashboard permission. |
| `a2a` | Serve or consume Agent-to-Agent bridge data; this is the A2A permission. |
| `namespace` | Administer project namespace membership and trust material; this is the namespace permission. |

Each rule should include an allowed verb, a target pattern, an optional channel
or project namespace constraint, and a decision reason suitable for receipts and
postmortems. Target patterns must be structured data, not ad hoc substring
checks, so a future policy engine can compare path claims, channel ids, agent
ids, and A2A endpoints consistently.

## Enforcement path

The hub should evaluate identity and ACLs at the point where a frame would change
state or reveal scoped state:

1. Resolve the connection credential to an audit subject.
2. Canonicalise the requested verb and target pattern.
3. Evaluate the identity's ACL rules with deny by default semantics.
4. Record the verification result, rule id, and reason in the event log.
5. Reject unauthorized frames before state mutation or scoped data disclosure.

Read paths need the same treatment as write paths. Metrics permission, dashboard
permission, private-channel history, A2A permission, and event-query access all
need explicit rules because they can expose sensitive coordination metadata even
when no payload is changed.

## Migration from shared-token mode

Migration should be gradual because local solo use must stay simple:

1. Inventory current sender names, `syn` identities, worker-session identities,
   service units, and shell-hook waiters.
2. Issue identity-bound credentials for known agent ids and seat ids.
3. Run a shadow mode that records which ACL rule would have matched while still
   accepting shared-token mode traffic.
4. Warn on ambiguous names, missing credentials, unscoped target patterns, and
   overbroad namespace permissions.
5. Enable enforcement per project namespace only after the operator has recovery
   credentials and rollback instructions.

Rollback should restore shared-token mode without deleting audit history. The
event log must keep both the asserted sender and the resolved audit subject so a
postmortem can explain what changed during migration.

## Relationship to other designs

- [Per-message authentication](per-message-authentication.md) authenticates
  selected frames after WebSocket connect authentication. Identity and ACLs
  decide whether the authenticated caller may perform the requested verb.
- [Signed events and mTLS](signed-events-mtls.md) make selected durable records
  tamper-evident and authenticate configured peers. Identity and ACLs decide who
  is authorized before those records are created or relayed.
- [Signed capability cards](signed-capability-cards.md) make route-relevant
  advertisements tamper-evident. Identity and ACLs decide who may advertise,
  update, project, or revoke those cards.
- [Private channels](private-channels.md) scope the intended audience. Identity
  and ACLs decide who may join, leave, read history, or publish to that channel.
- [Differential-privacy blackboard](differential-privacy-blackboard.md)
  projections can redact or perturb shared board reports. Identity and ACLs
  decide who may create board data or request those projections.
- [Policy engine](policy-engine.md) can later consume identity decisions,
  release receipts, and event-log evidence, but it should not be the first layer
  that authenticates a caller.

## Boundaries

This is a design target, not implemented yet. Identity and ACLs do not encrypt
payloads, do not replace per-message authentication, do not replace signed
events, do not replace TLS, do not sandbox agents, and do not make arbitrary
provider code safe to run.

The local-first tradeoff is administrative complexity. A single-owner loopback
hub should still work with shared-token mode. Exposed deployments need explicit
credentials, credential rotation, revocation, owner recovery, deny by default
ACLs, diagnostics, and operator procedures before identity and ACL enforcement
can become a runtime mode.
