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

## [Unreleased]

### Added
- A recall query-stream primitive for an optional persistent-memory layer. A
  `recall_log` message and `SynapseAgent.log_recall(...)` record each lookup the
  fleet makes — the query and its outcome (returned ids, whether the answer was
  used, whether the layer abstained) — as a durable `recall` event. The hub
  attests the producing identity and the time (they cannot be self-reported) and
  journals the record without broadcasting it, so a downstream memory adapter can
  calibrate recall against the real query distribution from the durable log. The
  hub stays memory-agnostic: it carries the record opaquely and never indexes it.

## [0.36.0] - 2026-06-24

### Fixed
- Cross-repository lease bleed. A `synapse lock <id> -- <cmd>` with no explicit
  `--paths` claimed the shared default worktree, so every keyless lock contended with
  every other claim regardless of its name — one repository's `:git` push-lock could
  block an unrelated repository's lock or claim. A keyless lock is now a pure named
  mutex scoped to its own id, so distinct ids never contend; passing `--paths` still
  opts into shared file-scope overlap. A `git-claim` likewise now resolves the
  repository root (`git rev-parse --show-toplevel`) and sets it as the claim's
  worktree, so two repositories declaring identically-named paths no longer conflict
  while overlaps within one repository are still detected.

### Added
- `synapse release <task> --name <owner>` — manually drop a claim you own. This is the
  escape hatch for a claim no commit or merge will auto-release (a
  `git-claim --auto-release-on manual`), which previously had no command-line release
  path.

## [0.35.1] - 2026-06-23

### Fixed
- Bare-project message routing. `is_recipient` — and so `is_directed`/`wakes` — now
  routes a bare `<project>` target to that project's `<project>/<id>` agents, mirroring
  `addresses_project`. An agent connected under a sub-identity no longer misses
  messages addressed to the bare project name, in both the wake predicate and the
  inbox filter. A bare name and cross-project targets are unchanged.
- Stale-waiter reaping. The client now sets explicit ping keepalive
  (`ping_interval`/`ping_timeout`, default 20s) on its connection, so a half-open
  socket — a killed hub, an ungraceful restart, or an eviction whose close frame never
  arrived — is detected and the connection returns instead of blocking indefinitely.

### Added
- A daily PyPI download tracker (`tools/pypi_downloads.py` and a scheduled workflow)
  that records the `without_mirrors` download series to a side `metrics` branch, so
  real installs can be watched above the CI/mirror baseline.

### Changed
- Bump `codecov/codecov-action` to v7.0.0.

## [0.35.0] - 2026-06-23

### Changed
- The package is reorganised into subpackages. The flat modules now live under
  `synapse_channel.core` (the hub, its state, journal, protocol, ledger, and the
  coordination primitives), `synapse_channel.client` (the agent and its on-channel
  workers), `synapse_channel.git` (the git-native claim helpers), and
  `synapse_channel.mcp` (the MCP face); `cli`, `relay`, and `update_check` stay at the
  top level. The documented public API is unchanged — `from synapse_channel import …`
  still re-exports every name — but deep imports moved. Migrate by prefixing the
  subpackage: `from synapse_channel.hub import SynapseHub` becomes
  `from synapse_channel.core.hub import SynapseHub`; `synapse_channel.client` becomes
  `synapse_channel.client.agent`; and `synapse_channel.mcp_server` becomes
  `synapse_channel.mcp.server`.
- The hub's message handlers moved out of the routing core into a per-responsibility
  registry (`synapse_channel.core.handlers`), so each message type is one dispatch-table
  entry and one handler function. The wire protocol and hub behaviour are unchanged.

### Added
- A measured scalability benchmark (`benchmarks/scalability_benchmark.py`, run with
  `make bench`) and a documented limits section quantifying the per-mutation lease-expiry
  scan from 10 to 100000 live claims.
- A link from the README to the commercial plans.

## [0.34.0] - 2026-06-23

### Added
- Git-native claims. A work claim can be scoped to the git branch it happens on:
  `synapse git-claim TASK --paths … --base … --auto-release-on …` resolves the current
  branch client-side, and `synapse state` shows it. `synapse git-hook install` writes
  post-commit and post-merge hooks that call `synapse git-release`, which releases the
  agent's claims whose paths were just committed or merged. `synapse conflicts`
  (optionally `--check-diff`) predicts merge conflicts between claims held on different
  branches whose paths overlap, exiting non-zero so a `synapse conflicts && <merge>` gate
  works. All git execution is client-side; the hub stores the branch as opaque metadata
  and never runs git or reads a filesystem.

## [0.33.0] - 2026-06-23

### Added
- `synapse mcp` runs a Model Context Protocol server over stdio that bridges to the
  hub: any MCP-compatible agent (Claude Desktop/Code, an editor assistant) claims and
  releases work, sends messages, hands off and declares/updates tasks, and reads the
  board, state, and capability manifest as live resources — with no Synapse-specific
  code. The MCP SDK is an optional extra (`pip install 'synapse-channel[mcp]'`); the
  core install keeps its single `websockets` dependency and the hub stays MCP-agnostic.

## [0.32.0] - 2026-06-22

### Added
- `synapse hub --max-clients N` and `--max-msg-kb K` cap concurrent connections and
  inbound frame size, so one host or one oversized message cannot exhaust the hub.
- `synapse health` probes a hub (exit 0 reachable, 1 not), wired as a Docker HEALTHCHECK.
- The hub token can be supplied with `--token-file PATH` or the `SYNAPSE_TOKEN`
  environment variable instead of `--token`, which is visible in the process list.

### Changed
- The hub drains on SIGTERM/SIGINT (graceful shutdown) instead of running on a bare
  future; a name is protected from an eviction storm by a takeover cooldown.
- The Docker image is pinned to `python:3.13-slim`, the highest version CI exercises.

### Security
- SECURITY.md documents the advisory file-scope model (the hub never reads the
  filesystem, so claim paths are not a traversal surface), the new caps, and that
  state is plaintext at rest on the local machine.

## [0.31.0] - 2026-06-22

### Added
- A best-effort update notice: `synapse --version` checks PyPI (cached once a day,
  silenced by `SYNAPSE_NO_UPDATE_CHECK=1`) and prints a one-line upgrade hint when a
  newer release exists; every network or cache failure is non-fatal and silent.
- CI runs `pip-audit` against the runtime dependencies and fails on any known
  vulnerability.
- README: PyPI version and downloads badges.

## [0.30.0] - 2026-06-22

### Added
- `synapse wait --wake-jitter <seconds>` (default 8): a broadcast wakes every
  terminal at once, so their agents all re-invoke and hit the model-provider API in
  the same instant — and the provider rate-limits the burst. The waiter now adds a
  random 0..jitter delay before exiting on a *broadcast* wake, spreading the
  re-invocations so each reacts without the synchronised stampede; a one-to-one
  directed message still wakes immediately. Set `0` to disable.

## [0.29.0] - 2026-06-22

### Added
- Name takeover for re-arming waiters: `synapse wait` registers with a takeover flag,
  so a re-arming waiter evicts a stale holder of its `<name>-rx` (a ghost connection
  left by an uncleanly-killed waiter) and rebinds the name immediately, instead of
  being rejected with a name conflict and waiting for the keepalive ping to reap the
  ghost. The hub closes the superseded socket with code 4010. `SynapseAgent` gains a
  `takeover` option.

## [0.28.1] - 2026-06-22

### Fixed
- `synapse wait` now exits (code 3) when its connection drops — a hub restart,
  supersede, or network blip — instead of looping forever on the dead socket. A
  `--timeout 0` waiter that silently stayed up after a hub restart was exactly how an
  agent went dark (reachable via its presence daemon, but never woken); it now exits
  so the caller re-arms.

## [0.28.0] - 2026-06-22

### Changed
- `synapse wait --directed-only` now also wakes on a **priority broadcast** and on
  any message from **`CEO`**, not only on directed messages — so an important `all`
  broadcast reaches a quiet waiter promptly while routine peer chatter stays
  suppressed (directed-only means "no routine broadcast wakes me", not "no broadcast
  ever"). `synapse send --priority` marks a message as priority. The `wakes`
  predicate and `PRIORITY_SENDERS` are exported.

## [0.27.2] - 2026-06-21

### Security
- Require `pytest>=9.0.3` (dev) to clear GHSA-6w46-j5rx-g56g (pytest tmpdir handling).

### Changed
- Bump CI actions (docker/setup-buildx-action v4, docker/login-action v4,
  docker/metadata-action v6, docker/build-push-action v7), the container base image
  (python 3.14-slim), and the `tomli` floor (>=2.4.1).

## [0.27.1] - 2026-06-21

### Added
- A `synapse-presence@.service` systemd template and its deployment guide: a
  per-project presence holder that keeps a project reachable on the hub even when
  its agent is down or rate limited (restarted by systemd if it dies, no model, no
  cost), decoupling reachability from the agent while the wake loop stays the
  promptness layer.

## [0.27.0] - 2026-06-21

### Fixed
- `synapse wait` no longer holds the bare identity it waits for: when the connection
  name would equal the waited-for name, it connects as `<name>-rx`, so an agent's
  own sends under that identity are no longer refused with a name conflict (a bare
  `synapse wait --name CEO` had locked out `--name CEO` sends).
- The hub sets an explicit keepalive ping (`ping_interval`/`ping_timeout`, 15s) so a
  dropped client's socket is reaped and its name freed promptly rather than lingering.

## [0.26.0] - 2026-06-21

### Added
- Recovery after a restart: `synapse state [--owner <name>]` prints the live claims
  and their resume checkpoints, and `synapse relay --project <name>` (backed by a
  new exported `addresses_project` predicate) keeps a project-stable inbox that
  catches messages to the project, any `project/...` instance or group, and
  broadcasts — so a returning terminal catches up regardless of the instance id it
  now runs as.

## [0.25.0] - 2026-06-21

### Added
- `synapse lock <id> -- <command>` holds a single live lease on `<id>` while it
  runs the command and releases it after, so several agents on one repo serialise
  operations that must not overlap — above all commits (`synapse lock
  <project>:git -- git push`). It waits its turn while another holds the lease
  (`--wait-timeout`, `0` fails fast).

## [0.24.0] - 2026-06-21

### Added
- Composite identities and group addressing: a `target` may be a group glob
  (`quantum/*` for every agent on a project, `quantum/claude-*` for one role),
  matched by `is_recipient`/`is_directed`, so several agents can share a project
  as `<project>/<agent>` and still address each other. `is_directed` is exported.
- `synapse who [--project <name>]` lists the agents currently online (optionally
  one project's instances) — discovery for the directory.
- `synapse wait --directed-only` wakes only on messages that name you or a group
  you are in, not on broadcasts.

## [0.23.1] - 2026-06-21

### Fixed
- `synapse wait` no longer wakes on the waiting agent's own messages: a chat whose
  sender is the waited-for identity is ignored, so the wake loop is not
  self-triggered by the agent's own sends.

## [0.23.0] - 2026-06-21

### Added
- `synapse wait --for <name>`: block on the hub until a message addressed to that
  name arrives (one, a group, or a broadcast), then print it and exit — a wake
  trigger a turn-based agent runs as a background task so it reacts to a message
  instead of polling. It holds presence and costs nothing while it waits.

## [0.22.0] - 2026-06-21

### Added
- A "parallel coding agents on one repository" recipe (`docs/recipes.md`) and a
  worked `examples/coding_agents_demo.py`: two agents lease disjoint file scopes,
  the hub refuses the overlapping claim so they never touch the same file, and
  they coordinate directly — the no-collision use case end to end.

## [0.21.0] - 2026-06-21

### Added
- Deployment support: a container image (`Dockerfile` + `docker-compose.yml`,
  published to `ghcr.io/anulum/synapse-channel` on release by a `docker`
  workflow), a systemd user unit (`deploy/synapse-hub.service`), and a deployment
  guide covering the local always-on service, containers, exposure/token security,
  and event-log backups.

## [0.20.0] - 2026-06-21

### Added
- Multi-recipient messages: `--target A,B` addresses several agents at once
  (alongside `all` for a broadcast and a single name for one).
- `synapse relay --for <name>` and `synapse listen --for <name>` show only the
  messages addressed to that name, dropping presence noise and other agents'
  cross-talk — a per-agent inbox that an offline agent still catches up from the
  durable relay log. The `is_recipient` predicate is exported.

## [0.19.0] - 2026-06-21

### Added
- `synapse task {declare,update,progress}` drives the shared blackboard plan from
  the command line: declare tasks with dependencies, mark a task done so its
  dependents unblock, and post progress notes — without writing a client.
- A runnable `examples/` directory: a narrated coordination demo and an
  LLM-worker round-trip demo, each starting its own in-process hub, with
  test-suite smoke coverage.

## [0.18.0] - 2026-06-21

### Added
- `synapse worker --prefix` and `synapse team --prefix` namespace a worker's
  registered identity (for example `remanentia/FAST`), so the same role can run
  under several projects on one hub without a name clash.

### Changed
- The offline `RuleBasedClient` acknowledgement no longer embeds the sender name;
  the wire envelope already records the author, so every reader renders the name
  exactly once.

### Removed
- `RuleBasedClient` no longer takes an `agent_name` argument.

## [0.17.0] - 2026-06-20

### Added
- Task-class routing (`routing` module): `classify` is an LLM-free, deterministic
  policy that sorts a prompt into `rule`, `slm`, or `heavy` by its length and a
  small keyword set, and `TieredChatClient` is a chat backend that dispatches
  each request to the backend for its class (falling back to a default), so
  trivial requests are answered cheaply and only hard ones reach a heavy model.
- The model worker gains a `tiered` provider (a rule path plus SLM and heavy HTTP
  models) and a `--heavy-model` option. `classify`, `TaskClass`, and
  `TieredChatClient` are exported.
- A committed routing benchmark (`benchmarks/routing_benchmark.py`): a fixed
  prompt set with checked-in results reporting the class distribution, the
  per-prompt decision, and a verification that a tiered client dispatches each
  prompt to its class. Decisions are exact and reproducible; backend latency is
  out of the offline scope (the `slm`/`heavy` tiers need a live model server).

## [0.16.0] - 2026-06-20

### Added
- Capability cards and a hub manifest (`capability` module): an agent advertises
  a small, A2A-shaped card — its description, skills, and the task classes it can
  take — and the hub keeps one card per agent in a `CapabilityRegistry`, exposed
  as a manifest so agents can discover who can do what and a router can pick a
  worker by task class. Cards are ephemeral: re-advertised on connect, dropped on
  disconnect, and expired after a soft TTL; they are never persisted.
- Hub handlers for `advertise` (stored and broadcast) and `manifest_request`;
  `SynapseAgent.advertise(...)`/`request_manifest()` client helpers; a `synapse
  manifest` view. The model worker advertises its own card on connect, with a
  `--task-class` option to set the classes it offers. `CapabilityCard` and
  `CapabilityRegistry` are exported.

## [0.15.0] - 2026-06-20

### Added
- Resumable task checkpoints: an owner can save an opaque resume token on a held
  task (`checkpoint`), and it survives lease expiry — when the lease lapses the
  checkpoint is retained, and the next agent to claim the same task inherits it
  in the claim grant instead of restarting. Checkpoints are durable (recorded in
  the event log and rebuilt on restart), carried across a handoff, and cleared
  on release. The owner's save is acknowledged privately and is idempotent under
  an `idem_key`; a non-owner or stale-epoch save is refused.
- `TaskClaim` gains a `checkpoint` field; `SynapseState.save_checkpoint(...)` and
  `SynapseAgent.save_checkpoint(...)` drive it; claim and handoff grants now
  carry the `checkpoint`.

## [0.14.0] - 2026-06-20

### Added
- LLM-free supervisor (`supervisor` module): a rule-based agent that watches the
  shared blackboard and re-offers stalled work, with no model in the default
  path. `detect_stalls` is the pure policy — an `in_progress` task with no
  activity (no progress note and no status change) for longer than an idle
  threshold, or a `blocked` task whose every dependency has reached a terminal
  status, is re-offered. Re-offering sets the task back to `open` (so it
  re-appears in `ready_tasks`) and records an `assessment` progress note; because
  the status changes, the same stall is not re-flagged.
- `SupervisorWorker` drives the policy on a poll, and `synapse supervisor` runs
  it. `SupervisorWorker`, `Intervention`, and `detect_stalls` are exported.

## [0.13.0] - 2026-06-20

### Added
- Atomic task handoff: an owner can transfer a held task to another online agent
  in one hub operation (`handoff`), with no release/re-claim window in which a
  third agent could grab it. The moved task keeps its file scope, status, and
  artefact reference, gets a fresh epoch (so the previous owner's epoch goes
  stale) and a full lease, and resets its version for the new owner. The hub
  refuses a handoff to an offline agent, by a non-owner, against a stale epoch,
  or to the current owner, and records the move as a progress note on the shared
  blackboard. `SynapseAgent.handoff(...)` drives it; handoffs are idempotent
  under an `idem_key`.

## [0.12.0] - 2026-06-20

### Added
- Proportionate connect authentication (`auth` module): an optional
  `TokenAuthenticator` validates a shared-secret token a connecting agent
  presents on its first message, optionally bound to a set of permitted agent
  names. Tokens are compared in constant time; with no token configured the hub
  stays open, which remains the default for a loopback bind. This is not a
  cryptographic identity system — a single secret gates the connection.
- `synapse hub --token` requires the token; `synapse worker/send/listen/board
  --token` present it. `SynapseHub` accepts an `authenticator`, and
  `SynapseAgent`/`SynapseLLMWorker` accept a `token`. `TokenAuthenticator` is
  exported from the package.
- The hub logs a warning when bound to a non-loopback host with no token
  configured, so an exposed deployment is not silently unauthenticated.

## [0.11.0] - 2026-06-20

### Added
- Shared blackboard (`ledger` module): a task ledger plus an append-only,
  bounded progress ledger, kept separate from the lease registry. A `LedgerTask`
  declares a unit of work — title, description, and dependencies — so any agent
  can read the plan and pick a ready task; dependency cycles are refused so the
  plan stays a DAG and `Blackboard.ready_tasks` is well-defined. The blackboard
  is event-sourced and rebuilt on restart alongside claims and chat history.
- Hub message types and handlers for the blackboard: declare/re-declare a task
  (`ledger_task`), change its planning status or suggested owner
  (`ledger_task_update`), append a structured progress note
  (`ledger_progress`), and request a board snapshot (`board_request`). Task
  changes are durable; progress notes follow the high-volume commit path.
- `SynapseAgent.post_task`, `update_ledger_task`, `post_progress`, and
  `request_board` client helpers, and a `synapse board` command that prints the
  shared plan, the ready tasks, and recent progress.
- `Blackboard`, `LedgerTask`, and `ProgressNote` are exported from the package;
  `SynapseHub` accepts a `max_progress` bound for the progress ledger.

## [0.10.0] - 2026-06-20

### Added
- First-class lite/heavy relay codec (`relay` module): `encode_lite` packs a full
  envelope into a short-key form and `decode_lite` reconstructs it, sharing one
  key schema. Both are exported from the package.
- `synapse hub --relay-log PATH` mirrors every broadcast to a compact
  newline-delimited file so a token-budgeted agent can observe the channel by
  tailing a file instead of holding a socket; the file is bounded by
  `--relay-max-lines`.
- `synapse relay PATH` decodes such a log back to readable lines and can resume
  from a persisted `--cursor`.
- Committed token benchmark (`benchmarks/`): a fixed broadcast trace and a
  runnable harness that report the byte and token cost of the lite encoding
  against the raw wire form, with results checked in under `benchmarks/results/`.
  Byte counts are exact; token counts use `tiktoken` (`pip install -e ".[benchmark]"`)
  with a labelled fallback estimate when it is absent.

### Changed
- The lite relay encoder/decoder were renamed from `compact_event` to the
  symmetric `encode_lite`/`decode_lite` pair.

## [0.9.0] - 2026-06-20

### Added
- Hold-and-wait deadlock detection (`deadlock` module): an agent may register an
  advisory wait for a task another agent holds (`wait_request`); the hub maintains
  the wait-for graph and refuses (`wait_denied`) a wait that would close a cycle,
  granting it (`wait_granted`) otherwise. Waits clear on the waiter's next
  successful claim or on disconnect. `SynapseAgent.request_wait(task_id)` drives it.

## [0.8.0] - 2026-06-20

### Added
- Typed task lifecycle (`lifecycle` module): a claim moves through
  `claimed → working → input_required → done/failed`; the hub rejects an illegal
  transition instead of accepting any free-form status.
- Optimistic concurrency: each claim carries a `version` bumped on every update;
  `update_task` accepts an `expected_version` and refuses a stale write
  (compare-and-swap against lost updates). `claim_granted`/`task_updated` now
  broadcast `version`.
- `SynapseAgent.update_task(...)` client helper.

### Changed
- Task status is now a checked lifecycle value, not a free-form string; the
  initial status remains `claimed`. A re-claim resets the version.

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
