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
- Added `synapse event-query` for read-only temporal event-log queries over the
  hub SQLite store, covering task timelines, point-in-time task state,
  path-touch windows, and historical claim conflicts.
- Added `tools/import_merge_risk.py` to combine changed paths or local branch
  diffs with claimed paths, Python import neighbours, CODEOWNERS, and mapped test
  owners for advisory pre-merge risk checks.
- Added `tools/generated_dependency_claims.py` to map source paths to generated
  outputs that should share the same file-scope claim and release receipt, with
  JSON, `--claim-args`, and integrity-check output.
- Added `tools/semantic_claims.py` to resolve module, symbol, API, source, test,
  generated, and migration selectors into ordinary file-scope claim paths and
  receipt-ready JSON.

## [0.51.0] - 2026-06-27

### Added
- `synapse compact` can write an owner-only static HTML archive report with
  event-kind counts, compaction removal counts, board tasks, release receipt
  notes, and a bounded coordination timeline from the pre-compaction event
  snapshot.
- Release receipts now include advisory `epistemic_status` and
  `epistemic_reasons` fields derived from submitted evidence, freshness, and
  known failures, and board assessment notes include the same metadata.
- Added `tools/check_dev_dependency_drift.py` to verify that the active local
  environment satisfies the repository's declared dev, docs, and benchmark
  extras; local preflight now runs it before the rest of the gate.
- Added `tools/test_ownership_map.py` to connect source files and symbols to
  likely owning tests through AST imports plus conservative filename fallback,
  with JSON, source filtering, and explicit required-ownership checks.

### Changed
- `synapse conflicts` now ignores branch-claim pairs with different merge bases,
  renders the real shared base in predicted-conflict output, and lets
  `--check-diff` refine directory-scoped and whole-worktree claims to the common
  files actually changed on both branches.

## [0.50.0] - 2026-06-27

### Added
- `tools/audit_mcp_surface.py` checks the registered MCP tools/resources against
  `docs/mcp.md` and pins the documented adapter, authentication, and
  optional-dependency boundaries in the local validation gate.
- `tools/check_release_claim_hygiene.py` checks changelog and release-note prose
  for agent-authorship, self-awarded quality labels, and unsupported
  conformance or certification claims.
- `tools/check_commercial_claim_hygiene.py` checks commercial docs for the
  AGPL/commercial boundary and for unsupported paid-code-path claims.

## [0.49.0] - 2026-06-27

### Added
- `synapse release` can attach evidence-backed release receipts with repeated
  evidence, artifact, changed-file, generated-artifact, approval, known-failure,
  confidence, and freshness fields. The hub echoes the receipt on
  `release_granted`, records it as a board assessment note, and `--receipt-json`
  prints the receipt for automation.

### Changed
- Public interoperability docs now position the MCP and A2A adapters as edge
  interop surfaces for existing frameworks and coding agents, not replacements
  for LangGraph, CrewAI, AutoGen, Copilot, Claude Code, Codex, Cursor, Aider,
  or similar tools.

## [0.48.0] - 2026-06-27

### Changed
- `synapse --version` no longer performs the PyPI newer-release check by default.
  Set `SYNAPSE_UPDATE_CHECK=1` to opt in; `SYNAPSE_NO_UPDATE_CHECK=1` still
  suppresses the check.
- `synapse hub` now exposes `--shutdown-close-timeout` so `SIGTERM`/`SIGINT`
  shutdown has an explicit bound for active WebSocket close handshakes.
- Hub takeover and identity-conflict paths now emit payload-free audit logs for
  accepted takeovers, cooldown refusals, name conflicts, and name-switch denials.
- Added `tools/fuzz_protocol_decode.py`, an Atheris-compatible local fuzz target
  and deterministic smoke corpus for the bounded wire JSON decoder.
- Refreshed public first-trial docs to foreground `doctor`, `git-init`, and the
  localhost A2A bridge path without implying external conformance.

## [0.47.0] - 2026-06-27

### Added
- `synapse doctor` now reports local filesystem pressure and exposes
  `--disk-path`, `--disk-warn-used-percent`, and `--disk-warn-free-mib` for
  workspace-specific checks.
- Provider shell wrappers now auto-bootstrap interactive Codex, Claude, Kimi, and
  Grok sessions into persistent tmux-backed Synapse wake targets from normal
  provider startup.

### Changed
- `synapse worker-session` now defaults to persistent tmux terminal mode for
  interactive providers launched from a real terminal, with
  `SYNAPSE_PROVIDER_TMUX=0` or `--terminal-tmux off` as the direct-execution
  escape hatch.

## [0.46.0] - 2026-06-27

### Added
- `synapse codex-tmux` adds a local tmux-backed wake transport for an existing
  Codex terminal session. The command can start, inspect, wake, or wait-and-wake
  a named tmux session while injecting only a fixed prompt that tells Codex to
  read its Synapse inbox.

### Changed
- Package metadata and public release notes now mark the `0.x` line as
  pre-1.0 development releases and reserve `1.0.0` for the first stable
  commercial release line.

### Fixed
- The `syn commit` packaging/documentation test now uses the configured
  Python 3.10 TOML fallback, keeping the full CI matrix green.

## [0.45.0] - 2026-06-26

### Added
- `synapse shell-hook` and `synapse install-shell-hook` now provide opt-in
  Bash/Fish/Zsh auto-arming for fresh terminals. The installed hook now keeps
  unassigned terminals on a neutral lane unless `SYN_PROJECT`/`SYN_IDENTITY` is
  set or the repository opts in with `.synapse/project`; it exports
  `SYN_PROJECT`/`SYN_IDENTITY`, keeps a cheap wake sidecar armed, and wraps common
  cloud and local provider commands through `synapse worker-session`.
- `synapse demo` now provides an installed first-run path that starts its own
  local hub, drives a planner/worker coordination flow, and prints
  `success: coordination demo completed`.
- `synapse new coding-fleet [path]` scaffolds a runnable two-agent coding demo
  workspace with editable source and test files.
- `synapse quickstart-coding` creates a temporary coding-fleet workspace, runs the
  no-collision demo, removes the temporary workspace by default, and can keep or
  refresh workspaces with `--keep`, explicit paths, and `--force`.
- `synapse who --me --name <identity>` reports the inspected identity's presence
  separately from its `<identity>-rx` waiter. The ergonomic `syn who --me` wrapper
  uses the resolved `syn` identity for the same check.
- `synapse hub --max-connections-per-host N` caps simultaneous sockets from one
  remote host independently of the global client, unauthenticated-client, and
  frame-rate limits.

### Changed
- The A2A HTTP edge, A2A CLI, MCP registration surface, read-only query CLI,
  messaging CLI, process CLI, state indexing, finding schema helpers, and client
  outbound/lifecycle internals were split into focused modules while keeping the
  previous compatibility import surfaces.
- Generated capability counts now report 112 package modules, 39 CLI subcommands,
  and 1394 test functions.

### Security
- A2A protected routes now compare Bearer tokens with constant-time comparison.
- A2A HTTP JSON bodies use the bounded parser for depth limits before bridge
  dispatch.
- A2A state-file writes use owner-only permissions for state files and write
  temporaries.
- A2A webhook delivery validates DNS and redirect targets before delivery and
  blocks localhost, loopback, private, and link-local destinations.
- A2A task retention, replay history, push-config counts, task history, artifacts,
  and terminal-task retention are bounded.
- Hub admission now enforces the per-host connection cap before authentication so
  pre-auth socket pressure is counted too.

### Fixed
- Fish shell auto-arm integration keeps the wake sidecar alive and is skipped in
  the shell syntax test when Fish is not installed.
- `syn say` preserves an exact `SYN_IDENTITY` by default, while `--as-project`
  keeps the explicit shared project sender when needed.
- Worker-session wake sidecars no longer leak routine output into the provider
  command's terminal stream.
- The A2A lifecycle now ignores late replies after timeout and keeps terminal
  task states immutable on cancel.
- A2A persistence now preserves the previous state file when a temp write fails
  and recovers stale working tasks on restart.

### Documentation
- README, quickstart, CLI, examples, recipes, deployment, troubleshooting,
  SECURITY, validation, and benchmark docs now describe the installed demo path,
  coding-fleet workflow, per-host connection cap, `who --me`, A2A bounded local
  soak evidence, and current A2A/security claim boundaries.
- The changelog and capability inventory were refreshed for the 0.45.0 release.

## [0.44.1] - 2026-06-26

### Added
- `synapse arm` now keeps a worker listener armed across repeated wakes and
  reconnects. The ergonomic `syn arm` and `syn-wait` wrappers use this persistent
  path instead of the one-shot `synapse wait` wake primitive.
- `synapse init` now prints or installs local user services for the hub, project
  presence, and provider-neutral wake arming. `synapse git-init` can install/start
  the same services, and `synapse doctor --fix` prints or applies the exact setup.
- `synapse worker-session` launches an arbitrary provider command with
  `SYN_PROJECT`/`SYN_IDENTITY` set and a cheap `syn arm` sidecar while the command
  runs.

### Security
- `synapse a2a-serve` now refuses a non-loopback bind unless Bearer auth and
  `--a2a-token` are configured, or unless the operator explicitly passes
  `--insecure-off-loopback`. This mirrors the hub's exposed-bind posture for the
  A2A HTTP edge and keeps unauthenticated network exposure opt-in.

### Fixed
- The client now classifies multi-address `OSError` connection refusals as a
  refused hub connection and keeps quiet mode quiet, matching the documented
  non-running-hub behaviour across Python versions.
- Hub-initiated name takeover, takeover-cooldown, and name-conflict closes now
  wait for close propagation when the WebSocket implementation supports it,
  making the coordination edge deterministic under CI timing.
- One-shot query and task CLIs now await client-task cancellation during cleanup,
  avoiding identity reuse races between sequential real-hub commands.
- Real-socket hub tests now handle Python 3.10 timeout semantics and wait for
  observable presence updates before asserting takeover or name-conflict close
  behaviour, keeping the CI matrix deterministic without fake sockets.
- The team launcher now waits after escalating a stubborn child process from
  terminate to kill, so shutdown returns only after the subprocess has exited.

### Documentation
- SECURITY.md, README.md, and the benchmark notes now state the current exposure
  and token behavior: metrics tokens use the `Authorization: Bearer` header by
  default, query-string metrics tokens require `--metrics-query-token-ok`, A2A is
  documented as a local HTTP+JSON bridge rather than an externally validated
  implementation, and the scalability notes describe the current heap expiry,
  replay, and scope-conflict scan measurements.

## [0.44.0] - 2026-06-25

### Added
- `synapse doctor` checks for the coordination misconfigs that quietly cost an
  agent its messages: an identity derived by accident (the home directory, a system
  path) or fragile (the working directory); a send name like `<project>-keeper`
  whose replies miss the project inbox; a hub URI exposed off loopback without a
  token; an unreachable hub; and — the common one — no live `-rx` waiter on the bus,
  so directed messages never wake you. Each line carries the fix, and the command
  exits non-zero when a check fails, so it slots into a setup script. Point it with
  `--uri`/`--project`/`--id`/`--send-name`/`--token` (or `--token-file`).
- `synapse git-init` makes a fresh clone claim-aware in one step: it installs the
  same `post-commit`/`post-merge` auto-release hooks as `git-hook install` and writes
  a short `.synapse/git-claims.md` guide — the branch-naming convention, the
  recommended one-worktree-per-claim workflow, and the exact claim/release commands.
  It is idempotent and never clobbers a file you wrote; `--base` sets the integration
  branch the convention assumes (default `main`).
- `synapse a2a-card` is the first Agent2Agent bridge slice: it reads the live
  SYNAPSE capability manifest and prints an A2A Agent Card JSON document that can
  be served by a thin HTTP edge as `/.well-known/agent-card.json`. It maps each
  advertised SYNAPSE capability card into an A2A skill and can declare Bearer auth
  for the advertised bridge endpoint.
- `synapse a2a-serve` runs a stdlib HTTP+JSON Agent2Agent bridge at the edge of
  the hub. It serves `/.well-known/agent-card.json` and `/extendedAgentCard`,
  accepts `POST /message:send` by forwarding text/data parts into SYNAPSE chat,
  exposes `GET /tasks` and `GET /tasks/{id}` over its local task view, and supports
  `POST /tasks/{id}:cancel`. `POST /message:stream` now returns an immediate
  Server-Sent Events task lifecycle stream; subscribing to a terminal task returns
  a clear `409` problem response. Push-notification configuration is now exposed
  through `POST/GET /tasks/{id}/pushNotificationConfigs`,
  `GET/DELETE /tasks/{id}/pushNotificationConfigs/{config_id}`, and send-time
  `configuration.taskPushNotificationConfig` capture; the served Agent Card
  advertises both streaming and push notification support.
- The A2A bridge now includes outbound push webhook delivery, JSON-RPC 2.0
  dispatch on `/rpc`, task pagination and history-length controls, Bearer-token
  enforcement for protected routes, file-part forwarding, and optional durable
  task/config state via `synapse a2a-serve --state-file`.
- The A2A bridge now has committed local benchmark evidence for task creation,
  reply correlation, task listing, push-delivery callback dispatch, and bounded
  subscriber fanout. The benchmark is explicitly in-process evidence, not a claim
  about third-party A2A conformance or real webhook/network latency.

### Changed
- The hub now **refuses to start** on a non-loopback address (e.g. `--host 0.0.0.0`)
  when it would be reachable without a token — and, with `--metrics`, without a
  `--metrics-token` — instead of only printing a warning and exposing the bus anyway.
  This makes the safe configuration the default: a coordination bus is never put on
  the network unauthenticated by accident. A loopback bind (the default) is unaffected.
- The A2A bridge now keeps validation, storage, event fanout, and handler logic in
  separate focused modules instead of growing the HTTP bridge into one large file.
- Caller-supplied A2A task creation is serialized around validation and insertion,
  so racing requests with the same `taskId` create one task and reject the duplicate.

### Security
- A2A webhook URLs now reject localhost, loopback, private, and link-local IP
  targets, and reject embedded credentials before push configuration enters bridge
  state.
- A2A state-file handling now fails fast on corrupt JSON, recovers stale in-flight
  persisted tasks as failed on restart, and rolls back in-memory task/push-config
  mutations when a state-file write fails.
- Caller-supplied A2A `taskId` and `contextId` values are restricted to bridge-safe
  characters, and duplicate caller task ids are rejected before task creation.

### Upgrade notes
- If you intentionally run an unauthenticated hub off loopback, add the new
  `synapse hub --insecure-off-loopback` flag to keep the previous warn-and-bind
  behaviour. The recommended fix is to set a token (`--token`, and `--metrics-token`
  when metrics are on) rather than override the guard. Loopback-only hubs and any hub
  that already sets a token need no change.

### Documentation
- The README leads with the file-safety promise and adds a "Use it with your coding
  agent" quickstart with one recipe each for Claude Code / Claude Desktop / Cursor
  (via MCP) and Aider or any non-MCP tool (via `git-init` + branch-scoped claims).
- The git-claims guide recommends gating a production setup on `synapse git-hook test`,
  which catches a missing hook or a moved `synapse` binary before it silently no-ops.
- The CLI and benchmark docs now state the A2A bridge's supported local HTTP+JSON
  subset, auth model, persistence semantics, timeout behavior, webhook validation,
  subscription replay boundary, benchmark limits, and remaining external validation
  blockers.
- GitHub Discussion #20 tracks community A2A interoperability and production
  validation work as a validation lane, not a bug report.

### CI
- CI now installs the auto-release hooks in a scratch repo and runs `synapse git-hook
  test` on every push (asserting both that a hookless repo fails and that an installed
  one passes), so a regression in the hook install-or-resolve path is caught up front.

## [0.43.0] - 2026-06-25

### Added
- `synapse worker` prints a loud egress warning to stderr before starting whenever
  the chosen backend will send channel context off the local machine — the `openai`
  provider (which also forwards the API key read from `--api-key-env`) or any provider
  pointed at a non-loopback `--base-url`. Local backends start silently.
- The hub's per-agent claim and offer quotas and the per-claim declared-path cap are
  now configurable with `synapse hub --max-claims-per-agent N`, `--max-offers-per-agent N`,
  and `--max-paths-per-claim N` (defaults 128, 64, and 512), for test labs, large
  monorepos, and managed deployments. A claim declaring more distinct paths than the cap
  widens to own its whole worktree — conservative, so it never misses a conflict.
- A hub started on a durable log larger than `--compact-hint-threshold N` records
  (default 100000) now logs a one-off hint to run `synapse compact`. The log is never
  compacted automatically — pruning is safe only below a sequence the read-side has
  already consumed, which the hub cannot know — so this surfaces unbounded growth
  without ever dropping an unconsumed finding or checkpoint.
- Two more knobs are now reachable from the CLI: `synapse hub --takeover-cooldown S`
  (seconds a name is protected from a second takeover, blunting an eviction storm) and
  `synapse mcp --request-timeout S` (seconds the MCP bridge awaits a hub reply). Both
  carry their previous defaults.
- `synapse git-hook test` reports whether the auto-release `post-commit` / `post-merge`
  hooks are installed and whether the `synapse` executable each one invokes still
  resolves, so a missing hook or a moved binary is caught up front instead of silently
  no-opping the next time a claim should have auto-released. It exits non-zero on any gap.
- `synapse hub` and `synapse worker` configure logging on startup with
  `--log-format {text,json}` and `--log-level LEVEL`. The JSON format emits one structured
  object per line (timestamp, level, logger, message, plus any contextual fields) for log
  aggregators; human-readable text stays the default.

### Security
- A declared claim path that is over-long (more than 4096 characters) or carries
  non-printable characters now widens the claim to its whole worktree rather than being
  trusted or scanned, consistent with the existing path-count bound. Claims stay
  advisory-only — the hub never reads the filesystem — so this only bounds work and noise.
- A hub can now apply a per-host frame-rate ceiling with `synapse hub --host-rate N`
  (and `--host-burst`), charging every inbound frame — heartbeats included — to a token
  bucket keyed by the connection's remote host. This bounds a single host that would
  otherwise flood the hub by cycling agent names or with bare heartbeats, independently
  of and in addition to the per-agent `--rate`. Off by default.
- Inbound wire frames are rejected before parsing when their array/object nesting
  exceeds 64 levels, so an adversarially deep payload (within the size cap) can no
  longer drive the JSON decoder into a `RecursionError` and tear down the handler.
  A frame over the depth bound is refused as malformed, like any other bad JSON.
- The SQLite event log's write-ahead-log sidecars (`<db>-wal`, `<db>-shm`) are now
  restricted to owner-only access (`0o600`) alongside the main database file. WAL mode
  creates them on the first write under the process umask, so they previously held the
  same plaintext chat and findings as the locked-down main file while remaining
  group/other readable.
- A token-protected `GET /metrics` / `/health` no longer accepts the token as a
  `?token=` query parameter by default — only an `Authorization: Bearer` header —
  because a query token can leak into access logs, shell history, and proxy records.
  The query form is available opt-in with `synapse hub --metrics-query-token-ok`.
- A secured hub now caps the number of sockets in their pre-authentication window
  with `synapse hub --max-unauth-clients N` (default: same as `--max-clients`), so an
  authentication-stall burst cannot occupy the connection table for the whole
  `--auth-timeout`. A connect over the cap is closed with code `4014`.

### Changed
- `VALIDATION.md` no longer hard-codes a module count or raw statement/branch totals
  that drift as the package grows; it defers the live counts to the CI-synced README
  capability inventory and states the gate-enforced 100% coverage instead.

### Upgrade notes
- No breaking API or wire changes; an in-place upgrade is safe. Every new hub knob
  (`--max-claims-per-agent` / `--max-offers-per-agent` / `--max-paths-per-claim`,
  `--takeover-cooldown`, `--compact-hint-threshold`) defaults to the previous behaviour.
  One default tightens for a token-secured `--metrics` hub: the metrics token is now
  read only from an `Authorization: Bearer` header unless you pass
  `--metrics-query-token-ok`. Inbound frames nesting deeper than 64 levels are now
  rejected as malformed, which no real Synapse envelope reaches.

## [0.42.0] - 2026-06-24

### Fixed
- A directed-only waiter (`synapse wait --directed-only`) is no longer woken by a
  priority or CEO message addressed to a *different* agent. The priority flag and a
  priority sender now elevate only a message that still reaches the waiter — a broadcast,
  or one addressed to it — so a flagged announcement or a CEO directive still wakes a
  quiet waiter promptly, while a priority message directed at one agent no longer wakes
  every directed-only waiter on the bus.
- On a multi-seat project, a `<project>/<seat>` directed-only waiter is no longer woken by
  every message addressed to the bare `<project>`. A bare-project message is now treated
  as a routine project-level broadcast for a seat — it still appears in the seat's inbox,
  and a CEO or priority project message still wakes it, but routine project traffic does
  not. A sole agent that wants project-addressed messages to wake it connects with
  `--for <project>` (the default for the `syn-wait` wrapper).

### Changed
- The README and the documentation site now carry a "Commercial use" section with the
  licence tiers and a direct link to the pricing/checkout page, plus a "Releases" note
  describing the release cadence.

## [0.41.0] - 2026-06-24

### Added
- The `/health` document now also reports the package `version` and
  `uptime_seconds` (alongside the existing `status`, `hub_id`, online-agent, and
  active-claim fields), so a probe can surface what is running and for how long.
  The hand-rendered Prometheus exposition is now also checked against the real
  `prometheus-client` parser in the test suite (a dev-only dependency), so a
  format drift is caught without taking a runtime dependency on the client.

### Security
- Logs and at-rest files are tightened. A message payload logged at INFO is now
  truncated past 120 characters (with a count of what was elided), so one large
  message cannot bloat the log; and the durable event store and the relay-log
  mirror — both plaintext — are created with owner-only permissions (`0o600`)
  where the platform supports it, so a stray group/other reader cannot read the
  channel's content at rest.
- `synapse git-hook install` now bakes the absolute path of the `synapse`
  executable into the generated hooks (resolved from `PATH` at install time, or
  set explicitly with `--synapse-bin`), instead of invoking `synapse` by bare
  name, so a hook is not vulnerable to a later `PATH` hijack. It falls back to the
  bare name only when `synapse` cannot be resolved.
- Per-agent quotas bound how much state one agent can register, so a runaway or
  buggy agent cannot exhaust the hub. An agent may hold at most 128 live claims
  and 64 live resource offers; a claim or offer past the bound is refused, while
  renewing a held claim or refreshing an existing offer is always free. (Per-item
  size — a finding or capability card — is already bounded by `--max-msg-kb`, and
  the blackboard's progress notes by its existing retention bound.)
- The optional `/metrics` and `/health` endpoint can now require a token. With
  `synapse hub --metrics --metrics-token <t>` (or `SynapseHub(metrics_token=...)`)
  both paths demand the token — presented as `Authorization: Bearer <t>` or a
  `?token=<t>` query, compared in constant time — and answer `401` without it, so
  an exposed endpoint no longer leaks operational metadata. Without a token the
  endpoint stays open (the right default for a loopback bind); a hub that enables
  metrics on a non-loopback host with no `--metrics-token` now logs a warning.
- A secured hub (`--token`) now authenticates a connection before it learns
  anything about the channel. Previously the hub sent the `WELCOME` frame — which
  carries the online-agent roster and the connection count — on connect, before
  the first message was authenticated, so an unauthenticated client could read
  that metadata; and an idle unauthenticated socket held a connection slot
  indefinitely. The welcome is now withheld until the socket authenticates, and a
  secured hub closes a socket that does not send an authenticated first frame
  within `--auth-timeout` seconds (default 10), so an idle unauthenticated
  connection is reaped instead of consuming the `--max-clients` budget. An open
  (tokenless) hub is unchanged — it welcomes on connect as before.

### Changed
- The scalability benchmark now measures the heap-based lease expiry honestly. It
  was still framed around the pre-0.40.0 linear claim scan (and populated claims in
  a way that bypassed the lease heap), so its numbers no longer described the code.
  It now reports the steady-heartbeat cost (near-constant in the claim count, as the
  heap intends) and the mass-expiry cost separately, and adds an event-replay
  profile (start-up rebuild cost up to 100k events). Live-hub storm scenarios are
  noted as needing an integration harness.
- File-scope path normalisation is now segment-based, so overlap detection is
  more accurate. `..` segments resolve against the path (`src/../tests` now
  overlaps `tests`), duplicate slashes collapse (`tests//app.py` == `tests/app.py`),
  and a leading `..` that escapes the tree root is kept literally so an out-of-tree
  path never falsely overlaps an in-tree claim. A claim that declares more than 512
  distinct paths is widened to the whole worktree rather than paying an unbounded
  pairwise-overlap cost — conservative, so a conflict is never missed.

### Fixed
- Corrected two stale "Known limitations" entries in the README that 0.40.0 had
  made false: per-mutation cost is no longer linear in the active claim count (the
  lease-expiry sweep is heap-based since 0.40.0), and the hub does have an opt-in
  Prometheus `/metrics` + `/health` endpoint (added in 0.40.0). The metrics entry
  now states the opt-in, no-authentication, loopback-only posture honestly.

### Upgrade notes
- No breaking API or wire changes; an in-place upgrade is safe. Two operator
  notes for a hub exposed off-loopback: a **secured** hub (`--token`) now requires
  the first frame to authenticate before it is welcomed or counted (tune the grace
  with `--auth-timeout`); and if you expose `--metrics`, set `--metrics-token` (or
  keep it on a loopback bind) so the endpoint does not serve metadata unauthenticated.

## [0.40.0] - 2026-06-24

### Changed
- Lease expiry no longer scans every claim on each mutation. The state keeps a
  min-heap of leases keyed by expiry, so an expiry pass pops only the leases that
  have actually lapsed instead of walking the whole claim table on every
  heartbeat, claim, update, and release. A renewal's superseded heap entry is
  recognised and skipped by its lease epoch (lazy deletion), and the heap is
  rebuilt when renewal churn grows it past the live-claim count, so its size stays
  bounded. Behaviour is unchanged; only the cost of expiry drops from linear in
  the number of claims to proportional to the number actually expiring.
- The relay log is now trimmed atomically. The kept tail is written to a
  temporary file and renamed over the log (`os.replace`, atomic on the same
  filesystem) instead of being rewritten in place, so a crash mid-trim can never
  leave the relay log half-written — a reader always sees either the old log or
  the fully trimmed one.

### Added
- An optional HTTP observability endpoint on the hub. With `synapse hub
  --metrics` (or `SynapseHub(enable_metrics=True)`) the same port also answers
  `GET /metrics` in the Prometheus text exposition format — connected clients,
  online agents, active claims, resource offers, retained history, blackboard
  tasks, and a monotonic message counter — and `GET /health` with a small JSON
  liveness document for container probes. Both are served in the hub's event loop
  via the WebSocket server's request hook, so a scrape reads a consistent view of
  the live state with no extra port, thread, or third-party dependency. Off by
  default — a plain hub serves no HTTP.
- An opt-in retention knob that bounds the durable write log. Resume checkpoints
  and authored findings are committed at full durability and otherwise accumulate
  without bound; `compact(store, RetentionPolicy(...), floor_seq=...)` (and the
  `synapse compact <db>` command) keeps the latest *N* checkpoints per task and
  ages out findings whose validity window closed more than a grace period ago. It
  deletes only events at or below a caller-supplied floor sequence, so a downstream
  ingest cursor at or below the floor never loses an unconsumed event, and a deleted
  sequence is never reused, so a cursor walks the gap. Keeping the latest checkpoint
  per task leaves coordination replay reconstructing each claim exactly as before;
  findings are skipped by replay, so ageing them out never touches coordination
  state. `EventStore` gains `max_seq()`, `delete(seqs)`, and `vacuum()` to support it.

### Fixed
- The idempotency guard now survives a hub restart. The cache that makes a retried
  mutation a no-op — so a reconnecting agent that resends a claim or release it is
  unsure landed replays the original response instead of applying it twice — was
  held only in memory and lost on restart, the one window where a retry is most
  likely. Each remembered key/response is now journalled durably (`idempotency`
  event, committed at `FULL` to match the lease mutations it protects) and the
  cache is rebuilt on replay, so the at-most-once guarantee holds across a restart.

## [0.39.0] - 2026-06-24

### Added
- A sequence-cursored ingest seam over the durable event store, for an optional
  persistent-memory adapter. `EventStore.read_since(after_seq, kinds=..., limit=...)`
  returns events whose monotonic sequence is above a cursor, optionally filtered to
  a set of kinds and capped to a batch size — so an adapter tracks the last sequence
  it consumed, polls forward in batches, and resumes with no loss or duplication
  across hub restarts. `MEMORY_KINDS` names the subset a memory layer ingests
  (`recall`, `finding`, `checkpoint`, `handoff`), excluding the pure coordination
  kinds. A `synapse ingest <db> [--since N | --cursor FILE] [--memory | --kind K ...]
  [--limit N]` command streams the events as newline-delimited JSON for an operator
  or a non-Python bridge, persisting the cursor between runs.
- An opaque `memory_tag` on `SynapseAgent.chat(...)` — a free-form marker (e.g.
  `"remember"`) that rides the durable chat event and the broadcast unchanged so a
  read-side filter can pick out actively authored context. The hub carries it
  without interpreting it, and it is omitted from the envelope when blank.
- A first-class `syn` command (with `syn-name`/`syn-wait`/`syn-say`/`syn-inbox`/
  `syn-board` aliases) — a thin, identity-correct front end over the package
  commands for the loop an agent runs each session. The project identity is
  resolved from `--project`, then `$SYN_PROJECT`/`$SYN_IDENTITY`, and the working
  directory only as a last resort, so a command run from the wrong directory no
  longer silently coordinates as the wrong project; an identity that looks
  accidental (the home directory, a system path) is flagged rather than used in
  silence. `syn arm` builds a directed-only waiter named distinctly from the sender
  in one place, correctly.

### Documentation
- `MEMORY.md` — the persistent-memory write-side architecture: the two-sided model,
  the three honesty axes (evidence kind / claim status / freshness), the emit-gate
  invariants, hub-attested provenance, the durable kinds + `MEMORY_KINDS`, the
  sequence-cursored ingest seam with a worked example, and the write-side ↔
  read-side honesty contract.

### Fixed
- Honest auto-release feedback. A `git-claim --auto-release-on commit|merge` is
  enacted only by the client-side git hook, never by the hub, so a claim made
  without `synapse git-hook` installed would sit held while the banner implied an
  automation that was not wired. The grant now checks whether the matching hook is
  installed and, when it is not, says so plainly and points at both remedies
  (install the hook, or drop the claim with `synapse release <task> --name <you>`).
- `git-release` no longer traps a manual caller. It is hook-invoked and auto-detects
  which claims to drop from the git diff, so it takes no task id and needs
  `--trigger`; running `synapse git-release <task>` or omitting `--trigger` now
  returns a message pointing at the verb that actually performs a manual drop
  (`synapse release <task> --name <you>`) instead of a bare argument error.

## [0.38.0] - 2026-06-24

### Added
- A `finding` event — the durable spine of the optional persistent-memory layer.
  A `finding` message and `SynapseAgent.record_finding(...)` author one memory
  atom (a codebase fact, lesson, decision, dead-end, or outcome) and place its
  assertion on three independent axes: what kind of evidence backs it, the
  standing of the claim, and how recently the supporting reference was re-checked
  at source (`freshness`). An emit gate admits, floors, or rejects each atom at the
  hub edge before it is journalled, so a claim stronger than its evidence is
  lowered rather than trusted: falsified evidence renders a claim refuted and,
  if it also claims reference-validated, is refused outright as a contradiction;
  producer-asserted testimony cannot be recorded as reference-validated nor declare
  itself verified-at-source; and a reference-validated claim must carry a reference
  *and* a source-verified freshness, so a reference that exists but was never
  re-checked this session is floored to bounded support rather than passing for a
  validated one. A record missing its provenance, validity window, or a required
  claim status is refused outright, and an unknown enum member is carried opaquely
  so the wire format can evolve. The hub attests the producing identity and the
  time (they cannot be self-reported), journals an admitted finding durably, and
  broadcasts the verdict to the fleet so a producer whose claim was floored learns
  what was downgraded. The hub stays memory-agnostic — it carries every record
  without interpreting it.
- Distinct durable event kinds for resume checkpoints and handoffs. A saved
  checkpoint and an atomic handoff were previously journalled as a `claim`
  re-snapshot; they now record under their own `checkpoint` and `handoff` kinds.
  Each still carries the full claim snapshot, so replay reconstructs the claim
  (and a legacy log that journalled them as `claim` still replays unchanged), but
  the persistent-memory read-side can now pick out resume summaries and ownership
  transfers — the highest-signal episodic memory — without re-deriving them from
  generic claim snapshots.

## [0.37.0] - 2026-06-24

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
