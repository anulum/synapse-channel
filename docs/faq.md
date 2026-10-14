# FAQ

## What is SYNAPSE CHANNEL, in one sentence?

A local-first coordination bus for a fleet of AI agents working in parallel: a single
WebSocket hub is the shared source of truth for presence, file-scoped work claims, chat,
task status, a shared plan, capabilities, and resource offers.

## What problem does it actually solve?

When more than one agent works at once — on one repository or across many — they collide:
two edit the same file, two pick up the same task, work is duplicated, and state is lost on
a crash. SYNAPSE gives them one place to claim a file scope before editing, share a plan,
hand off, and resume after a restart. It coordinates agents; it does not run them.

## How is it different from CrewAI, LangGraph, or AutoGen?

Those are **orchestration frameworks** — they define and drive an agent's control flow
inside one process. SYNAPSE is a **coordination substrate** *between* independently running
agents (and humans), across processes and repositories. It is complementary: an agent built
with any of them can claim work and share presence over SYNAPSE. See the
[comparison](comparison.md) for the detail.

The same boundary applies to coding-agent surfaces such as Claude Code, Codex, Cursor,
Copilot, and Aider: keep using them as the runtime/editor interface, and use SYNAPSE for
claims, presence, directed messages, shared plans, and durable coordination around them.

## Does it need a cloud account or a hosted service?

Base coordination needs no cloud account: it runs over a local WebSocket hub
with `websockets`, plus `typing-extensions` on Python 3.10. Optional workers,
adapters and external services have their own dependency, account, network and
cost requirements. Core coordination does not implicitly emit telemetry;
`synapse --version` is network-silent by default. Set `SYNAPSE_UPDATE_CHECK=1`
only if you want the synchronous PyPI newer-release check. A successful cached
result is reused for 24 hours; an absent or stale cache can trigger another
request on each invocation, including after a failed refresh. See the
[installation profile](installation.md) for the timeout and cache boundaries.

## Can a human use it, or is it only for agents?

Both. The `syn` commands (`syn arm`, `syn say`, `syn inbox`, `syn board`) are a thin,
identity-correct front end for the short loop a person or an agent runs each session, and
`synapse send` / `synapse listen` script cleanly.

## Which models can the workers use?

Use the [documented provider profiles](provider_matrix.md), local Ollama or a
deterministic rule backend. `tiered` selects a configured backend by task class.
Named paid providers require the explicit admission flags, budget and current
price quote described in [provider pricing](provider_pricing.md); protocol-fixture
acceptance is distinct from a live vendor/account qualification.

## Is it production-ready? What are the limits?

The current `0.x` line is pre-1.0. A configured healthy journal supports replay
within retained history, integrity/key configuration and lease expiry. Each
hub remains one authority; peer observation and forwarding do not create
automatic failover or consensus. Shared-token access and opt-in cryptographic
identity, signatures and TLS have distinct [documented boundaries](identity-and-acl.md).
The bus does not generally sandbox agents or isolate hostile tenants. See
[Known limitations](https://github.com/anulum/synapse-channel/blob/main/README.md#known-limitations).

## How does it refuse conflicting file authority?

An agent **claims** a unit of work with a file **scope** (literal file or directory
prefixes) before touching it. The hub refuses overlap with a different owner's
live claim in the same coordination/worktree domain; same-owner claims and
distinct worktree identities have different rules. Legacy literal scopes and
canonical path identities also have different alias handling. Supported
provider hooks enforce selected pre-edit paths and the staged Git gate checks the
index at commit time; neither is a general process or filesystem sandbox. Claims
are leases — they expire, can be renewed, and release on commit via the optional
git hooks. See [Git-native claims](git-claims.md).

## What happens if the hub restarts or crashes?

With `--db`, the hub replays retained admitted events using the matching
integrity/encryption configuration; leases may have expired during downtime.
Lease/control writes and ordinary chat have distinct durability classes. Reads
recover retained messages, not model processing or external effects. A waiter
that exits on disconnect needs its caller or supervisor to re-arm it; a presence
holder alone is not a reader. Without a journal, coordination state starts empty.

## Why did my waiter wake on a message that was not for me?

It should not, on 0.42.0 or later — earlier versions had two routing bugs (a priority/CEO
message directed at one agent woke everyone, and a bare-project message woke every seat of a
multi-seat project). Interactive `agent-tmux` pane bridges are stricter still: global
priority and CEO broadcasts remain passive inbox traffic and only exact targets wake
the provider pane. Upgrade, and see the wake entries in
[Troubleshooting](troubleshooting.md).

## Is there an MCP integration?

Yes. `synapse mcp` runs a Model Context Protocol server over stdio, bridged to the hub, so an
MCP client can read the board, state, and manifest as resources. See [MCP server](mcp.md).

## How do I observe what is happening?

`synapse board` (the shared plan), `synapse state` (live claims and checkpoints), `synapse who`
(the online roster), `synapse manifest` (advertised capabilities), and `synapse listen` (a live
stream). For a file-based observer, `synapse hub --relay-log <file>` mirrors the channel to a
compact log that `synapse relay` decodes. An opt-in Prometheus `/metrics` and JSON `/health`
endpoint is available with `synapse hub --metrics`.

## What does it cost, and what is the licence?

The public Core is available under **AGPL-3.0-or-later**, including commercial
use subject to its applicable conditions. A **commercial licence** provides
separately agreed permission when a deployment cannot meet those conditions;
hosted use or an internal-use label alone does not determine the obligations.
There is no feature difference between the builds. Infrastructure, optional
dependencies and provider costs remain separate. See
[Commercial use](commercial.md) for the evaluation path and contact details.

## How do I report a bug or ask for help?

Open an issue at <https://github.com/anulum/synapse-channel/issues>, or see
[`SUPPORT.md`](https://github.com/anulum/synapse-channel/blob/main/SUPPORT.md) for the security
and contact channels.

A Git claim timeout is an **unknown outcome** (exit `3`), not proof of denial. Use `--confirm-only` with the original identity and scope to verify an exact live lease without replaying a mutation. [Claim recovery](git-claims.md#claim-outcomes-and-recovery).
