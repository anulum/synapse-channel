<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — advisory agent ranking
-->

# Advisory agent ranking

Invoke `synapse reputation` explicitly to order agents for human triage using
retained coordination evidence. The default `trust-graph` command remains
unranked, and routing, permissions and execution gates do not consume this report.

    synapse reputation ./hub.db --json
    synapse reputation ./hub.db --agent TEAM/seat --since 1791900000 --as-of 1791990000

## Counts and provenance

Each positive release-receipt evidence edge contributes one positive count.
Each other evidence edge contributes one negative count: stale claims, declared
failed checks, broken handoff candidates and contention pairs. A contention pair
counts once for each agent endpoint. Net is positives minus negatives. The order
is descending net, then descending positives, then ascending agent label.
All kinds have unit weight; there is no trained model or hidden trust grade.

Every agent lists the source event sequences behind its count. These are
recorded declarations and inferred operational signals, including self-attested
receipt/check fields. They can be incomplete or gameable. Even an authenticated
journal row does not independently verify the named author, work or external
effect. Filtering changes the evidence set; it does not delete journal records.
An agent filter retains both agents of a shared conflict rather than hiding
its other endpoint. A time filter is inclusive and does not change the reference
clock used to reconstruct stale claims. Empty results are explicit. Human output escapes control characters in recorded
agent labels; JSON preserves them through standard escaping.

The schema is `agent-reputation.v1`; `authorises_execution` is always false.
An advisory order is never a permission, automated assignment or assurance verdict.

## Existing-journal access

One result uses a complete validated read-only SQLite or SQLCipher snapshot.
The command does not create missing databases, migrate schemas, change modes,
quarantine rows or provision keys. SQLite may create WAL coordination sidecars.

Authenticated journals require their existing trusted enrolled key:

    synapse reputation ./hub.db --row-mac-key-file ./hub.db.rowmac.key --json

Legacy rows before enrollment remain unverified. Every selected MAC-bearing
row and covered post-enrollment row must verify. Missing keys, tampered or
malformed rows and unsupported schemas refuse the whole result. For SQLCipher,
pass `--db-key-file` pointing to the existing owner-only key; there is no
plaintext fallback.

The default complete-read budgets are 100,000 events and 64 MiB encoded payload.
Use `--max-events` and `--max-payload-bytes` to choose other positive budgets.
Exhaustion refuses rather than emitting a partial ranking. `--through-seq`
selects an inclusive sequence ceiling; zero selects an empty prefix.
Both `--since` and `--as-of` must be finite Unix timestamps. Operational
refusals print one fixed sentence and exit 2 without disclosing exception text.

## Python API and dashboard

`core.reputation.run_reputation` reads and ranks an existing journal.
`build_reputation` projects a supplied public TrustGraph;
`reputation_to_json` and `render_reputation` encode its result.
See the [API reference](api.md). This is a Python local-journal projection with
no new wire operation or maintained language-SDK counterpart.

The dashboard does not yet offer this explicit ranking view. Operators use the
CLI or API; no default dashboard ranking or new execution control is claimed.
