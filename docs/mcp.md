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
| `synapse_directory()` | Return the discovery-only capability directory as JSON. |
| `synapse_route_task(task_id, limit?, include_zero?, event_store?)` | Return advisory route recommendations for a board task as JSON. |
| `synapse_resource_bids(task_id, resource_kind?, limit?, include_zero?)` | Return advisory resource bids for a board task as JSON. |
| `synapse_memory_recall(event_store, query, limit?, since_seq?)` | Return deterministic local memory recall hits as JSON. |

When the hub does not answer within the request window the tool returns a clear
"no response from the hub" line rather than hanging.

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
