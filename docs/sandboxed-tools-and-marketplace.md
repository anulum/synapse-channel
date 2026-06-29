<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Sandboxed tools and marketplace research

Synapse coordinates agents; it does not run untrusted code. This research lane
asks what it would take to do so safely — to let an untrusted tool or plugin run
under a capability-limited sandbox, and only then to consider distributing such
tools through a marketplace. The two are one design with one rule: **no executable
marketplace before the sandbox, the permissions, the signing, and the evidence
all exist.** The sandbox is the precondition; the marketplace is what it unlocks.

## Runtime status

Synapse today runs no third-party code and has no execution sandbox. What exists
is the trust and discovery scaffolding a sandboxed-tool layer would build on, not
the isolation itself:

- **Capability cards and routing** advertise what an agent can do and recommend
  who should take a task — discovery without execution.
- **Signed capability cards** (design, see [signed capability cards](signed-capability-cards.md))
  would give a card verifiable provenance and a key id.
- **Identity and ACLs** (see [identity and ACL](identity-and-acl.md)) provide
  deny-by-default, scoped permissions.
- **Release receipts** carry bounded evidence about a run.
- The **outbound MCP client** already calls external MCP tools behind a
  deny-by-default allowlist — the closest shipped analogue to a permissioned tool
  call, but it trusts the remote server rather than sandboxing it.

There is no WebAssembly runtime, no filesystem/network capability gate, and no
marketplace. This document is the boundary specification for that work.

## The sandbox: capability-limited execution

The isolation primitive is a WebAssembly runtime where an untrusted tool gets
**no ambient authority**: no filesystem, no network, no environment, no clock
beyond what is explicitly granted. Capabilities are passed in deny-by-default,
each scoped and revocable:

- **Filesystem** — only specific preopened paths, read or write, never the host
  root; a tool sees a virtual root, not the machine.
- **Network** — denied by default; a grant is a specific host/port allowlist, not
  raw sockets.
- **Resources** — bounded memory, fuel/instruction limits, and wall-clock so a
  tool cannot hang or exhaust the host.
- **Interface** — the tool speaks a narrow, typed host interface (WASI-style
  preopens plus a Synapse capability ABI), not arbitrary syscalls.

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

Sandboxed tools and a marketplace are **not implemented**. The design is a
boundary specification, deliberately gated.

- **No untrusted code runs without the sandbox.** Until a capability-limited WASM
  runtime exists, Synapse runs no third-party tool code; the outbound MCP client
  trusts a remote server and is not a sandbox.
- **No marketplace before the preconditions.** No executable marketplace ships
  before signed cards, the permission model, the sandbox, and run receipts all
  exist and compose.
- **Deny-by-default, always.** A sandboxed tool has no ambient authority; every
  filesystem, network, and resource grant is explicit, operator-approved,
  recorded, and revocable.
- It **adds no parallel authorisation path** — tool grants are ACL scopes; there
  is one deny-by-default model.
- It does **not** change the local-first default: the sandbox and any marketplace
  client run locally, add no required cloud service, and a host that installs no
  third-party tool behaves exactly as today.
