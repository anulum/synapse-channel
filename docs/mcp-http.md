<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — authenticated Streamable HTTP MCP
-->

# Authenticated HTTP MCP

`synapse mcp --transport streamable-http` exposes the existing coordination
actions through a private HTTPS listener. The default `synapse mcp` remains
stdio. The HTTP adapter uses MCP SDK **1.30.0**, with acceptance against protocol
**2025-11-25**. Install the optional runtime with
`python -m pip install 'synapse-channel[mcp]'`.

## Provisioning and transport

An operator provisions each issuer subject with one fixed native hub seat per
project. An HTTP client cannot choose that seat, a signing key, a project or a
local worktree. The native hub must independently enforce its identity trust
bundle, identity binding and applicable mutation permissions; follow
[identity and ACL](identity-and-acl.md). The bridge checks native admission
before opening HTTP service. A valid HTTP bearer does not authenticate another
native hub connection and is never forwarded to the hub.

The private profile binds only to a loopback IP, uses TLS directly, and ignores
forwarded scheme/identity headers. Use a separately configured private tunnel
to reach that listener. Certificate trust, tunnel access and issuer delivery
are operator responsibilities; this command does not deploy or expose a service.

```bash
synapse mcp --transport streamable-http \
  --project MY-PROJECT --uri ws://127.0.0.1:8876 \
  --http-auth-file /private/mcp/grants.json \
  --tls-cert-file /private/mcp/certificate.pem \
  --tls-key-file /private/mcp/key.pem \
  --http-host 127.0.0.1 --http-port 8888 \
  --http-allowed-host 'mcp.example.org:8888'
```

Use the actual certificate name and externally visible Host authority for your
tunnel. The TLS key and grant document must be regular owner-only, single-link
files. For a separately secured hub, add its owner-only `--token-file`; raw
`--token` is refused by the HTTP profile. Stdio identity, role and inbox flags
are also refused, because HTTP identities come exclusively from the grants.
Conversely, provisioning flags require the explicit HTTP transport and are
refused by stdio before ambient identity resolution.
Keep credentials out of command arguments, URLs, repository files and logs.

Repeat `--http-allowed-host` for each exact required Host value. The profile
refuses a universal wildcard. A supplied browser Origin must be explicitly
allowed with repeated `--http-allowed-origin`; wildcards are refused and no
browser Origin is allowed by default. Native clients may omit Origin. There
is no plaintext or WebSocket MCP endpoint.

## Issuer and grant contract

`--http-auth-file` is a JSON document with the following fields. Unknown fields,
duplicate JSON keys, invalid keys and shared seat assignments are refused.

| Field | Contract |
| --- | --- |
| `issuer` | Exact uncredentialed HTTPS issuer URL, without query or fragment. |
| `resource` | Exact uncredentialed HTTPS MCP audience, normally ending in `/mcp`. |
| `public_keys` | Map of issuer `kid` to Ed25519 public-key PEM; one to eight keys. |
| `subjects` | One to 32 provisioned issuer subjects. |
| `revoked_token_ids` | Optional list of revoked `jti` values, at most 4,096. |
| `max_token_age_seconds` | Maximum token age and lifetime, 30–3,600 seconds; default 900. |

Each subject has `projects`, optional `enabled` (default true), and optional
`revoked_before` (default zero, UTC epoch). Its `projects` map has at most 16
entries. Each project grant contains:

| Field | Contract |
| --- | --- |
| `seat` | Exact `PROJECT/seat` native identity, exclusively assigned to this subject. |
| `identity_key_file` | Operator-provisioned owner-only native signing-key file. |
| `identity_key_id` | Identifier already enrolled in the native hub trust bundle. |
| `task_prefix` | Exactly `PROJECT/`, including the terminating slash. |
| `tools` | Optional explicit action list; omission permits the five read tools below. |

The resource server verifies **EdDSA** only, using a configured `kid`. A token
must contain `iss`, `sub`, a single exact `aud`, integer `iat` and `exp`, `jti`,
`client_id`, and space-separated `scope`. It must be unexpired, within the
configured age/lifetime, and issued after the subject's `revoked_before`.
Scopes are limited to `synapse:read` and `synapse:mutate`; read is mandatory.
No token claim creates an operator grant.

The read defaults are `synapse_board`, `synapse_state`, `synapse_manifest`,
`synapse_directory` and `synapse_status`. To admit a mutation, add its exact
tool to the provisioned grant and issue a token with `synapse:mutate`. Supported
mutations are `synapse_task_declare`, `synapse_task_update`, `synapse_claim`,
`synapse_release`, `synapse_handoff` and `synapse_send`. A remote
`synapse_claim` must pass `task_only=true` and no `paths`. A file claim needs
local workspace authority, and a pathless claim would otherwise cover the
server's own worktree.

The process reloads current issuer keys, disabled subjects, revoked tokens and
tool removals on each request and again at operation dispatch. Replacing the
issuer/resource URL, seat or signing-key binding requires a restart. New tools
require reprovisioning; an existing session cannot acquire greater authority.
This is a resource-server verifier, not an OAuth authorization server. Public
protected-resource metadata points at the configured issuer; login, discovery,
consent and token delivery remain that issuer's responsibility.

## Project and retry semantics

Reads project the actual hub board, state and capability data. Foreign tasks,
claims, advertisements and resources are removed before resource rendering.
Unscoped tasks and tasks whose creator is outside the project are omitted.
Views may reflect the native hub's bounded snapshots and do not claim global
completeness. Imported descriptions and messages remain untrusted data.

Declare a project-qualified task before claiming it. Existing task references
must match both the actual board project and its native creator namespace;
dependencies must refer to existing scoped tasks. Recipients must be exact
project-qualified identities: broadcasts, globs and comma-separated targets
are refused. The HTTP face provides no local file claims, file receipts,
filesystem reads, shell execution, account ledger or operator actions.

Every mutation requires a stable request `_meta` entry:

```json
{"synapse/operation-id": "declare-task-20260927-1"}
```

Use a new identifier for a new operation and reuse the original identifier and
arguments for a retry. The native hub provides retained idempotency for board
and lease writes. Durable replay additionally requires a journaled hub; an
unjournaled hub does not establish cross-restart deduplication. Directed chat
uses native message identifiers and receiver deduplication, preserving its
at-least-once delivery semantics. A send confirmation establishes submission,
not recipient acknowledgement or completed business work.

HTTP sessions are isolated per provisioned subject. A stolen session identifier
with another valid subject token is refused. Reinitialise after an expired or
unknown session; session identifiers are transport state, not mutation keys.
The adapter does not promise durable SSE event replay. A timeout, cancellation
or lost reply does not prove that a native mutation failed to commit: retry
with the same operation identifier and inspect the actual task/receipt.
Native refusal or an unconfirmed mutation is an MCP error with generic details.

## Resource envelope and diagnostics

| Limit | Default | Allowed range |
| --- | --- | --- |
| Active HTTP requests, including SSE GETs | 32 | 1–128 |
| SDK sessions per provisioned subject | 8 | 1–32 |
| Request body | 65,536 bytes | 1,024–1,048,576 |
| Serialised action/resource content | 262,144 bytes | 1,024–1,048,576 |
| Operation timeout, including dispatcher wait | 15 seconds | Positive, at most 60 |
| Native startup admission timeout | 5 seconds | Positive, at most 30 |

The flags are `--http-max-requests`, `--http-max-sessions`,
`--http-request-bytes`, `--http-reply-bytes`, `--request-timeout` and
`--ready-timeout`. SDK session idle expiry is 300 seconds. One dispatcher per
subject serialises native correlation; bounded admission limits queued work.
The private CLI also bounds open connections, HTTP headers, listener backlog,
keep-alive and graceful shutdown. Access logs are disabled; startup failures
use content-free diagnostics. This is finite resource admission, not a
guarantee of availability against denial-of-service or compromised native hosts.

## Verified clients and limits

| Client surface | Evidence / support boundary |
| --- | --- |
| MCP Inspector CLI 2.8.0 | Real verified HTTPS: initialize at 2025-11-25, list tools, declare, claim, resource read and release, with effects checked in the native hub. |
| MCP Python SDK 1.30.0 | Repository HTTPS tests for authentication, scoped operations, session ownership, reconnect and journal reopen. |
| Installed Synapse HTTP CLI | Real subprocess TLS, native admission, project mutation/refusal and shutdown tests. |
| Desktop, hosted web and cloud applications | No HTTP acceptance established by these checks; require their own documented client qualification. |

Configure Inspector headers and request metadata in its owner-only config file,
using its [documented configuration surface](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md).
Never put its bearer in `--header` command arguments. The stdio host rows in
[the MCP guide](mcp.md) remain distinct. Protocol references:
[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).
