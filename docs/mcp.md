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

## Configure an MCP client

Point the host at the command. For a Claude Desktop / Claude Code style
`mcpServers` block:

```json
{
  "mcpServers": {
    "synapse": {
      "command": "synapse",
      "args": ["mcp", "--uri", "ws://localhost:8876"]
    }
  }
}
```

Add `"--token-file", "/path/to/token"` (or set `SYNAPSE_TOKEN` in the host's
environment) when the hub requires authentication. The adapter registers on the
hub under `--name` (default `synapse-mcp`); give each host its own name when
several share one hub.

## Tools

Each tool maps to one coordination verb and returns a short text result. Action
tools wait for the hub's grant or denial; query tools return JSON.

| Tool | Effect |
|---|---|
| `synapse_claim(task_id, paths?)` | Take a work lease, optionally scoped to file paths. |
| `synapse_release(task_id)` | Release a lease you hold. |
| `synapse_send(target, message)` | Send a chat to an agent, a group glob, or `all`. |
| `synapse_handoff(task_id, to_agent)` | Hand a held task to another online agent. |
| `synapse_task_declare(task_id, title, depends_on?)` | Declare or refine a task on the plan. |
| `synapse_task_update(task_id, status?, suggested_owner?)` | Update a plan task. |
| `synapse_board()` | Return the shared task/progress board as JSON. |
| `synapse_state()` | Return the live claims and checkpoints as JSON. |
| `synapse_manifest()` | Return the capability manifest of advertised agents as JSON. |

When the hub does not answer within the request window the tool returns a clear
"no response from the hub" line rather than hanging.

## Resources

Three read-only resources let an agent pull live coordination context without
issuing a tool call:

| Resource | Content |
|---|---|
| `synapse://board` | The shared task/progress blackboard. |
| `synapse://state` | Active claims and their resume checkpoints. |
| `synapse://manifest` | The capability cards of advertised agents. |

## What stays out of the hub

The adapter holds all MCP knowledge; the hub holds none. The MCP SDK is never a
core dependency, the hub protocol is unchanged, and the bridge translates each
MCP call into an ordinary hub message and correlates the reply. That keeps the
single-transport-dependency guarantee — and the local-first model — intact.
