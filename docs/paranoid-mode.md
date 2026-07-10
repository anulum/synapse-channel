# Paranoid mode

`synapse hub --paranoid` is an operator switch that tightens local hub startup
settings and reports missing hardening hooks. It is implemented for the hub
runtime only. A2A and doctor paranoid profiles remain future work.

For a multi-seat *trust* profile (identity binding, role grants, private directed
messages) without mandating TLS/ACL/HMAC, use
[`--team-secure`](team-secure.md). The two compose: `--team-secure --paranoid`
is the multi-agent + network-exposed posture.

The mode is for single-owner or small trusted-team deployments that want a
repeatable strict profile before exposing more surfaces. It remains local-first:
the hub and evidence stay on the operator's machine unless the operator
deliberately adds network, model-worker, A2A, or relay egress.

## Operator outcome

`synapse hub --paranoid` does two things:

1. Refuse relaxed hub runtime settings when a safer local setting exists.
2. Print an operator checklist for controls the flag does not compose, including
   controls that ship as separate opt-in profiles.

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
- **ACL enforcement required** through `--require-acl` with an `--acl-policy`, so
  mutating verbs are authorised against the policy before routing rather than
  passing on the shared token alone.
- **Native WSS (TLS) required** through `--tls-certfile` and `--tls-keyfile`, so
  the transport is encrypted rather than plain `ws://`.
- **Metrics token required** whenever `--metrics` is enabled.
- **Metrics query tokens disabled** even if the deprecated
  `--metrics-query-token-ok` compatibility flag is passed; `Authorization:
  Bearer` remains the only token presentation in paranoid mode.
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

## Controls not composed by this profile

The checklist explicitly reports controls that `--paranoid` does not enable. A
listed control may be available separately; the list prevents the one flag from
implying a broader posture than it actually configures:

- **At-rest encryption** ships separately: `--db-key-file` protects the live
  event store with SQLCipher, and the AES-256-GCM profile protects whole-file
  surfaces. `--paranoid` does not choose or load those keys. See
  [at-rest encryption](at-rest-encryption.md).
- **Mutual-TLS client-certificate verification and the signed-events/mTLS operator
  workflow** beyond the runtime primitives. Paranoid mode requires server TLS
  and HMAC-authenticated mutating frames, but the packaged hub CLI does not load
  an Ed25519 event-trust bundle or client CA. Federation commands manage a
  different operator-confirmed domain bundle. See
  [signed events and mTLS](signed-events-mtls.md).
- **Per-message key rotation and revocation operator workflow** beyond the
  runtime's explicit HMAC key list. The hub can enforce selected signed
  mutating frames, but there is no managed key store, no key file lifecycle, and
  no automatic rotation workflow. See the
  [per-message authentication runtime](per-message-authentication.md).
- **Cryptographic per-agent identity** ships through machine-key
  trust-on-first-use and operator identity bundles; `--team-secure` requires the
  latter. `--paranoid` alone does not enable either, so its ACL may still
  authorise a declared sender name. See [identity and ACL](identity-and-acl.md).
- **Private channels** ship as an audience-scoped runtime, but `--paranoid` does
  not create channels or membership. See [private channels](private-channels.md).
- **Differential-privacy blackboard projections** for multi-organisation views
  that should share aggregate progress without raw notes. See the
  [differential-privacy blackboard design](differential-privacy-blackboard.md)
  for redaction policy, aggregation boundary, cohort thresholds, privacy budget,
  and audit-trail requirements.
- **End-to-end encrypted chat** ships through explicit endpoint key files, but
  `--paranoid` does not select participant keys or enable encryption for a
  sender/listener. Broader encrypted payload profiles and managed key discovery
  remain staged. See [encrypted channels](end-to-end-encrypted-channels.md).
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
example, the paranoid hub requires a token and durable event log, but it does not
claim at-rest encryption unless the operator separately supplies and verifies
the SQLCipher or envelope profile.

## Boundaries

Paranoid mode does not encrypt existing databases. It does not create
cryptographic identity. It does not certify exposed deployments. It does not
sandbox connected agents, replace host firewalls, or validate third-party A2A
conformance.

The hub implementation remains an operator checklist plus strict local defaults.
Later work can promote individual checks into enforcement only after the
relevant feature exists and has focused tests, documentation, migration notes,
and release evidence.
