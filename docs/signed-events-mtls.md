<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — signed events and mTLS design
-->

# Signed events and mTLS design

Signed events and mutual TLS are a design target for selected coordination
events and trusted multi-host deployments. They are not implemented yet. The
current local-first hub still relies on loopback binding, optional shared
tokens, file permissions, and operator trust rather than cryptographic event
authenticity.

The goal is narrow: make selected durable events tamper-evident and let
operator-managed peers authenticate each other when a deployment intentionally
spans more than one host. This design does not encrypt payloads, does not
replace per-agent identity, and does not certify federation for untrusted
organisations.

## Event signature profile

The first signing profile should cover events where a forged or replayed record
would change coordination truth:

- Claim, release, renew, and checkpoint events.
- Task declarations, dependency edits, status changes, and evidence updates.
- Handoffs, release receipts, and owner approvals.
- Capability card updates and route-relevant capability evidence, once signed
  capability cards have their own profile.

Every signed event should carry an **event signature** envelope with these
fields:

```json
{
  "signature": {
    "version": 1,
    "key_id": "project:main:2026-06",
    "algorithm": "ed25519",
    "signed_at": "2026-06-28T12:00:00Z",
    "value": "base64..."
  }
}
```

The signed bytes should be a **canonical payload** derived from the stable event
fields, not from arbitrary JSON formatting. Canonicalisation should sort object
keys, preserve integer and string values exactly, reject duplicate keys before
signing, and exclude the signature value itself.

## Replay protection

Replay protection needs more than a detached signature. The canonical payload
should bind:

- Event kind, sender, target, project, task id, claim id, and channel id when
  present.
- Durable event sequence after the hub assigns it, or a pre-sequence nonce when
  the sender signs before hub admission.
- Prior sequence or prior event digest for **sequence binding** in durable logs.
- Timestamp window for admission, with a small operator-tunable skew allowance.
- Idempotency key where the event is a mutating retry.

Verification should produce an explicit **verification result** for operators,
policy checks, and postmortems: `valid`, `missing`, `expired`, `unknown_key`,
`revoked_key`, `bad_signature`, `sequence_mismatch`, or `replayed`. A failed
verification result should never be hidden behind a normal release receipt.

## Key lifecycle

Keys must be ordinary operator-managed trust data:

- A **key id** identifies one signing key and its project, worktree, or peer
  scope.
- A trust bundle lists accepted public keys, certificate pins, peer names,
  expiry dates, and revocation entries.
- **Key rotation** creates a new key id and marks the old key as verify-only
  until its replay window and retention window have passed.
- **Revocation** blocks new events immediately and makes older events report a
  `revoked_key` verification result while preserving the audit trail.
- Lost-key recovery is an operator procedure, not a hub guess. The hub can
  report missing trust material but should not mint replacement identity.

Trust bundles should live beside existing local configuration, with owner-only
file permissions and clear export/import commands before any multi-host feature
ships.

## Mutual TLS for trusted peers

Mutual TLS is the transport profile for trusted peer connections between hubs,
bridges, or future relays. It should be opt-in and explicit:

- A **trusted peer** has a stable peer id, endpoint, certificate pin, accepted
  signing key ids, and allowed project or channel scope.
- **Certificate pinning** binds the peer to an expected certificate or public
  key fingerprint, avoiding blind trust in arbitrary local certificate stores.
- A trust bundle records the peer certificate pins and signing keys together so
  the operator can review one object before enabling a peer.
- Multi-host routing should refuse unknown peers by default and log the
  verification result for every accepted connection.

mTLS authenticates the transport peer. Event signatures authenticate selected
events across storage, relay logs, postmortems, and policy checks. They are
related, but neither replaces the other.

## Cross-project and multi-host boundaries

The first federation design should be small enough to audit:

- A peer may be allowed for one project, one worktree group, one channel id, or
  one A2A bridge.
- Cross-project trust should require an explicit mapping from local project
  names to remote project names.
- The hub should record which peer admitted or forwarded an event, including
  the peer id, certificate pin, signing key id, event sequence, and verification
  result.
- Receipts should name signed evidence by sequence and key id, not by copying
  private payload bodies.

This is a local-first tradeoff. Operator-managed trust keeps the simple
single-machine workflow intact, but multi-host deployments need more procedure:
trust bundle review, key rotation, revocation, clock handling, peer inventory,
and incident response for compromised peers.

## Relationship to other hardening designs

Signed events and mTLS sit beside the other security designs:

- [Paranoid mode](paranoid-mode.md) can report missing signature, trust bundle,
  and mTLS hooks before exposing a service.
- [At-rest encryption](at-rest-encryption.md) protects local files; signed
  events make selected records tamper-evident.
- [End-to-end encrypted channels](end-to-end-encrypted-channels.md) hide
  selected payload bodies; signed events can authenticate their visible
  envelopes without decrypting them.
- [Private channels](private-channels.md) scope the intended audience; signed
  events can bind channel ids and membership changes into the event signature.
- [Per-message authentication](per-message-authentication.md) can reject bad
  frames before hub admission; signed events verify selected records after
  admission, storage, relay export, and postmortem reconstruction.
- Per-agent identity and ACLs remain separate designs. Signatures can carry an
  asserted key id, but policy still needs identity-bound permissions before the
  hub can enforce who may perform each action.

## Boundaries

This is a design target, not implemented yet. Signed events do not encrypt
payloads, do not replace per-agent identity, do not replace ACL enforcement, do
not sandbox connected agents, do not make shared-token mode safe on untrusted
networks, and do not certify federation with arbitrary external systems.

Mutual TLS authenticates configured peers only when the operator manages the
trust bundle, certificate pinning, key rotation, revocation, and deployment
procedures. Until those hooks exist, the supported security posture remains the
current trusted local hub with explicit warnings for exposed deployments.
