<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Sandboxed tools and marketplace

Synapse coordinates agents, and now also runs untrusted tool code safely: a tool or
plugin executes under a capability-limited sandbox with no authority beyond what an
operator grants. Distributing such tools through a marketplace is the gated next step.
The two are one design with one rule: **no executable marketplace before the sandbox,
the permissions, the signing, and the evidence all exist.** The sandbox is the
precondition — it now ships; the marketplace is what it unlocks.

## Runtime status

Synapse ships a capability-limited **WebAssembly** sandbox behind the optional `[wasm]`
extra (`synapse sandbox`). A tool runs only inside it, under a permission manifest an
operator approves. The surrounding trust and discovery scaffolding it builds on:

- **Capability cards and routing** advertise what an agent can do and recommend who
  should take a task — discovery without execution.
- **The permission manifest and policy core** (`core/sandbox_policy.py`) model a tool's
  filesystem, network, and resource grants deny-by-default and compile them to ACL
  scopes, so a tool's capabilities are evaluated the same way as any other access.
- **The WASM runtime** (`core/wasm_sandbox.py`) enforces a granted manifest: memory and
  fuel caps, a wall-clock backstop, WASI preopened paths, and no sockets. Each run emits
  a bounded run receipt.
- **Signed capability cards** (design, see [signed capability cards](signed-capability-cards.md))
  would give a card verifiable provenance and a key id — a marketplace precondition.
- **Identity and ACLs** (see [identity and ACL](identity-and-acl.md)) provide
  deny-by-default, scoped permissions; the sandbox reuses them.
- The **outbound MCP client** calls external MCP tools behind a deny-by-default
  allowlist, but it trusts the remote server rather than sandboxing it.

The marketplace that would distribute sandboxed tools is not yet built; the rest of this
document is its boundary specification.

### Operator verbs

The `synapse sandbox` CLI exposes three verbs, ordered as an operator works up to a run:

- **`validate <manifest>`** — load a capability manifest and report the normalised,
  deny-by-default grants it declares. A check of the policy, before any tool is involved.
- **`test <tool.wasm> --manifest <manifest>`** — pre-flight the tool *without running it*:
  compile the module, confirm the `--entrypoint` (default `run`) is an exported function,
  and confirm the module matches its manifest digest. No fuel is spent and none of the
  tool's behaviour happens — a runaway tool still pre-flights instantly. It exits `0` when
  the tool is ready, `1` when the pre-flight ran but the tool is not ready (invalid module,
  missing entrypoint, or digest mismatch), and `2` when it could not pre-flight at all, so
  `sandbox test … && sandbox run … --approve` is a safe gate.
- **`run <tool.wasm> --manifest <manifest> --approve`** — execute the tool under its
  manifest, bound to the exact module by content digest, and print the bounded run receipt.
  The explicit `--approve` keeps a capability-bearing run an operator decision.

`test` and `run` need the optional `[wasm]` extra (`pip install 'synapse-channel[wasm]'`)
and report that hint when it is absent rather than failing obscurely.

## The sandbox: capability-limited execution

The isolation primitive is a WebAssembly runtime where an untrusted tool gets
**no ambient authority**: no filesystem, no network, no environment, no clock
beyond what is explicitly granted. Capabilities are passed in deny-by-default,
each scoped and revocable:

- **Filesystem** — only specific preopened paths, read or write, never the host
  root; a tool sees a virtual root, not the machine.
- **Network** — denied by default, and denied by construction: WASI preview1 exposes no
  sockets, so a tool reaches the network only through a host import that is never linked.
- **Resources** — bounded memory, a fuel/instruction budget, and a wall-clock backstop,
  so a tool cannot hang or exhaust the host; a fuel bomb or a runaway loop is trapped.
- **Interface** — the tool speaks a narrow, typed host interface (WASI-style
  preopens plus a minimal Synapse capability ABI), not arbitrary syscalls.

The sandbox composes with the existing authorisation path: a tool's grants are an
ACL scope, evaluated the same way as any other permission, so there is one
deny-by-default authorisation model, not a parallel one.

## The marketplace: distribution on top of the sandbox

A marketplace is only safe once the sandbox exists, because distribution widens
who supplies the code. Each listed tool would carry, and a host would verify
before running:

- a **signed capability card** binding the tool's identity, declared capabilities,
  and a content digest to a key id (provenance);
- a **declared permission manifest** — the exact filesystem/network/resource
  grants it requests, shown to the operator for an explicit, deny-by-default
  decision;
- **discovery metadata** reusing the existing capability directory, so finding a
  tool reuses routing rather than a new index;
- **run receipts** — every sandboxed run produces a bounded receipt (inputs digest,
  granted capabilities, exit, output digest) as audit evidence, exactly like a
  release receipt.

Install and run are operator-confirmed and reversible: a tool runs only with the
capabilities the operator approved, the approval is recorded, and revoking a key
or a grant stops future runs.

## Trust chain

The trust chain is end to end and reuses the design set: a [signed capability
card](signed-capability-cards.md) proves *what* a tool is and *who* signed it;
[identity and ACL](identity-and-acl.md) decides *whether* it may run and with
*which* grants; the WASM sandbox enforces those grants at runtime; and a receipt
records *what it did*. The [federated trust model](federated-trust-model.md)
extends the same chain across domains for a cross-organisation marketplace. No new
trust root is introduced.

## Boundaries

The **marketplace** is **not implemented**. The sandbox it builds on now ships; the
marketplace remains a boundary specification, deliberately gated.

- **Untrusted code runs only inside the sandbox.** A tool executes only through
  `synapse sandbox run`, under an operator-approved manifest, bound to one module by
  content digest; the outbound MCP client trusts a remote server and is not a sandbox.
- **No marketplace before the preconditions.** No executable marketplace ships
  before signed cards, the permission model, the sandbox, and run receipts all
  exist and compose. The permission model, the sandbox, and run receipts now exist;
  signed capability cards and the distribution layer remain.
- **Deny-by-default, always.** A sandboxed tool has no ambient authority; every
  filesystem, network, and resource grant is explicit, operator-approved,
  recorded, and revocable.
- It **adds no parallel authorisation path** — tool grants are ACL scopes; there
  is one deny-by-default model.
- It does **not** change the local-first default: the sandbox and any marketplace
  client run locally, add no required cloud service, and a host that installs no
  third-party tool behaves exactly as today.
