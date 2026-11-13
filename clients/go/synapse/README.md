<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
Copyright Concepts 1996-2026 Miroslav Sotek. All rights reserved.
Copyright Code 2020-2026 Miroslav Sotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE CHANNEL Go Client

Official Go client for read-only ops and CI tooling.

The client targets HTTP JSON surfaces such as `synapse dashboard` at
`/snapshot.json`. It does not implement the WebSocket mutation protocol for
claims, chat, board writes, release receipts, or hub presence.

## Use

```go
package main

import (
	"context"
	"fmt"
	"log"
	"os"

	synapse "github.com/anulum/synapse-channel/clients/go/synapse"
)

func main() {
	client, err := synapse.NewClient(
		"http://127.0.0.1:8765",
		synapse.WithBearerToken(os.Getenv("DASHBOARD_TOKEN")),
	)
	if err != nil {
		log.Fatal(err)
	}

	snapshot, err := client.DashboardSnapshot(context.Background())
	if err != nil {
		log.Fatal(err)
	}

	fmt.Println(snapshot.OnlineAgents)
}
```

The dashboard requires its bearer on loopback too. Set `DASHBOARD_TOKEN` from
the operator-managed token or the dashboard's generated startup token. For
other HTTP surfaces, use `WithBearerToken` when their contract requires it:

```go
client, err := synapse.NewClient(
	"http://127.0.0.1:8765",
	synapse.WithBearerToken(os.Getenv("DASHBOARD_TOKEN")),
)
```

The snapshot is bounded: `Board` carries every task that is not closed and
the newest progress notes, and `Bounds` states the exact totals, including how
many closed tasks were left out. `DashboardSnapshotWithClosed` requests
`/snapshot.json?closed=1`, which adds them. `GetJSON` keeps a query as a query,
so `client.GetJSON(ctx, "/findings.json?limit=20", &payload)` reads one page of
a feed. `Headline` holds the counters the hub computed once on the whole board,
keyed by name; read them instead of counting the bounded `Board`.

## Test

```bash
go test ./...
```
