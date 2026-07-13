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
Where the table says *recommended*, the hub still starts but warns: a token
presented off loopback over plaintext `ws://` logs a startup advisory (the
token and every frame are readable on the network path) — native WSS
(`--tls-certfile`/`--tls-keyfile`) or a `wss://` proxy silences it, and
`--paranoid` makes native WSS mandatory.

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
| Identity binding + role claims + private directed | — | **`--team-secure`** | **`--team-secure`** | **`--team-secure`** + `--paranoid` |
| One-flag preset | — | `--team-secure` | `--paranoid` | `--paranoid` (+ `--team-secure`) |

[`synapse hub --team-secure`](docs/team-secure.md) is the multi-seat trust
preset (token, identity trust bundle, role grants, private directed messages).
`synapse hub --paranoid` composes the team-LAN / internet-exposed column into a
single switch (token, durable log, per-message auth, ACL, native WSS; query
tokens and the off-loopback override disabled) and fails closed when any of them
is absent. Combine both when a multi-seat hub is also network-exposed.

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

### Runtime evidence map

The following controls are shipped, but most are opt-in and none turns the
default local hub into a managed multi-tenant service. The source and focused
tests named here are the repository evidence for that boundary.

| Capability | Shipped activation or runtime | Focused evidence | Remaining boundary |
|---|---|---|---|
| Multi-seat trust | `hub --team-secure --identity-trust FILE --role-grants FILE` forces identity binding, role-claim grants, and private directed routing. | `tests/test_team_secure_mode_runtime.py`, `tests/test_hub_identity_binding.py`, `tests/test_hub_role_claim.py` | It does not require TLS, ACL, HMAC, or a durable log; compose `--paranoid` for an exposed hub. |
| Strict exposed-hub profile | `hub --paranoid` requires a token, `--db`, HMAC per-message authentication, ACL enforcement, native WSS, and metrics bearer auth when metrics are enabled. | `tests/test_paranoid_policy.py`, `tests/test_paranoid_mode_runtime.py` | It does not automatically enable identity binding, at-rest encryption, private/E2E channels, or mutual-TLS client verification. |
| Identity and ACL | With the `encryption` extra, default clients sign registration with a machine key and the hub persists trust-on-first-use name pins. Operator bundles use `--identity-trust --require-identity-binding`; ACLs use `--acl-policy --require-acl`. | `tests/test_hub_identity_tofu.py`, `tests/test_hub_identity_binding.py`, `tests/test_hub_acl_enforcement.py` | Core-only clients without `cryptography` remain unsigned; read-surface ACLs and full multi-tenant IAM remain out of scope. |
| Per-message and signed-event authentication | `--message-auth-key --require-message-auth` enforces HMAC on selected mutating frames. Embedded hubs may supply an `EventSignatureTrustBundle` as the Ed25519 alternative. | `tests/test_hub_per_message_auth.py`, `tests/test_message_auth.py`, `tests/test_agent_identity_signing.py` | The packaged hub CLI does not load an Ed25519 event-trust bundle; neither profile encrypts payloads. |
| TLS and trusted peers | `--tls-certfile --tls-keyfile` enables native WSS. The library ships mutual-TLS server contexts and certificate-pin peer bundles used by guarded multi-hub paths. | `tests/test_hub_tls.py`, `tests/test_multihub_federation.py`, `tests/test_multihub_serving.py` | The packaged hub CLI does not expose a client-CA option, so native WSS alone is server TLS, not mutual TLS. |
| Federation | `--federation-store`, `--federation-observe-only`, and `--federation-offer` compose with `federation import/list/revoke/rotate/offer/fetch` and deny-by-default frame/peer gates. | `tests/test_hub_federation_frame_path.py`, `tests/test_federation_lifecycle.py`, `tests/test_federation_rotation.py` | Trust remains an explicit out-of-band operator decision; there is no automatic trust distribution or external federation certification. |
| Data protection | `--db-key-file` enables SQLCipher for the live event store; the encryption profile covers whole-file envelopes; `send/listen --encrypt-key-file` protects selected chat bodies; private channels restrict audience. | `tests/test_hub_sqlcipher_e2e.py`, `tests/test_at_rest.py`, `tests/test_e2ee_channels_runtime.py`, `tests/test_private_channel_runtime.py` | These are separate opt-ins. They do not protect hub RAM, compromised endpoints, or routing metadata. |
| Trust evidence | `synapse trust-graph` projects the durable event log into provenance-linked evidence edges. | `tests/test_cli_trust_graph.py`, `tests/test_trust_graph.py` | It does not rank agents, authorise execution, or implement the planned owner-annotation workflow. |

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
  only for literal path or directory-prefix overlap — wildcard-glob algebra is
  intentionally out of scope, so a path is treated as a literal file or directory
  prefix and never expanded. The hub never reads, opens, or resolves the strings
  on the filesystem. A claim on `../../etc/passwd` coordinates nothing and touches
  nothing on disk, so scope strings are not a path-traversal surface.
- **Metrics endpoint.** The optional `synapse hub --metrics` endpoint is off by
  default. Without `--metrics-token`, enabled metrics and health probes carry
  operational metadata unauthenticated, so keep them on a loopback bind. When
  metrics are enabled on a non-loopback host, the hub refuses to start without a
  metrics token unless `--insecure-off-loopback` is set. The recommended token
  presentation is `Authorization: Bearer <token>`. The `?token=<token>` query
  form is accepted only when the operator opts in with the deprecated
  `--metrics-query-token-ok` compatibility flag. It warns at parse time and is
  scheduled for removal in 0.101.0; migrate probes to the bearer header now.
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
- **Update check.** `synapse --version` is network-silent by default. Set
  `SYNAPSE_UPDATE_CHECK=1` to opt in to a best-effort daily PyPI version check;
  `SYNAPSE_NO_UPDATE_CHECK=1` suppresses it even when the opt-in is present.
- **Verified release receipts.** `synapse verify-release` executes commands
  supplied by the local caller and records digest-only stdout/stderr evidence,
  artifact hashes, and Git state for `synapse release --receipt`. It does not
  sandbox untrusted commands, review whether commands are sufficient, or turn a
  `supported` receipt into independent proof of correctness.
- **Published distribution provenance.** Beginning with the first release after
  0.99.3, each GitHub Release carries `SHA256SUMS` and a portable Sigstore bundle
  for the exact wheel, source archive, and SBOM published from the tag workflow.
  With a current GitHub CLI, verify the downloaded bytes and their GitHub-hosted
  SLSA provenance before installing them:

  ```bash
  gh release download vX.Y.Z -R anulum/synapse-channel --dir synapse-release
  cd synapse-release
  sha256sum --check SHA256SUMS
  bundle="synapse-channel-vX.Y.Z-provenance.sigstore.json"
  while read -r _ artifact; do
    gh attestation verify "$artifact" \
      --repo anulum/synapse-channel \
      --bundle "$bundle" \
      --signer-workflow anulum/synapse-channel/.github/workflows/publish.yml \
      --source-ref refs/tags/vX.Y.Z \
      --deny-self-hosted-runners
  done < SHA256SUMS
  ```

  Release 0.99.3 predates this signing workflow and has checksums but no
  provenance bundle. A valid attestation binds bytes to the named repository,
  workflow, and source ref; it is not a semantic safety review. It also does not
  enable GitHub's separate owner-controlled immutable-release setting.

[`synapse hub --team-secure`](docs/team-secure.md) is the multi-seat trust
preset: it requires a connect token, an identity trust bundle with binding
enforced, a role-grant store with role-claim enforcement, and private directed
messages. It recommends (but does not require) message-auth, ACL, TLS, and a
durable event log. [`synapse hub --paranoid`](docs/paranoid-mode.md) is the
production secure preset for the hub runtime. It refuses to start unless the hub
is fully hardened — a connect token, durable event-log replay, per-message
authentication on selected mutating frames, ACL enforcement with a policy,
native WSS (TLS), and metrics bearer-token auth when metrics are enabled — and
it disables the metrics query token and the insecure off-loopback override. It
still reports hooks it does not automatically enable: mutual-TLS
client-certificate verification, cryptographic per-agent identity (use
`--team-secure` or `--require-identity-binding`), and exposed deployment threat
modelling. At-rest encryption and private channels ship as separate opt-in
profiles (below) that paranoid mode does not automatically enable.

The [at-rest encryption](docs/at-rest-encryption.md) runtime encrypts local
storage surfaces at rest with AES-256-GCM envelope encryption (relay logs, A2A
state, cursor files, archive reports, temporary files, backups, and cold copies
of SQLite files). A random data key does the bulk encryption and is wrapped by a
pluggable key-encryption key — a scrypt passphrase, or a hardware backend
(PKCS#11 token, TPM 2.0) — so rotating that key rewraps the data key without
re-encrypting any data; the cipher counts sealed messages and refuses to encrypt
past the AES-GCM per-key safety bound. It writes owner-only files, ships a
migration/rekey flow, and starts fail-safe. For the **live** hub event store
opened with `synapse hub --db`, page-level SQLCipher encryption is available via
`pip install synapse-channel[sqlcipher]` and `--db-key-file` (see
[at-rest encryption](docs/at-rest-encryption.md)). SQLCipher encrypts pages on
disk for offline confidentiality; neither profile protects a running hub's RAM
or replaces host filesystem permissions.

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

The [agent trust graph](docs/agent-trust-graph.md) read side is implemented:
`synapse trust-graph` projects reliability signals, release receipts, handoff
outcomes, and conflict history into provenance-linked evidence edges. Routing
integration and owner annotations remain design targets. The graph does not rank
agents, assign trust grades, authorise execution, replace code review, or replace
identity and ACL.

The [federated trust model](docs/federated-trust-model.md) has shipped as an
opt-in, deny-by-default policy/store/lifecycle and bundle-exchange layer. A hub
can compose operator-confirmed domains into signed-frame authorisation with
`--federation-store`; operators can offer, fetch, fingerprint, import, list,
rotate, and revoke bundle material. The trust decision stays out-of-band by
design. This is not a certificate authority, automatic trust distribution,
authorisation for untrusted organisations, or external federation certification.

The [signed events and mTLS](docs/signed-events-mtls.md) runtime primitives are
implemented for embedded hubs and guarded multi-hub paths: Ed25519 event
verification, replay and scope checks, mutual-TLS server contexts, and peer
certificate pins. The packaged `hub` CLI still has no signed-event trust-bundle
loader or client-CA option, so native WSS is not automatically mTLS. These
controls do not encrypt payloads, replace per-agent identity, or certify external
federation.

The [per-message authentication](docs/per-message-authentication.md) runtime
enforces opt-in HMAC-SHA256 authentication for selected mutating WebSocket
frames after connect authentication. It uses canonical frames, key ids, sender
binding, nonces, signed sequence metadata, timestamp windows, and a bounded
in-memory replay cache. It does not encrypt payloads, does not replace TLS,
does not add public-key signatures or signed durable events, and does not
replace per-agent identity or ACL enforcement.

The [identity and ACL](docs/identity-and-acl.md) runtime provides trust-on-first-use
machine-key pins when the `encryption` extra is installed, operator-managed
identity binding through `--identity-trust --require-identity-binding`, and
deny-by-default mutating-frame authorisation through `--acl-policy
--require-acl`. `--team-secure` composes identity binding with role grants and
private directed routing. Read-surface ACLs, automated credential lifecycle,
owner recovery, and full multi-tenant IAM remain outside this runtime. Identity
and ACL do not replace per-message authentication, signed events, TLS, or host
process isolation.

The [signed capability cards](docs/signed-capability-cards.md) runtime verifies
domain-separated Ed25519 advertisements for manifests, directories, dashboards,
MCP resources, and A2A Agent Card projections. Card keys live in a separate,
explicitly scoped trust bundle; verification reports unknown/revoked keys, bad
signatures, expiry, replay, downgrade, binding, digest, and bounded-history failures.
Unsigned local cards remain visible as advisory discovery. Replay/downgrade history
uses a bounded in-memory default. The opt-in owner-only SQLite store enabled with
`--capability-card-history-db` persists replay and downgrade floors across hub
restarts; runtime persistence failure reports `history_unavailable` rather than a
verified card. Verification remains advisory and no enforcement flag exists: a
verified card does not authorize tools, replace per-message authentication, replace
signed events, or sandbox agents.

## Out of scope / known limitations

- The connect token is a proportionate shared secret, **not** a cryptographic
  identity system. Machine-key trust-on-first-use, operator identity bundles,
  ACL enforcement, Ed25519 signed events, and mutual-TLS/pinning primitives are
  separate opt-ins; none is implied by `--token`. Per-message HMAC protects only
  selected mutating frames. Do not expose the hub on an untrusted network and
  rely on the token alone.
- The bus does not sandbox the agents that connect to it. An agent is trusted to
  the extent the operator trusts the process it runs in. Never run untrusted agent
  code against a hub.
- The event log and SQLite database are plaintext by default. SQLCipher page
  encryption for the live store and AES-256-GCM whole-file envelopes are shipped
  opt-ins; they require explicit key management and do not protect a running
  hub's RAM or create multi-tenant isolation.
- The A2A bridge is a local HTTP+JSON bridge over SYNAPSE capabilities, not
  externally validated for full A2A conformance. Remote conformance, real webhook
  receiver behavior, and operator-visible production deployment receipts remain
  external validation work. The A2A-specific exposed-edge threat model is
  documented in `docs/a2a-deployment-threat-model.md`.
- `tools/fuzz_protocol_decode.py` provides local decoder hardening evidence for
  malformed bytes, malformed JSON, quoted bracket runs, valid nested JSON, and
  depth-limit rejection. A weekly and manually dispatchable read-only workflow
  adds 1,000-example Hypothesis properties over the production wire decoder and
  SQLite event-store reopen, cursor, and deletion paths. Falsifying examples are
  committed as explicit regressions rather than relying on CI's ephemeral
  Hypothesis database. This automated property-based fuzzing is not an external
  protocol-conformance certification.

## Licensing

SYNAPSE CHANNEL is AGPL-3.0-or-later with a commercial licence available; see
[`NOTICE.md`](NOTICE.md).
