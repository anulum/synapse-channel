<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Public surface and stability

The CLI has dozens of subcommands, and they do not all carry the same weight.
This page classifies the whole surface into stability tiers so the daily-safe
core is obvious and an experimental verb is never mistaken for a load-bearing
one. The authoritative map lives in `synapse_channel/surface_taxonomy.py`, and a
regression test asserts it and the live parser agree — a new subcommand cannot
ship without being placed here, and a removed one cannot linger.

This is still a `0.x` line. Tiers describe *relative* stability within that line,
not a 1.0 stability promise: the wire protocol and the public Python API stay
backwards-compatible within a major version, and any breaking change is called
out in the changelog.

## Tiers

### Stable core — `stable`

Daily-safe coordination core with a stable wire and CLI surface.

`arm`  `board`  `channel`  `demo`  `hub`  `init`  `listen`  `lock`  `new`  `quickstart-coding`  `send`  `task`  `team`  `wait`  `who`

### Adapters — `adapter`

Bridges to other ecosystems and tools; optional extras, not core. These integrate
Synapse with A2A, MCP, git, tmux-driven agents, and model workers; some require
optional extras and none belongs to the single-dependency local core.

`a2a-card`  `a2a-serve`  `agent-tmux`  `codex-tmux`  `git-claim`  `git-hook`  `git-init`  `git-release`  `ingest`  `install-shell-hook`  `mcp`  `mcp-call`  `mcp-tools`  `shell-hook`  `worker`  `worker-session`

### Read-only analysis — `analysis`

Read-only inspection and reporting with no coordination side effects. Safe to run
at any time; they observe state and never mutate the plan or leases.

`accounting`  `conflicts`  `dashboard`  `directory`  `doctor`  `event-query`  `health`  `identity`  `manifest`  `relay`  `reliability`  `state`

### Advisory governance — `governance`

Advisory governance: policy, approvals, access control, and release integrity.
These express and check intent; they advise and record rather than enforce at the
transport layer.

`acl`  `approval`  `compact`  `encrypt-key`  `policy-check`  `postmortem`  `release`  `supervisor`  `verify-release`

### Experimental — `experimental`

Newer or advisory surfaces still settling; shape may change before 1.0. Use them,
but pin to a version if you depend on their exact behaviour.

`memory-recall`  `resource-bids`  `route-task`  `ttl-advice`  `workflow`

## Design-preview documentation

Some documentation pages describe designs that are intentionally **not yet
implemented** — research and architecture written down before any code, so the
boundary is explicit. They are documentation, never CLI surface:

- [Agent Air Traffic Control](agent-air-traffic-control.md)
- [Cross-agent adapter kits](cross-agent-adapter-kits.md)
- [Federated trust model](federated-trust-model.md)
- [Multi-hub sync (CRDT) research](multi-hub-sync.md)
- [Sandboxed tools and marketplace](sandboxed-tools-and-marketplace.md)

Each such page states that it is not implemented; treat it as direction, not a
shipped feature.
