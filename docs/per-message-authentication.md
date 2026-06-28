<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — per-message authentication design
-->

# Per-message authentication design

Per-message authentication is a design target for exposed deployments where a
WebSocket connection token is not enough assurance for every later frame. It is
not implemented yet. Today, the hub can require WebSocket connect
authentication with a shared token, but subsequent frames are trusted once the
connection is admitted.

The goal is to authenticate selected hub frames after admission while preserving
the simple local workflow. The first design should support either a keyed
message authentication code or a signature over each authenticated frame. It
does not encrypt payloads, does not replace TLS, does not replace signed events,
and does not replace the
[per-agent identity and ACL design](identity-and-acl.md).

## Frame authentication profile

An authenticated frame should remain an ordinary protocol envelope with one
additional authentication object:

```json
{
  "sender": "project/agent",
  "target": "all",
  "type": "claim",
  "payload": {"task_id": "TASK-1", "paths": ["src/auth.py"]},
  "timestamp": 1782648000.0,
  "auth": {
    "version": 1,
    "key_id": "project:main:2026-06",
    "algorithm": "hmac-sha256",
    "nonce": "base64...",
    "value": "base64..."
  }
}
```

The **authenticated frame** bytes should come from a **canonical frame**:
stable envelope fields, sorted object keys, exact strings and integers, no
duplicate JSON keys, and no authentication value inside the signed bytes. The
canonical frame should bind the frame type, sender, target, timestamp, payload,
and key id.

Two authentication modes should share the same envelope:

- A keyed **message authentication code** for trusted peers that share a secret.
- A public-key **signature** for deployments that need asymmetric verification.

The hub should reject an authenticated frame whose sender does not match the
key scope. This **sender binding** prevents one admitted participant from
reusing another participant's key id.

## Replay controls

Per-message authentication must include replay protection:

- A **nonce** makes each authenticated frame unique within a key id.
- **Sequence binding** can bind a frame to the previous accepted frame sequence
  for the same sender and key id.
- A **timestamp window** bounds old frames and limits clock-skew tolerance.
- A bounded **replay cache** records recent nonces and accepted sequences per
  key id.
- The existing idempotency key remains part of mutating retry semantics; it is
  not a replay-protection system by itself.

Verification should produce a visible **verification result** such as `valid`,
`missing`, `expired`, `unknown_key`, `revoked_key`, `bad_authentication`,
`sender_mismatch`, `sequence_mismatch`, or `replayed`. Release receipts and
postmortems should be able to cite that result without exposing secrets.

## Key lifecycle

Operators need a concrete key lifecycle before this feature ships:

- A **key id** names one shared secret or public key and its allowed sender,
  project, worktree, or peer scope.
- **Key rotation** introduces a new key id for new frames while keeping the old
  key verify-only for its bounded replay and retention windows.
- **Revocation** blocks new frames immediately and reports `revoked_key` for
  older evidence that used the revoked key.
- Key export and import should preserve owner-only file permissions and should
  never print secrets in logs, receipts, or diagnostics.
- Lost-key recovery is an operator action, not a hub action.

The first implementation should keep keys local and explicit. Managed or
multi-tenant key services belong outside the local core until their trust model
exists.

## Relationship to signed events

Per-message authentication and signed events solve different problems:

- Per-message authentication protects frames in transit after WebSocket connect
  authentication.
- Signed events make selected durable event-log records tamper-evident after
  admission, storage, replay, relay export, or postmortem reconstruction.

An exposed deployment may need both. Per-message authentication can reject a
bad incoming frame before it mutates hub state. Signed events can later verify
that accepted durable evidence still matches the signer and sequence recorded
at admission.

## Boundaries

This is a design target, not implemented yet. Per-message authentication does
not encrypt payloads, does not replace TLS, does not replace signed events,
does not replace per-agent identity, does not replace ACL enforcement, does not
sandbox connected agents, and does not make a shared-token hub safe on an
untrusted network by itself.

Per-message authentication can prove that a selected frame came from a key
holder. The [identity and ACL design](identity-and-acl.md) remains the future
layer that maps that key holder to an audit subject and decides whether the
requested verb and target are allowed.

The local-first tradeoff is key-management complexity. Loopback-only single
operator use should remain simple. Exposed deployments need explicit keys,
sender binding, replay cache bounds, key rotation, revocation, diagnostics, and
operator procedures before per-message authentication can become an enforced
runtime mode.
