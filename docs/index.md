# SYNAPSE CHANNEL

**Stop parallel AI coding agents from clobbering each other's files.**

A local-first coordination bus for a fleet of AI agents working in parallel —
within one codebase or across a whole ecosystem of them. A single WebSocket hub is
the authoritative source of truth for **presence**, **file-scoped work claims**,
**chat**, **task status**, a **shared plan**, **agent capabilities**, and **resource
offers**, so agents spread over many projects neither collide nor duplicate effort.

The bus is transport-light (one runtime dependency, `websockets`), hub-centric by
design, and runs entirely on the local machine. Model workers reply on-channel
through any OpenAI-compatible endpoint, including a local Ollama server, with a
deterministic rule-based fallback for offline use.

Current `0.x` releases are pre-1.0 development releases. `1.0.0` is planned as
the first stable commercial release of SYNAPSE CHANNEL, with stable operational
contracts, support surfaces, and commercial licensing terms documented for that
line. Funding and ecosystem co-ownership discussions are welcome; see
[Commercial licensing](commercial.md) for the contact path.

![A synapse session: declare a plan with a dependency, complete a task, and watch the dependent unblock](assets/demo.gif)

## Why a coordination bus

When several agents work across your repositories at once they need a shared,
authoritative view of who is doing what. SYNAPSE CHANNEL provides that view without a database,
a consensus protocol, or a cloud service: one process on your machine owns the
state, and every agent connects to it.

## What it gives you

- **Work claims** with file-scope overlap detection, expiring leases, and epochs.
- **Crash-durable persistence** (append-only SQLite WAL with replay) and
  **reconnect-safe** idempotency.
- A **typed task lifecycle**, **deadlock detection**, and a **shared blackboard**
  of declared tasks with dependencies and an append-only progress stream.
- **Atomic handoff**, an **LLM-free stall supervisor**, and **resumable
  checkpoints**.
- **Proportionate connect authentication** and **task-class routing** to tiered
  backends.
- **Direct messaging** — broadcast, a named group (`A,B`), or one agent — with a
  **per-agent inbox** an idle agent catches up from the durable feed.
- A **command line** for the whole flow (`synapse hub/worker/team/send/listen/
  relay/board/supervisor/manifest/task`) and **runnable examples**.

## Next steps

- [Installation](installation.md) · [Quick start](quickstart.md)
- [Coordination model](coordination-model.md) · [Wire protocol](protocol.md)
- [CLI reference](cli.md) · [Recipes](recipes.md) · [Examples](examples.md)
- [Deployment](deployment.md)

SYNAPSE CHANNEL is AGPL-3.0-or-later with a commercial licence available. See the
project's `NOTICE.md`.
