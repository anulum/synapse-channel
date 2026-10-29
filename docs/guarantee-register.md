<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — how far each capability has come
-->

# Guarantee register

This page shows, for each listed capability, the last stage it has reached.
The table is generated from `tools/guarantee_register.toml` by
`python tools/guarantee_register.py --update`. A pre-commit hook runs
`--check` and refuses a stale table or a stale
`docs/_generated/guarantee_register.json`.

| Stage | Evidence the tool checks |
| --- | --- |
| described | Public documentation pages that exist |
| implemented | Package source files that exist |
| wired | A CLI subcommand, a console script's entry module or a named module from which a chain of run-time imports reaches an implementing module. Imports under `if TYPE_CHECKING:` do not count; a module named only by a string at run time is not seen |
| qualified | A passing public receipt in `docs/qualification/` for one platform and one artefact: a source commit or a wheel with its version and SHA-256 |
| released | A passing receipt for the released wheel of that version |

A stage counts only if every earlier stage has also been reached.

Two kinds of claim are refused:

- A receipt for one platform cannot qualify another. For example, Linux test
  evidence cannot qualify Windows.
- A receipt for a source commit cannot qualify a release. Only a receipt for
  the released wheel of the same version can.

No qualification receipt has been committed yet, so no capability reaches the
qualified or released stage on this page. Test results recorded elsewhere,
for example in [A2A validation receipts](a2a-validation-receipts.md), are not
receipts in this sense until they are committed as one.

Receipts are maintainer-attested declarations. The tool checks their syntax,
refuses a repeated JSON member, and checks that they agree with the register.
It does not check the wheel's bytes or its publication, that the source
commit exists, or that the tests ran. A qualification or release row rests on
whoever wrote the receipt until independent provenance backs it.

A receipt is a JSON file with exactly these fields:

```json
{
  "schema": "synapse-qualification-receipt.v1",
  "capability": "a2a-bridge",
  "platform": "linux",
  "artefact": {"kind": "wheel", "version": "0.99.39", "sha256": "<64 hex>"},
  "tests": ["tests/test_a2a_security.py"],
  "result": "passed"
}
```

A source receipt uses `{"kind": "source", "commit": "<40 hex>"}` as its
artefact.

<!-- guarantee-register:start -->
| Capability | Stage | Wired through | Qualified (platform: artefact) | Released |
| --- | --- | --- | --- | --- |
| Coordination hub | wired | `hub` (cli) | — | — |
| Git path claims | wired | `git-claim` (cli), `git-claim-check` (cli) | — | — |
| Lease-held command lock | wired | `lock` (cli), `release` (cli) | — | — |
| A2A bridge | wired | `a2a-serve` (cli) | — | — |
| A2A TCK conformance backend | wired | `src/synapse_channel/cli_a2a_serve.py` (imported_by) | — | — |
| Authenticated HTTP MCP | wired | `mcp` (cli) | — | — |
| stdio MCP server | wired | `synapse-channel` (console_script), `mcp` (cli) | — | — |
| Scoped execution outcomes | wired | `src/synapse_channel/a2a_outcome.py` (imported_by), `src/synapse_channel/mcp/outcome.py` (imported_by) | — | — |
| Wake relay | wired | `src/synapse_channel/cli_waker_relay.py` (imported_by) | — | — |
| Protected-write driver | implemented | — | — | — |
<!-- guarantee-register:end -->
