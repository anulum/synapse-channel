<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Machine-readable setup

`synapse setup` gives an LLM agent a versioned description of what Synapse
needs, a read-only measurement of the current host, an immutable plan of what
remains, and a short-lived authorization envelope for one exact plan and target.
The initial contract is `synapse-setup.v1`; its first profile is
`local-single-user`.

This is preparation, not installation. None of these commands writes
configuration, installs packages or services, starts or restarts a process, or
changes a terminal. `authorize` records narrowly scoped operator intent in its
output, but does not consume that authority or apply an effect. The JSON Schema
is shipped in the installed wheel as
`synapse_channel/schemas/synapse-setup-v1.schema.json`.

## Read the profile contract

```bash
synapse setup spec --profile local-single-user --json
```

The deterministic `spec` document lists every requirement, whether it is
mandatory, the evidence source, and the remedy an agent may propose. In this
tranche the supported operations are exactly `spec`, `inspect`, `plan`, and
`authorize`; there is no apply route.

## Inspect a host

```bash
synapse setup inspect --profile local-single-user --json
```

The `inspection` document reports these facts:

- installed package and version;
- Python executable and version;
- operating system, release, and machine architecture;
- the resolved `synapse` executable;
- resolved project and identity;
- live hub reachability and the identity's durable `-rx` waiter;
- optional systemd availability for persistent Linux user services and the
  active hub service's read-only `MainPID` when one exists.

The command uses the same identity and hub diagnostics as `synapse doctor`.
`--project`, `--id`, and `--uri` select non-secret inputs. A secured hub may use
an existing `SYNAPSE_TOKEN` environment value; the value is consumed only by the
read-only probe and never appears in output. There is deliberately no `--token`
argument on this surface. The URI must use `ws://` or `wss://` and cannot contain
userinfo, a query string, or a fragment, so inline credentials are refused before
the probe runs.

## Derive a non-executable plan

```bash
synapse setup plan --profile local-single-user --json
```

`plan` performs the same fresh read-only inspection and maps only failed,
warning, or unavailable required checks to package-owned effect identifiers. It
does not accept an arbitrary effect, command, file, or previously supplied JSON
document. Every plan contains:

- `inspection_digest`, binding the complete canonical inspection;
- `profile_digest`, binding the exact package-owned profile specification;
- `target`, binding the credential-free hub URI, project, and identity observed
  by the inspection;
- `plan_digest`, binding every plan field except the digest itself;
- `effects`, each with its trigger check, observed status, disposition,
  authority class, disruption class, reversibility, and verification check;
- `process_id`, normally `null`, or the exact observed hub PID when the effect
  would restart an already-running hub;
- `authority_required`, deduplicated from effects that are safe enough to plan;
- `can_apply: false` and `apply_not_available`, which prevent the document from
  being mistaken for mutation authority.

An unavailable observation is blocked rather than guessed. Unsupported
platform, package, Python, executable-path, and identity changes are also
blocked: the installed package cannot safely replace its own environment or
choose where a customer's repository identity should be persisted. A missing
hub with no active user-service PID is a first start and carries
`operator_confirmation`. A hub effect against an observed active PID carries
`operator_restart_authority` and binds that exact PID. Waiter establishment
carries `operator_confirmation`. Those labels describe future authority
requirements only—there is no executor in this tranche.

## Authorize one exact plan

Save the plan explicitly, inspect it, then confirm its printed digest:

```bash
synapse setup plan --profile local-single-user --json > setup-plan.json
synapse setup authorize \
  --plan ./setup-plan.json \
  --confirm-digest PLAN_DIGEST_FROM_THE_REVIEWED_PLAN \
  --nonce UNIQUE_URL_SAFE_TOKEN_OF_AT_LEAST_22_CHARACTERS \
  --expires-in 300 \
  --json
```

The redirection in the first command is the operator's shell writing a file;
the setup CLI itself writes only to standard output. `authorize` accepts only a
regular, non-symlink plan file no larger than 64 KiB. It rejects duplicate JSON
keys, non-finite numbers, an altered digest, a stale profile contract, unknown
effects, blocked effects, and a target containing credentials.

The confirmation nonce is a replay token, not a credential. It must contain
22–128 URL-safe letters, digits, `_`, or `-`, and must be unique for every
authorization. The lifetime must be 30–900 seconds. The resulting
`authorization` document binds the exact `plan_digest`, target, nonce, issue and
expiry times, and the authorities already required by the plan. It retains
`read_only: true`, `can_apply: false`, and `apply_not_available`.

If a plan requires `operator_restart_authority`, authorization also requires
the exact PID already recorded in the reviewed plan:

```bash
synapse setup authorize \
  --plan ./setup-plan.json \
  --confirm-digest PLAN_DIGEST_FROM_THE_REVIEWED_PLAN \
  --nonce UNIQUE_URL_SAFE_TOKEN_OF_AT_LEAST_22_CHARACTERS \
  --authorize-restart-pid 4321 \
  --json
```

A different PID, or any PID when the plan represents a first start, is refused
to prevent scope widening and stale-process authority.

The package now includes an owner-only durable authorization ledger for the
future executor. It stores only a domain-separated SHA-256 nonce digest, never
the nonce itself; `BEGIN IMMEDIATE` atomically reserves the nonce and exact
authorization/plan digests before any future effect. Its lifecycle is explicit:
`reserved`, `applied`, `failed`, or `recovered`, with separate effect and
recovery receipt digests. Reopening the SQLite store verifies its schema,
integrity, permissions, and existing records. A replay is refused across
separate processes and database connections.

`setup authorize` deliberately does not open or mutate that ledger. The
envelope's `consumption_required: true` tells a future apply implementation that
it must validate expiry and all bindings, recheck the exact target and PID, and
reserve the nonce durably immediately before its first effect. Until that
consumer ships, the envelope is evidence of intent only and cannot change the
host.

Exit codes are stable: for `inspect`, `0` means every required check passed and
`1` means inspection completed but the profile is not ready. A successfully
derived `plan` exits `0` even when it contains proposed or blocked effects. A
successfully emitted authorization exits `0`; invalid, mismatched, blocked, or
over-broad authorization requests exit `2`. Every operation returns `2` when
its request cannot be processed. Consumers should use `schema_version`,
`document_kind`, `code`, and the per-check `status` fields, not parse human text.

## Validation example

Python agents can load the schema from the installed package without a source
checkout:

```python
import json
import subprocess
from importlib.resources import files

from jsonschema import Draft202012Validator

result = subprocess.run(
    [
        "synapse",
        "setup",
        "inspect",
        "--profile",
        "local-single-user",
        "--json",
    ],
    check=False,
    capture_output=True,
    text=True,
)
document = json.loads(result.stdout)
schema_path = (
    files("synapse_channel")
    .joinpath("schemas")
    .joinpath("synapse-setup-v1.schema.json")
)
Draft202012Validator(json.loads(schema_path.read_text())).validate(document)
```

`jsonschema` is used by this example's consumer; Synapse itself keeps the base
installation single-dependency and does not require that package at runtime.

## Compatibility and authority

A consumer must refuse an unknown `schema_version` or profile version. New
profiles may be added without changing v1 documents; incompatible field changes
require a new schema version. An inspection and its derived plan are evidence,
never permission to mutate the host. An authorization envelope is bounded input
for a future consumer, not an executable capability. The durable ledger is a
package-internal prerequisite and is not called by `authorize`. A future apply
surface must reserve the nonce once, revalidate all bindings and authority, and
emit verification and recovery receipts; it is intentionally outside this
tranche.
