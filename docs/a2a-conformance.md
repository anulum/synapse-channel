<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# A2A conformance matrix

`synapse a2a-conformance` prints the local Agent2Agent bridge matrix. The matrix
is an inventory, not a certification: it maps the bridge to the A2A 1.0.0
operation model and keeps external validation gates visible until independent
clients, real webhook receivers, and deployment reviews produce receipts.

Comparison sources:

- A2A Protocol Specification `1.0.0`:
  <https://a2a-protocol.org/v1.0.0/specification>
- Normative A2A proto source:
  <https://github.com/a2aproject/A2A/blob/main/spec/a2a.proto>

## Usage

```bash
synapse a2a-conformance
synapse a2a-conformance --json
synapse a2a-conformance --status partial
```

Status labels:

| Status | Meaning |
| --- | --- |
| `supported` | Covered by the local bridge and focused repository tests. |
| `partial` | Implemented with a documented limitation or narrower local semantics. |
| `unsupported` | Not implemented by the local bridge. |
| `external` | Requires independent infrastructure, client, or operator validation. |

## Current bridge boundaries

The bridge currently exposes Agent Card discovery, HTTP+JSON/REST routes,
JSON-RPC dispatch, bridge-local task storage, local Server-Sent Events snapshots,
and push-notification configuration storage. The matrix marks these as
`supported` or `partial` according to the local behavior and its limits.

The following rows remain `external` until receipts exist:

- independent A2A client/server interoperability traces;
- a real webhook receiver test behind TLS and proxy behavior;
- deployment threat-model review for an exposed bridge.

Use [A2A bridge validation receipts](a2a-validation-receipts.md) to record those
external traces. Once a receipt exists, update the matrix row, the receipt page,
and the TODO item in the same commit.
