# At-rest encryption

At-rest encryption optionally protects local Synapse storage when an operator's
disk, backup target, or support workflow requires a stronger confidentiality
boundary than file permissions alone.

The default product remains local-first and low-dependency. Encryption is an
opt-in profile that protects data when files are copied, backed up, or read
offline. It does not protect data while the hub is running, does not replace
filesystem permissions, and does not solve multi-tenant isolation.

## Implemented runtime profile

The encryption primitive, key-file management, and operator runtime profile are implemented in
:mod:`synapse_channel.core.at_rest`:

- `AtRestCipher` is an AES-256-GCM envelope over a 32-byte key, with a versioned
  header bound as additional authenticated data, fresh per-message nonces, and
  atomic `encrypt_file` / `decrypt_file` helpers. Keys come from a raw key file
  or from a passphrase via the memory-hard scrypt KDF.
- `synapse encrypt-key generate <path>` writes a fresh owner-only 32-byte key;
  `synapse encrypt-key check <path>` verifies a key file's ownership, mode, and
  length before an encrypted workflow trusts it.
- `synapse encrypt-key profile --key <path> ... --require-encrypted` inspects
  the selected runtime surfaces and fails closed if any existing file is
  plaintext, unreadable, or cannot be authenticated with the key.
- `synapse encrypt-key migrate --key <path> ...` encrypts existing plaintext
  profile files in place. It covers SQLite event-store files plus `-wal` and
  `-shm` sidecars, relay logs, A2A state files, cursor files, and archive
  outputs. Optional `--backup-dir` copies owner-only plaintext originals before
  replacement.
- `synapse encrypt-key rekey --old-key <path> --new-key <path> ...` rotates
  already encrypted profile files and refuses missed plaintext files so rotation
  cannot hide an incomplete migration.
- `synapse encrypt-key backup --key <path> --backup-dir <dir> ...` writes an
  owner-only recovery bundle manifest that copies encrypted bytes exactly as
  stored. Key material is deliberately excluded.
- `synapse encrypt-key restore --key <path> --manifest <manifest.json>` restores
  a bundle after authenticating every encrypted file with the supplied key.
- The AES-GCM primitive comes from the optional `cryptography` dependency
  (`pip install synapse-channel[encryption]`); the package still imports without
  it, and an encryption attempt without it raises a clear install hint.

The implemented profile is whole-file operator encryption. It is intended for
cold migration, rekey, backup, restore, and fail-safe startup checks around the
real files Synapse already writes. Transparent live database opening is not
implemented for this whole-file profile because SQLite must keep a database file
open while it writes pages and WAL frames.

### Live hub event store (SQLCipher)

The live `synapse hub --db` store can use **SQLCipher page encryption** so the
main database, WAL, and indexes stay ciphertext on disk while the hub holds the
file open:

```bash
pip install 'synapse-channel[sqlcipher]'   # or sqlcipher3-binary==0.6.0
synapse encrypt-key generate ~/synapse/hub.key
# new store:
synapse hub --db ~/synapse/hub.db --db-key-file ~/synapse/hub.key
# migrate an existing plaintext store (hub stopped; destination must not exist):
synapse encrypt-key migrate-sqlcipher \
  --key ~/synapse/hub.key \
  --source ~/synapse/hub-plain.db \
  --destination ~/synapse/hub.db
```

Rotate an existing encrypted store (hub stopped):

```bash
synapse encrypt-key generate ~/synapse/hub.key.new
synapse encrypt-key rekey-sqlcipher \
  --db ~/synapse/hub.db \
  --old-key ~/synapse/hub.key \
  --new-key ~/synapse/hub.key.new
mv ~/synapse/hub.key.new ~/synapse/hub.key
synapse hub --db ~/synapse/hub.db --db-key-file ~/synapse/hub.key
```

Read the same store without decrypting offline copies:

```bash
synapse ingest ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse event-query ~/synapse/hub.db --db-key-file ~/synapse/hub.key 'task T timeline'
synapse postmortem ~/synapse/hub.db --db-key-file ~/synapse/hub.key T
synapse merkle root ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse causality causes ~/synapse/hub.db --db-key-file ~/synapse/hub.key 1
synapse accounting report ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse memory-recall ~/synapse/hub.db --db-key-file ~/synapse/hub.key 'probe'
synapse debug ~/synapse/hub.db --db-key-file ~/synapse/hub.key --fork-at 1
synapse reproduce ~/synapse/hub.db --db-key-file ~/synapse/hub.key T
synapse approval status ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse trust-graph ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse ttl-advice ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse cross-repo ~/repos --db ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse participant costs ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse route-task T --event-store ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse sandbox run tool.wasm --manifest m.json --approve \
  --attest ~/synapse/hub.db --db-key-file ~/synapse/hub.key
synapse dashboard --feeds-db ~/synapse/hub.db --feeds-db-key-file ~/synapse/hub.key
```

Doctor can verify the key opens the store:

```bash
synapse doctor --db-path ~/synapse/hub.db --db-key-file ~/synapse/hub.key
```

Without `[sqlcipher]` the stock install stays dependency-free and `--db-key-file`
fails closed with an install hint. Whole-file AES-GCM envelopes still apply to
relay logs, A2A state, cursors, and archives — they are complementary, not a
substitute for page encryption of a live open database. SQLCipher protects
offline copies of the event store; it does not protect a running hub's RAM and
does not replace filesystem permissions.

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
- **Wrapped key (envelope encryption)**: a random data key does the bulk AES-GCM,
  and a key-encryption key (KEK) wraps it with RFC 3394 AES-KW. The wrapped data key
  is stored on disk and unwrapped at startup. Because only the KEK changes when it
  rotates (or moves into hardware), **no encrypted data is re-written** — the data key
  underneath is unchanged. The wrapped-key file records which `backend` produced it,
  and backends are pluggable:
  - `synapse encrypt-key generate-wrapped` derives the KEK from a passphrase in
    software; `synapse encrypt-key rewrap` rotates that passphrase.
  - `synapse encrypt-key generate-wrapped-pkcs11` holds the KEK on a **PKCS#11 token**
    (YubiKey PIV, cloud/network HSM, or SoftHSM), wrapping and unwrapping on the device
    so the key never leaves it — the optional `python-pkcs11` dependency
    (`synapse-channel[pkcs11]`) and a module path via `--pkcs11-module` / `PKCS11_MODULE`.
  - `synapse encrypt-key generate-wrapped-tpm2` roots the KEK in a **TPM 2.0** device: a
    decrypt-only RSA-2048 key is derived from the TPM storage seed and wraps the data key
    with RSA-OAEP, so the RSA private key never leaves the chip. The optional `tpm2-pytss`
    dependency (`synapse-channel[tpm2]`) and a TPM interface via `--tcti` / `TPM2_TCTI`
    (default the in-kernel resource manager `device:/dev/tpmrm0`).

Key storage must be explicit. Synapse should never silently write an encryption
key next to the encrypted database with broad permissions. A doctor check should
verify key-file ownership and mode before an encrypted hub starts.

## Rotation and metadata

Key rotation should create a new encrypted copy, verify it, then swap atomically:

1. Verify the old key file with `synapse encrypt-key check`.
2. Generate and verify the new key with `synapse encrypt-key generate` and
   `synapse encrypt-key check`.
3. Stop writers for whole-file surfaces before migration or rotation. For
   SQLite stores, include the main database plus any `-wal` and `-shm` sidecars.
4. Run `synapse encrypt-key profile --key OLD --require-encrypted ...` to prove
   the selected existing files are encrypted before rotation.
5. Run `synapse encrypt-key rekey --old-key OLD --new-key NEW --backup-dir
   ./rekey-backup ...`. The command authenticates old envelopes, writes new
   envelopes atomically, and keeps owner-only old-envelope copies when
   `--backup-dir` is provided.
6. Run `synapse encrypt-key profile --key NEW --require-encrypted ...` before
   restarting the runtime.

The current backup manifest records the manifest schema, storage role, original
source path, and copied encrypted file path without storing raw key material.
Future platform-keyring profiles can add key ids and KDF metadata without
changing the envelope bytes.

### AES-GCM message limit

AES-GCM with random 96-bit nonces carries a per-key safety bound: after roughly
`2**32` messages the probability of a nonce repeating climbs past the `2**-32`
threshold, which would weaken confidentiality. `AtRestCipher` counts the messages
it seals (exposed as `encrypted_count`), logs a one-time warning once it passes
fifteen-sixteenths of that limit, and raises `AtRestKeyExhausted` rather than
encrypt beyond it. The correct response is a key rotation (rekey, above).

By default the count lives in memory (`InMemoryMessageCounter`) and resets when
the process restarts or the key is reloaded, so it guards a single long-running
hub rather than a key's cumulative lifetime. For a key whose count must survive
restarts, pass a `PersistentMessageCounter`: it persists the count to a sidecar
file and is crash-safe by reserving a batch ahead of use, so a crash resumes at
or above the true count and never under-counts into a nonce collision. That
counter is **single-writer** — it holds no inter-process lock, so exactly one
live hub process may own a given key and its sidecar; a genuine multi-writer
deployment would need inter-process file locking, which is out of scope here.

## Backup recovery

Backup recovery must be boring and documented:

- A backup bundle includes encrypted store files and `manifest.json`.
- The key material is backed up separately from the encrypted data; the manifest
  never contains the raw key.
- `synapse encrypt-key restore --key KEY --manifest manifest.json` authenticates
  every encrypted file before replacing the recorded source path.
- Restored files are written owner-only.
- For SQLite event stores, recovery replay should be checked after restore by
  opening the event store with the normal `synapse hub --db` or read-side CLI
  after the operator has restored/decrypted the cold envelope into the expected
  runtime file.
- Lost-key recovery is impossible by design unless the operator has an escrowed
  passphrase, key-file backup, or platform-keyring recovery path.

The command-line UX should state that lost-key recovery cannot decrypt the data.
It can help identify which key id is needed, but it must not imply a bypass.

## Runtime examples

Generate and verify a file key:

```bash
synapse encrypt-key generate ~/.config/synapse/at-rest.key
synapse encrypt-key check ~/.config/synapse/at-rest.key
```

Encrypt the full local profile after stopping writers:

```bash
synapse encrypt-key migrate \
  --key ~/.config/synapse/at-rest.key \
  --sqlite-db ~/synapse/hub.db \
  --relay-log ~/synapse/feed.ndjson \
  --a2a-state-file ~/synapse/a2a-state.json \
  --cursor ~/synapse/feed.cursor \
  --archive-report ~/synapse/compact-report.html \
  --backup-dir ~/synapse/at-rest-migration-backup
```

Fail closed before startup:

```bash
synapse encrypt-key profile \
  --key ~/.config/synapse/at-rest.key \
  --sqlite-db ~/synapse/hub.db \
  --relay-log ~/synapse/feed.ndjson \
  --a2a-state-file ~/synapse/a2a-state.json \
  --cursor ~/synapse/feed.cursor \
  --archive-report ~/synapse/compact-report.html \
  --require-encrypted
```

Create and restore a recovery bundle:

```bash
synapse encrypt-key backup \
  --key ~/.config/synapse/at-rest.key \
  --backup-dir ~/synapse/at-rest-backup \
  --sqlite-db ~/synapse/hub.db \
  --relay-log ~/synapse/feed.ndjson

synapse encrypt-key restore \
  --key ~/.config/synapse/at-rest.key \
  --manifest ~/synapse/at-rest-backup/manifest.json
```

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

This profile can encrypt current event-store files as cold whole-file envelopes,
including WAL and SHM sidecars when they exist. It does not provide transparent
SQLCipher-style live encryption for the standard `sqlite3` event store. It does
not protect data while the hub is running and has decrypted state in memory. It
does not replace filesystem permissions, process isolation, or host-disk
encryption. It does not solve multi-tenant isolation, per-agent secrecy, signed
events, or network transport security.

At-rest encryption should integrate with paranoid mode as one reported hook:
paranoid mode can require encrypted storage only after the encryption feature
exists, has migration tests, and has recovery documentation. It remains separate
from [end-to-end encrypted channels](end-to-end-encrypted-channels.md), which
hide selected payload bodies from the hub while preserving visible routing
metadata.
