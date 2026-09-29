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

## 4. Inclusion in a signed tree head

```bash
synapse aef inclusion --trust hub-a.aef-trust.json \
  --receipt receipt.json --sth sth.json --proof proof.json
```

The output is `INCLUSION_VALID`, `INCLUSION_INVALID`, `STH_INVALID` or
`STH_UNTRUSTED`. It exits `0` only for `INCLUSION_VALID`.

## What this does not establish

- **Chain continuity.** A missing receipt, or a fork in the `prev_receipt` links
  across files, is not yet a verdict. The gap, fork and rollback verdicts are
  tracked for the next profile version.
- **Completeness.** A verified export proves that the receipts it contains are
  authentic. It does not prove that the hub recorded every decision.
- **Legal or compliance status.** AEF is an evidence format, not a
  certification.

The verdict fixtures in `tests/fixtures/aef_receipt_v0_1.json` are exercised
through this command in `tests/test_cli_aef.py`.
