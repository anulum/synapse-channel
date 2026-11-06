<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — official-client interop lane
-->

# Official-client interop lane

The `interop` workflow runs the official MCP and A2A client libraries against
real Synapse processes. It runs the shipped `synapse hub`, `synapse mcp` and
`synapse a2a-serve` commands. It judges every journey by the hub's own journal,
not only by what the client reports.

## What runs

<!-- interop-matrix:start -->
| Client | Version | Transport | Journey | Identities |
| --- | --- | --- | --- | --- |
| `mcp-python-sdk` | 2.3.0 | stdio | claim-conflict-message-release | names asserted under one hub token |
| `mcp-python-sdk` | 2.3.0 | Streamable HTTP | claim-conflict-message-release | signed subjects mapped to seats |
| `mcp-typescript-sdk` | 1.32.2 | stdio | claim-conflict-message-release | names asserted under one hub token |
| `mcp-typescript-sdk` | 1.32.2 | Streamable HTTP | claim-conflict-message-release | signed subjects mapped to seats |
| `a2a-sdk` | 1.2.3 | HTTP+JSON | card-send-get-list-cancel | bearer-protected bridge |
<!-- interop-matrix:end -->

**claim-conflict-message-release** runs these steps:

1. initialize, then list the tools and resources;
2. the first identity claims a task;
3. the second identity claims the same task and is refused;
4. the first identity sends the second a directed message;
5. the first identity reads the board resource and releases the task.

The journal must then hold the following:

- the grant to the first identity;
- the live-lease denial of the second identity, with no release in between;
- the chat;
- the release.

The denial record is content-minimised and does not name the holder. The
grant record that is still live proves who held the task.

The two transports prove different things:

- **stdio** shows protocol interop only. Its two identities are names that
  both connect under one hub token, so it does not show per-identity
  authentication.
- **Streamable HTTP** is the authenticated case. Each subject presents its own
  signed access token, and the hub requires each seat to prove its enrolled
  identity key.

The MCP clients are the official Python SDK (`mcp` on PyPI) and the official
TypeScript SDK (`@modelcontextprotocol/sdk` on npm). The A2A client is the
official `a2a-sdk` on PyPI.

**card-send-get-list-cancel** works as follows:

1. The client resolves the Agent Card.
2. Over the HTTP+JSON transport the SDK selects from it, the client:
   - sends a message;
   - gets, lists and cancels the task.
3. A raw HTTP request with a browser `Origin` header, not made through the SDK
   because the SDK sends no Origin, must be refused.

The journal must hold the forwarded chat from the bridge to its target, and
that chat must carry the A2A task id the client saw.

## How a journey can fail

Each journey also runs twice as a negative control:

- **surface disabled.** The hub or the server refuses the client. The journey
  must fail on a client step. Each transport is refused its own way:
  - stdio runs against a hub that expects another token;
  - MCP over HTTP presents a token for another audience;
  - A2A presents a bearer the bridge does not know.
- **journal oracle.** The client steps run unchanged and succeed, but the
  journal assertions read an unrelated, untouched store. The journey must fail
  on the first journal assertion: `journal-claim-granted` for MCP, or
  `journal-chat` for A2A. Every journal assertion is positive, so an empty
  store cannot pass.

The lane fails when a real journey fails, or when a control does not fail the
way it must.

## Evidence

Each run uploads `interop-results.json`, which records:

- the client and its exact version;
- the transport;
- each step's route, its outcome code, and the key shape of the request and
  the response;
- the SHA-256 of the journal rows the journey asserted;
- the commit and the run URL.

It never records message bodies, error text, tokens or key material. The
recorder refuses to write a record in which one of the run's secrets appears.

## Limits

- Only the versions in the table are tested.
- This is protocol interop with the client libraries, not host interop. No
  claim is made about Claude Code, Codex or any other host's model turns. Host
  versions are reviewed by hand and listed in the
  [integration catalog](integration-catalog.md).

## Pins and updates

Each client is pinned to the newest release that was at least 14 days old when
it was pinned. The pins live only in the files CI installs from:

- `.github/requirements/requirements-dev.txt` for `mcp`;
- `clients/interop-mcp-ts/package-lock.json` for the TypeScript SDK;
- `.github/requirements/requirements-interop-clients.txt` for `a2a-sdk`.

A test fails when this page names any other version. Each run reports a newer
upstream release as a notice and never changes a pin. A re-pin is a reviewed
change.

## Run it locally

From a checkout with the `mcp` extra installed:

```bash
python -m venv /tmp/a2a-client
/tmp/a2a-client/bin/python -m pip install --require-hashes \
  -r .github/requirements/requirements-interop-clients.txt
(cd clients/interop-mcp-ts && npm ci --ignore-scripts && npx --no-install tsc -p tsconfig.json)
python -m tools.interop_lane --out /tmp/interop-results.json \
  --mcp-ts --a2a-python /tmp/a2a-client/bin/python
```

The lane prints one line per row and mode, and exits non-zero when any outcome
is not the one required.
