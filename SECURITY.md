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

The planned [`--paranoid` mode](docs/paranoid-mode.md) is a design target for one
operator switch that tightens local settings and reports missing hooks. It is not
implemented as a CLI flag yet, and it does not change the current security
boundary: encryption, signed events, per-agent identity, ACLs, private channels,
and exposed deployment threat modelling remain explicit future work.

The planned [at-rest encryption](docs/at-rest-encryption.md) profile scopes
optional protection for local SQLite event stores, relay logs, A2A state, cursor
files, archive reports, temporary files, and backups. It is not implemented yet
and does not encrypt existing databases or replace host filesystem permissions.

The planned [end-to-end encrypted channels](docs/end-to-end-encrypted-channels.md)
profile scopes selected payload encryption while keeping routing metadata
visible. It is not implemented yet, does not replace at-rest encryption, and
does not protect compromised endpoints or plaintext after a participant decrypts
content.

The planned [private channels](docs/private-channels.md) profile scopes audience
control for project, worktree, task, and direct channels. It is not implemented
yet, does not encrypt payloads, and does not create cryptographic identity or ACL
enforcement by itself.

The planned [signed events and mTLS](docs/signed-events-mtls.md) profile scopes
event signatures, key rotation, replay protection, verification results, trust
bundles, certificate pinning, and trusted multi-host peers. It is not
implemented yet, does not encrypt payloads, does not replace per-agent identity,
and does not certify federation.

## Out of scope / known limitations

- The connect token is a proportionate shared secret, **not** a cryptographic
  identity system: there is no implemented key exchange, signatures,
  per-message authentication, or mTLS trust bundle. Do not expose the hub on an
  untrusted network and rely on the token alone.
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
