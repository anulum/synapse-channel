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
Transport encryption is one of those guards off loopback: a token presented off
loopback over plaintext `ws://` is **refused**, not merely warned about (the token
and every frame would be readable on the network path). Terminate TLS natively
(`--tls-certfile`/`--tls-keyfile`) or front the hub with a `wss://` proxy to
satisfy it; `--insecure-off-loopback` downgrades the refusal to a warning for a
trusted private network, and `--paranoid` makes native WSS mandatory with no
override.

| Control | local-dev | single-user workstation | team LAN | internet-exposed (behind reverse proxy) |
|---|---|---|---|---|
| Bind | loopback | loopback | private interface | loopback behind the proxy |
| Connect token (`--token-file`) | optional | recommended | **required** | **required** |
| Transport encryption (TLS / WSS) | — | — | **required** off-loopback (proxy or `--tls-certfile`; override `--insecure-off-loopback`) | **required** (proxy or `--tls-certfile`) |
| ACL policy (`--require-acl`) | — | optional | recommended | **required** |
| Per-message auth (`--require-message-auth`) | — | optional | recommended | **required** |
| Metrics token (`--metrics-token`) | — | required if `--metrics` | **required** if `--metrics` | **required** if `--metrics` |
| Metrics query token | loopback debug only | loopback debug only | disabled | disabled |
| Durable log (`--db`) | optional | recommended | recommended | recommended |
| At-rest store encryption (`--db-key-file`) | — | — | **required** with `--db` off-loopback (override `--insecure-plaintext-at-rest`) | **required** with `--db` |
| Identity binding + role claims + private directed | — | **`--team-secure`** | **`--team-secure`** | **`--team-secure`** + `--paranoid` |
| One-flag preset | — | `--team-secure` | `--paranoid` | `--secure` (composes both) |

[`synapse hub --secure`](docs/secure-mode.md) is the strict multi-seat
production umbrella: it composes `--team-secure` and `--paranoid`, adds bounded
per-agent, per-host, and per-host-connection flood limits, fails closed listing
all missing material at once, and prints one consolidated report.
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
| Secure production umbrella | `hub --secure` composes `--team-secure` and `--paranoid`, forces both profiles' gates, and bounds per-agent (100/s), per-host (500/s), and per-host-connection (10) flood limits. Missing material fails closed in one aggregate error; a stricter positive limit is kept and a limit above a ceiling is refused. | `tests/test_secure_preset.py`, `tests/test_secure_preset_runtime.py` | It generates no credentials, rotates no keys, verifies no client certificates, and composes only the controls the subordinate profiles already own. |
| Flood auto-enable (REV-SEC-06) | Without `--secure`, hub startup fills disabled flood limits when exposure posture is true: off-loopback bind, connect token configured, multi-seat intent, or bridge exposed. `--bridge-exposed` (default off) declares an A2A/MCP bridge is knowingly reachable alongside the hub; `--expect-multi-seat` (default off) declares multi-seat intent (also inferred from team-secure/secure, role/identity requires, private-directed messages, identity-trust, and role-grants). Operator-positive limits are preserved; loopback single-seat without those signals stays unbounded. | `tests/test_rate_policy.py`, `tests/test_hub_auto_rate_policy_wire.py`, `tests/test_cli_processes_security_args.py` | Does not detect runtime seat count or auto-discover a2a-serve/mcp processes; operators must set `--bridge-exposed` / multi-seat intent when those postures apply. Does not replace `--secure`. |
| Identity and ACL | With the `encryption` extra, default clients sign registration with a machine key and the hub persists trust-on-first-use name pins. Operator bundles use `--identity-trust --require-identity-binding`; ACLs use `--acl-policy --require-acl`. | `tests/test_hub_identity_tofu.py`, `tests/test_hub_identity_binding.py`, `tests/test_hub_acl_enforcement.py` | Core-only clients without `cryptography` remain unsigned; read-surface ACLs and full multi-tenant IAM remain out of scope. |
| Per-message and signed-event authentication | `--message-auth-key --require-message-auth` enforces HMAC on selected mutating frames. Embedded hubs may supply an `EventSignatureTrustBundle` as the Ed25519 alternative. | `tests/test_hub_per_message_auth.py`, `tests/test_message_auth.py`, `tests/test_agent_identity_signing.py` | The packaged hub CLI does not load an Ed25519 event-trust bundle; neither profile encrypts payloads. |
| TLS and trusted peers | `--tls-certfile --tls-keyfile` enables native WSS. The library ships mutual-TLS server contexts and certificate-pin peer bundles used by guarded multi-hub paths. | `tests/test_hub_tls.py`, `tests/test_multihub_federation.py`, `tests/test_multihub_serving.py` | The packaged hub CLI does not expose a client-CA option, so native WSS alone is server TLS, not mutual TLS. |
| Federation | `--federation-store`, `--federation-observe-only`, and `--federation-offer` compose with `federation import/list/revoke/rotate/offer/fetch` and deny-by-default frame/peer gates. | `tests/test_hub_federation_frame_path.py`, `tests/test_federation_lifecycle.py`, `tests/test_federation_rotation.py` | Trust remains an explicit out-of-band operator decision; there is no automatic trust distribution or external federation certification. |
| Data protection | `--db-key-file` enables SQLCipher for the live event store (**required off loopback**: a plaintext `--db` bound off loopback is refused unless `--insecure-plaintext-at-rest` is set); the encryption profile covers whole-file envelopes; `send/listen --encrypt-key-file` protects selected chat bodies; private channels restrict audience. | `tests/test_hub_sqlcipher_e2e.py`, `tests/test_at_rest.py`, `tests/test_at_rest_guard.py`, `tests/test_e2ee_channels_runtime.py`, `tests/test_private_channel_runtime.py` | Body/profile encryption and loopback stores remain opt-in. They do not protect hub RAM, compromised endpoints, or routing metadata. |
| Trust evidence | `synapse trust-graph` projects the durable event log into provenance-linked evidence edges. | `tests/test_cli_trust_graph.py`, `tests/test_trust_graph.py` | It does not rank agents, authorise execution, or implement the planned owner-annotation workflow. |

When that boundary is crossed, the proportionate controls are:

- **Connect authentication.** `synapse hub --token SECRET` requires a shared
  secret on the first message of each connection, compared in constant time. The
  hub refuses a non-loopback bind unless a token is configured, or unless the
  operator explicitly passes `--insecure-off-loopback` to accept an exposed
  unauthenticated hub. Prefer `--token-file PATH` or the `SYNAPSE_TOKEN`
  environment variable over `--token`, which is visible in the process list.
  Owner-file readers open every path component with `O_NOFOLLOW`; configure the
  real path rather than a symlinked ancestor directory.
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
  restricted to owner-only permissions. Webhook delivery resolves each
  target once and pins the connection to that validated address, so a DNS name
  cannot rebind to a local address between the check and the connect. It admits
  only globally routable destinations — rejecting loopback, private, link-local,
  carrier-grade NAT, multicast, reserved, and unspecified addresses, including
  IPv4-mapped IPv6 — applies the same policy to redirect targets, preserves the
  hostname for TLS, ignores environment proxies, and reads the discarded response
  under a fixed byte bound. Stored tasks, task
  history, artifacts, push configs, in-process replay history, and terminal-task
  retention are bounded. Treat any non-loopback A2A bind as an exposed HTTP
  service: use bearer auth, keep state files private, and do not claim external
  A2A conformance until interoperability and webhook validation have run.
- **Dashboard Host boundary.** The read-only `synapse dashboard` serves live JSON
  and audit feeds unauthenticated on loopback so the browser cockpit — which
  cannot attach an `Authorization` header on navigation — can load. That open read
  path is a DNS-rebinding target, so an always-on `Host`-header boundary runs
  before authentication on every read and write: a request is admitted only when
  its `Host` names the loopback authority, the bind host at the served port, or a
  host the operator explicitly approved with `--dashboard-allow-host`. A request
  whose `Host` is absent, malformed, or the attacker-chosen rebinding authority is
  refused with 403, so a page the operator visits cannot rebind its name to
  loopback and read coordination state cross-origin. A wildcard `0.0.0.0`/`::`
  bind names no fixed authority and already mandates a read token — which a
  rebinding page cannot present — so the boundary defers to that token there
  unless the operator lists exact hosts with `--dashboard-allow-host`. The
  boundary is transport hardening, not a substitute for a token: keep the bind on
  loopback and set a `--dashboard-token` before any deliberate exposure.

The core hub and its state stay on the operator's machine, but two boundaries are
worth stating plainly:

- **Model workers are a deliberate egress.** An on-channel model worker
  (`synapse worker`) sends recent channel context — and an `Authorization` bearer
  token — to the OpenAI-compatible endpoint the operator configures with
  `--base-url`. The hub is local-first, but a worker is an intentional bridge to
  whatever backend it is pointed at, so `--base-url` must be trusted. A rule-based
  worker (`--provider rule`) never leaves the machine.
- **Outbound MCP config is process-launch authority.** `synapse mcp-tools` and
  `synapse mcp-call` start the configured server before its MCP tool allowlist can
  apply. Their config therefore defaults to an owner-only, single-link file
  outside the active repository, with every path component opened by descriptor
  under `O_NOFOLLOW`; repository agents cannot plant or hardlink launch policy
  for a later operator command. Server commands are raw absolute paths without
  symlink components. Their validated bytes are copied into a sealed Linux
  `memfd` and that exact descriptor snapshot is launched, with an optional
  SHA-256 pin checked against the executing bytes. Configured working directories
  are required, outside-repository by default, and retained through exact
  descriptors; group/world-writable cwd paths are rejected, and low-level specs
  that omit cwd are bound to `/`. The proof covers
  the command bytes, not auxiliary files named in
  arguments. Shebang scripts are rejected as commands because their interpreter
  is opened outside the sealed snapshot; configure a native interpreter as the
  command and treat doctor's value-free warning on every argument as residual
  executable-chain risk. Child processes
  receive no inherited parent values: SDK baseline
  names are blanked unless approved, and only literal config values plus
  individually approved `inherit_env` names are populated. A positive finite
  per-operation timeout no greater than 3600 seconds is the server startup and
  discovery/call deadline; non-representable values fail parsing. On
  cancellation, the pinned SDK applies a separate audited two-second graceful
  exit window before force-terminating the process tree. The
  exact audited MCP SDK release, inherited-name list, and cleanup window are checked
  at runtime, so dependency drift fails closed. A separate
  owner-only trust bundle can require a domain-separated Ed25519 manifest
  signature that binds its algorithm and key ID; whitespace aliases and
  duplicate public-key identities are rejected. A present-but-unverified
  signature fails closed. The
  `--allow-repo-mcp-config` escape hatch is an explicit trust-boundary downgrade,
  not a safe default; doctor reports config and trust-bundle locality separately,
  and the escape hatch never relaxes owner-only file or cwd-mode checks. Startup
  and transport errors are reduced to stable, value-free CLI diagnostics instead
  of reflecting exception-group text. Configured server stderr remains attached
  to the operator's stderr and must be treated as trusted server output. These
  controls authenticate local launch policy; they do not sandbox a trusted MCP
  server after it starts.
- **Outbound MCP platform floor.** Descriptor-bound MCP launch currently requires
  Linux `memfd_create`, sealing, and procfs. It fails closed on macOS, Windows,
  or Linux environments without `/proc/self/fd`; no weaker pathname fallback is
  used.
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

The global agent names `SynapseHub`, `Synapse`, and `system` are reserved
case-insensitively for hub and protocol provenance. The hub refuses their
registration before authentication, trust-on-first-use pinning, takeover, or
ownership-lease state can be created. The reservation applies only to the exact
global names, so project-scoped identities such as `PROJECT/system` remain
valid. An existing client that uses a reserved global name must migrate to a
non-reserved name or a project-scoped identity. This naming boundary prevents a
client from impersonating protocol provenance; it does not replace connect
authentication, identity binding, ACL enforcement, or message authentication.

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

### Container image bind posture

The published container image starts the hub with `--host 0.0.0.0` because a
container port can only be published if the process binds a non-loopback
address inside the container's network namespace; an in-container `127.0.0.1`
bind would leave every `docker run -p` and compose publish dead. Binding all
interfaces inside the container does not by itself expose the hub — but
reachability has two distinct audiences, and the publish flags govern only one
of them. **Host ingress** is decided by the publish flags (`-p`, `ports:`): a
loopback-only publish keeps the hub unreachable from other machines.
**Container-network peers** are the other audience: any container attached to
the same Docker network reaches the hub on `hub:8876` directly, container port
to container port, regardless of what the host publishes. A loopback-only
publish therefore bounds host ingress and nothing else; the containers sharing
the hub's network must themselves be trusted, or the hub must require a token.

The bind is guarded, not trusted. On any non-loopback bind the hub runs the
exposure guard before opening sockets or durable stores: with no authenticator
configured it raises `InsecureBindError` and refuses to start, so a bare
`docker run -p 8876:8876` without a token stops at startup instead of serving
an open hub. A refused start terminates before the durable event store is
constructed, so it leaves no database file behind. The operator opt-out is
explicit — `--insecure-off-loopback` acknowledges the accepted risk on the
command line itself. The shipped `docker-compose.yml` publishes the port
loopback-only (`127.0.0.1:8876:8876`), attaches the hub to a dedicated compose
network that carries no other service, and passes that opt-out precisely
because both audiences are then bounded: the host-side publish keeps the hub
unreachable from other machines, and the single-service network keeps it
unreachable from other containers. Its comments direct operators to require a
token the moment either boundary widens — publishing beyond loopback, or
attaching any container they do not fully trust to the hub's network.

To expose a containerised hub beyond the host, provide a shared secret
(`--token`, delivered from a file or environment secret) and terminate TLS
(`--tls-certfile`/`--tls-keyfile`, or a wss:// proxy); `--paranoid` makes both
mandatory. This posture is an intentional, repeatedly reviewed design:
external security audits of 0.98.27, 0.99.2, 0.99.3, and 0.99.4 each raised
the in-container `0.0.0.0` default, and each closed with the same disposition —
the fail-closed startup refusal is the control, and a container image binding
loopback would only break port publishing without adding security.

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
  documented in `docs/a2a-deployment-threat-model.md`. The Origin/Host browser
  boundary is **opt-in** (`synapse a2a-serve --allow-origin`); opaque `null`
  origins are always rejected. Print the effective policy with
  `synapse doctor --a2a-policy` (and optional `--a2a-allow-origin` mirrors).
- Provider mutation claim hooks are **not** a full OS sandbox. Supported shell
  hooks require an exclusive whole-worktree claim instead of guessing paths from
  command text. Several hosts still fail open on crash/timeout, Codex documents
  incomplete `unified_exec` interception, and MCP/custom write paths may stay
  outside the matchers. See the provider × fail-closed
  matrix in [`docs/claim-guard-hooks.md`](docs/claim-guard-hooks.md). Commit-time
  `synapse git-claim-check --staged` remains the independent second gate.
  Semantic claims do not make one shared physical file safe for two owners:
  precise native edits accept a symbol claim only when its worktree/branch has
  no competing semantic owner, whole-file/patch tools require a file claim, and
  sibling-symbol concurrency belongs in isolated worktrees. The staged gate
  resolves `HEAD` versus the index and denies on ambiguous or unavailable
  semantic evidence; non-blocking auto-release retains an unproven symbol claim
  for manual release.
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
