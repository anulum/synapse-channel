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
- `on(type, handler)` / `onMessage(handler)` — subscribe by `MessageType` or to every frame; each returns an unsubscribe function.
- `chat(payload, { target?, channel?, priority? })`, `claim(taskId, paths?)`, `release(taskId)`.
- `requestBoard()`, `requestWho()`, `requestState()`.
- `send(type, { target?, payload?, extra? })` for any other protocol frame.
- `close()`.

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
