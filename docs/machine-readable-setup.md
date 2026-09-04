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
needs, a read-only host inspection, an immutable plan, a short-lived
authorization, and a fail-closed executor for the initial package-owned Linux
service effects. The initial contract is `synapse-setup.v1`; its first profile
is `local-single-user`.

`spec`, `inspect`, `plan`, and `authorize` do not mutate the host. `apply` is a
separate explicit mutation boundary: it consumes one exact authorization and
may install or start only the local hub and exact identity waiter described
below. It never installs or replaces Python, the package, identity
configuration, a terminal, or a provider process. The JSON Schema is shipped
in the installed wheel as
`synapse_channel/schemas/synapse-setup-v1.schema.json`.

## Read the profile contract

```bash
synapse setup spec --profile local-single-user --json
```

The deterministic `spec` document lists every requirement, whether it is
mandatory, the evidence source, and its remedy. The supported operations are
exactly `spec`, `inspect`, `plan`, `authorize`, and `apply`.

## Inspect a host

```bash
synapse setup inspect --profile local-single-user --json
```

The `inspection` document reports:

- installed package and version;
- Python executable and version;
- operating system, release, and machine architecture;
- the resolved `synapse` executable;
- resolved project and identity;
- live hub reachability and the identity's durable `-rx` waiter;
- optional systemd availability and the active hub service's read-only
  `MainPID`, when one exists.

The command uses the same identity and hub diagnostics as `synapse doctor`.
`--project`, `--id`, and `--uri` select non-secret inputs. A secured hub may use
an existing `SYNAPSE_TOKEN` environment value; the probe never emits it. There
is deliberately no `--token` argument. The URI must use `ws://` or `wss://` and
cannot contain userinfo, a query string, or a fragment.

## Derive an inert plan

```bash
synapse setup plan --profile local-single-user --json
```

`plan` performs a fresh read-only inspection and maps only unmet checks to
package-owned effect identifiers. It accepts no command, arbitrary effect, or
previous inspection document. Every plan binds:

- the complete inspection through `inspection_digest`;
- the package-owned profile through `profile_digest`;
- the credential-free URI, project, and identity through `target`;
- the exact package version, Python executable, Synapse executable, platform,
  and service-manager executable through `generation`;
- every remaining field through `plan_digest`;
- each effect's trigger, disposition, authority, disruption, reversibility,
  verification check, and optional exact restart PID.

`can_apply` is true only for a non-empty plan whose every effect has a supported
adapter and is not blocked. A ready plan has `no_changes_required`; a fully
applicable plan has `authorization_required`; a blocked plan has
`manual_remediation_required`.

Unavailable observations are blocked rather than guessed. Package, Python,
platform, executable-path, and identity changes are also blocked. A missing hub
with no active service PID is a first start with `operator_confirmation`. A hub
effect against an active service carries `operator_restart_authority` and its
exact PID. Waiter establishment carries `operator_confirmation`.

## Authorize one exact plan

Save and review the plan, then confirm its printed digest:

```bash
synapse setup plan --profile local-single-user --json > setup-plan.json
synapse setup authorize \
  --plan ./setup-plan.json \
  --confirm-digest PLAN_DIGEST_FROM_THE_REVIEWED_PLAN \
  --nonce UNIQUE_URL_SAFE_TOKEN_OF_AT_LEAST_22_CHARACTERS \
  --expires-in 300 \
  --json > setup-authorization.json
```

The shell redirections write the files. `authorize` accepts only a regular,
non-symlink plan no larger than 64 KiB. It rejects duplicate JSON keys,
non-finite numbers, altered digests, stale profiles, unknown or blocked effects,
and credential-bearing targets.

The nonce is a replay token, not a credential. It must contain 22–128 URL-safe
letters, digits, `_`, or `-`, and be unique for every authorization. The
lifetime must be 30–900 seconds. The authorization has `read_only: true`,
`can_apply: true`, `single_use_authorization`, and
`consumption_required: true`; it remains inert until passed to `apply`.

When the plan requires a hub restart, add the exact PID already in the plan:

```bash
synapse setup authorize \
  --plan ./setup-plan.json \
  --confirm-digest PLAN_DIGEST_FROM_THE_REVIEWED_PLAN \
  --nonce UNIQUE_URL_SAFE_TOKEN_OF_AT_LEAST_22_CHARACTERS \
  --authorize-restart-pid 4321 \
  --json > setup-authorization.json
```

A different PID, or any PID for a first start, is refused.

## Apply the authorized Linux effects

Review both files, confirm the digest again, and declare any additional process
that must remain alive:

```bash
synapse setup apply \
  --plan ./setup-plan.json \
  --authorization ./setup-authorization.json \
  --confirm-digest PLAN_DIGEST_FROM_THE_REVIEWED_PLAN \
  --protect-pid 12345 \
  --receipt "$PWD/setup-receipt.json" \
  --json
```

`--receipt` must be an absolute path whose parent already exists. A new receipt
is owner-only (`0600`); an existing symlink or non-regular leaf is refused.
`--protect-pid` is repeatable. The executor protects its direct parent
automatically. An authorized restart target cannot simultaneously be a
preservation target.

Immediately before mutation, `apply` takes a non-blocking owner-only host lock,
re-inspects the exact target, and compares the plan-bound generation. A new or
blocked effect, changed executable, package, platform, target, or service
manager, expired or replayed authorization, and absent protected PID all fail
closed. When an authorized effect became satisfied, the receipt says
`already_satisfied` and no command runs for it.

The initial adapter is deliberately narrow:

- Linux with an answering systemd user manager only;
- `establish_local_loopback_hub` atomically installs the package-rendered
  `synapse-hub.service`, then starts it or restarts only its freshly rechecked,
  authorized `MainPID`;
- `establish_identity_waiter` atomically installs `synapse-arm@.service`,
  escapes the exact identity through the generation-adjacent `systemd-escape`,
  and enables only that instance;
- all commands use fixed argv, a bounded timeout, and no shell;
- setup directories are traversed component by component without following
  symlink leaves; each managed child directory must be owner-controlled and may
  not be group- or world-writable;
- existing unit leaves must be bounded, regular, owner-controlled files.

The owner-only SQLite ledger stores a domain-separated SHA-256 nonce digest,
never the nonce. `BEGIN IMMEDIATE` reserves the nonce and authorization/plan
digests after fresh validation and before the first service-file write. Replay
is refused across processes and connections.

On success, the ledger stores the `applied` receipt digest. On partial failure,
the executor records `failed`, restores prior unit bytes and modes, restores
the prior enabled/active service state, removes only package-created empty
directories, checks protected PIDs again, and records `recovered`. If exact
restoration cannot be proven, the receipt says `recovery_failed` and the ledger
remains `failed`; this is never reported as success.

`inspect` exits `0` when required checks pass and `1` when inspection completes
but the profile is not ready. Valid `plan` and `authorize` output exits `0`.
`apply` exits `0` only for `applied`, `1` after a proven `recovered` failure,
and `2` for a precondition, authorization, receipt, or unrecoverable executor
error. Consumers should parse `schema_version`, `document_kind`, `code`,
`outcome`, and per-check status, not human text.

## Validate output from an installed wheel

```python
import json
import subprocess
from importlib.resources import files

from jsonschema import Draft202012Validator

result = subprocess.run(
    ["synapse", "setup", "inspect", "--profile", "local-single-user", "--json"],
    check=False,
    capture_output=True,
    text=True,
)
document = json.loads(result.stdout)
schema_path = files("synapse_channel").joinpath("schemas", "synapse-setup-v1.schema.json")
Draft202012Validator(json.loads(schema_path.read_text())).validate(document)
```

`jsonschema` belongs to this consumer example; the Synapse base installation
does not require it.

## Compatibility and remaining release gate

Consumers must refuse unknown schema or profile versions. Incompatible field
changes require a new schema version. Inspection and plans are evidence, never
permission. Authorization is bounded input for the separate executor, not an
executable script or general capability.

This tranche verifies unit ownership, systemd active state, non-zero service
PIDs, protected-PID continuity, replay refusal, and bounded restoration. Strict
profile verification—including a directed canary and durable event-store
restart/replay evidence—is a separate release gate and is not implied by an
`applied` receipt. macOS launchd, native Windows services, containers, remote
hubs, secret provisioning, package replacement, and identity persistence remain
unsupported by this executor.
