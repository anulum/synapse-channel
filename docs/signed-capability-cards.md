<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — signed capability cards design
-->

# Signed capability cards design

Signed capability cards are a design target for making agent advertisements
tamper-evident before a router, directory, dashboard, or bridge projects them
into work recommendations. They are not implemented yet. Today, capability
cards remain advisory discovery metadata published by connected agents and
trusted according to the operator's local process trust.

The goal is narrow: sign the capability advertisement itself so downstream
surfaces can report whether the card still matches the agent, project,
manifest, and key material that claimed to publish it. A signed capability card
does not authorize tools, does not replace per-message authentication, does not
replace signed events, and does not sandbox agents.

## Card signing profile

The signed bytes should come from a **canonical card** derived from stable
capability-card fields:

- Agent name and future agent id.
- Project namespace and optional worktree or seat scope.
- Task classes, skills, model label, description, and capability contracts.
- Resource-offer references when a directory entry joins live resources with a
  card.
- Manifest digest and card digest for the hub snapshot that contained the
  advertisement.

The **card signature** should be a small envelope attached to the card without
changing the discovery shape:

```json
{
  "agent": "SYNAPSE-CHANNEL/coder",
  "task_classes": ["code-review", "docs"],
  "contracts": [],
  "signature": {
    "version": 1,
    "key_id": "SYNAPSE-CHANNEL:agent:2026-06",
    "algorithm": "ed25519",
    "signed_at": "2026-06-28T12:00:00Z",
    "expires_at": "2026-06-28T13:00:00Z",
    "sequence": 42,
    "value": "base64..."
  }
}
```

Canonicalisation must sort object keys, preserve strings and integers exactly,
reject duplicate JSON keys before signing, and exclude the signature value from
the signed bytes. The key id identifies the verification key or trust-bundle
entry; it is never a secret.

## Binding requirements

A card signature is useful only when it binds the advertisement to the context
that made it meaningful:

- **Agent binding:** the signed card must name the agent id or advertised sender
  that is allowed to publish it.
- **Project namespace binding:** a card signed for one project must not verify
  inside another project namespace.
- **Manifest digest:** the signature should bind the card digest and the
  manifest digest when the card is projected into `synapse manifest`,
  `synapse directory`, dashboard snapshots, MCP resources, or A2A Agent Cards.
- **Sequence binding:** each signed update should carry the previous accepted
  sequence or an increasing sequence number for the same agent and key id.
- **Timestamp window:** admission should reject cards outside an
  operator-configured clock-skew window.

These bindings provide tamper evidence for route-relevant metadata. They do not
prove that the advertised tool exists, that the agent will execute a task, or
that a caller may invoke any advertised capability.

## Verification results

Verification should produce explicit results for humans, policy checks, and
postmortems:

| Result | Meaning |
| --- | --- |
| `valid` | The signature, binding, expiry, and trust-bundle checks passed. |
| `missing_signature` | The card is unsigned and should remain advisory discovery. |
| `unknown_key` | The key id is absent from the current trust bundle. |
| `revoked_key` | The trust bundle marks the key as revoked. |
| `bad_signature` | The card signature does not verify over the canonical card. |
| `expired` | The card is outside its expiry or timestamp window. |
| `sequence_mismatch` | Sequence binding or replay protection failed. |
| `capability_downgrade` | A newer signed card removed a capability in a way policy flagged for review. |

Unsigned cards should still work in local shared-token mode. Surfaces that show
unsigned or failed cards should label the verification result rather than hiding
the card or presenting it as verified.

## Lifecycle controls

Signed cards need a small lifecycle before enforcement can be safe:

- **Replay protection:** remember recent card digests, sequences, and key ids
  for each agent binding, bounded by retention and expiry.
- **Expiry:** keep card lifetimes short enough that stale routing claims age out
  without manual cleanup.
- **Credential rotation:** publish a new key id and allow the old key to verify
  retained cards until its replay and expiry windows close.
- **Revocation:** block new cards from a revoked key immediately while preserving
  old evidence with a `revoked_key` verification result.
- **Trust bundle:** store accepted public keys, key ids, project namespaces,
  expiry dates, and revocation entries in operator-managed local configuration.
- **Capability downgrade review:** flag route-relevant removals, such as a card
  dropping a contract or task class that release receipts still reference.

The local-first tradeoff is operational complexity. A single-owner loopback hub
can keep unsigned advisory discovery. Multi-operator or exposed deployments
need trust-bundle review, credential rotation, revocation, replay windows, and
clear diagnostics before signed cards become an enforced runtime requirement.

## Relationship to other designs

- [Identity and ACL](identity-and-acl.md) decides who may advertise, update, or
  project a capability card. Signed capability cards make the advertisement
  tamper-evident; they do not authorize the action.
- [Per-message authentication](per-message-authentication.md) authenticates
  selected frames after connect authentication. Signed capability cards verify
  the card content that those frames may carry.
- [Signed events and mTLS](signed-events-mtls.md) verify selected durable
  coordination records and trusted peers. Signed capability cards have a
  separate profile because advertisements have expiry, downgrade, and manifest
  projection concerns. Card-update events can be carried through the signed
  event runtime, but card-content signing and downgrade policy remain this
  separate profile.
- [End-to-end encrypted channels](end-to-end-encrypted-channels.md) may later
  use signed capability cards for public encryption-key discovery. Cards do not
  encrypt payloads or protect decrypted plaintext.
- [Policy engine](policy-engine.md) can consume verification results as
  advisory evidence before any local hook enforces them.

## Migration path

Migration should preserve current local ergonomics:

1. Keep unsigned cards accepted and marked as `missing_signature`.
2. Add signed-card diagnostics to manifests, directories, dashboards, and A2A
   Agent Card projections.
3. Let project namespaces opt into warnings for expired cards, unknown keys,
   replay failures, and capability downgrade events.
4. Enable enforcement only after operators have recovery keys, trust-bundle
   export/import, credential rotation, revocation, and rollback instructions.

## Boundaries

This is a design target, not implemented yet. Signed capability cards do not
authorize tools, do not replace per-message authentication, do not replace
signed events, do not replace identity and ACL enforcement, do not sandbox
agents, and do not certify external interoperability.

Signed cards provide advisory discovery with tamper evidence. Runtime tool
execution, process isolation, route selection, external A2A conformance, and
operator approval remain separate concerns.
