<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — roadmap
-->

# Roadmap

This roadmap describes direction, not promises. The authoritative record of what
has changed is [`CHANGELOG.md`](CHANGELOG.md).

## Shipped

The coordination plane is in place:

- **Work claims** with file-scope overlap detection, expiring leases, epochs, and
  optimistic-concurrency versions.
- **Durability** via an append-only SQLite (WAL) event log with replay on
  restart, and **reconnect-safety** via idempotency keys and a resume cursor.
- **Load protection**: per-agent rate limits and bounded history, progress, and
  relay buffers.
- **Typed task lifecycle** and **hold-and-wait deadlock detection**.
- **Shared blackboard**: a declared task plan with dependencies and an
  append-only progress stream.
- **Atomic handoff** of a held task to another online agent.
- **LLM-free supervisor** that re-offers stalled tasks.
- **Durable, resumable checkpoints** carried across lease expiry.
- **Proportionate connect authentication** (shared-secret token, off-loopback
  warning).
- **Capability cards** and a hub manifest, with **task-class routing** to a
  tiered backend.
- A **token-thrifty lite relay** for file-based observers, with a committed
  benchmark.

## Planned

- Hardening and ergonomics of the existing surface as it sees real use.
- Wider documentation, examples, and recipes.

## Exploring

These are under consideration; each would ship only if it earns its complexity:

- Bounded tool-use for workers inside a proportionate sandbox, gated by
  human-in-the-loop approval with resume.
- Human-in-the-loop interrupt and approval gates with a review outbox.
- An optional MCP-server face that exposes hub operations as tools over stdio.
- One memory story: a projection over the event log that an external memory
  system can read, rather than a separate datastore.
- An optional OpenTelemetry exporter over the event log (never a core
  dependency).
- A gated cross-host bridge, only on real cross-machine demand.

## Non-goals

To keep the bus simple and correct, these are deliberately **not** on the
roadmap: an internal consensus protocol, distributed lock managers, CRDTs, a
standalone graph database, and cryptographic agent identity in the
single-owner local setting.
