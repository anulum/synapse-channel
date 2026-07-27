<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# A2A deployment threat model

This review covers the `synapse a2a-serve` HTTP+JSON edge and its optional gRPC
listener. It is a deployment checklist and threat model for the bridge surface;
it is not an external A2A interoperability receipt and it is not a production
operator sign-off.

## Scope

In scope:

- Agent Card discovery, task routes, JSON-RPC, streaming, and push-notification
  configuration routes served by `synapse a2a-serve`.
- The optional JSON-over-gRPC `SendMessage` and `GetTask` methods enabled by
  `--grpc-port`.
- A reverse proxy or native process bind that exposes the A2A bridge to other
  hosts.
- Local bridge state written through `--state-file`.
- Outbound webhook delivery from the bridge to configured receiver URLs.
- Logs and receipts produced around the bridge boundary.

Out of scope:

- The core hub's WebSocket transport threat model; see
  [Deployment](deployment.md) and [Signed events and mTLS](signed-events-mtls.md).
- Third-party A2A client/server conformance.
- Public webhook receiver validation behind production TLS infrastructure.

## Assets

| Asset | Why it matters | Required handling |
| --- | --- | --- |
| A2A bearer token | Protects task, RPC, extended-card, and push-config routes. | Enable `--bearer-auth --a2a-token` with native HTTPS (`--tls-certfile` / `--tls-keyfile`) or a loopback bind behind a TLS-terminating proxy (or pass `--insecure-off-loopback` only when accepting cleartext bearer traffic). |
| Hub token | Lets the bridge connect to a secured Synapse hub. | Pass with `--token`; do not expose it through proxy logs or shell history. |
| Task payloads and artifacts | May carry operator or agent data. | Keep bridge state local, bounded, and owner-readable only. |
| A2A state file | Persists task and push-config state across restart. | Store it on a local trusted filesystem; rely on owner-only temp/state writes. |
| Webhook URLs and headers | Control bridge egress to external receivers. | Treat them as egress policy inputs; keep SSRF checks enabled. |
| Access logs and validation receipts | Prove deployment behavior but can reveal metadata. | Record route, status, timing, and decision; avoid payload and token logging. |

## Trust Boundaries

| Boundary | Main risk | Shipped control | Operator duty |
| --- | --- | --- | --- |
| A2A client -> reverse proxy -> bridge | Untrusted clients submit task or push-config requests. | Non-loopback bind refuses without bearer auth; non-loopback bind with bearer over plaintext HTTP also refuses unless native TLS or `--insecure-off-loopback` is set (hub R4 parity). | Prefer loopback + proxy TLS, or native `--tls-certfile`/`--tls-keyfile`; require bearer auth for protected routes. |
| A2A gRPC client -> bridge | The optional listener can submit messages and read tasks. | `--grpc-port` is absent by default. When enabled, the CLI composes its shared bearer, native TLS/mTLS files, concurrency ceiling, one-MiB message bounds, bounded JSON parser, finite deadline ceiling, and stable errors into gRPC. | Supply the bearer and a deadline from every client. Use native TLS/mTLS or an explicitly configured gRPC-capable TLS proxy for exposure; the shared bearer is not per-client identity. |
| Bridge -> Synapse hub | Bridge forwards task text/data/file parts into Synapse chat. | Bridge uses the configured hub URI and optional hub token. | Point the bridge only at the intended hub and target. |
| Bridge -> webhook receiver | A client can configure outbound webhook targets. | Delivery resolves each target once and pins the connection to that validated address (no re-resolve between check and connect), admits only globally routable destinations — rejecting loopback, private, link-local, carrier-grade NAT, multicast, reserved, and unspecified addresses including IPv4-mapped IPv6 — applies the same policy to redirect targets, ignores environment proxies, and bounds the discarded response body. | Permit only receiver domains that match the deployment policy; review redirects. |
| Bridge -> local filesystem | State persistence can leak task metadata if permissions are loose. | A2A state and temp files are owner-only and writes replace atomically. | Place `--state-file` on a trusted local disk, not a shared web root. |
| Bridge logs -> operators | Logs can leak bearer tokens or task payloads. | The stdlib handler suppresses default access logging. | If a proxy logs requests, redact `Authorization` and avoid body logging. |

## Current gRPC activation boundary

`--grpc-port` is absent by default and requires the optional `a2a-grpc` extra.
When enabled, the integrated `synapse a2a-serve` command starts the
`synapse.a2a.v1.A2ABridge` JSON-over-gRPC service on the same `--host` and
advertises `grpcs://` when native TLS is selected and `grpc://` otherwise.

The CLI composes `--bearer-auth --a2a-token`, the native TLS certificate/key,
the optional mTLS client CA, `--max-concurrent-requests`, and
`--request-read-timeout` into a typed gRPC policy. Missing or wrong bearer
metadata is refused before either method reaches the bridge. Requests and
responses are capped at one MiB; JSON nesting uses the same bounded parser as
the hub/HTTP edge; `maximum_concurrent_rpcs` enforces admission and recovers
capacity after a call exits; every call must include a finite deadline no
longer than the configured ceiling; pending hub work is cancelled when that
deadline expires; and errors contain stable value-free details. The shipped
client applies a finite deadline and accepts bearer and channel credentials
explicitly.

Host/Origin policy is specific to the HTTP/browser boundary and is not a gRPC
authentication mechanism. The shared bearer authorises both shipped methods;
it does not establish per-client identity or a method-level ACL. A
TLS-terminating HTTP reverse proxy protects gRPC only if it is separately
configured to proxy the gRPC listener.
This binding is a custom two-method JSON-over-gRPC subset, not the generated
official A2A protobuf contract. It remains `partial`; do not claim official
interoperability or certification from the policy repair.

### Effective gRPC profile matrix

| Operator profile | gRPC result | Effective security boundary |
| --- | --- | --- |
| No `--grpc-port` | No gRPC listener or advertised interface | Safe default; capability remains installed but inactive. |
| Loopback + `--grpc-port`, no bearer/TLS | Explicit plaintext local listener | One-MiB bounds, parser, admission, deadlines, cancellation, and stable errors apply; every local process remains inside the trust boundary. |
| Loopback + bearer | Plaintext listener requiring bearer metadata | Missing, wrong, empty, or duplicate bearer metadata is refused before bridge effects; use TLS as well if traffic crosses a transport boundary. |
| Native TLS + bearer | `grpcs://` listener requiring trusted server TLS and bearer metadata | Plaintext clients fail; bearer remains a shared credential. |
| Native mTLS + bearer | `grpcs://` listener requiring a client certificate rooted in the configured CA plus bearer metadata | Missing or untrusted client certificates fail during transport setup; the bearer still authorises both shipped methods. |
| Non-loopback without the existing HTTP exposure prerequisites | Startup refused before listener creation | Same fail-closed bind gate as the HTTP edge. |
| Explicit `--insecure-off-loopback` override | Selected downgrade is allowed and warned | gRPC still applies bounds, deadlines, cancellation, and stable errors, but plaintext/no-bearer traffic is not made secure by those resource controls. |

## Required Exposed-Bridge Posture

Use this posture before accepting traffic from any host other than the local
operator machine. This reverse-proxy example protects the HTTP+JSON listener.
Add `--grpc-port` only when the proxy is also configured for gRPC and forwards
the bearer metadata, or use native TLS/mTLS for both listeners:

```bash
synapse a2a-serve \
  --uri ws://127.0.0.1:8876 \
  --token "$SYNAPSE_TOKEN" \
  --host 127.0.0.1 \
  --port 8877 \
  --endpoint-url https://agent.example.com/a2a/v1 \
  --bearer-auth \
  --a2a-token "$A2A_TOKEN" \
  --state-file /var/lib/synapse-channel/a2a-state.json \
  --task-timeout 300 \
  --subscribe-timeout 10
```

Put a TLS-terminating reverse proxy in front of the loopback bridge, **or** bind
with native HTTPS:

```bash
synapse a2a-serve \
  --host 0.0.0.0 \
  --port 8877 \
  --endpoint-url https://agent.example.com/a2a/v1 \
  --bearer-auth \
  --a2a-token "$A2A_TOKEN" \
  --tls-certfile /etc/synapse/a2a-fullchain.pem \
  --tls-keyfile /etc/synapse/a2a-privkey.pem
```

Binding off loopback with `--bearer-auth` but **without** native TLS still presents
the bearer over plaintext HTTP, so the process refuses that posture unless you
pass `--insecure-off-loopback`. Do not use that override for a shared or public
deployment; it exists only as an explicit local risk acceptance (same flag
semantics as the hub's plaintext-token refuse).

When a browser-based operator UI calls the bridge, add `--allow-origin` for each
exact concrete web origin that UI serves from (`scheme://host[:port]`). Opaque
`null` origins are refused because they do not identify one principal. The list
is an opt-in defence against DNS rebinding and drive-by requests: with it
configured, every request must address the exact Host authority advertised by
`--endpoint-url`, and any present `Origin` must be listed. Both checks run on
every route, including the public agent card, before authentication. A
non-browser client without `Origin` remains compatible only through that exact
Host boundary. A reverse proxy must therefore preserve the advertised Host. With
no list configured the check is a no-op.

## Route Policy

| Route class | Public without bearer auth | Protected posture |
| --- | --- | --- |
| Agent Card discovery | Acceptable when the card contains only intended public metadata. | Keep endpoint URLs and documentation links accurate. |
| Task, RPC, extended-card, and push-config routes | No. | Require `--bearer-auth --a2a-token`; compare bearer values through the bridge path. |
| Streaming and subscription routes | No. | Keep `--subscribe-timeout` bounded so one client cannot hold a worker indefinitely. |
| Push delivery | No inbound route by itself, but push config creates egress. | Keep webhook SSRF and redirect validation enabled; review receiver domains. |

## Abuse Cases And Controls

| Abuse case | Expected result |
| --- | --- |
| Client sends an over-large JSON body. | Bridge returns `413 Request body too large` before dispatch. |
| Client sends deeply nested JSON. | Bridge rejects the body through bounded JSON parsing before dispatch. |
| Client opens more concurrent HTTP requests than the configured ceiling. | Bridge admits at most `--max-concurrent-requests` in-flight handlers and answers extras with deterministic `503` (`A2A_HTTP_CAPACITY_EXHAUSTED`) without starting additional worker threads. Capacity is released on normal completion, parse error, timeout, disconnect, and handler exception. |
| Client stalls or incompletely delivers a declared request body. | Bridge applies `--request-read-timeout` as a wall-clock body-read deadline and returns deterministic `408` (`A2A_HTTP_READ_TIMEOUT`) before dispatch. |
| Client configures `localhost`, loopback, private, link-local, CGNAT, or other non-routable webhook URLs, or a name that rebinds to one after validation. | Delivery pins the once-resolved address and rejects any non-globally-routable target before the socket is opened. |
| Client configures a public-looking host that resolves to a local address. | Delivery rejects the target before sending. |
| Webhook receiver redirects to a local address. | Redirect handler validates and rejects the new target before following it. |
| Reverse proxy strips the `Authorization` header. | Protected routes fail authentication at the bridge. |
| A hostile web page in the operator's browser calls the loopback bridge (DNS rebinding / drive-by). | With `--allow-origin` configured, an unlisted or opaque `Origin` is refused `403 Forbidden`; a missing `Origin` still requires the exact advertised Host, so a rebound hostile authority is also refused. |
| Bridge restarts with open tasks. | Persisted non-terminal tasks recover as failed according to the local state policy. |

## Logging And Receipts

A deployment receipt should record:

- bridge command line with secrets redacted;
- proxy origin, TLS termination point, and forwarded host/path policy;
- whether `--bearer-auth --a2a-token` was enabled;
- state-file path class and filesystem ownership policy;
- webhook receiver allowlist or domain policy;
- negative tests for missing bearer auth, local webhook targets, DNS rebinding,
  redirect-to-local, oversize JSON, and bounded subscription;
- log-redaction evidence showing no bearer token or task payload in proxy or
  bridge logs.

Do not record task bodies, bearer tokens, hub tokens, or webhook credentials in
public receipts.

## Residual Risk

- The bridge uses bearer-token authorization, not per-client identity binding.
- The optional gRPC listener is default-off and composes the CLI's shared
  bearer, native TLS/mTLS, message, concurrency, deadline, parser, and error
  policy when enabled. The shared bearer authorises both methods and does not
  provide per-client identity or method-level ACLs. The custom protocol remains
  partial and requires independent exact-object clearance before a security
  release claim.
- Reverse-proxy TLS proves transport protection to the proxy boundary; it is not
  hub mTLS and does not authenticate Synapse agents.
- Subscription replay is local process memory, not a durable cross-restart
  event stream.
- Public webhook receiver behavior still needs a real deployment receipt behind
  production TLS and proxy infrastructure.
- Independent A2A clients/servers still need interoperability traces before any
  broader conformance claim.
