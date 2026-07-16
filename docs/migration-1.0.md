<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Migration guide: 0.x to 1.0

Current `0.x` releases do not promise backward compatibility across minor
releases. They guard public surfaces against accidental drift, but a reviewed
minor release may deliberately change the package export facade, wire message
vocabulary, federation consumer primitives, or classified CLI tiers. Such a
release updates the corresponding frozen contract, changelog, and migration
notes; a wire-incompatible change also bumps `WIRE_PROTOCOL_VERSION`.

The `1.0` line makes those documented public surfaces stable. Starting with
`1.0.0`, a breaking stable public Python API change requires a package major
release, while a wire-incompatible change requires a wire-protocol version bump.
The canonical policy and its CI guards are documented in
[API and wire stability](api-stability.md).

This guide is for operators who pin SYNAPSE in local hubs, fleet waiters,
automation scripts, or out-of-tree integrations.

## What stays stable

The stable contract is guarded in CI:

- Package exports: `synapse_channel.__all__`, pinned by
  `tests/test_public_api.py`.
- Wire vocabulary: `MessageType` name/value pairs, reserved envelope keys, and
  `WIRE_PROTOCOL_VERSION`, pinned by `tests/test_wire_surface_freeze.py`.
- Federation primitives: out-of-tree imports listed in
  `tests/test_federation_consumer_contract.py`.
- CLI tiers: subcommand classification in `synapse_channel.surface_taxonomy`,
  checked by `tests/test_surface_taxonomy.py`.
- Capability counts: `tools/capability_manifest.py --check`.

The wire-protocol integer is independent from the package version. A 1.0 package
may still speak wire protocol `2`; only wire-incompatible changes bump
`WIRE_PROTOCOL_VERSION`.

## Before upgrading

1. Record the installed version and hub health:

   ```bash
   synapse --version
   synapse doctor
   ```

2. Stop long-running fleet waiters cleanly, then upgrade the package in the
   environment that owns the hub and in each waiter environment:

   ```bash
   python -m pip install --upgrade synapse-channel
   ```

   For `pipx` installs:

   ```bash
   pipx upgrade synapse-channel
   ```

3. Restart the hub first, then restart waiters. Mixed `0.x` and `1.0` peers may
   connect if their wire versions overlap, but a single fleet should converge on
   one package line before release or sustained soak work.

4. Re-run:

   ```bash
   synapse --version
   synapse doctor
   synapse who
   ```

## Integration checks

For Python integrations, import only names from `synapse_channel.__all__` or the
documented deep federation primitives. Treat any other deep import as private
unless a guide names it.

For wire clients:

- Read the hub's `protocol_version` from the `welcome` frame or `/health`.
- Accept the negotiated-down version reported by the client or multi-hub fetcher.
- Do not assume package version and wire version are the same value.
- Keep unknown fields in envelopes and JSON documents non-fatal unless the
  specific command documents a closed schema.

For CLI automation:

- Prefer stable-tier commands for daily operations.
- Pin exact package versions when using `experimental` commands.
- Review the changelog for any `0.x` command-shape changes before upgrading.

## Store and dashboard checks

The event store remains SQLite/WAL. Before a release cut or fleet upgrade, keep a
copy of the hub database and verify the read-side feeds you rely on:

```bash
synapse event-query ./hub.db "universal-receipts all" --json
synapse causality ./hub.db --json
synapse reliability ./hub.db --json
```

For dashboards started with `--feeds-db`, verify the durable feed endpoints after
the hub upgrade:

- `/events.json`
- `/state-at.json`
- `/receipts.json`
- `/operator-actions.json`
- `/reliability.json`

## When an upgrade fails

1. Keep the database copy.
2. Capture `synapse doctor` and the hub startup error.
3. Downgrade only the package, not the database file, unless the changelog says a
   migration changed the store format.
4. File the failure with the package version, `WIRE_PROTOCOL_VERSION`, command
   line, and the smallest event-store excerpt that reproduces the read-side
   problem.

## Release-cut checklist

Before declaring a 1.0 upgrade complete:

- `tests/test_public_api.py` passes with the exact export list.
- `tests/test_wire_surface_freeze.py` passes with the exact wire map.
- `tests/test_federation_consumer_contract.py` passes.
- `tools/capability_manifest.py --check` passes.
- `mkdocs build --strict` includes this guide.
- The fleet's hub and waiters report the intended package version.
