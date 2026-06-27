# How it compares

The most common question is "how is this different from CrewAI / LangGraph / AutoGen?".
The short answer: they sit at a **different layer**, and SYNAPSE is usually **complementary**
to them rather than a replacement. This page is meant to be fair — if your problem is the one
those frameworks solve, use them; SYNAPSE solves the one *next to* it.
It is not a replacement for orchestration frameworks or coding agents such as
CrewAI, LangGraph, AutoGen, Copilot, Claude Code, Codex, Cursor, or Aider.
SYNAPSE sits below and beside those tools: the adapters are interop surfaces
that let them coordinate through one local bus while they keep owning model
selection, prompting, tool execution, editor integration, and agent control
flow.

## Two different layers

- **Orchestration frameworks** (CrewAI, LangGraph, AutoGen, and similar) define and drive an
  agent's **control flow** — the graph of steps, tool calls, and hand-offs **inside one
  program**. They are where you *build* an agent or a crew.
- **SYNAPSE** is a **coordination substrate** **between** independently running agents and
  humans, **across processes and repositories**. It does not define an agent's reasoning; it
  gives separate agents one place to claim work, share presence and a plan, address each other,
  and survive a restart.
- **Adapters** such as the MCP server face and A2A bridge are edge interop processes. They
  translate existing tool protocols into ordinary SYNAPSE coordination messages; they do not
  make SYNAPSE an orchestration framework, an editor, or a coding-agent runtime.

An agent built with any orchestration framework can connect to SYNAPSE to claim a file scope,
read the shared board, and wake on a directed message. The two compose: the framework runs the
agent, SYNAPSE keeps a fleet of them off each other's work.

## At a glance

| | Orchestration frameworks | SYNAPSE CHANNEL |
| --- | --- | --- |
| Primary job | define & run one agent / crew's control flow | coordinate many independent agents (and humans) |
| Boundary | within one process | across processes, terminals, and repositories |
| Owns agent reasoning / prompts | yes | no — it carries no model logic of its own |
| File-scope work claims (collision-free editing) | not the focus | core — overlapping claims are refused |
| Cross-process presence & directory | not the focus | core (`who`, group globs, project identities) |
| Shared task plan with dependencies | varies | core (the blackboard; done unblocks dependents) |
| Event-driven wakeups (no polling) | varies | core (`wait`, the directed-only waiter) |
| Durable, replay-on-restart coordination | varies | core (SQLite-WAL event log) |
| Humans on the same channel | varies | core (the `syn` commands) |
| Runtime dependencies | many | one (`websockets`); the rest is the standard library |
| Runs fully local, no cloud account | varies | yes, by design |

## Concrete differences you can verify

These are the practical differences the project claims today. Each row points to
a local command or committed surface you can inspect instead of relying on a
comparison slogan.

| Difference | What SYNAPSE does | Local verification surface |
| --- | --- | --- |
| File-scope claims | Refuses overlapping task claims before two agents edit the same declared files. | `synapse lock TASK --paths src/example.py -- ...` or the claim conflict tests in `tests/test_hub_core_claims.py`. |
| Claim-aware Git hooks | Installs client-side hooks that release branch-scoped claims after commit or merge. | `synapse git-init --name AGENT`, then `synapse git-hook test`. |
| Durable event log | Replays accepted coordination mutations from a SQLite WAL-backed store after hub restart. | `synapse hub --db ./synapse.db`, plus the journal and persistence tests. |
| Metrics and health endpoints | Exposes opt-in operational metrics and health JSON without enabling HTTP by default. | `synapse hub --metrics --metrics-token TOKEN`, then query `/metrics` or `/health`. |
| MCP server face | Runs a separate stdio adapter that maps MCP tools/resources to ordinary hub messages. | `synapse mcp --uri ws://localhost:8876` and the audited list in `docs/mcp.md`. |
| A2A bridge | Runs a local HTTP+JSON edge that projects SYNAPSE tasks and capability cards into A2A-shaped operations. | `synapse a2a-card --endpoint-url ...` and `synapse a2a-serve --endpoint-url ...`; external conformance remains a separate validation task. |
| Release receipts | Attaches evidence, artifacts, changed files, generated artifacts, approvals, known failures, freshness, and advisory epistemic status to manual releases. | `synapse release TASK --name AGENT --evidence ... --receipt-json`. |
| Local-first operation | Runs the hub, demos, claims, waits, and adapters on loopback with no cloud account or hosted control plane. | `synapse demo`, `synapse doctor`, and the `synapse hub` default bind. |

## When to pick which

- **Building one agent or a crew with branching tool-use logic?** Reach for an orchestration
  framework — that is exactly what they model.
- **Running several agents at once that can step on each other** (same files, same task), or a
  **fleet across repositories** that needs one plan and one roster, or **event-driven** agents
  that should wake on a message rather than poll? That is SYNAPSE.
- **Both at once?** Common and supported: build each agent however you like, and connect them to
  SYNAPSE so the fleet coordinates.
- **Using a coding agent directly?** Keep using Claude Code, Codex, Cursor, Copilot, Aider, or
  another editor/terminal agent as the working surface, and use SYNAPSE for claims, presence,
  directed wakeups, and shared task state around it.

## What SYNAPSE is deliberately *not*

So the comparison is honest in both directions:

- It carries **no agent reasoning, prompting, or tool-use DSL** — that is the framework's job.
- It is **not an editor, model host, or coding-agent replacement** — Claude Code, Codex,
  Cursor, Copilot, Aider, and similar tools remain the interaction/runtime layer.
- It is **one hub on one machine** — no built-in failover or horizontal scale (a hub restart
  resumes from the durable log, but it is not a high-availability cluster).
- Its connect authentication is a **proportionate shared secret**, not a cryptographic
  identity system, and it **does not sandbox** the agents it coordinates.

If those constraints rule it out, it is the wrong tool for your case — and that is fine. Where
it fits, the [use cases](use-cases.md) and the [quick start](quickstart.md) show it end to end.
