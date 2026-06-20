<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Changelog

All notable changes to this project are documented here.

## [0.7.0] - 2026-06-20

### Added
- Per-agent rate limiting: an optional token-bucket limiter (`ratelimit` module)
  refuses non-heartbeat messages from an agent over its sustained rate, so one
  runaway agent cannot swamp the single hub. `synapse hub --rate/--burst` enable it.
- Bounded chat history: the hub drops the oldest in-memory messages beyond
  `--max-history`, so history cannot grow without limit (the durable log, when
  attached, still records every message).
- Inbound backpressure: the WebSocket server runs with a bounded per-connection
  receive queue.

### Changed
- `SynapseHub` accepts `rate_limiter` and `max_history`; agents' rate buckets are
  dropped on disconnect.

## [0.6.0] - 2026-06-20

### Added
- Idempotent mutations: a state-mutating message may carry an `idem_key`; the hub
  caches the response of each applied mutation (`idempotency` module, bounded LRU)
  and replays it on a repeated key instead of applying twice, so a reconnect retry
  cannot duplicate a claim. Only applied mutations are cached; failures re-evaluate.
- Resume cursor: `resume_request`/`resume_snapshot` let a reconnected agent fetch
  exactly the chat messages numbered after a `since` cursor, rather than a
  fixed-size history window. `SynapseAgent.request_resume(since)` drives it.

### Changed
- `claim` and `release` accept an optional `idem_key`.

## [0.5.0] - 2026-06-20

### Added
- Durable persistence: an append-only SQLite event log (`persistence` module,
  WAL mode, standard-library only). The hub records every authoritative mutation
  and rebuilds its state on start-up by replaying the log (`journal` module), so a
  restart resumes live leases and history instead of an empty registry.
- `synapse hub --db PATH` enables persistence; without it the hub stays in-memory.

### Changed
- Durability is split by workload: the lease/claim path commits at
  `synchronous=FULL` (survives an OS crash); the high-volume chat/history path
  commits at `synchronous=NORMAL` (survives an application crash).

## [0.4.0] - 2026-06-20

### Added
- File-scoped work claims: a claim may declare a `worktree` and a set of `paths`,
  and the hub refuses a claim whose file scope overlaps another agent's live
  claim (`scoping` module; claims in different worktrees never contend).
- Claim epochs: every claim/renewal is stamped with a strictly-increasing epoch,
  and `release`/`task_update` reject a stale epoch so a superseded agent cannot
  act on a dead lease.

### Changed
- `claim` gains `worktree`/`paths`; `release`/`update_task` accept an optional
  `epoch`. Claim grants now broadcast `worktree`, `paths`, and `epoch`.

## [0.3.0] - 2026-06-20

### Added
- `src/` layout installable package `synapse_channel` with a public API surface.
- Unified `synapse` console command with `hub`, `worker`, `team`, `send`, and
  `listen` subcommands.
- In-process hub + client integration test suite and an end-to-end roundtrip.
- Strict typing and NumPy-convention docstrings across every public symbol.

### Changed
- Hub routing state moved from module globals into a `SynapseHub` instance,
  allowing multiple hubs per process and deterministic testing.
- Message-envelope construction and message-type names consolidated into a single
  `protocol` module shared by the hub and client.
- Chat reply backends split into a dedicated `chat_backends` module behind a
  `ChatBackend` protocol.
- Default worker URI aligned to port 8876 across the package.
- Default worker role names changed to `FAST` and `REASON`.

### Removed
- Pre-package experimental scripts (gateways, daemons, relay bridges, terminal
  UI) moved out of the package surface pending a later hardening pass.
