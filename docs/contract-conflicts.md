<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — API contract conflicts between branches that share no file
-->

# Contract conflicts

Path and symbol claims find a conflict only when two tasks touch the same
file. One task can change an API contract while another regenerates a client
or edits a deployment built against the old contract. The two share no file,
and they break each other once both are merged. `synapse contracts check`
finds that case from exact artefact versions.

## Declaring contracts

A repository declares its contracts in `contracts.json` at its root:

```json
{
  "contracts": [
    {
      "name": "public-api",
      "spec": "api/openapi.yaml",
      "dependents": [
        {"kind": "client", "path": "clients/python/", "record": "clients/python/CONTRACT"},
        {"kind": "deployment", "path": "deploy/service.yaml", "record": "deploy/CONTRACT"}
      ]
    }
  ]
}
```

`kind` is `client`, `deployment` or `other`; `path` is a file, or a directory
ending in `/`. JSON keeps the reader the same on every supported Python, and a
repeated member name is refused.

A contract's identity is the SHA-256 of its `spec` file at a revision. Each
dependent keeps a record file with exactly one line, `<name> sha256:<64 hex>`.
That line names the contract version the dependent was built from. The
generator or the deploy step writes it. Nothing is inferred. A model may
suggest a `contracts.json` entry, but only the file decides. A matching record
is that step's statement; it does not prove the generated client or the
deployed service behaves correctly.

Paths are repository-relative. Absolute paths, `..`, empty segments
(including a repeated or a file's trailing `/`), backslashes, NUL and text
that is not valid UTF-8 (such as an unpaired `\ud800` escape) are refused, and so are repeated contract names. Only a dependent's `path` may end
in one `/`. A `kind` that is not one of the three strings is refused.

## What counts as a conflict

Every revision is resolved to its commit once, at the start, and all reads
come from those commits' git objects, never from the working tree. Each blob
read is checked against its object id. A path the commit's tree has no entry
for reads as absent. A directory, a symbolic link, or an object git cannot
read or that does not match its id is refused, never read as absent: a
damaged repository must not look unchanged. A spec the manifest declares must
exist at the base. A branch that deletes it changes it, to `absent`. The report
names the commits it read (`base_oid`, `branch_oids`) and the manifest's
digest. The base must be an ancestor of every branch. Each branch is compared
with that exact base commit, without rename detection, so a moved or deleted
record shows as a change of its old path.

The contracts are those `contracts.json` declares at the base. A branch that
changes `contracts.json` is refused, not read, because a dropped declaration
must not look like the absence of a conflict. Check it once it is on the base.

| Kind | When | Evidence |
|---|---|---|
| `record_mismatch` | A branch changes a contract's spec, but one of its dependents still records another digest: the regeneration was forgotten. | branch, new spec digest, dependent, recorded digest |
| `stale_dependent` | One branch changes a contract's spec while another branch changes a dependent of it that records a digest other than the first branch's new one. | both branches, new spec digest, dependent, recorded digest |

A dependent counts as changed by a branch when a path it changed against the
base commit equals the dependent's path or lies inside it.
`clients/python-extra/` is not inside `clients/python/`. A missing record is
reported as `absent`, and a record naming another contract or in another
form as `unreadable`; neither passes silently.

The comparison is byte identity, on purpose. A whitespace-only change of a
spec changes its digest and is reported. That is a false block, the price of
deciding from exact versions only. Semantic compatibility (breaking or not
breaking) is out of scope.

## Running the check

```bash
synapse contracts check --base main --branch api-change --branch client-regen
```

It prints one JSON report with the base and branch commits, the manifest
digest and every conflict with its evidence. The branches are named
explicitly; the command does not read the hub's claims.

| Exit | Meaning |
|---|---|
| 0 | no conflict among the declared contracts |
| 1 | at least one conflict |
| 2 | the input cannot be used: an unknown revision, a base that is not an ancestor of a branch, a branch that changes `contracts.json`, a declared spec missing at the base, a declared file that is not a regular file, a missing or damaged git object, or a malformed manifest |
| 3 | not configured: the base has no `contracts.json`, which says nothing about undeclared dependencies |

The check is advisory. It reads git objects and each branch's diff against the
base commit, and never claims, releases, blocks or grants anything. A hook,
CI job or supervisor may choose to act on it. The hub never runs git.

## Measurement

`benchmarks/contract_conflict_benchmark.py` builds labelled scenarios in real
disposable repositories. It runs the check first, then really merges the
branches in a scratch clone. It judges the result by an oracle independent of
the digests: every client file may call only endpoints the merged spec
defines, and every route the merged deployment serves must be one the merged
spec defines. Composed scenarios join an interface change, a client built on
the old version and a deployment, on separate branches with no shared file.

- *caught* means a finding and a broken merge;
- *missed* means a broken merge and no finding;
- *false block* means a finding and a working merge. That is counterfactual,
  since the check blocks nothing, and is counted under a "block on any
  finding" policy.
- *lost parallelism* is the share of independently mergeable scenarios that
  such a policy would have stopped.

The scenarios are chosen cases, not a sample of real work. The counts show
where byte identity is wrong (additive and whitespace-only spec changes, a
deployment that only scales) and what it cannot see (a client or deployment
not declared in `contracts.json`). They are not a rate.
