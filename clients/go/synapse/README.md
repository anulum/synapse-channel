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

	synapse "github.com/anulum/synapse-channel/clients/go/synapse"
)

func main() {
	client, err := synapse.NewClient("http://127.0.0.1:8765")
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

Use `WithBearerToken` when the HTTP surface requires a bearer token:

```go
client, err := synapse.NewClient(
	"http://127.0.0.1:8765",
	synapse.WithBearerToken("secret"),
)
```

## Test

```bash
go test ./...
```
