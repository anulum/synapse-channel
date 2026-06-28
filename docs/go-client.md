<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Go client

The official Go client lives in `clients/go/synapse`. It is a small read-only
client for ops tools and CI services that need to inspect a running SYNAPSE
dashboard snapshot.

It reads HTTP JSON surfaces. It does not implement the WebSocket mutation
protocol for claims, chat, board writes, release receipts, or presence. Use the
Python CLI/client or MCP/A2A adapters for those flows.

## Install

Use the module directly from this repository:

```bash
cd clients/go/synapse
go test ./...
```

Downstream tools can import:

```go
import synapse "github.com/anulum/synapse-channel/clients/go/synapse"
```

## Read a dashboard snapshot

Start the read-only dashboard beside a hub:

```bash
synapse dashboard --port 8765
```

Then read `/snapshot.json`:

```go
client, err := synapse.NewClient("http://127.0.0.1:8765")
if err != nil {
	return err
}

snapshot, err := client.DashboardSnapshot(ctx)
if err != nil {
	return err
}

for _, agent := range snapshot.OnlineAgents {
	fmt.Println(agent)
}
```

`DashboardSnapshot` decodes the stable dashboard keys:

- `OnlineAgents`
- `State`
- `Board`
- `Manifest`
- `Fleet`

Use `GetJSON` for another local HTTP JSON endpoint:

```go
var payload map[string]any
err := client.GetJSON(ctx, "/snapshot.json", &payload)
```

## Authentication

Pass `WithBearerToken` when the HTTP surface requires a bearer token:

```go
client, err := synapse.NewClient(
	"http://127.0.0.1:8765",
	synapse.WithBearerToken(os.Getenv("SYNAPSE_TOKEN")),
)
```

The client sends `Authorization: Bearer <token>` and returns a `StatusError`
when the endpoint rejects the request.

## Boundary

This client is intentionally dependency-free and read-only. It supports
operational inspection for CI jobs, status pages, and local tooling. It does not
claim work, release tasks, mutate the blackboard, or send messages.
