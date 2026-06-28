# Paranoid mode design

`--paranoid` is a design target for one operator switch that tightens local
Synapse settings and reports missing hardening hooks. It is not implemented as a
CLI flag yet. Today, operators can apply the equivalent checklist manually with
existing hub, A2A, metrics, persistence, and release-receipt controls.

The mode is for single-owner or small trusted-team deployments that want a
repeatable strict profile before exposing more surfaces. It remains local-first:
the hub and evidence stay on the operator's machine unless the operator
deliberately adds network, model-worker, A2A, or relay egress.

## Operator outcome

When implemented, `--paranoid` should do two things:

1. Refuse relaxed runtime settings when a safer local setting exists.
2. Print an operator checklist for missing hooks that Synapse cannot honestly
   provide yet.

The command should never imply that one flag makes an exposed deployment safe.
It should make the current posture obvious, repeatable, and auditable.

## Strict settings

The switch should map to these concrete settings:

- **Token required** for any hub that is not explicitly private to the current
  operator workflow.
- **Loopback-only by default** for hub, metrics, dashboard, and A2A HTTP
  surfaces unless the operator passes a separate exposed-bind override.
- **Metrics token required** whenever metrics are enabled.
- **Metrics query tokens disabled**; `Authorization: Bearer` remains the only
  token presentation in paranoid mode.
- **A2A bearer auth required** for task, RPC, extended-card, and push routes
  when the bridge is enabled outside a localhost smoke check.
- **Owner-only state files** for SQLite state, A2A state, relay cursors, and
  generated reports.
- **Bounded retention** for blackboard progress, findings, chat history, relay
  lines, A2A task state, push configs, replay history, and terminal-task
  retention.
- **Durable event log required** so claims, releases, task updates, handoffs,
  findings, and chat can be replayed after restart.
- **Release receipt required** before a claim is treated as complete by local
  hooks or future policy checks.

These settings should be visible in a dry-run report before any service unit or
hook is rewritten.

## Missing hardening hooks

The checklist should explicitly report these missing or future hooks instead of
pretending they are solved:

- **At-rest encryption** for SQLite databases, relay logs, A2A state files, and
  generated reports. See the [at-rest encryption design](at-rest-encryption.md)
  for storage scope, key storage, rotation, backup recovery, and local-first
  tradeoffs.
- **Signed events** for durable event-log authenticity and tamper evidence.
- **Per-message authentication** after WebSocket connect authentication,
  including replay protection and key rotation.
- **Per-agent identity** beyond the current shared-token and caller-name model.
- **ACL enforcement** for verbs, namespaces, metrics, dashboard, A2A, and
  release actions.
- **Private channels** for project-local or worktree-local payloads that the hub
  should not broadcast to every trusted participant.
- **End-to-end encrypted channels** for selected payloads that the hub should
  route without reading plaintext. See the
  [encrypted channels design](end-to-end-encrypted-channels.md) for recipient
  sets, per-project keys, per-worktree keys, and operational key management.
- **Deployment threat model** evidence for exposed bridges, reverse proxies,
  TLS termination, logging, retention, DNS rebinding, and operator procedures.

Reporting a hook as missing is a security feature. It keeps the operator from
mistaking a strict local profile for cryptographic federation or managed-cloud
isolation.

## Command shape

A future command should support dry-run first:

```bash
synapse doctor --paranoid
synapse hub --paranoid --db ~/synapse/hub.db --token-file ~/.config/synapse/token
synapse a2a-serve --paranoid --a2a-token-file ~/.config/synapse/a2a-token
```

The doctor report should include:

- Current effective setting.
- Required paranoid value.
- Evidence source, such as command-line flag, environment variable, service
  unit, file permission, or event-store path.
- Status: `pass`, `warn`, `fail`, or `missing_hook`.
- Exact remediation text.

Runtime commands should fail closed only for settings they directly control. For
example, a paranoid hub can require a token and durable event log, but it cannot
claim at-rest encryption until that hook exists.

## Boundaries

Paranoid mode does not encrypt existing databases. It does not create
cryptographic identity. It does not certify exposed deployments. It does not
sandbox connected agents, replace host firewalls, or validate third-party A2A
conformance.

The first implementation should remain an operator checklist plus strict local
defaults. Later work can promote individual checks into enforcement only after
the relevant feature exists and has focused tests, documentation, migration
notes, and release evidence.
