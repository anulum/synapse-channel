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

## What this does not establish

- **A cut-off end or a rollback.** Without a signed tree head, a file whose
  last receipts were removed cannot be told from a complete one. A later
  export that ends below an earlier one needs state kept across exports.
  The chain check sees only gaps, broken links, forks and duplicates within
  one file.
- **Completeness.** A verified export proves that the receipts it contains are
  authentic. It does not prove that the hub recorded every decision.
- **Legal or compliance status.** AEF is an evidence format, not a
  certification.

The verdict fixtures in `tests/fixtures/aef_receipt_v0_1.json` are exercised
through this command in `tests/test_cli_aef.py`.
