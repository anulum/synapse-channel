<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# MCP server face

The Model Context Protocol (MCP) is the emerging standard that lets an agent host
— Claude Desktop, Claude Code, an editor assistant — discover and call external
tools. The `synapse mcp` command exposes the hub through it, so **any
MCP-compatible agent coordinates through Synapse with no Synapse-specific code**:
it adds one server entry and gains tools to claim work, send messages, hand off
and declare tasks, and resources that read the shared board, state, and manifest
as live context.

## How it fits

`synapse mcp` runs an MCP server over stdio that is **itself a client of the
hub** — it opens one `SynapseAgent` connection and re-exposes the coordination
verbs as MCP tools and resources. The hub itself never learns about MCP: the face
is a separate adapter process, not a hub change, so the hub stays exactly as
local-first and dependency-light as before.

This makes MCP an interoperability edge, not a replacement layer. MCP-compatible
hosts such as Claude Code, Claude Desktop, Cursor, or other editor assistants
still own their prompt/runtime/editor behavior; SYNAPSE only supplies shared
coordination state through MCP tools and resources.

The MCP SDK is an **optional extra** — the core install keeps its single
`websockets` dependency:

```bash
pip install 'synapse-channel[mcp]'
```

Running `synapse mcp` without the extra prints a one-line install hint and exits
non-zero, so nothing fails silently.

For a bounded local walkthrough that places the MCP adapter beside the CLI and
A2A surfaces, see the [integration demo matrix](integration-demos.md).

## Connect your agent

Install the adapter once. It needs no shell hook:

```bash
python -m pip install 'synapse-channel[mcp]'
```

The adapter registers on the
hub under `--name`. An explicit name always wins. Without one, an agreeing
`SYN_PROJECT`/`SYN_IDENTITY` pair supplies the exact identity; otherwise the
git project becomes `<project>/mcp`. The command prints that resolution to
stderr, leaving stdout exclusively for MCP frames. Give every concurrent host
an explicit distinct name such as `my-repo/codex` or `my-repo/claude`.

### Claude Code

The shortest local-scope registration derives `<git-project>/mcp`:

```bash
claude mcp add synapse -- synapse mcp
```

For a multi-seat project, pin the host identity:

```bash
claude mcp add --scope local --transport stdio synapse \
  -- synapse mcp --name my-repo/claude
claude mcp get synapse
```

Use `--scope project` only when the team intends to commit a shared
`.mcp.json`; Claude Code asks each user to approve a project-scoped server.

### Codex CLI

```bash
codex mcp add synapse -- synapse mcp --name my-repo/codex
codex mcp list
```

Codex stores the stdio server in its MCP configuration. Use `--env
SYN_PROJECT=my-repo --env SYN_IDENTITY=my-repo/codex` before the `--` separator
if the server also needs those environment values.

### Cursor

Cursor reads project servers from `.cursor/mcp.json` and global servers from
`~/.cursor/mcp.json`. Copy the object from
[`examples/mcp/.mcp.json`](https://github.com/anulum/synapse-channel/blob/main/examples/mcp/.mcp.json), replace both
`YOUR_PROJECT`/`YOUR_CLIENT` placeholders, and save the same JSON body at the
Cursor path. Cursor lists the resulting tools under Available Tools.

### Claude Desktop and generic stdio hosts

Merge the same `mcpServers.synapse` object into the host's MCP configuration.
The transport-independent launch contract is:

```json
{
  "command": "synapse",
  "args": ["mcp", "--name", "my-repo/desktop"]
}
```

The checked-in [`examples/mcp/.mcp.json`](https://github.com/anulum/synapse-channel/blob/main/examples/mcp/.mcp.json) template
contains no secret. Add `"--token-file", "/owner-only/path/to/token"` to
`args`, or provide `SYNAPSE_TOKEN` through the host's private environment, when
the hub requires authentication. Never commit a raw token.

## Tools

Each tool maps to one coordination verb and returns a short text result. Action
tools wait for the hub's grant or denial; query tools return JSON.

| Tool | Effect |
|---|---|
| `synapse_claim(task_id, paths?)` | Take a work lease, optionally scoped to file paths. |
| `synapse_release(task_id)` | Release a lease you hold. |
| `synapse_send(target, message)` | Send a chat to an agent, a group glob, or `all`. |
| `synapse_inbox(limit?)` | Consume up to 1–100 local durable relay messages for this bridge identity as JSON. |
| `synapse_handoff(task_id, to_agent)` | Hand a held task to another online agent. |
| `synapse_task_declare(task_id, title, depends_on?)` | Declare or refine a task on the plan. |
| `synapse_task_update(task_id, status?, suggested_owner?)` | Update a plan task. |
| `synapse_board()` | Return the shared task/progress board as JSON. |
| `synapse_status()` | Return live roster, waiter, work, resource, and mailbox-pending counts as JSON. |
| `synapse_state()` | Return the live claims and checkpoints as JSON. |
| `synapse_manifest()` | Return the capability manifest of advertised agents as JSON. |
| `synapse_directory()` | Return the discovery-only capability directory as JSON. |
| `synapse_route_task(task_id, limit?, include_zero?, event_store?)` | Return advisory route recommendations for a board task as JSON. |
| `synapse_resource_bids(task_id, resource_kind?, limit?, include_zero?)` | Return advisory resource bids for a board task as JSON. |
| `synapse_memory_recall(event_store, query, limit?, since_seq?)` | Return deterministic local memory recall hits as JSON. |

When the hub does not answer within the request window the tool returns a clear
"no response from the hub" line rather than hanging.

`synapse_inbox` reads the hub host's local durable relay file (default
`$SYN_HOME/feed.ndjson`) through an owner-only per-identity cursor. It consumes
only complete lines, pages without skipping a remaining tail, and reports
`available: false` when the adapter cannot see that local file. For a custom
local layout pass `synapse mcp --inbox-feed PATH --inbox-cursor PATH`. A remote
MCP process does not pretend that a remote hub's file is locally available.

There is deliberately no MCP `synapse_lock(command)` tool. `synapse lock` owns
a local child process; exposing that wrapper would turn an MCP call into
arbitrary shell execution. Through MCP, call `synapse_claim(task_id, paths)`
before editing and `synapse_release(task_id)` after verification. That preserves
the lease while the host itself remains responsible for commands and file I/O.

## Wake and inbox pattern

Tool discovery is automatic after the host registers this server. Wake delivery
is separate: this adapter exposes tools and resources, but it does not implement
the vendor `claude/channel` extension, inject prompts into Codex or Cursor, or
start a provider turn. An idle client therefore does not react merely because a
message reached the hub.

Use the same honest loop in every host:

1. At session/turn start, call `synapse_status`, then `synapse_inbox` until
   `has_more` is false.
2. Keep a permanent receiver active for prompt delivery:

   ```bash
   synapse arm install --identity my-repo/codex --start
   ```

   Use the exact identity for that provider seat. On systems without Linux
   systemd, use the documented WSL or terminal wake bridge instead.
3. Treat the MCP server connection as tool availability, not as a waiter.
   `synapse_status` reports whether `<identity>-rx` is online and whether the
   durable hub has pending mailbox messages.

This split prevents a tool process from silently acknowledging provider work it
never surfaced.

## Resources

Four read-only resources let an agent pull live coordination context without
issuing a tool call:

| Resource | Content |
|---|---|
| `synapse://board` | The shared task/progress blackboard. |
| `synapse://state` | Active claims and their resume checkpoints. |
| `synapse://manifest` | The capability cards of advertised agents. |
| `synapse://directory` | Discovery-only directory joining capability cards and resource offers. |

Three read-only resource templates expose narrow dynamic views without adding
new tools:

| Resource template | Content |
|---|---|
| `synapse://task/{task_id}` | A single board task by id. |
| `synapse://agent/{agent}` | One agent's capability card plus resource offers. |
| `synapse://resource-kind/{kind}` | Resource offers matching one resource kind. |

The directory is a marketplace-shaped discovery surface, not an executable
marketplace. Its entries can help an agent host choose a likely worker or tool,
but they do not reserve capacity, authorize execution, or certify trust.
`synapse_route_task` uses the directory plus the shared board to rank likely
agents with deterministic local signals. It returns the reasons behind each
candidate. When `event_store` points at a local hub database, the tool also adds
positive release-receipt evidence with source task ids and event sequences. The
boundary stays the same: no claim, assignment, capacity reservation, permission
grant, agent grade, or trust certification happens through the route.

`synapse_resource_bids` uses the same directory and board snapshots to rank live
resource offers for a task. The JSON report includes resource id, provider,
kind, name, capacity, score, and reason codes. It is a marketplace-style
directory hint only: it does not reserve capacity, authorize execution, mutate
tasks, or certify provider trust.

`synapse_memory_recall` is a local event-store reader. It projects findings,
checkpoints, and handoffs into deterministic token matches and returns matched
hits with source sequence, event kind, source field, task id, actor, evidence
reference, and matched tokens. It does not create external embeddings, call an
outside service, certify truth, or mutate hub state.

The dynamic resource templates are MCP v2-style read-only views over the same
hub snapshots used by `synapse_board`, `synapse_manifest`, and `synapse_state`.
They provide narrower context retrieval for hosts that support resource
templates. They do not stream updates, chain tools, reserve resources, assign
work, or change the hub protocol.

## Official registry metadata

The repository ships [`server.json`](https://github.com/anulum/synapse-channel/blob/main/server.json) for
`io.github.anulum/synapse-channel`. It follows the official 2025-12-11 schema,
points at the PyPI package and stdio transport, and supplies a `uvx --with
mcp>=1.28.0` runtime hint. The `synapse-channel` console entry starts this MCP
face directly for package launchers; humans can keep using `synapse mcp`.

The official MCP Registry is still a preview and its published versions are
immutable. SYNAPSE CHANNEL is already active there: the live record was version
`0.99.2` when verified on 2026-07-13. Registry metadata does not automatically
follow PyPI or this repository, so the newer version in `server.json` is a
prepared update until the registry query returns that exact version. Check live
publication rather than inferring it from local metadata:

```bash
curl --get --data-urlencode \
  'search=io.github.anulum/synapse-channel' \
  https://registry.modelcontextprotocol.io/v0.1/servers
```

## Surface audit

The registered MCP tools and resources are checked against this guide by a
source parser:

```bash
.venv/bin/python tools/audit_mcp_surface.py --check
```

The audit fails when `src/synapse_channel/mcp/registration.py` exposes a tool or
resource that this page does not list, or when this page loses the adapter,
authentication, or optional-dependency boundary text. It verifies documentation
completeness for the local adapter surface; it does not certify external MCP
client conformance.

## What stays out of the hub

The adapter holds all MCP knowledge; the hub holds none. The MCP SDK is never a
core dependency, the hub protocol is unchanged, and the bridge translates each
MCP call into an ordinary hub message and correlates the reply. That keeps the
single-transport-dependency guarantee — and the local-first model — intact.

## Outbound: calling external MCP tools

The directions are independent. `synapse mcp` serves the hub *to* MCP clients;
`synapse mcp-tools` and `synapse mcp-call` let a Synapse operator *call* tools on
an external MCP server, with a deny-by-default trust boundary.

A JSON config names the allowed servers and, per server, the tools that may run
(`"*"` opts the whole server in). A server or tool that is not allowlisted is
refused before the server is contacted:

```json
{
  "servers": [
    {
      "name": "fs",
      "command": "mcp-server-filesystem",
      "args": ["--root", "/data"],
      "allowed_tools": ["read_file", "list_directory"]
    }
  ]
}
```

```bash
synapse mcp-tools fs --config mcp-allow.json
synapse mcp-call fs read_file --config mcp-allow.json --arg path="/data/notes.txt"
```

The `mcp` SDK is the optional `synapse-channel[mcp]` extra; the commands import it
only when a call is made. Per-agent ACLs over which identity may invoke outbound
MCP remain a later tranche — this tranche's boundary is the config allowlist.
