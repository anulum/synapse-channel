<!--
SPDX-License-Identifier: AGPL-3.0-or-later
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Exporting the journal

`synapse export` copies a hub store's journal and AEF receipts into plain files,
checks them, and turns them into an ordinary SQLite file. None of it needs a
running hub, and none of it changes the store.

```bash
synapse export create ~/synapse/hub.db --out hub-export
synapse export verify hub-export
synapse export to-sqlite hub-export hub-history.sqlite
sqlite3 hub-history.sqlite "SELECT kind, count(*) FROM events GROUP BY kind"
```

## What `create` writes

`create` writes a new owner-only directory and refuses to replace an existing
one. It reads the `events` and `aef_receipts` tables in one read transaction on
one connection, so the files are one snapshot even while a hub keeps writing.
Add `--db-key-file` for a SQLCipher store.

- **`events.jsonl`** holds one stored journal row per line, in `seq` order:
  - `seq` and `ts`. A timestamp that is not a finite number is written as
    `ts: null`, with the stored value in `ts_stored`.
  - `kind` and `payload`, the stored text exactly. Bytes that are not valid
    UTF-8 go into `kind_base64` or `payload_base64` instead; nothing is ever
    replaced. (Both columns are `NOT NULL` with text affinity, so SQLite holds
    any number there as text.)
  - `payload_sha256`, over the stored payload bytes.
  - `mac`, the stored row MAC text, or `null`.
  - `row_mac`: see below.
  - `quarantined`: true when the hub's own decoder would quarantine the row,
    or the row failed its MAC. Such rows are exported as stored, never
    dropped, and `create` warns about them.
- **`aef_receipts.jsonl`** holds the native AEF receipts as stored, one per line.
- **`MANIFEST.json`** records:
  - the format `synapse-export-v1`;
  - the source file's name only;
  - the tool version;
  - the event count, and the SHA-256 of the sorted `seq` list. Event
    maintenance can delete rows, so gaps are legitimate;
  - the first and last `seq`;
  - the counts of each `row_mac` value and of quarantined rows;
  - the AEF receipt count;
  - the SHA-256 of each data file;
  - what is not included: keys, the anti-rollback checkpoint store, binary
    upload objects, and the delivery, message-forward and operation state.

An export holds the journal's content in plaintext. Its custody is yours.

## `row_mac` is the exporter's statement

`row_mac` is `"unchecked"` unless `create` is given the store's row key with
`--row-mac-key`. With the key, it is:

- `"legacy"` for a row at or before the key's first sequence;
- `"verified"` when the stored MAC matches;
- `"failed"` otherwise.

The value in the file is what the exporter found. A reader of the export
cannot re-check it without the key, and `export verify` does not check it.

To re-check a row with the key, compute HMAC-SHA256 over these bytes,
concatenated in order:

1. the domain `SYNAPSE-CHANNEL:EVENT-ROW-MAC:v1` and one NUL byte;
2. `seq` as a signed 64-bit big-endian integer;
3. `ts` as Python's `float.hex()` text, in ASCII, then one NUL byte;
4. the length of `kind` in UTF-8 bytes, as an unsigned 32-bit big-endian
   integer;
5. `kind` in UTF-8;
6. `payload` in UTF-8.

The hex digest must equal the stored `mac`. The key file holds the 32 key
bytes and the first covered sequence.

## `verify`: consistency, not authenticity

`synapse export verify DIR` refuses a directory that has anything besides the
three files, or holds a symlink or a non-regular file. It then recomputes from
the files:

- every file digest;
- every `payload_sha256`;
- the event and AEF counts;
- the `seq`-set digest.

It compares all of them with the manifest and prints `EXPORT_CONSISTENT` or the
first inconsistency (`EXPORT_INCONSISTENT`, exit 1). It needs no key. A
consistent export is one whose files agree with each other; it is not proof that
a hub wrote them.

## `to-sqlite`: the same history in plain SQLite

`synapse export to-sqlite DIR OUT.db` verifies the export first. It then writes
a new owner-only SQLite file holding exactly the rows it verified. Its schema:

```sql
CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, ts_stored TEXT, kind TEXT,
  kind_base64 TEXT, payload TEXT, payload_base64 TEXT, payload_sha256 TEXT NOT NULL,
  mac TEXT, row_mac TEXT NOT NULL, quarantined INTEGER NOT NULL);
CREATE TABLE aef_receipts (position INTEGER PRIMARY KEY, log_id TEXT, seq INTEGER,
  receipt_id TEXT, receipt TEXT NOT NULL);
```

Any SQLite tool can query it. It is not a hub store: no hub opens it, and
loading an export back into a hub is not supported, because new rows would
need a new row key and could no longer show where they came from.
