# End-to-end encrypted channels

End-to-end encrypted channels now have an implemented runtime tranche for
selected chat payloads. `synapse send --encrypt-key-file` encrypts the message
body before it reaches the hub, `synapse listen --decrypt-key-file` decrypts it
locally for a recipient with the same key, and the hub routes ciphertext while
keeping routing metadata visible. This first tranche uses AES-256-GCM envelopes
with route-bound authenticated associated data. It does not manage key discovery,
does not rotate keys, does not remove old member access, and does not protect
compromised endpoints.

The broader encrypted-channel profile remains a design target for routing
selected payload classes through the hub while the hub cannot read plaintext.
The current hub still processes ordinary JSON metadata and should not be
described as hiding routing metadata beyond transport security and local file
permissions.

The goal is narrow: let trusted participants hide sensitive content from the
coordination hub while preserving enough metadata for routing, delivery,
retention, receipts, and audit. The design does not hide routing metadata and
does not protect compromised endpoints.

## Payload scope

The first encrypted profile should cover selected payloads, not every hub frame:

- **Direct messages** where sender and recipient already know the intended
  recipient set.
- **Private progress notes** attached to a task when the board should show that
  progress exists but not reveal the body to every participant.
- **Handoff checkpoints** that contain local commands, partial findings, or
  operator context only the next owner should read.
- **A2A artifacts** that the bridge stores or forwards for a selected task.

Claims, leases, task ids, timestamps, sender names, recipient names, delivery
status, retention counters, and release receipt references remain visible
metadata. In short, metadata remains visible. Encrypting every hub frame would
break routing and conflict detection; selected payload encryption keeps the hub
useful while hiding bodies.

## Envelope model

Encrypted payloads should remain ordinary hub messages with an encrypted body:

```json
{
  "kind": "chat",
  "from": "project/alice",
  "to": "project/bob",
  "task_id": "TASK-12",
  "encrypted": {
    "version": 1,
    "key_id": "project:main:2026-06",
    "recipients": ["project/bob"],
    "ciphertext": "base64...",
    "nonce": "base64...",
    "aad": "base64..."
  }
}
```

Authenticated associated data should bind visible routing fields, including
message kind, sender, recipient set, task id, event sequence when known, and
project/worktree identifiers. That prevents ciphertext replay under a different
visible envelope without requiring the hub to decrypt content.

## Key model

The first design should support both broad and narrow collaboration scopes:

- **Per-project keys** for a trusted local fleet that shares one project-level
  private channel.
- **Per-worktree keys** for one repository checkout or branch group.
- **Recipient set keys** for direct messages or handoffs where only named
  identities should decrypt the body.

Key discovery should be explicit. Capability cards or a future identity layer
can advertise public encryption keys, but the hub must treat those keys as data
until [signed capability cards](signed-capability-cards.md) or per-agent
identity exist. Operators need a manual trust-on-first-use path and a way to pin
expected key fingerprints.

## Membership operations

Encrypted channels need operational key management, not only cryptographic
primitives:

- **Key rotation** creates a new key id, publishes the recipient set, and stops
  encrypting new payloads to the old key.
- **Member removal** rotates future keys and records that old ciphertext may
  remain decryptable to the removed member if they retained old key material.
- **Device loss** revokes the lost device for future messages and rotates
  affected per-project keys, per-worktree keys, or recipient set keys.
- **Recovery phrase** or escrowed local backup lets an operator recover their
  own key material without giving the hub plaintext access.

Historical ciphertext should stay immutable. Re-encrypting history is a later
migration task and should not be required for the first feature.

## Hub behavior

The hub should route encrypted envelopes exactly like ordinary messages where
possible. It can enforce size limits, retention limits, recipient delivery,
lease rules, and release receipt references without reading plaintext. It can
also store ciphertext in the event log for durable replay.

The hub cannot index encrypted body text, summarize it, scan it for policy
signals, or include it in plain postmortems. Reports should show encrypted
payload metadata, key id, recipient set, and ciphertext presence without
pretending to understand the hidden content.

For shared board reports that should expose aggregate progress without raw
notes, the
[differential-privacy blackboard design](differential-privacy-blackboard.md)
defines a separate redaction, aggregation, and noise profile. That profile does
not encrypt payloads.

## Boundaries

This runtime tranche does not replace at-rest encryption; ciphertext can still
live in local SQLite files, relay logs, and backups. Put another way, the
feature does not replace at-rest encryption. It does not hide routing metadata.
It does not protect compromised endpoints, malicious recipients, terminal
scrollback, copied plaintext, or model-worker egress after a participant
decrypts content.

The local-first tradeoff is operational complexity: key discovery, key
rotation, member removal, device loss, and recovery phrase handling must be
implemented before encrypted channels become a default coordination path. Until
signed events, per-agent identity, and ACLs exist, encrypted channels remain
opt-in and explicit. The
[signed events and mTLS design](signed-events-mtls.md) can authenticate visible
envelopes and trusted peers later, but it does not encrypt payloads. For
advertisement tamper evidence, see the
[signed capability cards design](signed-capability-cards.md). For audience
scoping without body encryption, see the [private channels design](private-channels.md).
