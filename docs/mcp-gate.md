<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — stable-bearer gate for the HTTP MCP listener
-->

# Stable-bearer MCP gate

The [authenticated HTTP MCP listener](mcp-http.md) accepts only issuer tokens that
live at most an hour. Some remote clients can keep only one static secret.
`synapse mcp-gate` stands in front of the listener for such a client:

1. It checks a stable bearer on every request.
2. It replaces the bearer with the seat's current issuer token.
3. It forwards one request per connection to the listener.

The gate holds no issuer key and grants nothing. The listener still verifies the
token, the seat binding and the grants.

```bash
synapse mcp-gate new-bearer --secret-out /private/mcp/bot.secret \
  --config /private/mcp/gate.json --seat PROJECT/bot
synapse mcp-gate serve --config /private/mcp/gate.json
```

`new-bearer` requires exactly one of `--secret-out FILE` and `--print-secret`.
`--secret-out` writes the 256-bit bearer to a new owner-only file and prints
only that path. `--print-secret` prints it once, for the operator to give the
client. The old form, which printed the secret with no flag, now refuses, and
the message names both options. The command stores only the SHA-256 digest
with an issue and an expiry time 90 days later. `--replace` rotates it, which
revokes the old one. The gate warns from 14 days before the expiry and refuses
the bearer after it.

## Configuration

The configuration is an owner-only JSON file, `synapse-mcp-gate.v1`:

```json
{
  "schema": "synapse-mcp-gate.v1",
  "listen": {"host": "127.0.0.1", "port": 8889},
  "upstream": "https://127.0.0.1:8888",
  "upstream_cert_sha256": "<SHA-256 of the listener's DER certificate>",
  "public_host": "gate.example.net",
  "tls_cert_file": "/private/mcp/gate.crt",
  "tls_key_file": "/private/mcp/gate.key",
  "grant_file": "/private/mcp/grants.json",
  "seats": [
    {
      "seat": "PROJECT/bot",
      "bearer_file": "/private/mcp/bot.bearer",
      "token_file": "/private/mcp/bot.token",
      "tools": ["synapse_status", "synapse_inbox_page", "synapse_send"]
    }
  ]
}
```

- **Addresses:** the gate and the listener both bind loopback addresses. An operator's tunnel reaches the gate.
- **The upstream pin:** `upstream_cert_sha256` is required. A listener that presents another certificate gets no byte
  of the request, so a process that takes the port while the listener is down collects no token.
- **`public_host`** is the `Host` the listener's resource URL expects.
- **`grant_file`** is the listener's `--http-auth-file`, and **`tools`** is the operator's explicit list of the tools
  each seat may hold. The gate refuses to start when a configured seat is not granted there, or when the listener
  grants it a tool its entry does not list. While the gate is running it states that file before each request.
  The same inode, size and mtime is not read again. A change is checked the same way as at start; a file that now
  grants a tool the seat's entry does not list answers `503`. A tool that changes work (`synapse_claim`, `synapse_release`,
  `synapse_handoff`, `synapse_task_declare`, `synapse_task_update`) is therefore held through the gate only when the
  operator writes it into that seat's list, and a tool added to the profile later is refused until it is listed.
- **`token_file`** is read at every request. The operator's renewal job rewrites it with a temporary file and a
  rename. A missing, malformed or expired token gives `503`, as does a token the listener refuses. That keeps an
  operator's renewal failure apart from a wrong bearer (`401`).

## Request rules

| Rule | Behaviour |
| --- | --- |
| The bearer | Required on every request, as exactly one `Authorization: Bearer` header. Its digest is compared with every seat's digest. A missing or broken bearer record revokes that seat. |
| Sessions | An MCP session id is never a credential. A session the gate saw with another seat is refused. An unknown session is forwarded with the requesting seat's token, and the listener decides. |
| Headers | Only `Content-Type`, `Accept`, `Mcp-Session-Id`, `MCP-Protocol-Version`, `Last-Event-ID` and `User-Agent` are forwarded. The gate sets `Host`, `Authorization`, `Content-Length` and `Connection: close`. |
| Framing | Origin-form targets only; no `CONNECT` or `TRACE`. Exactly one `Content-Length` of at most seven digits, or `Transfer-Encoding: chunked` alone with hex chunk sizes of at most eight digits; at most 1 MiB, forwarded with an exact `Content-Length`. Folded lines, NUL, bare CR, duplicate `Host` and every other framing give `400`. |
| Deadlines | 10 s for the TLS handshake and the whole head, 30 s for the whole body. Event streams close after 120 s of silence. |
| Bounds | 64 KiB heads (`431` above), 16 MiB buffered responses (`502` above). A `Content-Length` wider than the decimal width of that 16 MiB bound is the same `502` and is not parsed as an integer. 32 connections, 1,024 remembered sessions. After 20 failed authentications in 10 s, each refusal waits a second. |
| Logs | Method, path without its query, seat and status. Never a bearer, a token, a body or a header value. |

## Exposure

A tunnel that reaches the gate from the public internet makes the stable bearer
the client's only authentication. Whoever holds the bearer holds the gated
seat's listed grants until the bearer is rotated (`new-bearer --replace`) or its
record is removed. List only the tools the client needs. The HTTP profile itself
has no spend, operator or approval tool.
