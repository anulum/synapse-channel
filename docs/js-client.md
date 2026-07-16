<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# TypeScript/JavaScript client

The official TypeScript/JavaScript client lives in `clients/js`. Unlike the
read-only [Go client](go-client.md), it speaks the WebSocket mutation protocol:
chat, claims, releases, board reads, presence, and receipts. It runs unchanged in
the browser and in Node 20+ (both expose a global `WebSocket`) and has no runtime
dependencies.

## Install

```bash
npm install @anulum/synapse-channel
```

## Connect and coordinate

```ts
import { SynapseClient, MessageType } from "@anulum/synapse-channel";

const client = new SynapseClient({
  uri: "ws://127.0.0.1:8876",
  name: "SYNAPSE-CHANNEL/web-agent",
  token: process.env.SYNAPSE_TOKEN,
});

client.on(MessageType.Chat, (m) => console.log(`${m.sender}: ${m.payload}`));
await client.connect();

client.chat("hello", { target: "all" });
client.claim("synapse-channel:web", ["src/web/**"]);
client.requestBoard();
client.release("synapse-channel:web");
client.close();
```

`connect()` opens the socket, sends the registration heartbeat (with the token
when one is configured), and resolves once the hub returns its welcome; it
rejects if the hub closes the socket before welcoming the identity or if no
welcome arrives within `readyTimeoutMs`.

## Scope and boundaries

The client implements the agent-side envelope and the connection lifecycle:
registration, keepalive heartbeats, typed send helpers, and inbound dispatch by
`MessageType`. It does not run the hub, does not enforce ACLs, and does not verify
per-message authentication — those are hub-side. On a secured or ACL-enforcing
hub, supply the connect `token`; namespace authorisation still depends on the hub
binding the sender, so use a token (and per-message auth) on an exposed hub.

It is a separate npm package and does not ship inside the Python `synapse-channel`
distribution.
