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
  hub logs a warning when it is bound to a non-loopback host with no token. Prefer
  `--token-file PATH` or the `SYNAPSE_TOKEN` environment variable over `--token`,
  which is visible in the process list.
- **Bounded resources.** A `--max-clients` connection cap, a `--max-msg-kb` frame
  size cap, per-agent rate limiting, bounded chat history, a bounded progress
  ledger, and a bounded relay log keep one runaway agent or a flood from exhausting
  the single hub.
- **Lease and epoch guards.** Claims expire; each lease carries an epoch so a
  superseded agent cannot act on a dead claim; mutations support idempotency keys
  so a reconnect retry is applied once.
- **Advisory file scopes.** A claim's `paths` are opaque strings the hub compares
  only for glob overlap — it never reads, opens, or resolves them on the
  filesystem. A claim on `../../etc/passwd` coordinates nothing and touches nothing
  on disk, so scope strings are not a path-traversal surface.

## Out of scope / known limitations

- The connect token is a proportionate shared secret, **not** a cryptographic
  identity system: there is no key exchange, signatures, or per-message
  authentication. Do not expose the hub on an untrusted network and rely on the
  token alone.
- The bus does not sandbox the agents that connect to it. An agent is trusted to
  the extent the operator trusts the process it runs in. Never run untrusted agent
  code against a hub.
- The event log and SQLite database are stored in plaintext on the operator's own
  machine. Encryption at rest is out of scope for the local-first niche; it is a
  concern for a future managed multi-tenant hub, not the single-owner core.

## Licensing

SYNAPSE CHANNEL is AGPL-3.0-or-later with a commercial licence available; see
[`NOTICE.md`](NOTICE.md).
