<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE CHANNEL TypeScript/JavaScript client

Official typed WebSocket client for the coordination hub. Unlike the read-only Go
client, this client speaks the WebSocket mutation protocol: chat, claims,
releases, board reads, presence, and receipts. It runs unchanged in the browser
and in Node 20+ (both expose a global `WebSocket`), with no runtime dependencies.

## Install

```bash
npm install @anulum/synapse-channel
```

## Use

```ts
import { SynapseClient, MessageType } from "@anulum/synapse-channel";

const client = new SynapseClient({
  uri: "ws://127.0.0.1:8876",
  name: "SYNAPSE-CHANNEL/web-agent",
  token: process.env.SYNAPSE_TOKEN, // omit for an open loopback hub
});

client.on(MessageType.Chat, (message) => {
  console.log(`${message.sender}: ${message.payload}`);
});
client.on(MessageType.ClaimDenied, (message) => {
  console.warn("claim denied:", message.payload);
});

await client.connect(); // resolves when the hub welcomes the registration

client.chat("hello from the browser", { target: "all" });
client.claim("synapse-channel:web", ["src/web/**"]);
client.requestBoard();
// ...later
client.release("synapse-channel:web");
client.close();
```

## API

- `new SynapseClient({ uri, name, token?, takeover?, heartbeatIntervalMs?, readyTimeoutMs? })`
- `connect(): Promise<void>` — opens the socket, registers the identity, resolves on the hub welcome.
  One call is one socket generation: after a hub close, error, welcome timeout or `close()` the
  client is not ready and the same instance may `connect()` again; a call while a socket is open
  or pending rejects.
- `on(type, handler)` / `onMessage(handler)` — subscribe by `MessageType` or to every frame; each returns an unsubscribe function.
- `chat(payload, { target?, channel?, priority? })`,
  `claim(taskId, paths?, pathIdentity?)`, `release(taskId)`. The optional
  `ClaimScopeIdentity` is for bridges carrying output from the trusted Python
  Git/filesystem resolver; its worktree path is sent automatically. Omit it
  rather than inventing canonical values.
- `requestBoard()`, `requestWho()`, `requestState()`.
- `send(type, { target?, payload?, extra? })` for any other protocol frame.
- `close()` — closes the socket, stops heartbeats, leaves `isReady` false and rejects a
  `connect()` still awaiting its welcome.

## Develop

```bash
cd clients/js
npm install
npm run typecheck   # strict tsc
npm test            # vitest
npm run build       # emit dist/
```

This client is a separate npm package; it does not ship inside the Python
`synapse-channel` distribution.

For real-protocol integration, use Node 22+ and a Python interpreter with the
repository's Python dependencies installed:

```bash
SYNAPSE_TEST_PYTHON=../../.venv/bin/python npm run test:integration
```

This builds the SDK and starts a temporary authenticated Python hub on an
OS-assigned loopback port. It checks rejected authentication, directed chat,
conflicting claims, release, snapshots, and reconnecting the same client.
The fixture uses checkout source, no persistent journal, and no running Synapse
service or user data. Its child process is stopped after the test.

The `clients-js` CI workflow runs this check with Node 22 and Python 3.12,
alongside type checking and unit tests. Changes to the Python source, package
configuration, or its hash-locked test dependencies also trigger the workflow.
