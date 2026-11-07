<!--
SPDX-License-Identifier: AGPL-3.0-or-later
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Verifying AEF evidence offline

A hub started with `--aef-signing-key` (and `--db`, `--hub-id`) writes a
native AEF v0.1 receipt for each mapped coordination decision. Each receipt is
signed with Ed25519, content-addressed and chained. `synapse aef` lets someone
who does not run the hub check those receipts from files alone. No hub
connection is needed.

## 1. The operator publishes the trust input

The trust file is the verifier's only trust input. It names the receipt-signing
keys the verifier accepts and the log each key may sign for. The operator
builds it from the public half of the hub's signing key and the hub id:

```bash
synapse aef trust --public-key ~/synapse/hub-aef.key.pub --hub-id hub-a \
  --out hub-a.aef-trust.json
```

The `log_id` is derived from the hub id and the key, and each `key_id` from its
public key. A verifier can recompute both. Send the file over a channel you
already trust. Anyone who can change it can make forged receipts verify.

### Trust-file contract (`aef-trust-v0.1`)

```json
{
  "format": "aef-trust-v0.1",
  "keys": {
    "<16-hex key_id>": {
      "public_key_hex": "<64 hex: raw Ed25519 public key>",
      "revoked": false,
      "not_before": 1783940400000,
      "not_after": 1815476400000,
      "senders": ["agent-7"]
    }
  },
  "logs": {"<64-hex log_id>": "<key_id>"}
}
```

`revoked`, `not_before`, `not_after` (epoch milliseconds) and `senders` (the
allowed `actor.agent_id` values) are optional. Every `key_id` must match its
public key, and every log must name a listed key. Unknown or repeated fields
and wrongly typed values are refused. To revoke a key, set `"revoked": true`.
Its receipts then verify as `REVOKED_KEY`.

## 2. The operator exports the receipts

```bash
synapse aef export ~/synapse/hub.db --out hub-a.receipts.jsonl
# add --db-key-file for a SQLCipher-encrypted store
```

The export is read-only. It writes each stored canonical receipt on one line,
in log order. It needs no signing key.

## 3. Anyone verifies them

```bash
synapse aef verify hub-a.receipts.jsonl --trust hub-a.aef-trust.json
synapse aef verify hub-a.receipts.jsonl --trust hub-a.aef-trust.json \
  --now-ms 1783941000000 --json
```

Input is a `.jsonl` file, or a JSON object or array of objects.

Each receipt is checked in the published AEF order: structure, version and
type, domain, log trust, key policy (revocation, validity window, sender
scope), content identity, signature, and expiry against `--now-ms` (default:
the current time). Receipts in one run share a replay index:
- a receipt seen twice is `REPLAYED`;
- a different receipt for a `(log_id, seq)` already taken is `CHAIN_CONFLICT`.

The command prints one verdict per receipt and a summary. It exits `0` only when
every receipt is `VALID`, `1` otherwise (an empty file is not a pass), and `2`
when an input cannot be read or parsed.

### Chain continuity

Each log numbers its receipts from 1, and each receipt names the one before it
in `prev_receipt`: seq 1 names the genesis id `aef1:` followed by 64 zeros,
and every later seq names the `receipt_id` of seq − 1. After the per-receipt
verdicts, the command checks this chain for every log in the file and prints
one `chain` line per log (`--json`: a `chains` array with each log's range,
verdict and findings). Only authentic receipts take part, those that reached
`VALID` or `EXPIRED`, because both passed the content-identity and signature
checks. A receipt that failed earlier leaves a hole.

| Finding | Meaning |
| --- | --- |
| `CHAIN_CONTINUOUS` | No finding breaks the chain. |
| `CHAIN_OPEN_START` | Informational: the lowest seq present is above 1, so the file cannot show what came before. |
| `CHAIN_GAP` | A seq between the lowest and the highest present is missing. |
| `CHAIN_BROKEN_LINK` | A receipt's `prev_receipt` is not the id of the seq − 1 present. |
| `CHAIN_FORK` | Two different receipts hold the same seq, or name the same `prev_receipt`. |
| `CHAIN_DUPLICATE` | The same receipt appears more than once. |
| `CHAIN_GENESIS_INVALID` | Seq 1 does not name the genesis id. |

The chain check never changes a receipt's verdict. By default it does not
change the exit code either; `--require-chain` also exits `1` unless every
log's chain is unbroken. `tests/fixtures/aef_chain_v0_1.json`
(`aef-chain-conformance-v0.1`) holds signed example files with their expected
verdicts and findings, and `tools/aef_chain_vectors.py --check` reproduces it
byte for byte.

The five basic conformance cases are covered as follows: a valid receipt
(`v01`), a forged signature (`v02`), an expired receipt (`v04`), a replayed
seq (`v05`) and a missing predecessor (`c04-missing-prev`). A lease's own
expiry is hub state, not part of a receipt, so it has no vector of its own.

## 4. Inclusion in a signed tree head

```bash
synapse aef inclusion --trust hub-a.aef-trust.json \
  --receipt receipt.json --sth sth.json --proof proof.json
```

The output is `INCLUSION_VALID`, `INCLUSION_INVALID`, `STH_INVALID` or
`STH_UNTRUSTED`. It exits `0` only for `INCLUSION_VALID`.

## 5. Complete up to a signed tree head

```bash
synapse aef sth ~/synapse/hub.db --hub-id hub-a --signing-key hub-a.aef.key --out sth.json
synapse aef verify hub-a.receipts.jsonl --trust hub-a.aef-trust.json \
  --sth sth.json --require-sth
```

- **Issuing a tree head.** `aef sth` reads the store's log read-only and
  re-checks every receipt under the log's key and its chain. The log must
  start at seq 1 with no gap. The command then signs a tree head
  `{aef, log_id, tree_size, root, timestamp}` under the domain `aef:sth:v0.1`.
  - `root` is the RFC 6962 Merkle root over `SHA-256(0x00 || JCS(receipt))`
    in seq order.
  - `timestamp` is the last receipt's `issued_at`, so one log always gives
    the same head. It is not a freshness signal.
  - It writes a new owner-only file (or stdout) and never replaces one.
- **Checking an export against a head.** `verify --sth` (repeatable) compares
  the export with each head. Every receipt of that log counts exactly as it
  is presented, including one that fails its own verdict. A receipt without a
  positive integer `seq`, or one the canonical form cannot represent, fills
  no position. The first verdict that applies wins:
  1. `STH_UNTRUSTED` or `STH_INVALID`: the tree head itself fails.
  2. `STH_TRUNCATED`: a position between 1 and `tree_size` has no receipt.
     This covers a cut-off end, an inner gap, or a rollback below a signed
     state.
  3. `STH_ROOT_MISMATCH`: a position holds two different receipts (a fork),
     or the root over the positions differs (a rewritten history). Identical
     copies count once.
  4. `STH_COMPLETE`: every signed position matches. Receipts past
     `tree_size` are growth.
- **Exit status.** Without `--require-sth` the heads are reported but the
  exit status does not change. With it, any verdict other than `STH_COMPLETE`
  fails the command.
- **Detecting a rollback.** Keep an earlier head and verify each later export
  against it. `STH_COMPLETE` shows that the export still extends the signed
  state.
- **Limits.** An export cut back exactly to an older head's size is
  `STH_COMPLETE` against that older head; only a newer head shows the cut.
  There are no consistency proofs between two heads, so the check needs the
  receipts. The vectors are in `tests/fixtures/aef_sth_v0_1.json`
  (reproducible with `tools/aef_sth_vectors.py`).

## 6. A post-quantum countersignature of a tree head

```bash
synapse aef pq-key new --out witness.pq.key --trust-out witness.pq-trust.json
synapse aef countersign --sth sth.json --trust hub-a.aef-trust.json \
  --key witness.pq.key --out sth.countersign.json
synapse aef verify hub-a.receipts.jsonl --trust hub-a.aef-trust.json --sth sth.json \
  --countersignature sth.countersign.json --countersign-trust witness.pq-trust.json \
  --require-countersignature
```

- **The key.** `pq-key new` writes a new owner-only hybrid key, Ed25519 plus
  ML-DSA-65 (suite `ed25519+ml-dsa-65/v1`), and a trust file naming its public
  key. Commands read the key only from an owner-only file. With `--log-id`
  (repeatable), the trust file binds the key to those logs, and a
  countersignature of any other log is `UNTRUSTED`.
- **Countersigning.** `countersign` first verifies the head under the log's
  AEF trust file, and refuses one that does not verify. It then signs a
  document `aef-sth-countersignature` `0.1` with the hybrid key. The document
  binds the head by the SHA-256 of the head's canonical JSON, signature
  included, and repeats its `log_id`, `tree_size` and `root`.
- **Verdicts.** `verify --countersignature` (repeatable) matches each
  countersignature to the `--sth` head it names. The first check that fails
  gives the verdict:
  1. `INVALID`: the document is malformed.
  2. `UNTRUSTED`: its key is not a hybrid key of `--countersign-trust`.
  3. `INVALID`: its signature fails.
  4. `MISMATCH`: it binds another head. A head that differs only in its own
     Ed25519 signature bytes is another head.
  5. `VALID`.
- **Exit status.** Each countersignature is reported with its head's verdict.
  `--require-countersignature` fails the command unless every countersignature
  is `VALID` **and** its head is `STH_COMPLETE` for the export. It needs at
  least one `--countersignature`, as `--require-sth` needs a `--sth`.
- **What it proves.** After a break of Ed25519, the authenticity of the
  receipts up to `tree_size` rests on two things: the export matching the
  countersigned root, and the post-quantum countersignature. It does not rest
  on any Ed25519 signature. A countersignature proves endorsement by the
  post-quantum key holder at a time it does not prove. The countersigner
  checks the head with Ed25519, so a countersignature made after a break may
  endorse a forged head. Countersign routinely, and keep the countersignatures
  in custody independent of the log host. See [Crypto agility](crypto-agility.md).

## Design choices

AEF v0.1 settles a few points where receipt formats commonly differ:

- **Canonical bytes.** Receipts are serialized with RFC 8785 (JCS) over a
  restricted data model. Numbers must be integers within ±(2**53 − 1), and
  every time is integer milliseconds, so no implementation has to reproduce
  floating-point formatting.
- **What is signed.** The Ed25519 signature covers the domain
  `aef:receipt:v0.1`, a NUL byte, then the canonical bytes of the receipt
  without `signature.value`. Because the domain is part of what is signed,
  an AEF receipt signature cannot be presented as a signature over anything
  that does not begin with that domain.
- **Key identity.** `key_id` is the first 16 hex characters of the SHA-256 of
  the raw public key. The verifier recomputes it from the public key in the
  trust file, which also binds each `log_id` to the one key that signs it.
  Identity is therefore pinned by the operator's trust file rather than
  carried in each receipt, for example as a `did:key`.
- **Receipt identity.** `receipt_id` is `aef1:` followed by the SHA-256 of
  the canonical receipt without its id and signature, so the id names the
  content.
- **Chaining and inclusion.** Each receipt names its predecessor in
  `prev_receipt`, which is checked as a chain (see *Chain continuity*).
  Inclusion in the hub's Merkle log is checked separately, against a signed
  tree head and an audit path (`synapse aef inclusion`), instead of each
  receipt carrying a Merkle root.
- **Verdicts.** Verification returns one value from a closed set of
  verdicts, not a yes/no, so a tool can tell an expired receipt from a
  forged one.

## What this does not establish

- **A cut-off end or a rollback without a tree head.** Without `--sth`, a
  file whose last receipts were removed cannot be told from a complete one;
  the chain check sees only gaps, broken links, forks and duplicates within
  one file. With a kept tree head see section 5, and its limits.
- **Completeness.** A verified export proves that the receipts it contains are
  authentic. It does not prove that the hub recorded every decision.
- **Legal or compliance status.** AEF is an evidence format, not a
  certification.

The verdict fixtures in `tests/fixtures/aef_receipt_v0_1.json` are exercised
through this command in `tests/test_cli_aef.py`.
