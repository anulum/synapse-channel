# At-rest encryption

At-rest encryption optionally protects local Synapse storage when an operator's
disk, backup target, or support workflow requires a stronger confidentiality
boundary than file permissions alone.

The default product remains local-first and low-dependency. Encryption is an
opt-in profile that protects data when files are copied, backed up, or read
offline. It does not protect data while the hub is running, does not replace
filesystem permissions, and does not solve multi-tenant isolation.

## Implemented (foundation tranche)

The encryption primitive and key-file management are implemented in
:mod:`synapse_channel.core.at_rest`:

- `AtRestCipher` is an AES-256-GCM envelope over a 32-byte key, with a versioned
  header bound as additional authenticated data, fresh per-message nonces, and
  atomic `encrypt_file` / `decrypt_file` helpers. Keys come from a raw key file
  or from a passphrase via the memory-hard scrypt KDF.
- `synapse encrypt-key generate <path>` writes a fresh owner-only 32-byte key;
  `synapse encrypt-key check <path>` verifies a key file's ownership, mode, and
  length before an encrypted workflow trusts it.
- The AES-GCM primitive comes from the optional `cryptography` dependency
  (`pip install synapse-channel[encryption]`); the package still imports without
  it, and an encryption attempt without it raises a clear install hint.

Storage-surface wiring (encrypting the relay log, A2A state files, archive
reports, and cursor files with this primitive) and live SQLite event-store
encryption are **not yet implemented** and remain described below as the next
tranches. The SQLite event store is live-queried and needs SQLCipher-class
transparent encryption, so it stays separate from the whole-file surfaces.

## Storage scope

The first design must cover every local surface that can contain coordination
content:

- **SQLite event store** files created by `synapse hub --db`.
- **WAL and SHM sidecars** that SQLite writes next to the event store.
- **Relay logs** written by `--relay-log` or relay workflows.
- **A2A state files** created by `synapse a2a-serve --state-file`.
- **Cursor files** used by relay, ingest, or other replay consumers.
- **Archive reports** written by compaction or postmortem workflows.
- **Temporary files** used during atomic writes, bridge state updates, report
  generation, and backup staging.
- **Backups** produced by SQLite online backup, filesystem copy, or operator
  archive jobs.

Generated documentation, public examples, and ordinary source checkouts are out
of scope unless they embed private event-log material.

## Key model

The design should support three operator profiles:

- **Passphrase**: a human-supplied passphrase unlocks the local store. Key
  derivation must use a memory-hard KDF when a dependency is available, with
  parameters recorded in metadata.
- **Platform keyring**: desktop/server key storage delegates secret wrapping to
  the OS keyring or an operator-approved secret manager.
- **File key**: automation reads a key file protected by owner-only file
  permissions, suitable for systemd user services where interactive prompts are
  impossible.

Key storage must be explicit. Synapse should never silently write an encryption
key next to the encrypted database with broad permissions. A doctor check should
verify key-file ownership and mode before an encrypted hub starts.

## Rotation and metadata

Key rotation should create a new encrypted copy, verify it, then swap atomically:

1. Open the old store with the old key.
2. Create a new encrypted store with a new key id.
3. Copy all events and sidecar-relevant state through SQLite APIs or an audited
   export/import path.
4. Verify event counts, last sequence, checksums, and replay success.
5. Move the old store into an owner-only backup location until the operator
   confirms recovery.

Metadata should record encryption version, KDF parameters, key id, creation
time, rotation source, and checksum algorithm without storing the raw key.

## Backup recovery

Backup recovery must be boring and documented:

- A backup bundle includes encrypted store files, metadata, and recovery notes.
- The key material is backed up separately from the encrypted data.
- Restores verify file permissions before opening recovered files.
- Recovery replay checks the last event sequence and hub state reconstruction.
- Lost-key recovery is impossible by design unless the operator has an escrowed
  passphrase, key-file backup, or platform-keyring recovery path.

The command-line UX should state that lost-key recovery cannot decrypt the data.
It can help identify which key id is needed, but it must not imply a bypass.

## Local-first tradeoff

At-rest encryption adds operational weight: key prompts, service unlock order,
rotation procedures, backup discipline, and failure modes. The local-first
tradeoff is that single-owner default installs should stay simple, while
operators with stronger storage requirements can opt in and accept the extra
runbook.

The first implementation should prefer a small, auditable encryption boundary
over broad dependency sprawl. If strong authenticated encryption cannot be
provided without a mature dependency, the feature should remain design-only
rather than ship a custom cipher or home-grown key schedule.

## Boundaries

This design does not encrypt current event stores. It does not protect data
while the hub is running and has decrypted state in memory. It does not replace
filesystem permissions, process isolation, or host-disk encryption. It does not
solve multi-tenant isolation, per-agent secrecy, signed events, or network
transport security.

At-rest encryption should integrate with paranoid mode as one reported hook:
paranoid mode can require encrypted storage only after the encryption feature
exists, has migration tests, and has recovery documentation. It remains separate
from [end-to-end encrypted channels](end-to-end-encrypted-channels.md), which
hide selected payload bodies from the hub while preserving visible routing
metadata.
