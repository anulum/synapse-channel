<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — verdicts that turn stale when an input is corrected
-->

# Verdicts and corrections

A review is bound to the object it accepted, usually by hash. That protects the
reviewed file, but not the inputs the reviewer read. If a raw run record is
corrected after an abstract built on it was approved, the abstract is unchanged
and its approval still looks valid.

SYNAPSE records what a verdict read, so a later correction of any of those
inputs turns the verdict **stale** and stops a new approval of its subject until
a reviewer looks again.

## Three records

All three are append-only progress notes with a canonical JSON body. Nothing is
ever edited in place; a correction is a new record that names what it corrects.
An object is identified by its namespace **and** its SHA-256: the same hash
declared in two namespaces is two objects, and a record in one namespace never
reaches a verdict in another.

| Record | Command | What it says |
|---|---|---|
| Evidence | `synapse verdict evidence --sha256 H --namespace NS [--members-file F]` | Object `H` belongs to namespace `NS`. With a members file it is a manifest of up to 100 000 member hashes. |
| Verdict | `synapse verdict record --namespace NS --subject A --outcome accepted --input H ... --author SEAT --sequence N` | The reviewer's outcome on `A` in `NS`, every input of `NS` it read, the corrections already considered (`--acknowledge ID`), and the reviewer's own verdict number on `A`. |
| Correction | `synapse verdict correct --namespace NS --corrects H --replacement H2 --reason TEXT` | Object `H` of `NS` is superseded by `H2`. |

Each command waits for the hub's answer and exits `1` with the hub's reason when
the record is refused.

## Who may declare, review and correct

- A seat may declare evidence and record verdicts in its own project namespace
  (the part of its name before `/`), and in any namespace whose allow-list names
  it (`synapse hub --verdict-authority NAMESPACE=SEAT`, repeatable). A reviewer
  from another project needs that allow-list entry; otherwise its verdict is
  refused and cannot block the namespace's approvals.
- Only a seat that declared an object in its namespace, or an allow-listed seat
  of that namespace, may correct it. An object nobody declared there cannot be
  corrected. Declaring the same hash in your own namespace gives no authority
  over another namespace's object.
- Verdict inputs are objects of the verdict's own namespace; dependencies across
  namespaces are not followed.

A refused record never enters the progress log. The hub journals a
`verdict_record_refused` row (author, kind, task and reason, never the body)
and answers the sender privately.

## When a verdict is stale

A verdict is stale when any input it depends on has a correction the verdict
does not acknowledge. Its dependencies are its direct inputs, the members of any
declared manifest among them, and, transitively, the inputs of verdicts on those
inputs. A correction stays in force permanently, so reverting `R → R' → R`
leaves both objects corrected until a verdict acknowledges them.

The state is a pure function of the recorded set, not of arrival order, so a
restarted hub, a replayed log and a federated peer agree. Two further states
fail closed:

- `inputs-unrecorded`: an accepted verdict that cites no input
  (`--allow-unrecorded-verdict-inputs` lets it pass the gate);
- `unbounded`: a dependency chain deeper than 32 levels.

When a correction turns verdicts stale, the hub journals one `verdict_stale`
row per verdict and sends a directed notice to the verdict's author and
reviewer. The journal row is the source of truth; a notice to an offline seat
may dead-letter.

## The approval gate

An `approved` decision on a subject that names a SHA-256 object
(`synapse approval decide --subject sha256:HEX --approve` or the bare hash) is
refused unless every reviewer's latest verdict on that object of the approver's
project namespace — the one with the highest `--sequence` — is accepted and
fresh. Requests and rejections are never gated,
and other subjects are unaffected.

An approval granted before its subject turned stale is not revoked. `synapse
verdict status` reports it under `granted_then_stale`.

The gate is on by default. `synapse hub --verdict-gate off` switches it off;
each approval of a SHA-256 subject is then journalled as a `verdict_gate_bypass`
row with what the gate would have decided.

## Status

```bash
synapse verdict status ./synapse.db
synapse verdict status ./synapse.db --subject HEX --json
```

The report lists every verdict with its state and unacknowledged corrections,
the gate decision per subject, the stale notices, refused records and gate
bypasses, and approvals granted before their subject turned stale. Subjects are
listed as `NAMESPACE:SHA256`. Pass the same `--verdict-authority` options the
hub uses so the report applies the same rules.

All three audit rows are also universal receipts (`verdict-stale`,
`verdict-record-refused`, `verdict-gate-bypass`), so they show up in
`synapse event-query ./synapse.db "universal-receipts SEAT"` and in the dashboard
receipt feed.
