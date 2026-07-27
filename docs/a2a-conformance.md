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
operation model and keeps the remaining gates visible after each independent
client, conformance, webhook, and deployment receipt.

Comparison sources:

- A2A Protocol Specification `1.0.0`:
  <https://a2a-protocol.org/v1.0.0/specification>
- Normative A2A proto source used by the TCK pin:
  <https://github.com/a2aproject/A2A/blob/173695755607e884aa9acf8ce4feed90e32727a1/specification/a2a.proto>

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

The HTTP+JSON edge derives exact Host authorities from the advertised endpoint
on every CLI start and enforces them before authentication and routing on every
route. A present browser Origin is independently denied unless explicitly
permitted with `--allow-origin`; origin-less clients still require the exact
Host. Direct HTTP handler construction fails closed without an advertised or
explicit trusted authority.

The optional gRPC row remains `partial` because `--grpc-port` starts only the
custom JSON-over-gRPC `SendMessage` / `GetTask` subset, not the generated
official protobuf surface. The listener is default-off. When enabled, the
integrated CLI composes the selected shared bearer, native TLS/mTLS files,
request-concurrency ceiling, one-MiB receive/send bounds, bounded JSON parser,
finite call-deadline ceiling, and stable value-free errors into gRPC. The
shared bearer is not per-client identity or a method-level ACL. See the
[current gRPC activation boundary](a2a-deployment-threat-model.md#current-grpc-activation-boundary).

The real-webhook row is `partial`: focused tests deliver to real local HTTPS
receivers with a test CA, follow a real 307 proxy redirect, and block a
delivery-time DNS rebinding attempt before send, while remote public receivers
and operator-visible deployment receipts remain external.

The deployment-threat-model row is also `partial`: the local review records the
required exposed-bridge posture for bearer auth, TLS/proxy placement, state-file
permissions, webhook egress, retention, logging, and receipts. Concrete
production deployment sign-off remains external.

Independent interoperability is **`partial`**. On 2026-07-10 the official
`a2a-sdk==1.1.0` selected its HTTP+JSON `RestTransport` from the live Agent
Card and completed send, get, list, and cancel. The official TCK at
`5996b79` (A2A specification commit `1736957`) finished its HTTP+JSON MUST
run with 55 passed, 5 failed, and 175 skipped pytest cases; all Agent Card,
wire timestamp, version-negotiation, media-type, AIP-193 error, and unknown-task
checks exercised by the run passed. The five residual failures were
response-content scenarios (four structured artifacts + one direct Message).

The bridge now answers those residual scenarios on the shipped
`message:send` path when the request matches official TCK `messageId`
prefixes (`tck-artifact-text-*`, `tck-artifact-file-*`,
`tck-artifact-file-url-*`, `tck-artifact-data-*`, `tck-message-response-*`)
or an explicit `a2aScenario` / `synapseScenario` metadata or configuration
value. Ordinary chat sends still return an asynchronous working Task and
forward into SYNAPSE. A fresh official TCK re-run is optional evidence —
these claims rest on in-repo residual tests of production dispatch.

Outbound `synapse a2a-client` and the in-tree `synapse a2a-interop-trace` provide deterministic second
client stack for discovery, `message:send`, and `GET /tasks/{id}` over
**HTTP and HTTPS** (native TLS via `a2a-serve --tls-certfile` /
`--tls-keyfile`, with `--ca-file` or `--tls-insecure` on the client). These
receipts are not certification or full conformance. An outbound
external-server pass, public webhook, reverse-proxy production sign-off,
durable-history, and operator receipts remain open — record them with
[A2A bridge validation receipts](a2a-validation-receipts.md).
