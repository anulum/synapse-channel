# SYNAPSE CHANNEL

A local-first coordination bus for several agents working in parallel on one
codebase. A single WebSocket hub is the authoritative source of truth for
**presence**, **file-scoped work claims**, **chat**, **task status**, a **shared
plan**, **agent capabilities**, and **resource offers**, so concurrent workers
neither collide nor duplicate effort.

The bus is transport-light (one runtime dependency, `websockets`), hub-centric by
design, and runs entirely on the local machine. Model workers reply on-channel
through any OpenAI-compatible endpoint, including a local Ollama server, with a
deterministic rule-based fallback for offline use.

## Why a coordination bus

When several agents edit one repository at once they need a shared, authoritative
view of who is doing what. SYNAPSE CHANNEL provides that view without a database,
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

## Next steps

- [Installation](installation.md)
- [Quick start](quickstart.md)
- [Coordination model](coordination-model.md)
- [Wire protocol](protocol.md)

SYNAPSE CHANNEL is AGPL-3.0-or-later with a commercial licence available. See the
project's `NOTICE.md`.
