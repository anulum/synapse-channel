<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — security policy
-->

# Security Policy

## Supported versions

The latest released `0.x` line receives security fixes. Older lines are not
maintained; upgrade to the latest version.

## Reporting a vulnerability

Please report security issues privately — do **not** open a public issue.

- Preferred: open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories)
  on the repository.
- Or email `protoscience@anulum.li` with `[SECURITY]` in the subject.

You can expect an acknowledgement within 72 hours and, for a confirmed issue, a
remediation plan with a target timeline based on severity.

## Threat model and posture

SYNAPSE CHANNEL is a **local-first** coordination bus. Its default and intended
deployment is a single operator on one machine, with the hub bound to loopback
and no authentication — appropriate for that single-owner setting.

### Deployment profiles

Security here is proportionate to exposure, not one-size-fits-all. Pick the
profile that matches where the hub is reachable from, and apply its controls; a
control is *required* where the table says so, and the hub refuses to start when
a required exposure guard is missing (override only with `--insecure-off-loopback`).

| Control | local-dev | single-user workstation | team LAN | internet-exposed (behind reverse proxy) |
|---|---|---|---|---|
| Bind | loopback | loopback | private interface | loopback behind the proxy |
| Connect token (`--token-file`) | optional | recommended | **required** | **required** |
| Transport encryption (TLS / WSS) | — | — | recommended | **required** (proxy or `--tls-certfile`) |
| ACL policy (`--require-acl`) | — | optional | recommended | **required** |
| Per-message auth (`--require-message-auth`) | — | optional | recommended | **required** |
| Metrics token (`--metrics-token`) | — | required if `--metrics` | **required** if `--metrics` | **required** if `--metrics` |
| Metrics query token | loopback debug only | loopback debug only | disabled | disabled |
| Durable log (`--db`) | optional | recommended | recommended | recommended |
| One-flag preset | — | — | `--paranoid` | `--paranoid` |

`synapse hub --paranoid` composes the team-LAN / internet-exposed column into a
single switch (token, durable log, per-message auth, ACL, native WSS; query
tokens and the off-loopback override disabled) and fails closed when any of them
is absent.

The runtime floor is a single dependency (`websockets`); several
security-relevant features need an optional extra installed:

| Capability | Extra |
|---|---|
| At-rest encryption, signed release receipts, Ed25519 per-message auth, TLS certificate pinning | `encryption` |
| WASM capability sandbox | `wasm` |
| OpenTelemetry span export | `otel` |
| MCP bridge and registration | `mcp` |
| All of the above | `all` |

Native WSS itself uses the standard-library `ssl` module and needs no extra; the
`encryption` extra adds the cryptographic identity and pinning helpers on top.

When that boundary is crossed, the proportionate controls are:

- **Connect authentication.** `synapse hub --token SECRET` requires a shared
  secret on the first message of each connection, compared in constant time. The
  hub refuses a non-loopback bind unless a token is configured, or unless the
  operator explicitly passes `--insecure-off-loopback` to accept an exposed
  unauthenticated hub. Prefer `--token-file PATH` or the `SYNAPSE_TOKEN`
  environment variable over `--token`, which is visible in the process list.
- **Bounded resources.** A `--max-clients` connection cap, a `--max-msg-kb` frame
  size cap, per-agent rate limiting, bounded chat history, a bounded progress
  ledger, a bounded relay log, and bounded JSON decode depth keep one runaway
  agent or a flood from exhausting the single hub.
- **Lease and epoch guards.** Claims expire; each lease carries an epoch so a
  superseded agent cannot act on a dead claim; mutations support idempotency keys
  so a reconnect retry is applied once while the hub retains its idempotency
  cache. The idempotency cache is not a durable identity or replay-protection
  system across arbitrary hub restarts.
- **Advisory file scopes.** A claim's `paths` are opaque strings the hub compares
  only for glob overlap — it never reads, opens, or resolves them on the
  filesystem. A claim on `../../etc/passwd` coordinates nothing and touches nothing
  on disk, so scope strings are not a path-traversal surface.
- **Metrics endpoint.** The optional `synapse hub --metrics` endpoint is off by
  default. Without `--metrics-token`, enabled metrics and health probes carry
  operational metadata unauthenticated, so keep them on a loopback bind. When
  metrics are enabled on a non-loopback host, the hub refuses to start without a
  metrics token unless `--insecure-off-loopback` is set. The recommended token
  presentation is `Authorization: Bearer <token>`. The `?token=<token>` query
  form is accepted only when the operator opts in with
  `--metrics-query-token-ok`.
- **A2A HTTP bridge.** `synapse a2a-serve` is a separate stdlib HTTP edge that
  defaults to `127.0.0.1`. Its public Agent Card is intentionally readable; the
  task, RPC, extended-card, and push-configuration routes can require HTTP
  Bearer auth with `--bearer-auth --a2a-token`, with bearer values compared in
  constant time. Request bodies are capped by byte size and JSON nesting depth
  before A2A dispatch. Persisted A2A state files and write temp files are
  restricted to owner-only permissions. Webhook delivery validates resolved
  target addresses before sending and before following redirects, blocking
  localhost, loopback, private, and link-local destinations. Stored tasks, task
  history, artifacts, push configs, in-process replay history, and terminal-task
  retention are bounded. Treat any non-loopback A2A bind as an exposed HTTP
  service: use bearer auth, keep state files private, and do not claim external
  A2A conformance until interoperability and webhook validation have run.

The core hub and its state stay on the operator's machine, but two boundaries are
worth stating plainly:

- **Model workers are a deliberate egress.** An on-channel model worker
  (`synapse worker`) sends recent channel context — and an `Authorization` bearer
  token — to the OpenAI-compatible endpoint the operator configures with
  `--base-url`. The hub is local-first, but a worker is an intentional bridge to
  whatever backend it is pointed at, so `--base-url` must be trusted. A rule-based
  worker (`--provider rule`) never leaves the machine.
- **Update check.** `synapse --version` makes one request a day to PyPI to check
  for a newer release; it sends nothing beyond the request itself. Silence it with
  `SYNAPSE_NO_UPDATE_CHECK=1`.
- **Verified release receipts.** `synapse verify-release` executes commands
  supplied by the local caller and records digest-only stdout/stderr evidence,
  artifact hashes, and Git state for `synapse release --receipt`. It does not
  sandbox untrusted commands, review whether commands are sufficient, or turn a
  `supported` receipt into independent proof of correctness.

[`synapse hub --paranoid`](docs/paranoid-mode.md) is the production secure preset
for the hub runtime. It refuses to start unless the hub is fully hardened — a
connect token, durable event-log replay, per-message authentication on selected
mutating frames, ACL enforcement with a policy, native WSS (TLS), and metrics
bearer-token auth when metrics are enabled — and it disables the metrics query
token and the insecure off-loopback override. It still reports the hooks it
cannot honestly enforce on its own: mutual-TLS client-certificate verification,
cryptographic per-agent identity, and exposed deployment threat modelling remain
explicit future work. At-rest encryption and private channels ship as separate
opt-in profiles (below) that paranoid mode does not automatically enable.

The [at-rest encryption](docs/at-rest-encryption.md) runtime encrypts local
SQLite event stores and their WAL/SHM sidecars, relay logs, A2A state, cursor
files, archive reports, temporary files, and backups at rest with AES-256-GCM
envelope encryption. A random data key does the bulk encryption and is wrapped by
a pluggable key-encryption key — a scrypt passphrase, or a hardware backend
(PKCS#11 token, TPM 2.0) — so rotating that key rewraps the data key without
re-encrypting any data; the cipher counts sealed messages and refuses to encrypt
past the AES-GCM per-key safety bound. It writes owner-only files, ships a
migration/rekey flow for existing databases, and starts fail-safe. It does not
transparently encrypt a live, open database's pages in memory — a page-level,
SQLCipher-class profile for the running event store remains future work — and it
does not replace host filesystem permissions.

The [end-to-end encrypted channels](docs/end-to-end-encrypted-channels.md)
runtime encrypts selected chat payloads on the sending endpoint and decrypts
them on the listening endpoint with a local key file. The hub still sees routing
metadata, key ids, recipient names, nonce, and ciphertext. It does not replace
at-rest encryption, does not manage key discovery or rotation, and does not
protect compromised endpoints or plaintext after a participant decrypts content.

The [private channels](docs/private-channels.md) runtime scopes chat delivery to
explicit channel members and exposes bounded member-only history plus filtered
relay/event-query projections. It does not encrypt payloads and does not create
cryptographic identity or ACL enforcement by itself.

The planned [differential-privacy blackboard](docs/differential-privacy-blackboard.md)
profile scopes redacted and noisy shared blackboard projections for
multi-organisation views. It is not implemented yet, keeps raw local board data
exact for the operator, and does not encrypt payloads, replace private channels,
replace end-to-end encrypted channels, anonymize raw logs, or authorize board
writes.

The planned [agent trust graph](docs/agent-trust-graph.md) profile scopes
evidence-linked routing review over reliability signals, release receipts,
capability observations, handoff outcomes, and conflict history. It is not
implemented yet and does not rank agents, assign trust grades, authorize
execution, replace code review, or replace identity and ACL.

The planned [federated trust model](docs/federated-trust-model.md) profile scopes
how independent operator-managed domains could peer: out-of-band, deny-by-default
bundle exchange that composes identity, signed events, mutual TLS, ACLs, and
receipts across a domain boundary. It is not implemented yet, is not a
certificate authority, does not authorize untrusted organisations, does not
weaken any single check it composes, and does not change the local-first default.

The planned [signed events and mTLS](docs/signed-events-mtls.md) profile scopes
event signatures, key rotation, replay protection, verification results, trust
bundles, certificate pinning, and trusted multi-host peers. It is not
implemented yet, does not encrypt payloads, does not replace per-agent identity,
and does not certify federation.

The [per-message authentication](docs/per-message-authentication.md) runtime
enforces opt-in HMAC-SHA256 authentication for selected mutating WebSocket
frames after connect authentication. It uses canonical frames, key ids, sender
binding, nonces, signed sequence metadata, timestamp windows, and a bounded
in-memory replay cache. It does not encrypt payloads, does not replace TLS,
does not add public-key signatures or signed durable events, and does not
replace per-agent identity or ACL enforcement.

The planned [identity and ACL](docs/identity-and-acl.md) profile scopes
per-agent identity, identity-bound credentials, project namespaces, allowed
verbs, target patterns, metrics/A2A/dashboard/release privileges,
deny-by-default authorization, credential rotation, revocation, and migration
from shared-token mode. It is not implemented yet and does not replace
per-message authentication, signed events, TLS, or host process isolation.

The planned [signed capability cards](docs/signed-capability-cards.md) profile
scopes tamper-evident capability advertisements for manifests, directories,
dashboards, MCP resources, and A2A Agent Card projections. It is not implemented
yet, leaves unsigned local cards as advisory discovery, and does not authorize
tools, replace per-message authentication, replace signed events, or sandbox
agents.

## Out of scope / known limitations

- The connect token is a proportionate shared secret, **not** a cryptographic
  identity system: there is no implemented key exchange, public-key signatures,
  per-agent identity, ACL enforcement, or mTLS trust bundle. Per-message HMAC
  authentication is opt-in and protects selected mutating frames only. Do not
  expose the hub on an untrusted network and rely on the token alone.
- The bus does not sandbox the agents that connect to it. An agent is trusted to
  the extent the operator trusts the process it runs in. Never run untrusted agent
  code against a hub.
- The event log and SQLite database are stored in plaintext on the operator's own
  machine. Encryption at rest is out of scope for the local-first niche; it is a
  concern for a future managed multi-tenant hub, not the single-owner core.
- The A2A bridge is a local HTTP+JSON bridge over SYNAPSE capabilities, not
  externally validated for full A2A conformance. Remote conformance, real webhook
  receiver behavior, TLS/reverse-proxy deployment, and exposed-edge threat
  modelling are tracked as future validation work.
- `tools/fuzz_protocol_decode.py` provides local decoder hardening evidence for
  malformed bytes, malformed JSON, quoted bracket runs, valid nested JSON, and
  depth-limit rejection. It is not an external protocol-conformance
  certification.

## Licensing

SYNAPSE CHANNEL is AGPL-3.0-or-later with a commercial licence available; see
[`NOTICE.md`](NOTICE.md).
