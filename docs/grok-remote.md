<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — remote Grok connector
-->

# Remote Grok connector

A Grok client is one Synapse seat. On the same computer it uses the stdio
server in the [MCP guide](mcp.md). On another computer it uses the loopback
HTTPS listener in the [authenticated HTTP profile](mcp-http.md). The product
does not publish that listener and does not deploy a tunnel.

## One seat, one subject

Provision one issuer subject for the seat. The HTTP client cannot choose the
seat, the signing key, the project, or a worktree. A grant that gives one
subject two seats, or two subjects the same seat, is refused. The hub token
lifetime is `max_token_age_seconds`, from 30 to 3,600 seconds. A client that
stores a credential only once cannot use that rotating hub token as its saved
secret.

Run [`synapse mcp-gate`](mcp-gate.md) on the loopback host in front of the
listener. `synapse mcp-gate new-bearer` prints a stable bearer once for the
client to keep and stores only its digest. The gate checks that bearer on every
request and sends the seat's current hub token to the pinned listener. The seat's
entry in the gate configuration lists the tools the client may use. The stable
bearer stays out of the repository, the process arguments, the URL, and the
logs. Pass hub and issuer secrets with `--token-file` and the grant file, never
with `--token`.

The listener binds a loopback address and ignores forwarded scheme and identity
headers. Reach it through a tunnel that the operator configures. If that tunnel
is reachable from the public internet, the stable bearer is the client
authentication. Do not put the bearer in a webhook URL.

## Wakes

A directed message for a local Grok pane is delivered by `synapse waker`, which
runs `synapse agent-tmux` and submits a fixed prompt only when the pane is
idle. The prompt names the seat. It does not contain the message body.

A seat that is not a local pane keeps `synapse arm --directed-only --mailbox`.
That waiter stores directed mail. It does not type into a terminal. The remote
client reads its inbox with the MCP inbox tool. A third-party webhook is not
required and is not a supported wake path.

`synapse_status` includes `peers`, the project's online agent names from one
state snapshot, together with the counts. The remote HTTP status uses that
same project boundary for claims, resources and agents. Its mailbox count
comes from one bounded who snapshot; a missing snapshot leaves
`mailbox_pending_available` false and still returns the other fields.

## Journey

`python tools/grok_remote_journey.py` prints a JSON report and exits 0. A
missing `grok` or `synapse` executable, or a `synapse` whose interpreter
cannot import the MCP extra, is the verdict `not installed`. The script does
not contact a hub and does not read credential files.
