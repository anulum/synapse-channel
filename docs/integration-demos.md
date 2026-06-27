<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Integration demo matrix

This page gives narrow, repeatable demos for common ways to place SYNAPSE under
existing agent tools. Each demo uses the current public CLI, A2A, or MCP surface
and keeps the boundary explicit: SYNAPSE coordinates claims, messages, board
state, and adapter calls; the surrounding agent host still owns prompting,
model choice, tool execution, and editor/runtime behavior.

External validation remains open. These demos are local evidence for the
documented surfaces, not certification for every external MCP host, A2A server,
reverse proxy, TLS stack, or webhook receiver.

| Demo | Status | Supported behavior | Unsupported or still open |
| --- | --- | --- | --- |
| CLI coding sessions | Supported | File-scope claims, claims release, board/status checks, direct messages. | Does not start or control the coding agent runtime. |
| MCP host adapter | Supported adapter surface | MCP tools/resources expose Synapse coordination through stdio. | Does not certify every MCP host, streaming, tool chaining, or resource templates. |
| Local A2A bridge | Local bridge surface | Agent Card projection plus local HTTP+JSON task/message routes. | Does not claim independent A2A conformance, remote TLS deployment, or real webhook receiver validation. |

## Demo 1: CLI coding sessions

Use this when an existing terminal coding agent, editor assistant, or manual
operator can run shell commands before touching a repository.

```bash
synapse hub --host 127.0.0.1 --port 8876
synapse git-init --name codex-1
synapse git-claim --task-id DEMO-CLI --paths src --name codex-1
synapse send --name codex-1 --target all "DEMO-CLI claimed src"
synapse state --owner codex-1
```

What this proves:

- A participant can claim a file scope before editing.
- Other participants can see the live lease through `synapse state`.
- Direct or broadcast messages can carry coordination context between tools.
- Git hooks installed by `synapse git-init` can release touched claims after a
  commit, while manual claims can still be dropped with `synapse release`.

What it does not prove:

- SYNAPSE does not launch or supervise the external coding agent in this flow.
- It does not inspect file contents or enforce editor permissions.
- It does not replace the agent host's own model, prompts, or tool policy.

## Demo 2: MCP host adapter

Use this when the host can run MCP servers over stdio, such as an editor
assistant or desktop agent host.

```bash
synapse hub --host 127.0.0.1 --port 8876
pip install 'synapse-channel[mcp]'
synapse mcp --uri ws://localhost:8876 --name claude-mcp
```

Configure the host to run the same command as an MCP server. Once connected, the
host can call the documented MCP tools for claims, releases, messages, handoffs,
board reads, state reads, and manifest reads. It can also read the
`synapse://board`, `synapse://state`, and `synapse://manifest` resources.

What this proves:

- The MCP adapter starts as a separate process and connects to the hub as one
  ordinary Synapse client.
- Coordination verbs are available to MCP-compatible hosts without changing the
  hub protocol.
- The core install keeps MCP as an optional extra.

What it does not prove:

- Streaming, tool chaining, and resource templates remain future MCP v2 work.
- External MCP host conformance is not certified by this local adapter demo.
- The host still decides when to call tools and how to interpret results.

## Demo 3: Local A2A bridge

Use this when a local tool wants an Agent Card or an HTTP+JSON bridge into a
running Synapse hub.

```bash
synapse hub --host 127.0.0.1 --port 8876
synapse a2a-card --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
```

Keep the bridge local-only unless you explicitly configure authentication and
deployment controls. The bridge refuses unsafe exposed defaults; do not bind it
off-loopback without bearer auth.

What this proves:

- A live capability manifest can be projected into an A2A-shaped Agent Card.
- Local HTTP+JSON message/task routes can bridge into a Synapse target.
- Local persistence, quotas, retention, auth checks, and SSRF guard behavior are
  covered by focused in-repo tests.

What it does not prove:

- Independent A2A interoperability validation remains open.
- Real webhook receiver validation remains open.
- TLS termination, reverse proxy behavior, and remote deployment threat-model
  review remain separate work.
