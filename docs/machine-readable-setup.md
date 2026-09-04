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
needs, a read-only measurement of the current host, and an immutable plan of
what remains. The initial contract is `synapse-setup.v1`; its first profile is
`local-single-user`.

This is discovery, not installation. Neither command writes configuration,
installs packages or services, starts or restarts a process, changes a terminal,
or claims operator authority. The JSON Schema is shipped in the installed wheel
as `synapse_channel/schemas/synapse-setup-v1.schema.json`.

## Read the profile contract

```bash
synapse setup spec --profile local-single-user --json
```

The deterministic `spec` document lists every requirement, whether it is
mandatory, the evidence source, and the remedy an agent may propose. In this
tranche the supported operations are exactly `spec`, `inspect`, and `plan`;
there is no apply route.

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
- optional systemd availability for persistent Linux user services.

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
- `plan_digest`, binding every plan field except the digest itself;
- `effects`, each with its trigger check, observed status, disposition,
  authority class, disruption class, reversibility, and verification check;
- `authority_required`, deduplicated from effects that are safe enough to plan;
- `can_apply: false` and `apply_not_available`, which prevent the document from
  being mistaken for mutation authority.

An unavailable observation is blocked rather than guessed. An unsupported
platform is also blocked and requires manual remediation. A hub change carries
`operator_restart_authority`; ordinary configuration/environment/service-start
proposals carry `operator_confirmation`. Those labels describe future authority
requirements only—there is no executor in this tranche.

Exit codes are stable: for `inspect`, `0` means every required check passed and
`1` means inspection completed but the profile is not ready. A successfully
derived `plan` exits `0` even when it contains proposed or blocked effects.
Every operation returns `2` when its request cannot be processed. Consumers should use
`schema_version`, `document_kind`, `code`, and the per-check `status` fields,
not parse human text.

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
never permission to mutate the host. A future apply surface must bind the exact
plan digest to an explicit operator confirmation and emit verification and
recovery receipts; it is intentionally outside this read-only tranche.
