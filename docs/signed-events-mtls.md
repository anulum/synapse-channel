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

Signed events and mutual TLS now have a first runtime enforcement layer for
selected coordination events and trusted multi-host deployments. The runtime is
still local-first and operator-managed: loopback binding, optional shared
tokens, file permissions, and operator trust remain the default posture, while
embedded hubs can opt into Ed25519 event signatures and mTLS peer trust bundles
for stricter deployments.

The goal is narrow: make selected durable events tamper-evident and let
operator-managed peers authenticate each other when a deployment intentionally
spans more than one host. This design does not encrypt payloads, does not
replace per-agent identity, and does not certify federation for untrusted
organisations.

## Runtime status

The implemented runtime covers these enforceable primitives:

- `EventSignatureKey` records an Ed25519 public verification key, allowed
  senders, allowed project namespaces, optional expiry, and revocation state.
- `EventSignatureTrustBundle` groups accepted event-signing keys with a bounded
  replay cache.
- `sign_event_frame(...)` attaches an Ed25519 `signature` envelope to a stable
  Synapse frame.
- `verify_event_signature(...)` verifies the signature, key id, sender binding,
  project scope, timestamp window, sequence shape, nonce replay, expiry, and
  revocation.
- `SynapseHub(..., require_per_message_auth=True,
  signed_event_trust_bundle=...)` accepts a valid signed event as an alternative
  to the existing HMAC `auth` envelope for selected mutating frames. HMAC
  remains supported and unchanged.
- `build_mutual_tls_server_ssl_context(...)` creates a native WSS server context
  that requires client certificates.
- `MTLSPeerTrustBundle` verifies trusted peer certificate pins, project scope,
  signing key scope, and peer revocation.

There is no CLI trust-bundle import command yet. Operators embedding the hub can
enforce these primitives today; packaged command-line workflow for trust-bundle
loading, rotation, import/export, and incident response remains future work.

## Event signature profile

The first signing profile should cover events where a forged or replayed record
would change coordination truth:

- Claim, release, renew, and checkpoint events.
- Task declarations, dependency edits, status changes, and evidence updates.
- Handoffs, release receipts, and owner approvals.
- Capability card update events and route-relevant capability evidence, once
  [signed capability cards](signed-capability-cards.md) provide their own card
  signing profile.

Every signed event carries an **event signature** envelope with these
fields:

```json
{
  "signature": {
    "version": 1,
    "key_id": "project:main:2026-06",
    "algorithm": "ed25519",
    "signed_at": 1782648000.0,
    "nonce": "base64-url-nonce",
    "sequence": 1,
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

Verification produces an explicit **verification result** for operators, policy
checks, and postmortems: `valid`, `missing_signature`, `expired`,
`unknown_key`, `revoked_key`, `bad_signature`, `sender_mismatch`,
`project_scope_mismatch`, `sequence_mismatch`, or `replayed`. A failed
verification result is surfaced on the hub `error` frame when signed-event
verification is used as the required mutating-frame authentication path.

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
file permissions and clear export/import commands before command-line
multi-host workflows ship. Embedded deployments can construct the runtime trust
bundle directly from their local configuration.

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

Reverse proxies change this profile unless they preserve the TLS socket. Direct
native WSS/mTLS and TCP/TLS passthrough keep the hub certificate as the pinned
object and let the hub inspect client certificates. A TLS-terminating proxy
instead presents the proxy certificate to the remote peer, and the hub sees only
the proxy connection. That path can still protect ordinary token-gated clients,
but it is not a hub mTLS path unless the operator deliberately pins the proxy
certificate and enforces client identity at the proxy as a separate policy.
`synapse doctor --federation-path PEER=direct-mtls|tls-passthrough|tailnet|tls-terminating-proxy`
reports this boundary explicitly for deployment checks.

The runtime peer verifier reports explicit failure modes: `valid`,
`missing_certificate`, `unknown_peer`, `revoked_peer`, `bad_certificate_pin`,
`project_scope_mismatch`, and `unknown_signing_key`.

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
- [Per-agent identity and ACLs](identity-and-acl.md) remain a separate design.
  Signatures can carry an asserted key id, but policy still needs
  identity-bound permissions before the hub can enforce who may perform each
  action.
- [Signed capability cards](signed-capability-cards.md) remain a separate
  profile for discovery advertisements, manifest digests, expiry, and capability
  downgrade diagnostics. Signed events verify durable records around those
  advertisements after admission.

## Boundaries

Signed events do not encrypt payloads, do not replace per-agent identity, do not
replace ACL enforcement, do not sandbox connected agents, do not make
shared-token mode safe on untrusted networks, and do not certify federation with
arbitrary external systems.

Mutual TLS authenticates configured peers only when the operator manages the
trust bundle, certificate pinning, key rotation, revocation, and deployment
procedures. Until command-line trust-bundle import/export, rotation, and
incident-response workflows exist, the supported default security posture
remains the trusted local hub with explicit warnings for exposed deployments.
