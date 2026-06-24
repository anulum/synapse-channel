# How it compares

The most common question is "how is this different from CrewAI / LangGraph / AutoGen?".
The short answer: they sit at a **different layer**, and SYNAPSE is usually **complementary**
to them rather than a replacement. This page is meant to be fair — if your problem is the one
those frameworks solve, use them; SYNAPSE solves the one *next to* it.

## Two different layers

- **Orchestration frameworks** (CrewAI, LangGraph, AutoGen, and similar) define and drive an
  agent's **control flow** — the graph of steps, tool calls, and hand-offs **inside one
  program**. They are where you *build* an agent or a crew.
- **SYNAPSE** is a **coordination substrate** **between** independently running agents and
  humans, **across processes and repositories**. It does not define an agent's reasoning; it
  gives separate agents one place to claim work, share presence and a plan, address each other,
  and survive a restart.

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

## When to pick which

- **Building one agent or a crew with branching tool-use logic?** Reach for an orchestration
  framework — that is exactly what they model.
- **Running several agents at once that can step on each other** (same files, same task), or a
  **fleet across repositories** that needs one plan and one roster, or **event-driven** agents
  that should wake on a message rather than poll? That is SYNAPSE.
- **Both at once?** Common and supported: build each agent however you like, and connect them to
  SYNAPSE so the fleet coordinates.

## What SYNAPSE is deliberately *not*

So the comparison is honest in both directions:

- It carries **no agent reasoning, prompting, or tool-use DSL** — that is the framework's job.
- It is **one hub on one machine** — no built-in failover or horizontal scale (a hub restart
  resumes from the durable log, but it is not a high-availability cluster).
- Its connect authentication is a **proportionate shared secret**, not a cryptographic
  identity system, and it **does not sandbox** the agents it coordinates.

If those constraints rule it out, it is the wrong tool for your case — and that is fine. Where
it fits, the [use cases](use-cases.md) and the [quick start](quickstart.md) show it end to end.
