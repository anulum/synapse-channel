<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — signed capability cards
-->

# Signed capability cards

SYNAPSE can attach a domain-separated Ed25519 signature to an agent capability
advertisement and verify it before projecting the card into manifests,
directories, dashboards, MCP resources, or A2A Agent Cards. Verification makes
discovery metadata tamper-evident. It is deliberately **advisory**: a valid card
does not authorize a tool, grant an ACL permission, execute code, prove that an
advertised capability works, or replace signed messages and connection identity.

Unsigned cards keep working. Every projected card carries an explicit
`verification.result`; an unsigned card is `missing_signature`, while a signed
card with a failure remains visible with the exact failure instead of being
hidden or presented as verified.

## Runtime status

The first complete advisory runtime ships:

- `synapse capability-card keygen` creates a profile-separated owner-only
  Ed25519 private key and can enrol its public key into a separate trust bundle;
- `synapse capability-card sign` signs strict canonical card JSON and refuses
  duplicate JSON keys or output-file replacement;
- `synapse capability-card verify` performs one-shot cryptographic and binding
  verification without changing live replay history;
- `synapse worker --capability-card-key ...` signs each normalized live
  advertisement with an increasing in-process sequence;
- `synapse hub --capability-card-trust ...` verifies cards against explicit
  agent and project bindings, a validity window, key expiry/revocation, and a
  bounded in-memory sequence/downgrade history;
- manifest, directory, fallback dashboard, and A2A projections show the
  advisory result.

The feature adds no required cloud service and no required dependency to the
core-only path. Ed25519 operations use the existing optional security dependency.
With no card trust bundle and no signing key, legacy unsigned behaviour remains
byte-compatible on the client wire.

## Operator walkthrough

Create a separate card-signing key and trust file:

```bash
synapse capability-card keygen \
  --key-id SYNAPSE-CHANNEL:worker:2026-07 \
  --private-out ./worker-card.pem \
  --agent SYNAPSE-CHANNEL/worker \
  --project SYNAPSE-CHANNEL \
  --trust ./capability-card-trust.json
```

Start an advisory-verifying hub. The trust file does not enable an enforcement
gate; it enables truthful diagnostics:

```bash
synapse hub \
  --capability-card-trust ./capability-card-trust.json \
  --capability-card-clock-skew-seconds 30 \
  --capability-card-history-capacity 4096 \
  --capability-card-history-retention-seconds 3600
```

Start a namespaced worker with the corresponding private key:

```bash
synapse worker \
  --prefix SYNAPSE-CHANNEL/ \
  --name worker \
  --capability-card-key ./worker-card.pem \
  --capability-card-key-id SYNAPSE-CHANNEL:worker:2026-07 \
  --capability-card-project SYNAPSE-CHANNEL
```

`synapse manifest` then renders `verify=valid`. A missing, unknown, revoked,
expired, replayed, downgraded, or tampered card stays listed with its own result.

For an offline card, put the stable advertisement fields in `card.json`, then:

```bash
synapse capability-card sign card.json \
  --key ./worker-card.pem \
  --key-id SYNAPSE-CHANNEL:worker:2026-07 \
  --sequence 1 \
  --out signed-card.json

synapse capability-card verify signed-card.json \
  --trust ./capability-card-trust.json \
  --json
```

The one-shot verifier checks the signature and bindings but intentionally does
not consume sequence state. Replay and downgrade checks happen on the live hub,
where consecutive advertisements share one bounded history.

## Trust-bundle profile

Card keys are separate from connection-identity, event-signing, federation, and
receipt-signing material. The public JSON shape is:

```json
{
  "keys": [
    {
      "key_id": "SYNAPSE-CHANNEL:worker:2026-07",
      "public_key": "base64-raw-ed25519-public-key",
      "agents": ["SYNAPSE-CHANNEL/worker"],
      "projects": ["SYNAPSE-CHANNEL"],
      "expires_at": 1785600000.0,
      "revoked": false
    }
  ]
}
```

Agent and project arrays are mandatory and non-empty. There is no wildcard and
no trust-on-first-use. Duplicate key ids, malformed base64, non-Ed25519 keys,
non-finite expiry, and malformed revocation state fail hub startup.

## Signing profile

The signer normalizes the stable card fields and attaches this envelope:

```json
{
  "version": 1,
  "key_id": "SYNAPSE-CHANNEL:worker:2026-07",
  "algorithm": "ed25519",
  "signed_at": 1783879200.0,
  "expires_at": 1783879500.0,
  "sequence": 42,
  "card_digest": "sha256-hex",
  "value": "base64-signature"
}
```

Canonical JSON sorts object keys, preserves JSON strings and integers, uses
compact UTF-8 encoding, rejects duplicate input keys and non-finite values, and
removes only `signature.value` from the signed bytes. The bytes and card digest
have distinct `SYNAPSE-CAPABILITY-CARD-...-V1` domain prefixes, so a card
signature cannot verify as a signed event, receipt, or connection proof.

`advertised_at` and `verification` are hub projection fields and are excluded;
the advertiser cannot know them before admission. The stable digest excludes the
whole signature envelope, preventing recursive content. The signature still
covers its own key id, sequence, validity window, and claimed digest.

## Verification results

| Result | Meaning |
| --- | --- |
| `valid` | Signature, digest, agent/project binding, expiry, and lifecycle checks passed. |
| `missing_signature` | Card is unsigned and remains advisory discovery. |
| `unknown_key` | Key id is absent from the card trust bundle. |
| `revoked_key` | Operator marked the key revoked. |
| `bad_signature` | Envelope, digest, canonical JSON, or Ed25519 signature is invalid. |
| `expired` | Key or card is outside its accepted validity window. |
| `sequence_mismatch` | Sequence did not increase for the same agent/key binding. |
| `capability_downgrade` | A newer valid card removed a task class, skill, or contract. |
| `agent_mismatch` | Card, socket sender, and allowed agent binding disagree. |
| `project_scope_mismatch` | Card, sender namespace, and allowed project binding disagree. |
| `manifest_mismatch` | A caller-required manifest digest differs from the signed card. |
| `history_full` | Bounded lifecycle state could not admit a new binding without evicting a live replay guard. |

A capability downgrade is recorded and surfaced for review; it is not silently
upgraded to `valid`. It still does not create an execution denial because card
verification is advisory in this tranche.

## Lifecycle and honest limits

- Card replay/downgrade history is bounded by binding count and time.
- History is currently **in memory**. A hub restart clears it; signed timestamps
  and expiry still reject stale cards, but durable cross-restart sequence
  protection is not claimed.
- A worker sequence is in-process. Operators that publish offline cards must
  persist and increase their own sequence.
- Credential rotation uses a new key id. Revocation blocks new verification
  immediately while old projected evidence retains its recorded result.
- Capability cards themselves remain ephemeral and are forgotten when their
  live agent disconnects or their ordinary card TTL expires.
- No enforcement flag exists yet. Enforcement must wait for recovery-key,
  rotation, durable replay, rollback, and operator playbooks.

## Relationship to other controls

- [Identity and ACL](identity-and-acl.md) decides who may advertise or execute;
  card signing only authenticates the advertisement.
- [Per-message authentication](per-message-authentication.md) authenticates
  selected frames and has its own key/profile/replay state.
- [Signed events and mTLS](signed-events-mtls.md) protect durable events and
  configured peers; their signatures cannot substitute for card signatures.
- [Sandboxed tools and marketplace](sandboxed-tools-and-marketplace.md) uses a
  signed card as provenance, a permission manifest as requested authority, the
  WASM runtime as enforcement, and a run receipt as evidence.

## Boundaries

Signed capability cards are implemented as advisory tamper evidence. They do
not authorize tools, replace message authentication, replace signed events,
sandbox agents, validate external A2A conformance, or certify that advertised
capabilities work. Runtime marketplace distribution and enforced signed-card
admission remain separate, unshipped layers.
