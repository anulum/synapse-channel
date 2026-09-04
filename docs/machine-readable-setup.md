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
needs and a read-only measurement of the current host. The initial contract is
`synapse-setup.v1`; its first profile is `local-single-user`.

This is discovery, not installation. Neither command writes configuration,
installs packages or services, starts or restarts a process, changes a terminal,
or claims operator authority. The JSON Schema is shipped in the installed wheel
as `synapse_channel/schemas/synapse-setup-v1.schema.json`.

## Read the profile contract

```bash
synapse setup spec --profile local-single-user --json
```

The deterministic `spec` document lists every requirement, whether it is
mandatory, the evidence source, and the remedy an agent may propose. In v1 the
supported operations are exactly `spec` and `inspect`; there is no apply route.

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

Exit codes are stable: `0` means every required check passed, `1` means the
inspection completed but the profile is not ready, and `2` means the request or
inspection itself could not be processed. Consumers should use
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
require a new schema version. An inspection is evidence for a later plan, never
permission to mutate the host. Future planning and apply surfaces must bind an
exact plan digest to an explicit operator confirmation and emit verification and
recovery receipts; they are intentionally outside this read-only tranche.
