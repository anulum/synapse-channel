# Paranoid mode

`synapse hub --paranoid` is an operator switch that tightens local hub startup
settings and reports missing hardening hooks. It is implemented for the hub
runtime only. A2A and doctor paranoid profiles remain future work.

The mode is for single-owner or small trusted-team deployments that want a
repeatable strict profile before exposing more surfaces. It remains local-first:
the hub and evidence stay on the operator's machine unless the operator
deliberately adds network, model-worker, A2A, or relay egress.

## Operator outcome

`synapse hub --paranoid` does two things:

1. Refuse relaxed hub runtime settings when a safer local setting exists.
2. Print an operator checklist for missing hooks that Synapse cannot honestly
   provide yet.

The command should never imply that one flag makes an exposed deployment safe.
It should make the current posture obvious, repeatable, and auditable.

## Strict hub settings

The hub switch maps to these concrete settings:

- **Token required** for hub access. Use `--token-file` for real deployments so
  the secret is not visible in process listings.
- **Durable event log required** through `--db`, so accepted mutations can be
  replayed after restart.
- **Per-message authentication required** for selected mutating frames. Provide
  at least one `--message-auth-key KEY_ID:SECRET:SENDER[,SENDER...]` and set
  `--require-message-auth`, so HMAC verification runs after WebSocket connect
  authentication.
- **Metrics token required** whenever `--metrics` is enabled.
- **Metrics query tokens disabled** even if `--metrics-query-token-ok` is passed;
  `Authorization: Bearer` remains the only token presentation in paranoid mode.
- **Insecure off-loopback override disabled** even if `--insecure-off-loopback`
  is passed. An off-loopback bind still needs the existing token and metrics
  token guards.

The published design target also covers future strict settings that the hub
switch does not yet enforce:

- **Loopback-only by default** for dashboard and A2A HTTP surfaces unless those
  commands grow their own paranoid profiles.
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

The hub profile prints its enforced settings and missing hooks to stderr at
startup. It does not rewrite service units or hooks.

## Missing hardening hooks

The checklist should explicitly report these missing or future hooks instead of
pretending they are solved:

- **At-rest encryption** for SQLite databases, relay logs, A2A state files, and
  generated reports. See the [at-rest encryption design](at-rest-encryption.md)
  for storage scope, key storage, rotation, backup recovery, and local-first
  tradeoffs.
- **Signed events and mTLS operator workflow** beyond the runtime primitives.
  Embedded hubs can enforce Ed25519 signed-event trust bundles and mTLS peer
  certificate pins, but the CLI has no trust-bundle import/export, key
  rotation, peer inventory, or incident-response workflow yet. See
  [signed events and mTLS](signed-events-mtls.md).
- **Per-message key rotation and revocation operator workflow** beyond the
  runtime's explicit HMAC key list. The hub can enforce selected signed
  mutating frames, but there is no managed key store, no key file lifecycle, and
  no automatic rotation workflow. See the
  [per-message authentication runtime](per-message-authentication.md).
- **Per-agent identity and ACL enforcement** beyond the current shared-token and
  caller-name model, including identity-bound credentials, verbs, namespaces,
  metrics, dashboard, A2A, and release actions. See the
  [identity and ACL design](identity-and-acl.md).
- **Private channels** for project-local or worktree-local payloads that the hub
  should not broadcast to every trusted participant. See the
  [private channels design](private-channels.md) for channel ids, membership
  lists, history visibility, retention boundaries, relay log filtering, and
  event-query filtering.
- **Differential-privacy blackboard projections** for multi-organisation views
  that should share aggregate progress without raw notes. See the
  [differential-privacy blackboard design](differential-privacy-blackboard.md)
  for redaction policy, aggregation boundary, cohort thresholds, privacy budget,
  and audit-trail requirements.
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

The hub runtime switch is available now:

```bash
synapse hub --paranoid --db ~/synapse/hub.db --token-file ~/.config/synapse/token
```

Future commands should support dry-run first:

```bash
synapse doctor --paranoid
synapse a2a-serve --paranoid --a2a-token-file ~/.config/synapse/a2a-token
```

The doctor report should include:

- Current effective setting.
- Required paranoid value.
- Evidence source, such as command-line flag, environment variable, service
  unit, file permission, or event-store path.
- Status: `pass`, `warn`, `fail`, or `missing_hook`.
- Exact remediation text.

Runtime commands fail closed only for settings they directly control. For
example, the paranoid hub requires a token and durable event log, but it cannot
claim at-rest encryption until that hook exists.

## Boundaries

Paranoid mode does not encrypt existing databases. It does not create
cryptographic identity. It does not certify exposed deployments. It does not
sandbox connected agents, replace host firewalls, or validate third-party A2A
conformance.

The hub implementation remains an operator checklist plus strict local defaults.
Later work can promote individual checks into enforcement only after the
relevant feature exists and has focused tests, documentation, migration notes,
and release evidence.
