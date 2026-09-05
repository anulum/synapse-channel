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

Current `0.x` releases do not promise backward compatibility across minor
releases. Tiers describe *relative* stability within that line, not a 1.0
stability promise. Deliberate public API or wire changes must update the frozen
contract, changelog, and migration notes; wire-incompatible changes also bump
`WIRE_PROTOCOL_VERSION`. The post-1.0 contract is defined in
[API and wire stability](api-stability.md).

## Core Versus Optional Layers

The package stays installable as one tool, but operators should read the surface
as layered:

| Layer | Taxonomy tier | Boundary |
|---|---|---|
| Local coordination core | `stable` | Hub, presence, directed messaging, claims, leases, task state, locks, board, status, and bootstrap commands. |
| Edge adapters | `adapter` | Optional tool bridges for MCP, A2A, git, tmux/provider seats, shell hooks, ingestion, and workers. |
| Operator analysis | `analysis` | Diagnostics, dashboards, event queries, causality, multihub views, reliability, accounting, fleet scorecard export, manifests, and trust graph reporting. These commands do not mutate coordination state; explicitly selected export modes may write a file or contact an operator-owned collector. |
| Governance and integrity | `governance` | Policy, approvals, ACL/role commands, federation, Merkle roots, release evidence, reproduction, compaction, and key operations. |
| Lab surfaces | `experimental` | Benchmarking, participant fabric, route-task, sandbox, workflow, TTL advice, memory recall, auto-action, and resource bidding. |

Adapters and lab surfaces are useful, but they remain layers on top of or beside
the local bus. They do not pull heavy dependencies into the core, replace the
hub's event-sourced coordination model, or turn design-preview pages into shipped
runtime promises.

## Measurable usage profiles

Profiles make the first-use and package boundaries inspectable without hiding
any of the classified commands. `synapse commands` still lists everything;
`synapse commands --profile NAME [--json]` selects one measured view.

| Profile | Included surface | Optional dependency groups | Activation and deactivation boundary |
|---|---|---|---|
| `first-use` | `doctor` plus the self-contained `demo`; three concepts and three shell commands, hard-limited to eight concepts | None | Run the recorded journey. The demo stops its temporary hub; no persistent service starts implicitly. |
| `core` | `stable` + `analysis` | None | Base install. Start and stop only the exact hub, waiter, or dashboard process/unit selected by the operator. |
| `adapters` | `adapter` | `a2a-grpc`, `mcp`, `otel`, `semantic`, `wasm` as individually selected | Install the required extra and launch its command. Stop the process, remove its host launch entry, or omit its opt-in flag to deactivate it. |
| `governance` | `governance` | `cloud-hsm`, `encryption`, `pkcs11`, `sqlcipher`, `tpm2` as individually selected | Install the selected backend and pass its explicit policy/key option. Remove it only under that backend's migration and custody contract. |
| `labs` | `experimental` | `benchmark`, `wasm` as selected | Invoke the experimental command explicitly; no lab runs in the background by default. |
| `all` | Every tier | `all` | Installs aggregate runtime dependencies but activates nothing by itself. Use a clean core-only environment to remove the aggregate dependency boundary. |

The `first-use` JSON is the acceptance surface for the smaller golden path:
concept count, shell-command count, exact journey, extras, and implicit service
count are regression-bound to the real CLI. The profiles are additive discovery
and installation envelopes, not authorization or secure-deployment claims.

## Tiers

### Stable core — `stable`

Daily-safe coordination core with a stable wire and CLI surface.

`arm`  `board`  `channel`  `commands`  `completions`  `demo`  `fleet-init`  `hub`  `init`  `listen`  `lock`  `new`  `quickstart-coding`  `send`  `status`  `task`  `team`  `wait`  `who`

### Adapters — `adapter`

Bridges to other ecosystems and tools; optional extras, not core. These integrate
Synapse with A2A, MCP, git, tmux-driven agents, and model workers; some require
optional extras and none belongs to the single-dependency local core.

`a2a-card`  `a2a-client`  `a2a-conformance`  `a2a-interop-trace`  `a2a-serve`  `adapters`  `agent-tmux`  `codex-tmux`  `git-claim`  `git-claim-check`  `git-hook`  `git-init`  `git-release`  `ingest`  `install-shell-hook`  `mcp`  `mcp-call`  `mcp-tools`  `shell-hook`  `waker`  `worker`  `worker-session`

### Operator analysis — `analysis`

Inspection and reporting that never mutates the coordination plan or leases.
Explicit export modes can write an operator-selected file or collector endpoint;
they never silently enable telemetry or change hub authority.

`accounting`  `approvals`  `causality`  `conflicts`  `cross-repo`  `dashboard`  `dead-letters`  `debug`  `directory`  `doctor`  `event-query`  `fleet-scorecard`  `health`  `identity`  `manifest`  `multihub`  `pid-monitor`  `relay`  `reliability`  `setup`  `state`  `trust-graph`

### Advisory governance — `governance`

Advisory governance: policy, approvals, access control, and release integrity.
Most commands create, inspect, or verify policy material. Some of that material
is consumed by explicit runtime gates — notably `--require-acl` and
`--federation-store` — but running a governance command does not silently enable
enforcement or widen trust.

`acl`  `approval`  `capability-card`  `compact`  `encrypt-key`  `federation`  `merkle`  `policy-check`  `postmortem`  `release`  `reproduce`  `role`  `sqlcipher`  `supervisor`  `verify-release`

### Experimental — `experimental`

Newer or advisory surfaces still settling; shape may change before 1.0. Use them,
but pin to a version if you depend on their exact behaviour.

`auto-action`  `benchmark`  `deliberate`  `dispatch`  `memory-recall`  `participant`  `resource-bids`  `route-task`
`sandbox`  `ttl-advice`  `workflow`

## Architecture and staged-profile documentation

Some pages describe how shipped primitives compose with remaining architecture.
They are documentation rather than additional CLI verbs, and each page states
its own runtime boundary:

- [Agent Air Traffic Control](agent-air-traffic-control.md)
- [Cross-agent adapter kits](cross-agent-adapter-kits.md)
- [Federated trust model](federated-trust-model.md)
- [Multi-hub sync (CRDT) research](multi-hub-sync.md)
- [Sandboxed tools and marketplace](sandboxed-tools-and-marketplace.md)

Do not infer that an entire page is either shipped or absent from its title.
Federation policy and exchange, multi-hub observation, and the WASM sandbox now
have runtime surfaces; automatic cross-organisation trust, CRDT claim merging,
and the marketplace remain outside those shipped tranches.
