<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Sandboxed tools and marketplace

Synapse runs operator-approved WebAssembly modules with resource limits and
filesystem preopens. A marketplace for distributing modules is planned. Signed
capability cards, ACL utilities and run records exist as separate facilities;
they are not yet composed into a marketplace install or execution admission path.

## Runtime status

The optional `[wasm]` extra supplies `synapse sandbox`. The CLI matches a
module's content digest against a supplied manifest and requires `--approve`
as local caller confirmation. It does not authenticate an operator, verify a
signed card or check revocation. Direct callers of `run_sandboxed` supply their
own digest and approval checks.

Related facilities have separate contracts:

- Capability cards and routing advertise declared abilities and recommend
  participants; discovery does not grant execution authority.
- `core/sandbox_policy.py` checks requested filesystem, network and resource
  capabilities against a supplied manifest. Its `to_acl_rules` conversion
  utility is separate from the CLI's direct manifest check.
- `core/wasm_sandbox.py` configures guest memory, fuel, an execution timer,
  filesystem preopens and standard WASI imports, and returns a run record.
- [Signed capability cards](signed-capability-cards.md) provide advisory
  Ed25519 provenance, scoped key trust, expiry and revocation diagnostics.
  They are not enforced admission for sandbox runs.
- [Identity and ACL](identity-and-acl.md) provide scoped permissions on their
  documented coordination surfaces. The sandbox CLI does not run enrolled-agent
  ACL checks.
- The outbound MCP client uses an allowlist and trusts the remote server. It
  does not sandbox that server.

The marketplace distribution layer is not implemented.

## Operator verbs

- **`validate <manifest>`** loads the manifest and reports its grants.
  `--check-paths` checks current filesystem paths;
  `--workspace-root DIR` also checks containment under approved roots.
- **`test <tool.wasm> --manifest <manifest>`** compiles without instantiating
  the module or running its entrypoint. It checks exported function names and
  digest equality. It does not check the full calling convention or promise a
  successful run. Compilation consumes host resources without a deadline.
  Exit `0` means these checks passed, `1` means they completed with a
  non-ready result, and `2` means the check could not proceed.
- **`run <tool.wasm> --manifest <manifest> --approve`** matches the digest,
  requires local caller confirmation, executes under the configured limits
  and prints a bounded run record. Inspect that record's `exit` value:
  command completion alone does not mean the guest returned successfully.

`test` and `run` need `pip install 'synapse-channel[wasm]'`. See the
[getting-started guide](wasm-sandbox-getting-started.md) for a complete example.

## Runtime limits

- **Filesystem:** only manifest-selected preopens, under guest names, with
  configured read or write permissions. The operator chooses their breadth
  and should avoid granting the host root.
- **Network:** no outbound socket-opening import or network descriptor is
  supplied. Network grants are recorded as policy but do not enable networking.
- **Clocks:** standard WASI clock imports remain available without a manifest
  grant. This runtime does not provide clock isolation.
- **Resources:** guest memory, fuel and an execution timer are configured.
  Store memory and fuel limits apply during guest initialization. The execution
  timer starts after instantiation and does not bound compilation or
  instantiation time; guest limits are not budgets for all host orchestration.
- **Host paths:** paths are checked before preopening by name. Validation
  does not pin directory descriptors against concurrent replacement. Keep
  path components stable and operator-controlled through that open.
- **Input/output:** the current interface hashes `inputs` for the receipt
  but does not deliver those bytes through a guest input ABI. Output records
  the exported function's return value as a digest.

A receipt records the local runner's declaration of module, grants, digests and
exit. `--attest DB` appends it to an event store; it is not independent execution
proof or authenticated operator approval. See the
[threat model](sandbox-threat-model.md) for containment mechanisms and limits.
Provider CLI processes and remote MCP tools are outside this sandbox.

## Proposed marketplace contract

A distribution layer would need to compose, before a module is admitted:

- an accepted signed card binding declared capabilities and module content to
  a trusted key, with expiry and revocation enforced at admission;
- authenticated caller permissions and an explicit decision on the requested
  filesystem and resource grants;
- module digest checks, runtime limits and authenticated run evidence;
- discovery metadata and, for cross-domain use, explicit
  [federated trust inputs](federated-trust-model.md).

A valid card signature binds declared content to an accepted key; it does not
prove module behaviour or grant execution authority. These checks are separate
today. A run's writes in a granted directory are not automatically reversible.

The proposed marketplace retains the local-first model. No distribution service
is required for current coordination or sandbox commands.
