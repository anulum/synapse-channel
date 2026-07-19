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

### Security

- Always install a WebSocket handshake Origin/Host guard on the core hub
  (even when metrics are disabled): browser Origins require an explicit
  concrete `--allow-origin` allow-list; Host authorities are derived from
  the loopback bind and optional `--advertised-host` (no wildcard trust);
  origin-less native clients remain compatible only through a trusted Host;
  disallowed upgrades receive a deterministic `403` before the socket
  upgrades and cannot read history.
- Persist a hash-chained, owner-only Merkle checkpoint of the durable event
  log OUTSIDE the log it attests, and verify it fail-closed before the hub
  serves: a log shorter than its checkpoint (tail truncation) or a recomputed
  prefix root that differs (log replacement) now raises `AntiRollbackError`
  at startup instead of restarting silently. Once verified, the hub anchors
  the current state as the newest chain link. `synapse merkle checkpoint`
  shows the newest link or verifies the log against it (exit 0 clean / 2
  detection, JSON verdict). This is the local anti-rollback layer only;
  external witnessing stays owner-gated and out of scope.
- Bound open-hub idle sockets and single-host connection floods by default:
  `--max-connections-per-host` now defaults to **32** (pass `0` to disable),
  and every hub — open or secured — reaps a socket that does not bind a name
  within `--auth-timeout` (close code `4012`; open hubs use reason
  `registration timeout`). Secure mode clamps the open default down to its
  stricter per-host ceiling rather than refusing `hub --secure` solely for
  inheriting the open default.
- Serve the multi-hub event log only under an operator-configured
  `MultiHubServingPolicy`: a hub with no policy now refuses every peer
  (fail-closed) instead of serving the entire log to any unauthenticated
  frame, matching the operator-relay posture. A refused peer receives an
  empty snapshot that leaks neither the log nor the grant's existence.
- Key wait edges by the waited task instead of the incumbent holder and
  resolve ownership live at cycle-check time: a claim, renewal, or handoff
  for one task can no longer erase a wait on an unrelated task, and a
  release, lease expiry, handoff, or disconnect can no longer leave a stale
  agent edge that refuses a later legitimate wait as a false-positive
  deadlock. Wait edges prune on release and on every heartbeat-driven lease
  expiry.
- Reserve the global agent names `SynapseHub`, `Synapse`, and `system`
  case-insensitively so a client cannot impersonate hub or protocol provenance.
  Registration is refused before authentication, trust-on-first-use pinning,
  takeover, or ownership-lease state changes. Project-scoped identities such as
  `PROJECT/system` remain valid; clients using a reserved global name must
  migrate to a non-reserved or project-scoped identity.

### Added

- Board tasks carry an optional project scope and a monotonic version, with an
  `expected_version` compare-and-set guard on `ledger_task`/`ledger_task_update`
  and matching `synapse task declare --project/--expected-version` and
  `task update --project/--expected-version` flags. A re-declaration with a
  conflicting non-empty scope is refused, the guard refuses stale writers, and
  both fields journal and replay durably.
- Persistent capability registration for automated dispatch: an `advertise`
  with `persist: true` registers a project-scoped seat's card so it survives
  disconnects (24-hour refresh TTL), with `dispatchable` opt-out, a structural
  `-rx` sidecar path for wake listeners registering their seat, and additive
  `persistent`/`dispatchable` keys in the manifest. `synapse wait
  --capability-card FILE` re-registers the card on every (re)connect, so
  registration self-heals across re-arms and hub restarts.
- Opt-in ready-task dispatcher: `synapse dispatch --project P` computes a
  deterministic plan from live board/state/manifest/who snapshots, pins
  `suggested_owner` through the version compare-and-set guard, and wakes the
  seat's online identity under a stable idempotency key — exactly once, even
  across crashes, via an append-only JSONL outbox that reconciles pending
  intents on restart. A `dispatch:<project>` singleton lease serialises
  concurrent dispatchers; the dispatcher never claims real tasks, approves,
  lands, or broadcasts.

### Changed

- Validation documentation now reports the enforced 98% coverage floor and
  the dated 98.85% Codecov measurement separately; it no longer turns the
  badge's rounded 99% display into an unsupported "above 99%" claim.
- The release workflow now publishes the verified wheel and sdist SHA-256
  digests in the GitHub Release notes body itself (a fenced "Artifact checksums
  (SHA-256)" section), not only as the attached `SHA256SUMS` asset, so a
  consumer can confirm own-provenance straight from the release notes. The
  digests are the same ones `sha256sum --check --strict`-verified before the
  assets are attached.
- The identity-and-ACL guide now carries an end-to-end worked example for
  recovering a changed machine key through the governed pin reclaim: a
  copy-paste `identity-pin-reclaim` ACL grant, the fresh-TOFU recovery-operator
  recipe that sidesteps the bootstrap paradox, and the exact `synapse identity
  reclaim` invocation with its compare-and-swap and audit-trail guarantees.

### Fixed

- Keep ambient agent-tmux wakes from replacing active user work with an
  unconditional stop. The fixed payload-free prompt now gates work on the
  configured/current identity and an exact-target inbox item, ignores routine
  status and broadcast-only wakes, resumes the active user-directed task when
  there is no actionable directed work, and waits only when otherwise idle.

## [0.99.11] - 2026-07-18

### Security

- Grade a release receipt by verification rather than by the mere presence of
  evidence. A hub-broadcast receipt built from a release frame's caller-supplied
  evidence is now `unverified`, never `supported`, so a forged release can no
  longer launder fabricated evidence — a fake digest, a "ci: green" line, an
  invented approver — into a `supported` verdict the hub relays as trust.
  `supported` is reserved for the signed verify-release path whose declared
  checks actually ran, and the capability routing-trust filter now excludes
  `unverified` and `disputed` receipts so a forgery is never read as positive
  routing evidence.
- Bound every task-lease TTL with a `MAXIMUM_TTL_SECONDS` ceiling (30 days) as
  well as the existing minimum floor. A requested or default `ttl_seconds` is
  clamped into `[MINIMUM_TTL_SECONDS, MAXIMUM_TTL_SECONDS]`, so a runaway or
  hostile `ttl_seconds=1e15` can no longer pin a task for longer than the
  process will ever run. Non-finite requests continue to fall back to the
  default TTL.
- Refuse an inbound frame whose routing or identity field (`sender`, `target`,
  `type`, `channel`) is present but not a string, instead of `str()`-coercing
  it. A list, object, bool, or number would otherwise become a plausible
  identity or route — `sender: ["a", "b"]` coerced to `"['a', 'b']"` — letting a
  client bind a type-confused name or address a target it never spelled out. A
  structured `payload` is still accepted, since an A2A task frame legitimately
  carries one.

### Fixed

 - Keep `synapse who` usable when its requested subject is pinned to a retired
   machine key: the read-only query retries under a bounded identity derived
   from the current Ed25519 public key without removing or replacing the stale
   pin. A permanent `synapse arm` now returns a distinct identity-recovery
   verdict, and generated/checked-in systemd arm units use `Restart=on-failure`
   so a fail-closed `4013` stop cannot become a restart storm.
 - Preserve every advisory wait edge when one agent waits on tasks held by
   several peers, so adding a later safe wait cannot erase an earlier edge and
   let a real multi-hop deadlock evade cycle detection.
- Block publication unless the exact built wheel is installed in a clean
  environment and every declared console-script wrapper loads its callable from
  that environment's site-packages tree. The gate also refuses missing, extra,
  duplicated, or target-drifted entry-point metadata.
- Report a non-UTF-8 binary frame as a clean "Malformed JSON." error instead of
  killing the hub connection with an unhandled `1011`. `loads_bounded` decodes
  bytes through `json.loads`, which raises `UnicodeDecodeError` (not
  `JSONDecodeError`) on an undecodable frame; it now honours its documented
  contract and re-raises that as `JSONDecodeError`, so every caller's decode
  guard — the hub included — covers a binary frame and the socket stays open.
- Preserve structured JSON payloads and auxiliary protocol fields in version-2
  relay-log rows instead of stringifying payloads and dropping grant metadata.
  Existing version-1 rows remain readable, and the relay benchmark now compares
  the same full envelope field set rather than counting discarded fields as a
  compression saving.
- Recover startup around malformed SQLite event rows using non-secret typed
  markers, degrade health/metrics, and fail closed on every mutation while the
  replayed state is incomplete. `synapse compact --drop-corrupt` now provides an
  explicit floor-bounded repair path and requires an owner-only archive report
  that preserves the row digest and validation reasons before deletion; a failed
  pre-delete archive write leaves the journal unchanged.
- Roll back a failed event-store append and restore SQLite
  `synchronous=NORMAL` after a rejected durable write, so an INSERT or commit
  error cannot leave later hub traffic running at the `FULL` durability mode.
- Catch a peer's malformed opening handshake in the multi-hub fetch so a failed
  federation poll surfaces as `MultiHubFetchError` and the standing
  partition-detection watch keeps polling every peer instead of the watch task
  dying silently and freezing its observations. The watch now also logs at
  `WARNING` if its loop exits on any unexpected error, and importing the agent
  client no longer calls `logging.basicConfig`, which had clamped the whole
  process's root logger to `ERROR` and swallowed those federation watch
  warnings.

## [0.99.10] - 2026-07-17

### Security

- Namespace the hub idempotency cache by the authenticated sender and message
  type, so a client can no longer suppress another agent's mutation or read back
  its cached grant by reusing an idempotency key.
- Reject a valid-JSON non-object frame at the decode boundary instead of letting
  it drop the connection with a 1011 close.
- Sanitise client-controlled sender, target, type, channel, and payload on the
  per-message log line, closing a newline log-injection vector.
- Refuse a same-size in-place rewrite in the OpenCode-adapter and Kimi-hook
  compare-before-replace file guards by verifying a SHA-256 content digest, not
  just the stat fingerprint (TOCTOU hardening).
- Refuse a foreign `SYN_IDENTITY` riding atop the generic `SYN_PROJECT=user`
  instead of silently coordinating under the shared name.

### Fixed

- `synapse send` without `--name` now uses a unique ephemeral sender, so a
  one-shot send no longer collides with a listener already connected under the
  same name.
- `synapse team` starts a single offline rule-based worker when Ollama offers no
  model, instead of spawning Ollama workers whose every reply fails with
  "connection refused"; it also verifies the hub is accepting connections before
  printing the READY banner and exits non-zero if the hub never binds.
- The release workflow resolves the release tag from the published commit rather
  than the unreliable `workflow_run.head_branch`, so a tag build publishes its
  GitHub Release automatically.
- Point `CODEOWNERS` at the relocated `core/` modules (hub, protocol, state,
  auth), and refresh the identity landing gates and canonical path-identity
  binding.
- The required OpenCode editor E2E gate now drains Emacs Agent Shell's
  post-turn ACP requests before teardown, with the transport lifecycle exercised
  through batch ERT against busy, reset, threshold-crossing output, and timeout
  branches. JetBrains selector ownership now stabilises across transient empty
  snapshots and owner-proven XID remaps from a focused selector module instead
  of the client orchestrator.
  Filtered JetBrains selectors now use lifecycle-specific geometry: readiness
  waits for a rendered result within the bounded popup height, while closure
  tracks every bounded positive height so a collapsed search shell cannot be
  mistaken for disappearance.
  An owner-proven remap with phase-invalid geometry now fails explicitly instead
  of counting toward clean closure. Ambiguous, unowned, malformed, or
  deadline-exhausted selector states remain fail-closed, and post-confirmation
  closure requires consecutive clean snapshots rather than a single raced
  absence. The required editor workflow now type-checks and executes both
  selector responsibility modules and their dedicated tests, while a structural
  contract pins the split and keeps each module below 500 lines.
- Filesystem-canonical Git claims now resolve synthetic semantic descendants
  through their physical source path without treating `.synapse-symbol` as a
  real child of a file. A backward-compatible object-relative semantic sub-scope
  makes whole-file aliases conflict with every declaration and preserves
  declaration ancestry across hard links, including links created after the
  first claim, while sibling declarations retain independent ownership.

### Changed

- Correct the `handle_chat` docstring: a channel-scoped chat is mirrored to the
  relay log and journalled with its channel tag (it is delivered only to online
  channel members and is never broadcast or kept in the public chat history).

## [0.99.9] - 2026-07-16

### Added

- `synapse demo` is now the complete five-minute Claude/Codex golden path. It
  starts a real local hub and disposable Git repository, grants separate file
  claims, deliberately proves overlap refusal and fail-closed mutation denial,
  atomically hands authority over, runs observed `unittest` and `git diff`
  checks, hashes both changed files, and releases with a supported receipt. The
  command writes machine-readable `golden-demo.json` evidence and a static
  dashboard that renders every safety milestone; `--output DIR` selects a
  persistent artifact directory.

- `synapse deliberate conclude` and `synapse deliberate verify` seal council
  deliberations into signed export packages: a canonical-JSON content
  commitment under an Ed25519 receipt, with verification that fails closed on
  any post-signing mutation. Land, audit, and research council patterns share
  one schema, including audit findings with severities and dispositions.
- Governance metrics define the measurable coordination-discipline wedge:
  `core/governance_metrics` computes the M1–M4 rates (unclaimed edits, ungated
  self-pushes, unattested main moves, detection latency) as a pure function
  over observed events, and the `git/attested_history` collector measures M3
  on a real repository by classifying every first-parent main advance against
  the hexadecimal citations kept in an audit-artifact tree.

### Security

- Outbound MCP configs are now treated as process-launch policy. They default to
  owner-only single-link files outside the active repository and walk every path
  component with `O_NOFOLLOW`. Absolute executables are copied into sealed Linux
  `memfd` snapshots before spawn; configured cwd descriptors remain bound through
  the session, and optional SHA-256 pins cover the bytes that execute. The exact
  audited MCP SDK is pinned and its environment contract checked at runtime; no
  parent value is inherited unless approved. A configured finite timeout, capped
  at 3600 seconds, is the session-startup and discovery/call deadline; the pinned
  SDK then has a separate
  audited two-second graceful cleanup window before force termination. Optional
  domain-separated Ed25519 manifests bind distributed policy and signature-key
  identity to a separate owner-only trust bundle; whitespace aliases and
  duplicate public-key identities fail closed. `doctor --mcp-config` reports repository,
  signature, executable-pin, and environment posture. The MCP extra now installs
  its signature-verification dependency.
  Each server now requires an explicit outside-repository, non-group/world-writable
  cwd. Doctor warns with value-free argument positions when arguments may select
  code not covered by the command snapshot. Shebang scripts are rejected as commands
  because their interpreters escape the sealed snapshot; operators must configure a
  native interpreter command and retain the script warning until auxiliary pins exist.
  Config and trust-bundle repository overrides are reported separately, and no escape
  hatch relaxes cwd-mode checks. Startup and transport failures now produce stable,
  sanitized operational errors rather than raw exception groups; configured server
  stderr remains operator-visible. The sealed launcher is intentionally
  Linux-only (`memfd_create` + procfs) and fails closed elsewhere until an
  equivalent native descriptor backend exists.
- Owner-only token/key/public-material file readers now reject symlinks in every
  ancestor component, not only the final filename. Existing deployments that
  deliberately route such a file through a symlinked directory must configure
  its real component-wise path. The single-link inode floor is enabled only for
  outbound MCP executable-policy and trust files.
- Git-aware claims now bind human-readable paths to a versioned, client-derived
  canonical identity: strict OS-resolved worktree and filesystem paths, Git-index
  component spelling, Unicode NFC, the actual worktree case policy, and existing
  host-namespaced device/object keys. Symlink, junction, hard-link, Windows 8.3, case, and
  Unicode-equivalent aliases therefore contend without collapsing distinct
  Linux/ext4 case-sensitive paths. Unknown case policy, malformed, ambiguous, escaping, or
  display-misaligned identities fail closed before state mutation; journals,
  replay, causality, yield advice, conflict scans, provider guards, staged checks,
  Git hooks, MCP, and the TypeScript mutation client preserve the additive field.
  Legacy claims remain wire-compatible with an explicit mixed-fleet limitation.
- Provider claim hooks now cover Claude and Kimi `Bash`, Codex's intercepted
  `Bash`, Gemini `run_shell_command`, Grok `run_terminal_command`, and OpenCode
  `bash`. Because arbitrary shell text has no trustworthy declared write set,
  these calls require an editable, unambiguous whole-worktree claim; command
  text is not parsed, persisted, or forwarded by the OpenCode plugin. Codex's
  incomplete `unified_exec` interception and each host's crash/timeout behavior
  remain documented residuals, and the staged claim check remains the independent
  second gate.
- The read-only dashboard now enforces an always-on `Host`-header boundary
  against DNS rebinding. Its live JSON and audit feeds are unauthenticated on
  loopback so the browser cockpit can load, which made them a rebinding target: a
  page the operator visits could rebind its own name to the loopback address and,
  as a then-same-origin request, read coordination state cross-origin. A new
  `dashboard_host_guard` derives the exact authorities a legitimate browser
  presents — the loopback names and the bind host at the served port — and refuses
  any request whose `Host` is absent, malformed, or unlisted, before
  authentication and on both reads and writes. A deliberate LAN or reverse-proxy
  exposure adds its hostname with the new `synapse dashboard --dashboard-allow-host`
  flag.
- A2A outbound webhook delivery is now DNS-rebinding resistant. A new
  `safe_webhook_transport` resolves each target once and pins the connection to
  the validated address, so a name cannot resolve to a permitted public address
  during the check and to a loopback, private, or cloud-metadata address at
  connect time. It replaces the previous loopback/private/link-local deny list
  with a positive policy that admits only globally routable destinations —
  rejecting carrier-grade NAT, multicast, reserved, unspecified, and IPv4-mapped
  IPv6 forms as well — applies the same policy to redirect targets, preserves the
  hostname for TLS SNI and certificate verification, disables environment proxies,
  and reads the discarded response body under a fixed byte bound instead of
  unbounded.

- Directed delivery now survives shared-identity environments. Explicit
  `--name`/`--for` flags replace the ambient identity pair entirely in the
  `syn` aliases, so a seat that names itself no longer keeps waking on the
  shared ambient identity's messages; the rendered shell hooks judge an
  inherited default-shape auto identity by process lineage and re-mint a
  distinct one when it was minted outside the session, so unrelated sessions
  stop collapsing onto one `user/terminal-<id>` name; and the wake surface
  prefixes any replayed directed message older than fifteen minutes with an
  unambiguous `[replayed <age> ago]` marker, so an era-old directive can never
  present itself in the shape of a live wake.
- Exposed hubs now auto-enable bounded flood limits. When any exposure signal
  is present — an off-loopback bind, a configured token, a bridge, or more
  than one seat — `core/rate_policy` fills in safe non-zero per-agent and
  per-host rates and connection caps for limits an operator left disabled,
  preserving every explicitly configured value verbatim.
- Semantic-symbol claims are now enforced end to end: worktree- and
  branch-bound claims cover precise provider edits, the staged gate projects
  the Git index onto exact symbols, and commit and merge hooks release only
  proven committed symbols, failing closed on missing parsers, malformed
  evidence, or whole-file changes.
- A named shared-host attack regression suite pins one attacker scenario per
  defence (temp-path clobber, symlinked runtime directories, symlinked or
  loose secret files, DNS rebinding, smuggled claim paths), backed by a new
  `ensure_private_dir` same-descriptor `O_NOFOLLOW` floor that closes the
  pre-existing-directory TOCTOU on private state directories.

### Changed

- The public compatibility contract is now unambiguous across the README,
  contribution guide, stability and migration guides, public-surface
  documentation, and all translated READMEs: `0.x` does not promise backward
  compatibility across minor releases. Deliberate pre-1.0 public API or wire
  changes must update their frozen contracts and ship changelog plus migration
  notes; wire-incompatible changes bump `WIRE_PROTOCOL_VERSION`. Starting with
  `1.0.0`, breaking stable public Python API changes require a package major
  release.
- Version 2 of the OpenCode compatibility manifest binds every ACP client to
  its exact wire name and version. The real-editor gate rejects any unmatched
  or erroneous bidirectional request and treats missing lifecycle responses as
  incomplete evidence.
- Every OpenCode editor-E2E lane now gates the workflow. Pinned Zed and
  JetBrains GUI turns run through validated X11 targets under Xvfb alongside
  the required headless Neovim and Emacs clients; neither GUI failure is hidden
  by `continue-on-error`.

### Fixed

- The JetBrains lane now waits on a stable ACP lifecycle marker, proves the
  pinned plugin gate precedes both independently scheduled session-readiness
  events without inventing an order between them, and proves the validated
  project frame, selector, or a bounded, cycle-free Swing descendant owns
  keyboard focus before current-focus XTEST input and re-proves selector focus
  immediately before confirmation. Selector discovery batches
  window geometry, validates
  exact root/transient ownership and cardinality only for pinned candidates,
  filters the exact pinned agent name, and proves the original selector XID and
  every valid replacement are absent after confirmation. Empty X11 searches
  are distinguished from timeout, transport, and property-query failures so
  unclassifiable desktop state cannot masquerade as selector closure; ownership
  accepts only positive canonical hexadecimal XIDs. Selector
  evidence is phase-bounded, owner-only, non-empty, and sealed to a previously
  absent path.
  Lifecycle reads bind the baseline log device/inode and reject rotation,
  truncation, or any post-baseline event for a competing agent. The driver is
  split by responsibility across orchestration/selection, X11 transport/input,
  first-run setup, and evidence modules, each with strict typing and focused
  branch coverage. It constrains every GUI subprocess by its phase's remaining
  absolute deadline and starts the prompt deadline before composer input. The
  parent timeout accounts for both final and cleanup-fallback screenshots;
  evidence-capture failures are aggregated without skipping complete IDEA
  process-group termination, while adapter removal preserves every non-owned
  OpenCode configuration entry.
  The ACP evidence proxy rejects malformed responses before consuming their
  pending correlation, and the parent timeout is derived from every phase,
  evidence capture, cleanup, and a separate supervision margin.
- The Zed lane now writes settings and keymaps to the `config` directory owned
  by pinned Zed 1.10.3's `--user-data-dir`, uses a dedicated binding that cannot
  fall through to the built-in remote-project picker, rejects ambiguous or
  malformed X11 window searches, and accepts only the intersection of anchored
  `dev.zed.Zed` class and instance identity with the pinned project title and a
  `_NET_WM_PID` owned by the isolated process group. It requires non-empty proxy
  arguments, waits for the successful session response and initial update,
  proves the exact raw owned X11 focus before modifier-cleared current-focus
  prompt input and again before submission, applies absolute
  startup/session/prompt-input deadlines, derives
  the outer timeout from every phase and cleanup reserve, kills the complete
  driver/editor/proxy/helper group, and fails if success screenshot evidence is
  absent. Its evidence contract requires the full build-qualified ACP client
  version emitted by the hash-pinned Zed archive, rather than mistaking the
  release-only version for the wire identity. The JetBrains selector snapshot
  retries only a line-anchored canonical
  `BadWindow`/`X_GetWindowAttributes` disappearance report, at most three times;
  mixed, duplicate, persistent, or different X11 failures remain fatal.

## [0.99.8] - 2026-07-14

### Added

- A machine-readable OpenCode compatibility contract now binds 1.17.20's
  official tag commit, all twelve published CLI archive digests, five exact
  GitHub runner lanes, and every editor/plugin pin used by the real-client gate.
  A dedicated workflow re-verifies official release and tag-ref evidence,
  reports newer stable releases as advisory drift without moving the pin, and
  installs the exact Linux x64/arm64, macOS x64/arm64, and Windows x64 archives
  before a real ACP v1 initialize exchange. Extraction accepts only the exact
  regular root binary, bounds ZIP metadata and expanded tar streams, traverses
  output parents without following links, refuses existing destinations, and
  runs the binary with isolated home/temp roots plus a credential-free
  environment allowlist. It requires the pinned agent version, MCP HTTP/SSE
  capabilities, and terminal-auth metadata on every executable lane.
- `synapse doctor --a2a-policy` reports the effective A2A browser Origin/Host
  boundary — opaque `null` origins rejected, the effective allow-list, and Host
  authority binding when the list is enabled — and the claim-guard documentation
  gains a provider × fail-closed matrix.
- The multi-seat `synapse doctor` posture now surfaces a disabled or unobserved
  hub flood limiter and points operators at `synapse hub --secure` or explicit
  `--rate` / `--host-rate`.

### Changed

- The MCP surface is split into a transport bridge and a dedicated claim-actions
  module so the two responsibilities evolve independently.
- The JetBrains and Zed OpenCode editor-E2E lanes are now advisory in continuous
  integration: a headless runner has no desktop accessibility bus (AT-SPI
  `org.a11y.Bus`), so these heavy GUI IDEs cannot reach the ACP `session/new`
  handshake even under Xvfb. They run best-effort for signal (`continue-on-error`)
  without gating the workflow; the full governance contract remains enforced by
  the required, headless Neovim and Emacs lanes.

### Fixed

- The real-editor OpenCode E2E proxy now preserves the editor's original
  terminal-auth capability in evidence while adding OpenCode's pinned legacy
  opt-in only when omitted or when mapping the standard `auth.terminal` form.
  It never overrides an explicit false and accepts the returned method only when
  it contains the exact `opencode auth login` command object with a non-empty
  label. Real OpenCode 1.17.20 command-object response shapes are pinned by
  regressions; the former impossible boolean fixture is gone.

### Security

- Hub connect-token files (`--token-file`, staged claim checks, and the worktree
  auto-release hook) and the receipt-signing private key are now read through a
  shared owner-only secret floor (`O_NOFOLLOW`, mode `0600`): a group/world-
  readable, symlinked, or empty secret is refused fail-closed instead of trusted,
  and the error never contains the secret. The CLI reports a refused token file
  and exits cleanly rather than raising.
- Shell integration and the reap runtime directory no longer fall back to a
  shared world-writable `/tmp/synapse-shell`; a private cache directory
  (`XDG_CACHE_HOME` / `~/.cache`, created mode `0700`) is preferred, with a
  uid-keyed temp directory only as a last resort. Provider-tmux pidfiles follow
  the same private parent.
- The OpenCode compatibility installer hardens its auth and artifact gates,
  rejects typed non-regular ZIP members, and canonicalises an owned macOS temp
  root before extraction.

## [0.99.7] - 2026-07-14

### Added

- OpenCode's ACP editor gate now proves the governance path, not only a prompt
  round trip. Every pinned Neovim, Emacs, Zed, and JetBrains lane must discover
  `synapse_git_claim` through OpenCode's native MCP catalog, fail a write before
  ownership, acquire an exact Git/path claim, allow only the claimed write,
  release with durable evidence, and fail another write after release. The MCP
  face resolves real Git context without silently widening paths and validates
  the hub's release receipt. ACP evidence is private, content-minimised, bounded,
  request-correlated, capability/version checked, and GUI readiness is driven by
  observable session state rather than timing guesses.

- A dedicated OpenCode editor E2E workflow now launches pinned real
  CodeCompanion.nvim, Emacs Agent Shell, Zed, and JetBrains AI Assistant
  clients against OpenCode 1.17.20 and a deterministic local provider. Each
  lane fails closed unless the editor identifies itself over ACP v1, creates a
  session, sends the exact prompt, receives an `end_turn` response, and reaches
  the expected model; supply-chain hashes or exact commits pin every downloaded
  client surface. Bounded private traces retain protocol metadata and prompt
  fingerprints without prompt content.

- The `0.99.7` distribution gate now verifies the exact public PyPI wheel and
  source archive, the packaged MCP ownership marker, and the immutable official
  MCP Registry record before reporting publication. The operator flow pins and
  verifies the official publisher binary, validates `server.json`, preserves the
  owner-only authentication step, and refuses to infer registry publication from
  repository metadata or an older live version.

### Fixed

- Auto-release git hooks are now worktree-aware. Git worktrees share one hooks
  directory, so the single `post-commit`/`post-merge` hook that `synapse git-hook`
  and `synapse git-init` install fired in every linked worktree under the identity
  baked at install time — mixed-identity worktrees had to fall back to
  `--auto-release-on manual`. The hook now passes `git-release --resolve-identity`,
  which reads the `synapse.identity` / `synapse.uri` / `synapse.tokenFile` recorded
  for the committing worktree and releases that seat's own claims, using the baked
  identity only as the fallback where a worktree records none.

- The `pre-push-capability-manifest` hook no longer crashes when `git push` runs
  from a linked worktree. The manifest scan inherited the push hook's `GIT_DIR` /
  `GIT_WORK_TREE`, so `git ls-files` resolved the hook's repository instead of the
  scanned directory and produced a phantom path; the scan now strips those Git
  overrides so the `-C` directory alone decides the repository.

### Changed

- Bumped `softprops/action-gh-release` to v3.0.2 and the GitHub App development
  requirements (coverage 7.15.1, mypy 2.3.0).

## [0.99.6] - 2026-07-14

### Added

- OpenCode 1.17.20 is now a pinned Participant Fabric and IDE bridge. A
  reversible project/global adapter owns one local-stdio Synapse MCP entry and
  one fail-closed native mutation plugin; strict ownership, bounded race-aware
  file updates, token-file persistence, and uninstall preservation keep existing
  OpenCode configuration outside its scope. Exact-version headless JSONL and
  authenticated HTTP API participants, cancellation, remote attach delivery,
  and an ACP protocol-v1 handshake are covered by isolated real-process tests.
  The focused CI lane verifies the official OpenCode archive and extracted
  binary hashes before exercising adapter lifecycle, claimed/denied native
  writes, local turns, authenticated server access, and package contents.

- The experimental VS Code/Cursor client now consumes a bounded strict editor
  wire projection with version negotiation, stale-last-good state, fail-closed
  mutations, bounded reconnect/backoff, and safe identity/auth close handling.
  Claims and releases use canonical Git/workspace roots plus deterministic
  per-file task IDs, so multi-root workspaces and two files held by one identity
  remain distinct. Gutter projections require the exact canonical worktree and
  path; stale credential reads cannot restore an older configuration; switching
  hub or identity clears the previous projection; and terminal authentication or
  seat-ownership refusals wait for an operator change instead of retrying. Focused
  coverage is enforced above 95%, while a real VS Code
  Extension Development Host exercises two independent token-gated hubs,
  per-hub SecretStorage, configuration reconnects, identical relative paths in
  two real Git roots, exact claims, and exact releases. Runtime, timer, protocol,
  scope, and test-harness responsibilities
  remain split below the documented Godfile threshold.
- `synapse hub --secure` is the strict multi-seat production umbrella: it
  composes the `--team-secure` trust profile and the `--paranoid` exposed-hub
  profile, then bounds per-agent (100/s, burst 20), per-host (500/s, burst 100),
  and per-host connection (10) flood limits. Missing operator material fails
  closed before any socket binds, listing every absent input in one aggregate
  error; a stricter positive limit is preserved and a rate, burst, or
  connection cap above a preset ceiling is refused; startup prints one
  consolidated report of the composed gates, effective limits, and the controls
  the preset genuinely does not compose. It generates no credentials and enables
  no metrics surface. See [Secure mode](docs/secure-mode.md).
- `--message-auth-key-file` and `--metrics-token-file` read hub secrets from
  owner-only (`chmod 600`) files instead of argv, so a per-message HMAC key or
  metrics bearer token is not exposed in the process list. The key file carries
  one `KEY_ID:SECRET:SENDER[,SENDER...]` entry per line (`#` comments allowed)
  and merges with argv keys for rotation; an explicit `--metrics-token` wins
  over its file, mirroring `--token`/`--token-file`. The loader refuses a group-
  or world-readable file and reports problems by flag and path, never by content.

### Security

- Human-readable chat, channel, hub-query, mailbox, routing, resource-bid,
  federation-relay, peer-hub, capability-directory, outbound-MCP, workflow,
  event-query, memory-recall, accounting, acknowledgement, and Participant
  Fabric output now renders ANSI, OSC, carriage-return, newline, bidi, and other
  control characters as visible escape notation before they reach a terminal.
  Copyable operator commands shell-quote terminal-safe values, bind long-option
  values with `=`, and terminate positional options with `--`. Generated shell
  wrappers, git hooks, and systemd units use the same option boundary; custom
  shell providers must be bare command names. This closes command-substitution,
  option capture, and shell-function injection paths in setup, doctor, locks,
  federation exchange, SQLCipher guidance, claim helpers, `fleet-init`, and
  automatic waiter, provider, hook, and service-start flows.
- Headless Claude turns now disable built-in tools, ambient MCP servers, skills,
  plugins, hooks, browser integration, and customisations explicitly while also
  selecting plan permission mode. Unsupported older CLIs fail closed rather than
  accepting a bus prompt with weaker local execution policy.
- The hub rejects a non-finite (`nan`, `inf`) `--rate`, `--burst`, `--host-rate`,
  or `--host-burst` at the argument parser for every run: `nan` previously passed
  every downstream comparison and then built no limiter at all, so a hub could
  appear rate-limited while enforcing nothing. `--secure` additionally rejects a
  non-finite value and holds each burst to its own ceiling.
- The exposure guard now runs before the durable event store is constructed, so
  a refused unauthenticated non-loopback bind leaves no database file on disk.
- `SECURITY.md` distinguishes host ingress from container-network reachability:
  same-network containers reach the hub container-to-container regardless of the
  host publish flags. The shipped `docker-compose.yml` now attaches the hub to a
  dedicated single-service network so both audiences are bounded.
- The `--secure` report no longer copies the paranoid profile's
  "compose `--team-secure`" missing-hook line, which the umbrella already
  enforces; genuinely uncomposed controls (at-rest encryption, mutual TLS, …)
  stay listed.

### Documentation

- `SECURITY.md` now explains the container image bind posture that external
  audits kept re-flagging: the in-container `0.0.0.0` bind is required for
  port publishing, the exposure guard refuses startup with `InsecureBindError`
  on an unauthenticated non-loopback bind, and the shipped compose file pairs
  a loopback-only publish with a dedicated network and the explicit
  `--insecure-off-loopback` opt-out. The Dockerfile comment now states the
  refusal instead of understating it as a warning, and documentation-accuracy
  regressions pin both surfaces.
- [Secure mode](docs/secure-mode.md) documents the file-backed secret flags and
  the burst ceilings, and its production example delivers every secret from an
  owner-only file instead of expanding it into argv.

- `synapse a2a-serve --allow-origin ORIGIN` restricts browser requests to an
  opt-in allow-list of exact web origins (`scheme://host[:port]`, or `null`),
  repeatable for several. A request whose `Origin` header is not listed is
  refused `403` on every route — the public agent card included — before
  authentication; a non-browser client sends no `Origin` and is unaffected, and
  with no list configured the check is a no-op. Hardens the loopback bridge
  against DNS rebinding and drive-by browser requests.

### Fixed

- Failed headless provider turns no longer copy raw CLI stderr or process-start
  exception text into relayable or durable results. Claude, Codex, Gemini, Grok,
  Kimi, and Ollama now expose only the exit status plus a bounded safe
  classification; unknown diagnostics are withheld. Gemini's binary-health row
  also states that account entitlement was not probed, while a recognised retired
  consumer-account failure points operators to Antigravity or an eligible
  enterprise/API-key configuration.

- `synapse git-init` now stores staged-claim identity, hub URI, and the optional
  token-file path in Git's official per-worktree config. Linked worktrees can
  therefore use different exact seat identities without overwriting one another;
  migration removes the old shared values only after the current worktree is
  configured and refuses unsafe `core.worktree` / `core.bare=true` layouts with
  an explicit repair instead of guessing.

- The MCP guide now reflects the live official-registry state: SYNAPSE CHANNEL
  is active there, while a newer `server.json` remains only a prepared update
  until the immutable registry record exposes that exact version.

- Path-based claim enforcement now rejects symlink loops and broken path
  components consistently on Python 3.10–3.13 while retaining support for
  valid paths whose final file or directory does not exist yet.

## [0.99.5] - 2026-07-13

### Documentation

- The public-face slice of the README — hero, value proposition, feature wall,
  at-a-glance, layer map, install, first 60 seconds, trial path, and releases —
  now ships in eight translations under `docs/readme/` (simplified Chinese,
  Spanish, Brazilian Portuguese, Japanese, Korean, German, French, and Slovak),
  linked from a language-switcher line at the top of the main README. The
  English README stays canonical: generated blocks (capability snapshot,
  citation) exist only there, each translation ends with a pointer to the full
  English reference, and technical terms plus code identifiers stay in their
  original form. The documentation site build excludes the translated files,
  which live as GitHub-rendered pages only.

### Added

- Signed capability cards now ship as an advisory Ed25519 runtime. Separate
  owner-only card keys and explicitly agent/project-scoped trust bundles feed
  strict canonical `capability-card keygen|sign|verify` verbs plus opt-in worker
  signing and hub verification. Manifests, directories, dashboards, MCP resources,
  and Agent Cards expose explicit valid, unsigned, key/signature/expiry, replay,
  downgrade, binding, digest, and bounded-history results. Unsigned wire behaviour
  stays compatible and verification grants no authority. The bounded in-memory
  history remains the default; an opt-in owner-only SQLite store now preserves
  replay and downgrade floors across hub restarts, rejects corrupt state at startup,
  and projects runtime persistence failure as `history_unavailable`. Recovery and
  safe-reset instructions ship with it; enforced admission remains separate.

- Grok is now a fully enabled Participant Fabric provider and the fifth
  provider-native file-claim guard. Its parser is pinned to a real stable
  `grok 0.2.93` capture of native `thought` / `text` / `end` events, so
  `GROK_SCHEMA_VERIFIED` is true and `participant ask grok` no longer carries
  the stale unverified-schema refusal. The small Grok hook adapter feeds
  camelCase `PreToolUse` events through the shared provider-neutral claim
  engine and emits Grok's top-level deny JSON on exit zero for every handled
  parse, Git, state, transport, timeout, or runtime failure. The printed
  `grok-claim-hook` recipe covers `search_replace` and compatibility aliases
  without embedding tokens; public docs retain Grok's host-level fail-open and
  alternate-tool limits. `adapters install grok` writes the separate
  kebab-case `~/.grok/skills/synapse/SKILL.md` contract required by the
  installed CLI, without changing Kimi's distinct skill schema.


- The generic tmux waker's default pane detection now recognises every
  first-class provider binary out of the box: `grok` and `gemini` join
  `codex`/`kimi`/`claude`/`node` in `DEFAULT_AGENT_PANE_COMMANDS`, so an
  interactive Grok or Gemini pane is detected as a live agent without an
  explicit `agent_command` override (which continues to union any custom
  binary into the set).
- Gemini CLI becomes the seventh registered participant provider and the fourth
  file-edit claim guard. The guard speaks Gemini's native hook contract —
  `BeforeTool` event, `replace`/`write_file` tool names, `tool_input.file_path`,
  and a top-level `{"decision": "deny", "reason": …}` blocking response on exit
  zero — verified against the installed 0.47.0 bundle source, with an anchored
  `^(replace|write_file)$` matcher and a millisecond hook timeout in the printed
  `settings.json` fragment (`synapse adapters gemini-claim-hook`). The headless
  driver (`gemini -p … -o stream-json --approval-mode plan`) parses the CLI's
  `init`/`message`/`error`/`result` events, and `GEMINI_SCHEMA_VERIFIED` is true:
  the envelope was behaviourally captured from the installed binary's real
  emitter via the CLI's own `--fake-responses-non-strict` harness (only the
  model API client is substituted; no account required), with the fixture
  SHA-256-pinned. The schema gate stays wired so turns are refused again should
  a future release change the wire shape. Account-side, OAuth-personal setup
  still fails with `IneligibleTierError`; live model turns need an API key or
  an eligible account.
- Codex and Kimi now join Claude Code on provider-native, live-claim file-edit
  guards. A shared resolver canonicalises every target against its Git worktree
  and requires exact worktree, branch, path, task-state, and owner coverage;
  Codex extracts all add/update/delete/move paths from one `apply_patch`, while
  Kimi validates its `Edit|Write` path shape. New read-only recipe commands emit
  token-file-safe Codex `hooks.json` and Kimi `config.toml` fragments, and every
  handled parse, Git, state, transport, timeout, or runtime failure returns
  structured denial on exit zero. Public docs keep Codex alternate-tool and Kimi
  host-level fail-open limitations explicit rather than claiming filesystem
  isolation. The reversible adapter installer also emits valid Kimi Agent Skills
  at user scope (`kimi`, respecting `$KIMI_CODE_HOME`) or explicit project scope
  (`kimi-project`), preserving Kimi's Project-over-User precedence. Skill install
  does not touch `config.toml`; the opt-in `--with-hook` / `--install-config`
  path manages one marked hook block with bounded reads, owner and symlink checks,
  mode-preserving atomic replacement, and compare-before-write refusal.

- Studio now serves pinned, self-hosted Space Grotesk, Inter, and JetBrains
  Mono variable WOFF2 subsets from the package itself. The six Latin and Latin
  Extended files total 217,608 bytes, retain system fallbacks for other scripts,
  carry exact source hashes and OFL-1.1 provenance, and are included in the
  wheel; no browser font request leaves the dashboard origin.

- Dashboard Studio can now authenticate distinct `viewer`, `operator`, and
  `admin` browser principals from a strict owner-only
  `--dashboard-access-file`. The token-free `/dashboard-access.json`
  descriptor drives capability-filtered rendering, while every write request
  independently re-resolves its bearer and exact route capability before the
  existing rate-limit, relay, hub ACL, and audit chain. Viewer DOM and command
  search contain no write controls; operator/admin expose only the three
  shipped writes under distinct relay identities, and a live downgrade closes
  write UI, restores command-trigger focus, and announces the change. The
  dependency-free Studio shows a fail-visible role pill and remains read-only.

- Participant `ask`, `exchange`, and `convene` can now opt into bounded,
  token-file-authenticated REMANENTIA recall. The stdlib HTTP adapter binds an
  operator-supplied origin to the fixed `/recall` path, refuses redirects,
  requires HTTPS outside literal loopback IPs, caps requests, responses,
  tokens, hits, rendering, and elapsed time, and treats every current HTTP hit
  as boundary data because the response omits honesty axes. Recall stays off by
  default; the operator prompt is unchanged, recalled text is fenced as data,
  and unavailable or empty memory remains visible without failing the provider
  turn.

### Changed

- The compact relay codec, NDJSON log, byte cursor, and command normaliser now
  live inside `synapse_channel.core`, removing the kernel's sole upward import.
  The existing `synapse_channel.relay` path remains a small compatibility
  facade that re-exports the identical objects, so public imports and wire/log
  behaviour do not change.

### Fixed

- `SECURITY.md` now describes signed-capability-card history truthfully: the
  bounded in-memory default, opt-in owner-only SQLite replay/downgrade floors,
  fail-visible `history_unavailable` result, and advisory no-enforcement boundary
  match the shipped runtime. A documentation-accuracy regression pins the contract.

## [0.99.4] - 2026-07-11

### Added

- The free read-only Studio command centre now renders exact-status board
  columns from the blackboard and live claim leases. Open, claimed, working,
  input-required, blocked, and closed work remain distinct; future statuses
  fail visibly into Other, ad-hoc claims are labelled undeclared, and partial
  board snapshots disclose truncation without fabricating omitted dependencies
  as blockers. The 550-line inline command renderer is
  split into focused packaged CSS/JavaScript assets, and every untrusted task,
  event, and operator field is inserted as DOM text rather than HTML.

- A standalone, hosting-neutral Managed GitHub App package now ships under
  `integrations/github-app/`. It renders a least-privilege App manifest,
  verifies bounded HMAC-SHA256 pull-request webhooks before parsing, creates
  short-lived RS256 App credentials, scopes installation tokens, reads bounded
  open-PR file inventories through a fixed-origin no-redirect REST client,
  reuses the existing core conflict finder, and creates completed neutral
  advisory checks. The local core gains no GitHub dependency or hosted state;
  App registration, secret custody, hosting, retries, tenancy, and billing
  remain owner-gated. A dedicated Python 3.10–3.13 workflow enforces strict
  typing, 100% line/branch coverage, security/dependency scans, package build,
  and wheel-boundary inspection.

- The read-only cockpit risk panel now enriches its bounded safe-work queue
  with explainable route candidates and resource bids from the existing local
  advisory scorers. Risk and ready-task rows link to a new
  `/postmortem.json?task=ID` durable-store feed. The renderer and styles live in
  dedicated risk-panel assets, shrinking the original cockpit JS/CSS instead
  of turning either into a Godfile; no hint claims work, assigns an owner,
  reserves capacity, or grants authority.

### Security

- A weekly or manual read-only Actions-history audit now classifies the latest
  500 main-branch workflow runs and fails visibly when a failure, timeout,
  stale run, action-required run, or cancellation lacks later successful
  evidence for the same workflow. Raw and classified evidence remains attached
  to every audit. A separate SHA-pinned link workflow checks public Markdown on
  documentation changes and weekly drift, refuses private or loopback targets,
  requires TLS 1.2 or newer, and does not treat HTTP 5xx responses as healthy.

- A read-only property-based fuzz workflow now exercises the production bounded
  wire decoder and SQLite event store on a weekly or manual schedule. Separate
  Hypothesis targets cover arbitrary wire bytes, JSON round-trips, depth bombs,
  persistence across reopen, bounded cursor walks, and exact deletion with
  1,000 examples per property. Any falsifying case is promoted to a committed
  regression; the workflow is automated local evidence, not external protocol
  certification.

- Future tag releases now fail closed unless GitHub's first-party attestation
  action signs SLSA provenance for every checksum-recorded wheel, source archive,
  and SBOM before PyPI publication. The GitHub Release workflow independently
  verifies the portable Sigstore bundle against the repository, tag, source
  commit, and pinned publishing workflow before attaching the same canonical
  files and a stable `synapse-channel-vX.Y.Z-provenance.sigstore.json` asset.
  This applies beginning with the next release; it does not retroactively sign
  the already-published 0.99.3 artifacts.

### Changed

- `make install-hooks` now installs a lightweight pre-push stage alongside the
  pre-commit and commit-message hooks. It fails on stale generated capability
  counts, invalid commit-trailer history, or version-surface drift, and
  deliberately runs no pytest, coverage, or exhaustive preflight. The latter is
  reserved for explicitly requested/resource-reserved verification while CI
  owns the full suite on ordinary pushes.

- Staging Python or `pyproject.toml` now runs strict whole-tree mypy in the
  pre-commit gate instead of relying on later CI or partial-path checks. A small
  cross-platform resolver prefers the repository virtual environment, supports
  one explicit fail-closed interpreter override, and invokes mypy with fixed
  non-shell arguments. The remote pre-commit job installs the same hash-locked
  development environment before running the hook.

- Commit identity discipline is now enforced instead of relying on operator
  habit. `make install-hooks` installs a `commit-msg` gate requiring exactly
  one vendor-neutral Synapse seat suffix and the exact project authorship line;
  it also rejects forbidden self-promotional subject language. A SHA-pinned
  workflow audits every introduced commit and the complete forward-only history
  weekly, and the same audit runs in local preflight.

- Main-branch CI no longer cancels an in-progress earlier head when another
  commit lands. Every push is keyed by its immutable commit SHA and must finish
  the existing Python 3.10–3.13 matrix; pull-request and manual runs retain
  stale-run cancellation on their shared ref.

- The repository coverage gate now fails below 98% instead of 95%, raising the
  enforced minimum floor. `pyproject.toml`, the contributor guide, and the
  validation guide record the same threshold, and no existing coverage was reduced.

### Fixed

- GitHub Releases and PyPI now receive the exact same wheel and source archive
  from one tag build. The tag-bound OIDC publish workflow records the complete
  distributions and SBOM in `SHA256SUMS`, preserves them as one immutable
  Actions artifact, verifies them before upload, and never tolerates an existing
  file from a rebuilt run. Only after PyPI succeeds does the GitHub Release
  workflow consume that exact source-run artifact; it rechecks the file set and
  every SHA-256 digest, pins the source SHA and version tag, and remains safely
  rerunnable without rebuilding if GitHub release creation fails.

- The handshake-abort log filter now recognises the reconnect-storm
  signature: `ConnectionClosedError` with no close frame received or sent.
  That class subclasses `WebSocketException` rather than `ConnectionError`
  and carries no cause chain, so the live 0.99.3 hub logged four full
  `opening handshake failed` tracebacks while its clients reconnected after
  a restart. The rule is fail-narrow — a framed close (a genuine close code
  in either direction) and every other handshake error stay loggable.

## [0.99.3] - 2026-07-11

### Added

- The experimental VS Code/Cursor extension 0.3.0 now renders file and
  directory claims on every visible gutter line, with distinct own/other shapes,
  theme-specific contrast, owner hover text, and true-scope overview marks.
  Semantic `.synapse-symbol` claims follow resolved editor symbol ranges; when a
  provider cannot resolve a symbol, one explicit alert marker appears instead
  of widening the lease to the whole file. Pure range decisions and VS Code
  rendering live in separate focused modules.

- The experimental VS Code/Cursor extension can now authenticate to secured
  hubs without a token setting. `SYNAPSE: Set hub token` stores one credential
  per canonical hub URI in VS Code SecretStorage; clear deletes it. A real
  Extension Development Host test now drives wrong-token refusal, the
  SecretStorage store/read/delete path, authenticated roster presence, and a
  file claim against a disposable token-gated Python hub.

- `synapse identity reclaim` adds a deny-by-default recovery path for stale
  trust-on-first-use pins. A cryptographically bound operator needs an exact
  `identity-pin-reclaim` ACL grant, the observed key id, a reason, and a durable
  hub journal. Normal recovery waits for the target socket to disappear and
  its ownership lease TTL to lapse, or for a socket-up target to remain without
  a recent reaction or live waiter for that TTL after its liveness window;
  explicit `--break-glass` may evict any other live or still-leased holder.
  Every applied action is compare-and-swap guarded,
  write-ahead audited, broadcast without key material, and removes rather than
  silently replacing the old key. Pin-refusal diagnostics now point at this
  governed command instead of manual JSON deletion.

### Changed

- The A2A HTTP+JSON bridge now aligns its explicit 1.0 wire path with the
  official SDK/TCK: ISO 8601 UTC task timestamps, `application/json` responses,
  explicit `A2A-Version` negotiation, AIP-193 errors, task-not-found semantics
  for unknown continuation ids, context-safe non-terminal continuation, and the
  direct 1.0 inline push-config shape. Independent validation with
  `a2a-sdk==1.1.0` completes discovery/send/get/list/cancel; official TCK
  `5996b79` HTTP+JSON MUST results are 55 passed, 5 structured-response
  failures, and 175 skipped. This is partial evidence, not certification.

- Stable error taxonomy codes now drive two real boundary families instead of
  remaining metadata only. The A2A HTTP bridge maps typed validation, missing,
  conflict, quota, and store failures to 400, 404, 409, 429, and 500 without
  message matching; outbound MCP CLI commands distinguish invalid config
  (exit 2), deny-by-default access refusal (exit 3), and tool failure (exit 1).
  Existing A2A exceptions remain `ValueError` compatible.

- `synapse who` now bounds the full-roster mailbox-pending section to the 20
  largest positive counts and reports complete identity/message totals, so
  hundreds of dead test identities cannot flood normal operator output.
  `--all-mailbox-pending` (alias `--all`) restores the complete retained map;
  project filtering happens before the bound. The hub-side 512-identity
  projection and durable journal evidence are unchanged.

### Deprecated

- `synapse hub --metrics-query-token-ok` now emits a parse-time warning and is
  scheduled for removal in 0.101.0. The safe default remains unchanged; send
  metrics credentials in the `Authorization: Bearer` header instead of URLs,
  where tokens can leak into logs, histories, and proxy records.

### Fixed

- The declared runtime floor `websockets>=12.0` was false metadata:
  production modules across the client, core transport, and demo layers
  import `websockets.asyncio`, which ships since websockets 13.0, so a 12.x
  environment resolved by the old floor failed with `ModuleNotFoundError`
  on first import. Both public declarations (`pyproject.toml` and
  `requirements.txt`) now state `>=13.0` and a packaging test pins the
  floor.

- `synapse demo` and the packaged coding-fleet demo no longer print an
  `opening handshake failed` traceback on stderr during a clean first run.
  Both readiness probes opened and closed a bare TCP socket, which the hub
  logs as an aborted handshake at `ERROR` — after the probe returns, so no
  probe-scoped suppression could catch it, and the old import-time silencing
  targeted the obsolete `websockets.server` logger anyway. The probes now
  complete one real WebSocket handshake and close it cleanly: no error
  record exists to hide, neither module mutates process-wide logging at
  import, and genuine handshake errors stay visible.

- The generated `synapse-hub.service` and the checked-in deploy template no
  longer order the hub `After=default.target`. Combined with
  `WantedBy=default.target` that ordering formed a boot cycle — systemd gives
  target units an implicit `After=` on their `Wants=` dependencies — and
  systemd broke it by deleting the dependent `synapse-presence@` and
  `synapse-arm@` start jobs, so presence holders and durable wake listeners
  stayed dead after every reboot while reading `enabled`. Regression tests now
  assert that no rendered or checked-in unit installing into `default.target`
  orders itself after it.

- `EventStore.delete()` now uses one static parameterised `DELETE` statement
  through `executemany()` instead of constructing an `IN` clause. This removes
  the `B608` suppression without depending on optional SQLite JSON1 support,
  preserves iterable inputs, and reports only rows that actually existed even
  when requested sequence numbers are duplicated or missing.

- `synapse a2a-serve` now cancels and awaits tasks owned by its private agent
  event loop before stopping and closing it, so normal `Ctrl+C` shutdown no
  longer destroys live WebSocket keepalive or heartbeat tasks.

- `syn inbox` and `syn-inbox` now read the exact resolved identity by default
  and advance an identity-specific cursor. Previously they silently used the
  project-wide filter and shared project cursor, so one terminal could display
  and consume another terminal's directed messages. Broader reads now require
  the explicit `--project-wide` flag; `--name PROJ/name` selects another exact
  address, and existing `--as` aliases retain independent cursors.

- The A2A HTTP protocol helpers import on Python 3.10 again: `datetime.UTC`
  (a 3.11 alias) is replaced by `timezone.utc`, and mypy now types the whole
  tree against the 3.10 floor (`python_version = "3.10"`) so an
  above-the-floor stdlib symbol fails locally instead of only in the 3.10 CI
  job. Two tools gained the version-branch `tomllib`/`tomli` import the drift
  checker already used.

### Documentation

- The deployment exposure guide now leads with the recommended team shape —
  token **and** TLS together (native `wss://` or a TLS-terminating proxy) —
  and describes token-without-TLS as the trusted-LAN fallback that logs the
  plaintext-transport advisory.
- The docs landing page ranks client paths by evidence-bounded support tiers:
  hub + `syn` CLI are the supported core under the stability contract; the
  MCP server face is a shipped adapter whose named boundaries the contract
  freezes; the A2A bridge is validated, partial interop (official SDK
  lifecycle passes; TCK MUST run 55 passed / 5 documented gaps — evidence,
  not certification); Go/JS embed within stated boundaries; the cockpit PWA
  and VS Code extension preview stay experimental.
- The experimental VS Code/Cursor extension now has a repeatable,
  version-pinned official `vsce` packaging path, a development-only payload
  exclusion list, local VSIX installation instructions, and a CI-uploaded
  installable artifact. Its unauthenticated-loopback runtime boundary is
  unchanged and explicit.

### Security

- The VS Code/Cursor extension refuses non-loopback `ws://` connections before
  opening a socket, rejects credentials/query parameters/fragments in hub URIs,
  and presents a shared token only on the first protocol heartbeat. Remote hubs
  require trusted `wss://`; bearer values never enter settings JSON or URLs.

- The exposure guard now logs a startup advisory when a token authenticates a
  non-loopback bind over plaintext `ws://` — the token and every coordination
  frame are readable on the network path. Native WSS (`--tls-certfile`/
  `--tls-keyfile`) or a `wss://` proxy silences it, and `--paranoid` continues
  to require native WSS outright. The advisory never blocks the documented
  team-LAN posture (transport encryption *recommended* there), so existing
  deployments keep starting — loudly instead of silently.

## [0.99.2] - 2026-07-10

### Added

- `SynapseAgent` now presents the zero-config machine identity by default, so
  every verb that connects — send, listen, arm, queries, the bridges, and any
  embedding application — signs its registration with the same
  trust-on-first-use key. Previously only `arm` and `wait` signed, and arming
  a name locked its holder out of every other verb under that name (refused
  `signature missing`; the 2026-07-10 directed-delivery incident class). An
  explicit `identity_key_path` wins over the default, `machine_identity=False`
  opts a deliberately unsigned agent out, and a core-only installation
  degrades to the unsigned path with a one-time warning. The test suite runs
  on a session-isolated throwaway key.

- `synapse git-claim --diff-base` now narrows safe tracked modifications to
  function or type scopes using local tree-sitter grammars for Python,
  JavaScript/JSX, TypeScript/TSX, Rust, and Go. Canonical `.synapse-symbol`
  descendants reuse the hub's existing path ancestry rule, so separate symbols
  can coexist without a wire change while class, file, and directory claims stay
  conservative ancestors. Old and new diff sides are both mapped; incomplete,
  unsupported, unsafe, add/delete/rename, or non-textual evidence widens to the
  whole file. The optional `semantic` extra installs individual upstream grammar
  wheels with no runtime download; `tools/semantic_diff_claims.py`, path filters,
  committed-head comparisons, owner-only JSON evidence, and owning
  test/generated companions expose the same local-first decision trail.

- `synapse fleet-scorecard` now composes the existing durable causality spans,
  opt-in accounting, advisory live-claim contention, evidence-only reliability,
  and optional benchmark history into one atomic owner-only JSON bundle. With
  the optional `otel` extra it pushes the same projection to the collector's
  standard trace and metric endpoints, fails visibly on either signal, and
  keeps historical benchmark backfill outside the current-gauge claim.

- MCP-first onboarding now includes identity-safe `synapse mcp` defaults, real
  `synapse_inbox` and `synapse_status` tools, a secret-free `.mcp.json` template,
  a dedicated PyPI/registry console entry, validated official-registry metadata,
  and client-specific setup guidance for Claude Code, Codex, Cursor, and generic
  stdio hosts. The guide keeps provider wake delivery separate from MCP tool
  discovery and excludes arbitrary local shell-command locking from the adapter.

- `synapse who`, `synapse status`, and `synapse doctor` now expose a durable
  per-identity mailbox pending count. Mailbox clients acknowledge every accepted
  live or replayed directed frame; the hub validates the logical recipient,
  journals a monotonic `mailbox_watermark`, restores it after restart, and reports
  unavailability instead of a false zero without a durable store. The count is
  explicitly receiver transport state, not evidence that a model processed work.

- `synapse arm install --identity NAME [--start]` now productizes the permanent
  waiter as a focused Linux systemd user-service path. It writes only the arm
  template, enables the exact `systemd-escape` instance on request, uses mailbox
  replay plus `Restart=always`, accepts remote URIs and protected token files,
  refuses persistent raw-token exposure, reports systemd failures as nonzero,
  and gives an honest WSL recommendation outside Linux.

- The hub now detects a dark seat when an identity owns an unexpired claim or
  assigned non-terminal board task without a fresh exact-identity `-rx` waiter.
  After a bounded grace it broadcasts one machine-readable `dark_seat_alert`
  per continuous episode with affected work and the explicit permanent-arm
  remedy; recovery re-arms detection without changing work authority.

- The React cockpit now incrementally renders `/receipts.json` and
  `/operator-actions.json` in a compact Audit inspector tab. Strict typed
  parsers, monotonic sequence cursors, de-duplication, and bounded retention
  keep the two durable histories distinct; `404`, failure, empty-present, and
  stale last-good states remain explicit. The production browser harness now
  uses a real temporary event store and proves a dashboard message receipt plus
  a journalled governed operator action in the built cockpit.

- The React cockpit command palette now exposes the dashboard's governed task
  declaration and task-update routes alongside operator messaging. Focused
  forms validate task IDs, dependencies, statuses, and notes; update IDs come
  from the live board but remain explicit-entry capable. A shared strict
  `{action, status, detail, ok}` parser fails closed on malformed or
  success-shaped non-2xx responses, and the production browser gate proves a
  parent/dependent declaration plus recorded update through a real hub.

- A dedicated, path-filtered `clients-cockpit` workflow now gates the strict
  TypeScript build, full Vitest coverage thresholds, the production Vite
  bundle, and a Chromium acceptance run against a real local hub and dashboard.
  The browser gate covers wrong and correct bearer handling, authenticated
  operator messaging, lock-on-`401`, service-worker cache discipline, and
  axe-core accessibility in both themes at desktop and phone widths. Failure
  traces use only a disposable test bearer. The root CI/preflight guard also
  fails closed when the workflow loses a required gate or the npm v3 lockfile
  drifts from `package.json` or loses registry integrity metadata.

- Every domain exception now derives from
  `synapse_channel.core.errors.SynapseError` and carries a stable
  machine-readable `code`, so boundary layers (CLI, A2A bridge, MCP server,
  embedding applications) can classify failures without matching on message
  text. Each class keeps its historical built-in base through multiple
  inheritance — every pre-existing `except ValueError`/`RuntimeError`/
  `PermissionError` clause keeps catching exactly what it caught before. The
  full class-to-code map is frozen by a registry test, and an AST drift gate
  refuses any future `*Error` class that does not join the taxonomy.

- Every generated systemd user unit (hub, presence, wake listener) and its
  checked-in `deploy/*.service` template now carries a sandbox block:
  `ProtectSystem=strict` + `ProtectHome=read-only` with narrow, code-derived
  `ReadWritePaths` (`~/synapse`; clients also `~/.local/share/synapse` for the
  trust-on-first-use key), `PrivateTmp`, `NoNewPrivileges`, `UMask=0077`,
  namespace/realtime/SUID restrictions, and per-role `LimitNOFILE` (65536 hub,
  4096 listeners). One shared module owns the block, tests pin the deploy
  templates to the renderers and pin the writable roots to the real storage
  modules, and the install paths create the writable directories up front.
  Directives that fail under a user manager (`ProtectClock`,
  `ProtectKernelModules`, `PrivateDevices`, `CapabilityBoundingSet`) are
  deliberately absent and test-forbidden. Measured on a live workstation the
  block moves a service from 9.8 (UNSAFE) to 7.4 (MEDIUM) in
  `systemd-analyze security --user`.

### Changed

- Directed `synapse send` now requests a negative delivery receipt by default.
  An online socket without a recent reaction or live waiter is classified
  `no_live_recipient`, recorded as a dead letter, and returns exit `1` just like
  an offline target; the durable chat is still best-effort routed and remains
  eligible for mailbox replay. Immediate receipt audits distinguish live,
  matched, and stale recipients plus their reason. Keepalives cannot clear the
  resulting blackhole entry. `--require-recipient` still prints positive
  receipts and additionally requires a receipt from older hubs.

- Stale-recipient liveness warnings and the matching `synapse who` annotations
  are now enabled by default for a bare hub. The existing
  `--warn-stale-recipients` spelling remains accepted, while
  `--no-warn-stale-recipients` provides an explicit compatibility opt-out.
  Directed messages remain journalled and best-effort routed; consume-stale-only
  matches now receive an honest negative delivery verdict instead of being
  promoted from socket presence.

- `cli_a2a_interop` now has a direct module-owned test surface covering parser
  defaults and overrides, live endpoint-URL and host/port receipt flows through
  the production HTTP bridge, operator-file output, invalid endpoints, and
  refused connections.

- `cli_a2a_types` now has a direct module-owned runtime and strict-typing test
  surface. The tests pin every callable alias's result contract, exercise real
  async, card, bridge, and store factories, accept the production A2A defaults,
  and reject a factory returning the wrong type.

- `core.handlers.offerings` now has a direct module-owned test surface covering
  capability normalization, private malformed-offer and quota rejection,
  resource aliases, journal persistence, idempotency memory, and broadcasts
  with or without a journal.

- `core.hub_http` now has a direct module-owned test surface covering response
  construction, bearer-versus-query token precedence, opt-in authorization,
  WebSocket-path fallthrough, private rejection, and the production Prometheus
  and health payloads.

- `core.state_scopes` now has a direct module-owned test surface covering empty
  state, self and same-owner exclusions, disjoint paths and worktrees,
  whole-worktree contention, and deterministic first-conflict selection.

### Fixed

- `syn say` now refuses a flag-shaped token in the target position with a
  local, actionable usage error. Previously `syn say --name X <target> <msg>`
  swallowed `--name` as the target and failed later inside `synapse send`
  with an unrelated parse error; the refusal points at the identity flags
  (`--project`/`--id`/`--type` before the verb) and at the trailing
  pass-through convention for package flags.

- Read-only query verbs (`who`, `state`, `board`, `manifest`) no longer exit
  `0` with no output when the hub accepts the welcome and then closes the socket
  before answering — an identity-pin refusal (`4013`), an ownership-lease
  refusal (`4016`), or a takeover. The query now surfaces the close reason and
  exits `1`, so a name pinned to another machine's key (the silent sink a
  borrowed shell's ambient name produced on the 0.99.2 workstation) fails
  visibly instead of looking like an empty roster. A genuine no-reply against a
  reachable hub still exits `0`.

- Fleet-scorecard history loading now stays at the CLI feature edge instead of
  importing `benchmark.trend` upward from the coordination kernel. Core accepts
  a minimal structural benchmark-run contract and preserves the existing JSON
  and metric shapes, restoring the package-decomposition boundary without an
  allowlist exception.

- Public security, protocol, glossary, staged-profile documentation, and the
  paranoid operator checklist now distinguish shipped opt-in controls from the
  remaining operator and external validation work. The evidence map pins the
  live team-secure, paranoid, identity/ACL, message-auth, signed-event/mTLS,
  federation, encryption, private-channel, and trust-graph boundaries.
  Federation lifecycle clocks are described as POSIX epoch time, and
  wire-version documentation now reflects the shipped version-2
  negotiate-down-and-warn path.

- The built React cockpit can now unlock a read-gated dashboard without putting
  its bearer in a URL. The server permits only files accepted by the existing
  cockpit-dist containment and content-type validator to load before read
  authentication; every live feed, history/proof/causality request, and
  operator write uses one authenticated fetch adapter. The bearer lives only
  in the tab's `sessionStorage`; a `401` clears it and removes the whole live
  presentation before the unlock veil returns. The service worker bypasses
  every credential-bearing request and caches only the token-free shell. The
  production build also keeps fonts as same-origin assets instead of
  CSP-blocked `data:` URLs. Loopback read-only and loopback operator dashboards
  retain their existing open-read posture. The 634-line React app shell was
  reduced below 500 lines by extracting the auth-bound feed lifecycle instead
  of growing a godfile.

### Documentation

- `ARCHITECTURE.md` is rewritten to describe the shipped system instead of the
  early prototype: the real module families, the default-on ownership keystone
  (ambient-identity refusal, hub-authoritative ownership lease, trust-on-first-use
  key pinning), the conflict-free multi-hub merge slice with its
  authority-never-merges boundary, and the stated scale ceiling. The previous
  revision still claimed the package had no CRDT slice and no cryptographic
  agent identity, which the 0.98–0.99 tree contradicts (external audit finding,
  2026-07-10).

## [0.99.1] - 2026-07-10

### Fixed

- **A core-only install could not arm a waiter on 0.99.0.** The machine
  identity provisioning called the Ed25519 primitives unconditionally, so
  `synapse wait`/`arm` crashed with `ModuleNotFoundError: cryptography` on
  any environment holding only the core dependency (`websockets`) — the
  optional `cryptography` package was never a hard requirement. Provisioning
  now degrades to an unsigned registration exactly as every pre-0.99 client
  behaved, and a hub without `cryptography` admits signed registrations
  unverified with a single actionable warning (`pip install
  synapse-channel[encryption]`) instead of refusing or crashing the frame
  handler. Trust-on-first-use pinning remains fully active wherever
  `cryptography` is installed. Regression tests pin the core-only arm path,
  both degradation branches, and the hub-side warning.

## [0.99.0] - 2026-07-10

### Added

- **Zero-config trust-on-first-use identity** (ownership keystone P-C). The
  first command that connects auto-provisions one Ed25519 keypair for the
  whole machine under `$XDG_DATA_HOME/synapse/identity/` (owner-only,
  exclusive-create, race-safe); the production waiter signs its registration
  with it and carries the public half in a new additive `identity_public_key`
  registration field. A hub running the default loopback posture verifies the
  self-contained proof with the same signed-event primitives the operator
  bundle uses (freshness, replay, sender binding) and **pins** the name to
  the key on first valid use — durably (`synapse hub --identity-pins`,
  default `~/synapse/identity-pins.json`; empty string keeps pins in
  memory). From then on the name binds only to a connection proving
  possession of the pinned key, across reconnects and hub restarts: a
  different key or a missing signature is refused with close code `4013`
  (`identity pin mismatch`) and a recovery path naming the pin file. Names
  that never sign keep classic first-come semantics, so no existing client
  is locked out, and `--require-identity-binding` still takes precedence
  with its operator-bundle semantics unchanged. The waiter yields (exit `4`)
  on an identity refusal instead of retrying. Provisioning is best-effort: a
  read-only home degrades to an unsigned connection, never a startup
  failure. The `4013` close code is now shared between the capacity refusal
  and identity refusals — clients disambiguate on the reason text
  (`is_identity_refused_close`).

- **Hub-authoritative name-ownership lease** (ownership keystone P-B). A name
  now has exactly one owner across reconnects, not merely per socket: a
  registration that declares `lease: true` on a free name is granted an opaque
  `owner_lease` token in a directed `lease_granted` frame (the hub keeps only
  a SHA-256 digest), and while the lease is live any claim on that name must
  present the token or it is refused with the new close code `4016`
  (`name owned`) — the `takeover` flag does not override it. A claim that
  presents the token still crosses the takeover damping, so the oscillation
  quarantine holds for lease holders too. The lease survives the holder's
  disconnect for `--lease-offline-ttl` seconds (default 3600, `synapse hub`
  flag / `SynapseHub(lease_offline_ttl=…)` / `TakeoverDamping`), then the name
  self-heals to first-come-first-owned, so a lost token file is a bounded
  wait, never a bricked name. The production waiter opts in end to end:
  `synapse wait`/`arm` persist the granted token per connect name under
  `~/synapse/owner-lease/` (atomic, `0600`) and present it on every re-arm, so
  a re-arm re-takes its own `-rx` identity and a stranger cannot squat the
  waiter in the gap — closing the squatting half of the 2026-07-10 identity
  incident class. A refusal is a *yield* verdict for the waiter (exit `4`,
  actionable message), not a retry. `SynapseAgent` exposes the same opt-in
  (`request_lease`/`owner_lease`/`on_lease_granted`) for any embedder;
  ephemeral one-shot verbs stay classic until the zero-config TOFU keypair
  (P-C) covers every verb uniformly. Fully additive on the wire: a pre-lease
  hub ignores the fields and a pre-lease client is never locked out of its
  own names.

### Changed

- **Ambient `$SYN_IDENTITY` is never a silent identity source** (ownership
  keystone P-A). It is honoured only when `$SYN_PROJECT` is also set and
  agrees with its project segment — the pair the shell hook exports together
  is the opt-in. An unqualified `syn` command in a shell carrying a lone or
  disagreeing `SYN_IDENTITY` (the borrowed-shell signature behind the
  2026-07-10 directed-delivery incident) now proceeds as the local identity
  and says so on stderr, or refuses (exit 2) when the local fallback also
  looks accidental; `syn name` reports the dropped ambient identity on an
  `ambient:` line. Behaviour change for shells that exported only
  `SYN_IDENTITY`: set `SYN_PROJECT` alongside it (what
  `synapse install-shell-hook` already does) or pass `--project`/`--id`.
  Pinned end to end against a live hub: the production `syn say` path in a
  poisoned shell authors as the local project, never as the borrowed name.

- The free tier now carries the ecosystem's standard donation surface: a
  low-key coffee line (Buy Me a Coffee, GitHub Sponsors, PayPal) with the
  canonical branded QR code under `assets/bmc_qr.png`, the BTC/ETH/LTC
  donation addresses and the TWINT link published at anulum.li, and
  `buy_me_a_coffee: anulum` joins `.github/FUNDING.yml` so the repository
  Sponsor button offers it too. Free tier only — paid surfaces stay
  donation-free.
- The footer logos render at one shared height (aspect ratios preserved)
  instead of one shared width, which had drawn them visibly unequal.
- README readability: the dense prose runs — the `syn` command ergonomics,
  the discovery/advisory-routing surfaces, the A2A bridge description, and
  the eleven security/governance profile paragraphs — are restructured into
  scannable tables, with the profile statuses corrected against the current
  design docs (identity+ACL: implemented in shadow mode with opt-in
  enforcement; policy engine: first tranche implemented, advisory; signed
  events + mTLS: runtime primitives shipped). New section: *Beyond one
  machine — SYNAPSE CHANNEL Fleet*, a factual description and topology map
  of the commercial multi-machine tier, cross-linked from the
  single-machine limitation and the licensing section.

## [0.98.27] - 2026-07-10

### Fixed

- Takeover is now atomic from every other session's point of view: the hub
  rebinds the name to the new socket *before* the eviction close handshake
  awaits (swap-then-close). Previously the identity→socket map kept pointing
  at the dying socket across that await, so a directed message racing the
  takeover could be sent to the evicted socket (and lost), and two takeovers
  racing the same name could each "evict" the same stale owner and co-bind
  two live sockets to one identity. Pinned against a live hub: a probe one
  scheduler step into the eviction sees the new owner, and racing takeovers
  end with exactly one bound survivor that receives directed mail.

### Added

- A coverage-exclusion ledger gate: every `pragma: no cover` in `src/` (49,
  audited — Protocol bodies, optional-dependency import guards, typing-only
  imports, entry points, environment-defensive branches) and every
  conditional test skip (32 — platform guards, optional-dependency guards,
  operator-triggered real provider smokes; zero unconditional skips, zero
  xfails) is now enumerated with a justification class in
  `tests/test_coverage_exclusion_ledger.py`, and the suite fails the moment
  the tree and the ledger disagree — exclusion counts can never drift
  silently in either direction again.

### Changed

- The vanished-recipient branches of the hub's directed send
  (`send_to_agent`: recipient with no live socket; socket that died before
  the hub pruned its binding) are now pinned by real tests — an actually
  closed server-side connection and a genuinely empty registry — instead of
  being excluded from coverage as "unreachable"; a channel member that
  disconnects mid fan-out reaches the first branch through public routing.
  Both misses report `False` to the caller, never raise.
- The legacy broad project-scoped arm detector
  (`legacy_project_scoped_terminal_sidecar`) now lives once in
  `waiter_identity` — the module that owns the ``-rx`` sidecar naming
  convention — instead of as two byte-identical private copies inside
  `arm` and `wait` dispatch. No behaviour change; both commands refuse
  the legacy shape exactly as before.

## [0.98.26] - 2026-07-10

### Added

- The persistent waiter states its binding out loud: the first line of
  every arm names exactly whose messages it wakes on
  (``waiting for messages to <identity>``), and when the session
  environment carries a DIFFERENT ``SYN_IDENTITY`` a second line says so
  explicitly — ambient env never overrides an explicit name, and the
  mismatch (the classic sign of arming from a borrowed shell) is now
  visible immediately instead of after a night of missed messages.

### Fixed

- Mailbox surfacing: a directed message is now either surfaced to the
  operator or stays pending — never silently consumed. A wake prints
  EVERY frame collected in its window (a replay burst previously printed
  only the last frame while the persisted cursor advanced past the whole
  backlog), the persisted resume point covers exactly the surfaced
  frames, and the mailbox cursor/ack no longer advances past a frame the
  wake filter refuses (`SynapseAgent` gains an optional
  ``mailbox_advance`` gate wired to the waiter's own wake predicate).
  Pinned end to end against a live hub with a durable journal, including
  the incident-shaped two-drain regression.
- Waiter identity binding: the ``SYN_TMUX_PROVIDER`` session marker now
  suppresses a passive arm/wait ONLY for the session's own
  ``$SYN_IDENTITY`` — an explicitly named waiter for a different seat is
  never yielded away. Previously any live provider session made every
  explicit ``syn-wait --name <other-seat>`` refuse to arm, so directed
  messages to that seat were lost from the live path while broadcasts
  kept flowing (P0, found live on the coordination fleet 2026-07-10).
  A dedicated regression surface pins the contract end to end against a
  live hub: explicit ``--name``/``--for`` beats ambient environment, the
  ambient identity's own arm still yields to its provider, and a foreign
  provider pidfile or corrupt pidfile never suppresses an explicit arm.
- Federation store durability: the peering store is now written atomically —
  to an owner-only sibling temporary file that is fsynced, renamed into place,
  then followed by a parent-directory fsync — so a power loss can neither leave
  a torn or partial policy behind nor lose a completed write. Loading validates
  every field deny-by-default (store version, record shape, provenance types,
  duplicate domain ids) and raises rather than admitting a partial or ambiguous
  policy, so a malformed store can never silently authorise or drop a peering.
- Identity resolution no longer splits a session across two projects: a
  ``$SYN_IDENTITY`` whose project segment disagrees with an explicit
  ``$SYN_PROJECT`` (the borrowed-shell signature of the directed-delivery
  incident) is no longer taken verbatim as the identity — since it did not
  supply the project, the identity falls back to the resolved bare project, so
  identity-scoped and project-scoped verbs always follow the same name.

## [0.98.25] - 2026-07-10

### Changed

- Split the `encrypt-key` CLI by domain: hardware-backed wrapping
  (`cli_encrypt_key_hardware` — PKCS#11/TPM 2.0/cloud HSM), threshold
  escrow (`cli_encrypt_key_escrow`), attestation gating
  (`cli_encrypt_key_attest`), and the at-rest profile lifecycle incl.
  SQLCipher (`cli_encrypt_key_profile`); `cli_encrypt_key` keeps the
  local key-file commands and composes the family under the same
  subparser group. Command surface, registration order, and every
  message unchanged; the test surface splits along the same lines.

- Decompose the dashboard HTTP handler: read-side feed serving moved to
  `dashboard_feed_serving` (pure `FeedResponse` values, the shared
  honest-absence/fail-visible store posture factored into one place) and
  the operator write-path validation/relay dispatch to
  `dashboard_operator_writes`; the handler keeps only routing, auth, and
  response plumbing. Behaviour-preserving — every route, status code, and
  message byte-identical; both new modules carry dedicated test surfaces
  at 100% line+branch.

## [0.98.24] - 2026-07-09

### Release integrity
- The 0.98.23 notes described the Cloud HSM / KMS KEK backend, threshold
  escrow, hardware attestation gate, `synapse sqlcipher rekey`/`migrate`, and
  the Studio operator-actions panel, but the 0.98.23 tag predates those
  commits — they ship in this release.

### Added
- Pinned-TLS multi-hub pull: `synapse multihub follow --pin sha256:<hex>` and
  `core.multihub_transport.pinned_connector` accept a self-signed or private-CA
  `wss://` peer by live certificate pin, mirroring the `federation fetch --pin`
  ceremony; a mismatch or plaintext URI fails the pull closed with the cursor
  unadvanced. Shared client-pin primitives live in `core.tls`
  (`pin_trust_client_context`, `live_peer_certificate_pin`).
- `synapse hub --multihub-watch-pin PEER=sha256:<hex>` (repeatable): the
  standing watch pulls a pinned self-signed `wss://` peer through the same
  pinned connector; a pin naming an unwatched peer is refused at startup.
- `--observed-pin HUB=sha256:<hex>` (repeatable) on `synapse who`, `state`,
  `status`, and `dashboard`: advisory observed-peer pulls accept a self-signed
  `wss://` peer by certificate pin; a pin naming an unfetched hub is refused.

### Changed
- CI installs `sqlcipher3-binary` in the test matrix so the SQLCipher at-rest
  suite runs instead of skipping.
- Dashboard HTML rendering extracted from the server module into
  `dashboard_render.py` (one escape helper, per-section renderers, cockpit
  hand-off); `dashboard.py` keeps the snapshot model, HTTP handler, and server.

### Fixed
- Remaining ad-hoc numeric coercions on untrusted fields moved to
  `safe_int`/`safe_float` with each site's fallback semantics preserved: A2A
  task-timeout sweep and store retention stamps (a hostile `updatedAt` mapping,
  a JSON integer too large for a double, or NaN no longer crashes the sweep or
  poisons retention ordering), replay claim reconstruction `epoch`/`version`,
  adaptive-TTL claim timestamps, lease-table rendering, and the relay resource
  `capacity` (now failing with the command's `ValueError` contract instead of a
  raw `OverflowError`/`TypeError`).

## [0.98.23] - 2026-07-09

### Added
- Cloud HSM / cloud KMS KEK backend for at-rest wrapped keys
  (`synapse encrypt-key generate-wrapped-cloud-hsm`): `local-aes-kw` master-key
  file for offline use and `aws-kms` via optional `boto3`
  (`synapse-channel[cloud-hsm]`). Same envelope format as passphrase / PKCS#11 /
  TPM2 backends.
- Threshold (Shamir GF(2^8)) key escrow and recovery:
  `synapse encrypt-key escrow-split` / `escrow-recover` — split a raw 32-byte
  data key into n shares with threshold k; recover writes a new owner-only key
  file (never a bypass).
- Hardware attestation gate for at-rest trust decisions:
  `synapse encrypt-key attest-policy-create` / `attest-create` / `attest-verify`
  (HMAC-SHA256 software path; `tpm2-quote` algorithm tag for TPM-oriented
  policies). Fail-closed PCR + signature checks before operators trust a host.
- `synapse sqlcipher rekey` / `synapse sqlcipher migrate` — top-level SQLCipher
  maintenance (``PRAGMA rekey`` page rotation and plaintext→encrypted copy);
  aliases the existing encrypt-key sqlcipher ops for operator discoverability.
- Studio command centre **operator actions** panel polls
  `/operator-actions.json` (governed relay history); dashboard startup prints
  the operator-actions and sessions feed URLs when `--feeds-db` is set.

### Documentation
- README documents SQLCipher as a first-class at-rest feature: install extra,
  hub `--db-key-file`, migrate/rekey, analysis/doctor readers, complementary
  whole-file AES-GCM envelopes, and honest limits (replaces “planned” wording).
  Scrypt cost flags (`--scrypt-n`/`--scrypt-r`/`--scrypt-p`) called out for
  passphrase key generation.

### Added
- SQLCipher `--db-key-file` on `reliability` and `workflow contention`
  (core `run_reliability_report` / `run_yield_advice` already accepted
  `key_file`; CLIs now pass it through).
- SQLCipher key material for multihub offline observe and MCP store joins:
  `multihub observe --db-key-file`, MCP `event_store_key_file` on
  `synapse_route_task` / `synapse_memory_recall` (bridge + registration).
- SQLCipher `--db-key-file` on session metrics and semantic routing store
  joins: `participant costs`, `route-task --event-store`, dashboard sessions
  feed (via `--feeds-db-key-file` context), and core
  `run_session_metric_report` / `read_observed_capability_index`.
- SQLCipher `--db-key-file` on multi-seat operator store readers: `approval
  status`, `trust-graph`, `ttl-advice`, `cross-repo` (with `--db`), and
  `sandbox run --attest` (core `run_*` / `_attest_run` accept `key_file`).
- SQLCipher `--db-key-file` on remaining analysis CLIs: `causality` (including
  otel/health/contention; federated peers share the same key file when set),
  `accounting report`, `memory-recall`, `debug`, and `reproduce` (core
  `run_*` / `read_memory_recall` / `load_task_for_seq` accept `key_file`).

## [0.98.22] - 2026-07-09

### Changed
- Multi-seat golden path default closeout is evidence-gated release:
  `synapse verify-release` → receipt → `synapse release --receipt` (quickstart
  step 6 + recipes section). Bare release remains for emergency drops only.
- Dashboard front door is the Studio command centre: `GET /` and
  `/studio/command` serve the same hub-independent shell; the pre-Studio hub
  HTML moves to `/classic`. CLI startup prints Studio URLs first. Quick start
  gains a multi-seat golden path ending in Studio.

### Added
- SQLCipher `--db-key-file` on analysis CLIs: `event-query`,
  `postmortem`, `merkle root`/`prove` (core run_* accept `key_file`).
- `synapse a2a-interop-trace`: independent stdlib HTTP client against a live
  A2A bridge (discovery + message:send + GET task) with structured receipt;
  Independent interoperability matrix row is now `partial`.
- SQLCipher key for store readers: `ingest`/`compact --db-key-file`,
  dashboard `--feeds-db-key-file` (store-backed feeds + reliability).
- SQLCipher `encrypt-key rekey-sqlcipher` (in-place PRAGMA rekey) and
  `synapse doctor --db-key-file` check that the encrypted event store opens.
- SQLCipher live event store: optional `synapse-channel[sqlcipher]` extra
  (`sqlcipher3-binary==0.6.0`), `EventStore(..., key_file=)` / `key=`,
  `synapse hub --db-key-file`, and offline
  `synapse encrypt-key migrate-sqlcipher` for plaintext→encrypted migration.
  Page-level encryption for main DB + WAL; stock install stays dependency-free.
- Studio `observed_fleet` section on `/studio.json`: multi-hub / FLEET advisory
  peer health (reachable, lagging, unreachable) from dashboard
  `observed_peers`. Command centre shows a peers headline and panel.
- `synapse doctor` multi-seat trust checklist: `--multi-seat`,
  `--identity-trust`, and `--role-grants` feed `check_multi_seat_posture`
  (token + trust + role materials → `--team-secure` remedy). Auto-detects a
  multi-seat roster. New `deaf-agents` check warns when live agents lack a
  matching `-rx` waiter.
- ACL permission verbs `mailbox` and `role-claim`: a policy can grant mailbox
  backlog replay for a non-self/non-`-rx` monitor (target kind `agent`) and
  authorise heartbeat role bindings alongside the role-grant store (target kind
  `role`). Wired into `_mailbox_recipient` and `permitted_role_claims`. See
  `docs/identity-and-acl.md`.
- `synapse hub --team-secure` multi-seat trust profile: requires a connect
  token, `--identity-trust` (forces identity binding), `--role-grants` (forces
  role-claim enforcement), and private directed messages. Prints recommended
  next hardening (message-auth, ACL, TLS/`--paranoid`, durable `--db`) without
  blocking startup. Composes with `--paranoid` for multi-seat + exposed binds.
  See `docs/team-secure.md`.
- Universal receipt projections now expose release/claim evidence, delivery
  receipts, sandbox run attestations, approval/policy/verification notes,
  operator relays, cross-hub pointers, A2A validation notes, and postmortem
  notes in one read-side shape. `synapse dashboard --feeds-db` serves the
  projection at `/receipts.json`, the cockpit prefers that feed when present,
  and `synapse event-query <db> "universal-receipts all"` renders the same
  first-class receipt objects without changing the legacy delivery-receipt
  query.
- The public API freeze now pins the exact `synapse_channel.__all__` export
  list, and the docs include a 0.x to 1.0 migration guide covering upgrade
  order, wire-version checks, stable-surface guards, and release-cut checks.
- The Studio `/studio.json` projection now includes a security-posture section
  covering sandbox grants, ACL/role visibility, dashboard exposure evidence,
  signed federation observation, and receipt evidence. `/studio/command` renders
  the same rows as a read-only posture panel beside the Coordination Clock.
- The Studio command centre now has a persistent NavRail, a HeaderBar with the
  live hub id, version, verdict, and connection state, and a LiveFeed panel that
  tails `/events.json` when the dashboard is started with `--feeds-db`.
- Declarative workflows now support step-level `requires` evidence predicates
  for proof-carrying steps. `workflow plan` and `workflow run` accept an
  `--evidence` snapshot and hold a step until its required receipt, test,
  policy, approval, sandbox, mailbox, dead-letter, or claim evidence matches.
- `synapse a2a-conformance` now prints the local A2A 1.0.0 support matrix as
  Markdown or JSON, including supported, partial, unsupported, and externally
  gated rows for the bridge.
- A2A push delivery now has an injectable `WebhookDeliveryClient` for local
  validation harnesses, plus real HTTPS receiver and 307 reverse-proxy redirect
  tests that keep the production default fail-closed against local/private
  webhook targets, including delivery-time DNS rebinding to a local receiver.
- The A2A deployment threat model now records the exposed-bridge posture for
  bearer auth, TLS/proxy placement, state-file handling, webhook egress,
  logging, and receipt evidence.

## [0.98.21] - 2026-07-09

### Fixed
- The quickstart `SynapseAgent` code sample now waits for `checkpoint_saved`
  and `release_granted` hub replies before shutting down, and its E2E test polls
  the durable timeline for claim/checkpoint/release evidence to avoid CI timing
  races.

## [0.98.20] - 2026-07-09

### Added
- `synapse doctor` now accepts `--federation-path PEER=MODE` for proxy-path
  diagnostics. Direct mTLS/WSS, TLS passthrough, and tailnet paths are reported
  as valid federation shapes, while TLS-terminating reverse proxies are flagged
  as a different trust boundary for certificate-pinned hub federation.

## [0.98.19] - 2026-07-09

### Added
- `synapse federation list` now shows each imported peering's bundle expiry
  distance and credential rotation state (`steady`, `overlap`, or
  `incomplete`). The dashboard federation feed exposes the same lifecycle fields
  so operator surfaces distinguish active, expired, revoked, stale, and
  add-new-before-retire rotation windows consistently.

## [0.98.18] - 2026-07-09

### Added
- Multi-hub peer pulls now measure local-minus-peer clock skew from the peer
  `welcome` timestamp and carry it through `network_fetcher`,
  `MultiHubFollower`, observed-peer snapshots, `who`, and `status` JSON/human
  output. Federated causality queries accept `--clock-skew HUB=SECONDS` plus
  `--skew-warn-seconds` so offline merged-log reports can flag when their
  timestamp-ordered cross-hub evidence exceeds the operator's skew threshold.

## [0.98.17] - 2026-07-09

### Changed
- The A2A bridge now carries `a2aTaskId` and `a2aContextId` in structured
  SYNAPSE chat metadata instead of appending inline `[A2A-TASK:...]` markers to
  message text. Inbound marker-looking text is treated as reply content, while
  metadata-correlated replies must carry both task and context ids.
- README and public-surface documentation now map the single-package install into
  core, adapter, analysis, governance, and experimental layers using the
  `surface_taxonomy` tiers, making the lean coordination core explicit without
  implying a package split.
- README now folds the Coordinate / Observe / Govern promise into the lead and
  anchors each loop to shipped coordination, observation, and governance
  surfaces.

## [0.98.16] - 2026-07-09

### Added
- `synapse who`, `synapse status`, `synapse state`, and `synapse dashboard` now accept
  repeatable `--observed-peer HUB=URI` flags. Each peer is fetched through the existing
  multi-hub event-log pull, folded into an advisory `observed@HUB` view, and rendered as
  peer rows, observed claim counts, dashboard JSON/HTML data, or status counters without
  mutating local claims or granting local authority.

## [0.98.15] - 2026-07-09

### Fixed
- Numeric input coercion now uses the shared `safe_int`/`safe_float` helpers across hub config,
  channel history limits, mailbox cursors, claim TTLs, resource/capability bounds, semantic and
  memory query limits, dead-letter snapshots, and dashboard write-rate windows. Non-finite,
  overflowing, and malformed values now preserve the existing fallback or clamp semantics at each
  call site instead of escaping through local ad-hoc conversions.
- Delivery receipt requests, immediate verdicts, deferred mailbox-ack verdicts, and pending-window
  expiries are now journaled as audit-only receipt events. Unsettled immediate failures re-seed the
  pending receipt map on hub restart, and `synapse event-query <db> "receipts <agent>"` exposes the
  durable ledger even when the original sender is offline.
- `synapse federation fetch` now accepts `--pin sha256:<hex>` for `wss://` peers that use
  private-CA or self-signed certificates. The fetch uses an unverified TLS context only in explicit
  pin mode, hashes the live peer certificate immediately after the handshake, and fails closed on a
  missing TLS certificate or pin mismatch.
- Multi-hub network pulls now negotiate the peer hub's advertised wire version from the `welcome`
  frame. Version-skewed peers are accepted, logged as operator-visible warnings, and recorded on
  `MultiHubFollower` at the lowest common effective wire version instead of being silently ignored.
- `synapse dashboard --feeds-db` now serves `/operator-actions.json`, a durable operator-action
  history feed reconstructed from `operator_relay` audit events with real sequence and timestamp
  anchors, relay direction, status, reason, break-glass tag, and peer/requester provenance.
- Forwarded multi-hub claims now distinguish owner timeouts from generic ownership refusals,
  expose forwarded/granted/denied/timeout counters on `/metrics`, and treat duplicate owner-side
  `(task_id, claimant)` retries as idempotent relays of the existing lease.
- `synapse doctor` now has opt-in federation checks for named peers: reachability through the
  multi-hub log request path, cursor lag via `log_end_seq`, measured welcome-frame clock skew, TLS
  certificate expiry warnings, and imported bundle expiry/revocation state from a federation store.

## [0.98.14] - 2026-07-09

### Fixed
- `agent-tmux` now strips the provider-only `SYN_TMUX_PROVIDER` marker from its
  one-shot `synapse wait` subprocess. Without this, the wait child took the
  shell-hook provider-yield path, exited successfully without a real message,
  and the bridge interpreted that yield as a wake to inject into Grok/Kimi panes.

## [0.98.13] - 2026-07-08

### Fixed
- Provider tmux sessions now pass `SYN_PROJECT`, `SYN_IDENTITY`,
  `SYN_TMUX_PROVIDER`, and `SYNAPSE_AUTO_CONNECT` through tmux `new-session -e`
  flags before the pane shell starts. This prevents Fish startup hooks from
  auto-arming a second passive receiver before the later `env ... grok` command
  applies the provider environment.

## [0.98.12] - 2026-07-08

### Fixed
- `synapse doctor` now suggests exact-identity waiter arming for missing `-rx`
  waiters, so terminal/provider identities no longer see a broad-project
  `--for <project>` hint after the wake-loop hotfix.
- Tmux wake prompts now suppress routine no-op status replies: provider panes
  reply once only for actionable directed work and stay quiet for peer-status
  chatter or empty inbox wakeups, preventing restarted Grok/Kimi sessions from
  re-entering a status-broadcast loop.
- Provider wrappers and tmux-launched provider panes now force
  `SYNAPSE_AUTO_CONNECT=0`, preventing an inner Fish/Bash shell from auto-arming
  a second passive receiver under a different `user/terminal-*` identity.

## [0.98.11] - 2026-07-08

### Fixed
- Shell-hook and `syn arm` generated receivers now wait on the exact terminal identity rather than
  the broad project lane, preventing `user/*` traffic from waking every terminal-side passive
  receiver.
- Passive `synapse wait`/`synapse arm` also derive the terminal identity from an explicit `*-rx`
  connection name before yielding to a live tmux pane bridge, so legacy broad `--for user` waiters
  for provider-backed terminals stop instead of competing with the pane bridge.

## [0.98.10] - 2026-07-08

### Added
- Receiver wake capability is now explicit in roster and receipt surfaces: direct agents, passive
  socket waiters, and pane-bridge waiters are distinguished so operators can tell "socket
  delivered" apart from an agent pane that can actually be woken.
- Tmux-backed provider sessions now export `SYN_TMUX_PROVIDER=1` alongside `SYN_PROJECT` and
  `SYN_IDENTITY`, giving inner agent shells a stable marker that the session's `agent-tmux wait`
  owns the long-lived `-rx` listener for the identity.

### Fixed
- Directed delivery receipts now count a live `identity-rx` waiter as delivery to the logical
  `identity`, closing the gap where a message reached the sidecar but was still reported as
  undelivered to the sender.
- Passive `synapse wait`/`synapse arm` instances now yield when an active tmux pane-bridge provider
  already owns that identity's receiver, avoiding repeated supersession churn, name instability, and
  duplicate plain waiters in provider-backed sessions.
- Grok CLI participant status text and source warnings now match the more stable CLI launch path and
  keep the smoke-test status output within the repository's formatting gate.

## [0.98.9] - 2026-07-08

### Added
- Opt-in stale-recipient warning (`synapse hub --warn-stale-recipients`): a directed message to a
  recipient that is present but not proven wake-capable — no live `-rx` waiter sidecar and no
  genuine reaction within `--recipient-liveness-window` seconds (default 90) — draws a private
  `recipient_liveness_warning` back to the sender, so a reply that never comes is surfaced instead
  of silently waited on. Off by default, so an open hub tracks no reactions and warns nobody; the
  message is still delivered and journalled unchanged. Closes the "online but deaf agent"
  coordination gap where presence outlived liveness.
- `synapse who` roster liveness (same opt-in): a present-but-deaf agent is marked `(deaf …)`, and a
  trailing `Unarmed (present, no live waiter)` line names the agents an operator should re-arm. The
  who snapshot carries an additive `agent_liveness` field when the warning is on.
- Waiter liveness now requires a *live* sidecar, not merely a present one: a `-rx` socket counts as a
  waiter only while its keepalive is fresh within `--waiter-liveness-window` seconds (default 20), so
  a hung or exiting waiter no longer vouches for its agent. A `synapse-arm@` systemd unit
  (`synapse init --install-user-services`, `Restart=always`) is the documented self-healing waiter
  path; `docs/recipes.md` covers it.

## [0.98.8] - 2026-07-07

### Added
- The capped board snapshot now carries the applied bound as `task_cap`, alongside the existing
  `total_tasks` and `truncated`, so a dashboard or cockpit can render a "kept / cap" gauge
  instead of only knowing the page was trimmed. Absent when the board is served without a cap.

### Changed
- `syn-wait` now enables `--mailbox` by default, so every waiter launched through the alias
  recovers the directed messages that arrived while it was disconnected (a reconnect or re-arm
  gap) instead of leaving them unread until an unrelated wake. Pass `--no-mailbox` to opt a
  waiter out; a bare `synapse arm` is unchanged (mailbox off). Against a hub older than wire
  version 2 the request is ignored, so the default is safe on a mixed-version fleet.

## [0.98.7] - 2026-07-07

### Fixed
- `syn-wait` now waits for a single directed message and then exits, so a harness that
  re-invokes an agent when its background task ends is actually woken. The alias previously
  mapped to `synapse arm` with no wake limit, which re-arms internally and never exits: the
  wake it printed stayed in the process's block-buffered stdout and the agent was never
  re-invoked — a waiter that held presence but woke nobody. The alias now defaults to
  `--max-wakes 1` (an explicit `--max-wakes` is still honoured) while keeping `arm`'s
  self-healing reconnect, so a dropped connection or a hub restart re-arms transparently and
  only a real wake ends the wait. `syn arm` is unaffected and stays persistently armed.

### Added
- Directed-message backlog replay on reconnect: a client that declares `mailbox: true` and a
  `since_seq` cursor on its registration heartbeat is delivered the directed messages it missed
  while offline, replayed from the durable journal as ordinary chat frames marked `replayed` —
  turning the manual `syn-inbox` catch-up into an automatic push on reconnect. Every chat frame
  now carries its durable journal `seq`, the stable cross-restart cursor a client resumes from
  and dedups on (the per-hub `msg_id` resets on restart, so it is not a durable cursor). Only
  messages directed at the client (by name, project, glob, or a role it holds) are replayed,
  never broadcasts, bounded by a per-reconnect scan cap; a hub with no durable journal does not
  replay. Payload-only — no new wire type, so the wire protocol version, the reserved envelope
  keys, and the federation consumer surface are unchanged.
- Deferred delivery receipts for directed messages that arrive after a reconnect. When a
  receipt-requested directed message reaches no live recipient, the hub still answers
  `delivered: false` at once, but now remembers it under its durable journal `seq` in a bounded
  pending-receipt store. A reconnecting recipient that drains the message from its backlog
  acknowledges it with the new `ACK` verb (client→hub), and the hub revises the verdict by
  sending the original sender a second `delivery_receipt` marked `delivered: true, deferred:
  true` — closing the gap where a sender was told "not delivered" and never learnt the message
  arrived. The acking client is re-checked as a genuine recipient of the target before the
  receipt is issued, so a spoofed ack neither fabricates a receipt nor drops the pending one. The
  wire protocol version is bumped to `2`; the `ACK` verb is additive and backward-compatible by
  construction — a client emits it only when the hub advertises version `2` or newer, and an
  older hub is never sent it, so no enforcement is added and the federation consumer surface is
  unchanged.
- Client mailbox mode on the reusable `SynapseAgent`. Constructed with `mailbox=True`, the
  agent declares its `since_seq` cursor on every registration heartbeat so a mailbox-capable
  hub replays the directed backlog it missed while offline; it advances the cursor on each chat
  frame it sees and acknowledges every replayed frame, so the hub can confirm a deferred
  delivery receipt to the original sender. A seeded `mailbox_since_seq` and the read-only
  `mailbox_cursor` property let a caller persist the cursor across reconnects and resume the
  backlog where it stopped. Off by default — an ordinary agent's registration and dispatch are
  unchanged, and a mailbox agent talking to a hub that predates the ack verb simply withholds
  the acknowledgement.
- A mailbox client may declare `mailbox_for` — the identity whose backlog to replay when it
  differs from the connection name — so a wake-listener connecting under a receive-only `-rx`
  name receives the messages directed at its bare identity rather than at the `-rx` name it
  connects under. Absent or blank, the hub replays the backlog for the connection name itself,
  unchanged; roles are still read from the connection the client bound them to.
- `synapse arm --mailbox` wakes a persistent waiter on directed messages that arrived while it
  was disconnected. On each connect the waiter resumes from a per-identity `since_seq` cursor
  and asks the hub to replay the directed messages it missed during a reconnect or re-arm gap,
  so a message that landed in that gap wakes it on the next connect instead of waiting unread
  until an unrelated wake. The cursor is persisted under `~/synapse/mailbox-cursor/`, keyed by
  the waited-on identity, so a re-arm resumes where it stopped rather than being replayed — and
  woken by — the whole retained backlog again. Off by default; a plain `arm` is unchanged.

## [0.98.6] - 2026-07-07

### Added
- Role-based addressing: an identity can answer to one or more `<project>/<role>` roles in
  addition to its instance name, so a directed message to a role (for example
  `SYNAPSE-CHANNEL/coordinator`) reaches whichever instance currently holds it. A waiter,
  the `arm` keeper, and the relay-log inbox (`synapse relay --for`) take a repeatable
  `--role <project>/<role>`; the waiter wakes and the per-agent inbox surfaces messages
  addressed to a role it holds, alongside those to its name. The addressing matchers
  (`is_recipient`, `is_directed`, `wakes`) gained an optional `roles` argument that is
  empty by default, so every existing name/project/glob match and the anti-wake-storm
  behaviour are unchanged. This closes the gap where a message addressed to a role bound to
  no instance name matched nobody — it neither woke nor inboxed the holder and was counted a
  dead letter.
- Hub-side role registry: an agent declares the roles it answers to on its registration
  heartbeat; the hub binds them (cleared on disconnect) so directed delivery resolves a role
  to whichever agents hold it. A message to a role with a live holder is no longer counted a
  dead letter (so it cannot raise a false dead-letter escalation), and the `who` snapshot now
  carries `agent_roles` showing who holds what. Roles are additive addresses, not exclusive —
  a role may be held by more than one agent and a message to it reaches every holder. The
  heartbeat `roles` field is parsed defensively (a non-list is ignored, non-string or blank
  entries are dropped) so a malformed field degrades to no roles rather than dropping the
  socket. Roles ride in the heartbeat payload and the `who` snapshot, so the wire protocol
  version, the reserved envelope keys, and the federation consumer surface are unchanged.

## [0.98.5] - 2026-07-07

### Fixed
- The participant turn-result parser and the relay-log reader are now hardened against non-finite and
  double-overflowing numbers. `turn_result_from_payload` reads a participant's `cost_usd`, token counts,
  and rate-limit signal off an untrusted bus payload with a bare `json.loads` (which accepts the
  non-standard `Infinity`/`NaN` tokens), and its `_as_int` passed a non-finite float straight to `int()`
  (which raises); a 400-digit integer also overflowed `float()`. The three coercers now default a
  non-numeric, non-finite, or overflowing value to zero (or `None` for the optional signal) so a malformed
  turn result cannot crash the bus handler awaiting it. `relay.decode_lite`'s existing tolerance of a
  malformed log entry (a non-numeric `t`/`i` defaulted to zero) now also covers a non-finite value, whose
  `int()` conversion raised `OverflowError` outside the caught set. Part of the non-finite-number family.
- A claim's `ttl_seconds` and a frame's `epoch`/`expected_version` are now guarded against non-finite and
  double-overflowing values. The claim handler converted `ttl_seconds` with a `float()` that caught only
  `TypeError`/`ValueError`, so a 400-digit integer raised an uncaught `OverflowError` out of the frame
  handler (dropping the socket), and a `1e400` (or `"inf"`) became an `inf` lease expiry — a task claimed
  with an `inf` ttl could never be taken over (a permanent lock), while a `nan` ttl read as instantly
  expired. `SynapseHub._optional_int`, which reads `epoch`/`expected_version` on the claim/renew/release/
  checkpoint frames, passed a non-finite float straight to `int()`, which raises. Both now treat a
  non-numeric, non-finite, or overflowing value as absent: the ttl falls back to the hub's default lease
  duration and the optional int to `None`. Found by live fault-injection of the claim path; part of the
  non-finite-number family below.
- A chat frame's client-supplied `timestamp` is now coerced to the hub clock when it is not a usable
  instant, instead of crashing the handler or broadcasting a non-finite time. The handler stamped the
  message with a bare `float(data.get("timestamp") or time.time())`, so a non-numeric timestamp (a string
  or a list) raised `ValueError`/`TypeError`, and a double-overflowing integer raised `OverflowError` —
  none caught by the connection loop (which handles only `ConnectionClosed`), so a single hostile chat
  dropped the sender's socket with a traceback. A finite-looking `1e400` (or a `"timestamp": "inf"`)
  instead decoded to `inf` and was retained in history, journalled, broadcast to every socket, and used as
  the dead-letter ledger's ordering key. The timestamp is advisory client metadata, so a missing, falsy,
  non-numeric, non-finite, or overflowing value now falls back to the hub's authoritative `time.time()`;
  a finite client timestamp is still preserved. Found by fault-injection of the chat handler (the hottest
  untrusted path); part of the non-finite-number family below.
- The federation gate now stays deny-closed when a peer certificate reads but does not parse. The gate
  already wraps the certificate *read* so a socket in a strange state (or an injected certificate source)
  cannot crash the frame handler, and refuses a peered key's cross-domain claim it cannot pin. Computing
  the pin was outside that guard, so a certificate that read as non-empty bytes but did not parse would
  have raised out of the handler. The pin computation now shares the guard: an unparsable certificate is
  treated exactly like a failed read — deny-closed for a peered key, degrade-to-local for a local key —
  never a crash. Defence in depth: the production certificate source returns the TLS-validated peer DER,
  so this is not reachable on a live mutual-TLS connection, but it completes the gate's "any certificate
  failure fails closed" invariant. Found by fault-injection of the federation trust gate.
- The federation-bundle and multi-hub numeric guards also reject a JSON integer too large for a double.
  The `NaN`/`Infinity` guards added above convert with `float()` and check `math.isfinite`, but a
  400-digit integer is finite JSON that passes the decoder yet raises `OverflowError` on the `float()`
  conversion (and on `math.isfinite` of the raw int). A peer bundle's `expires_at` or a peer event's `ts`
  set to such an integer therefore still escaped as an unhandled `OverflowError`; both guards now catch it
  and raise their own malformed-input error (`FederationStoreError` / `MultiHubWireError`). Completes the
  non-finite-number family below — the finding coercion helper already caught this case.
- The bounded frame decoder now rejects the non-standard `NaN`/`Infinity`/`-Infinity` JSON tokens. RFC
  8259 has no such literals, but `json.loads` accepts them by default, so a non-finite float could enter
  through any inbound frame or peer response and then break an ordering comparison (`nan` compares
  unequal to everything) or overflow an `int()`/`float()` conversion downstream. `loads_bounded` — the
  single depth-bounded loader every inbound frame, peer sync body, and federation reply passes through —
  now raises `json.JSONDecodeError` for a non-finite constant, so every consumer's existing malformed-JSON
  handling fails it closed. This is a defence in depth beneath the per-field guards on the federation
  bundle, the multi-hub event `ts`, and the finding numbers (below); the hub never emits a non-finite
  float, so no legitimate frame is affected.
- A finding with a non-finite number no longer crashes the finding handler. A finding envelope is
  decoded from an untrusted frame and `json.loads` yields `inf`/`nan` from the `Infinity`/`NaN` tokens,
  but the tolerant coercion helpers converted a numeric field with a bare `int()`/`float()`:
  `int(inf)` raises `OverflowError`, `int(nan)` raises `ValueError`, and `float()` of a JSON integer too
  large for a double raises `OverflowError`. A single frame carrying `Infinity` in
  `provenance.source_event_seq` (or `nan` in a confidence or validity bound) therefore raised an
  unhandled exception out of the handler, dropping the sender's connection with a traceback. The helpers
  now treat a non-finite or double-overflowing value as no usable number (`None`) — the same signal they
  already return for a non-numeric value — so a hostile finding is rejected cleanly and a `nan` can no
  longer corrupt finding ranking or validity-window checks. Found by fault-injection of the finding
  decode path.
- A non-finite timestamp in a peer hub's event no longer breaks the deterministic multi-hub merge. The
  cross-host event codec converted a stored event's `ts` with a type check that accepted any float, but
  `json.loads` parses the `NaN`/`Infinity` tokens, so a peer's wire body could carry a non-finite `ts`.
  A `nan` compares unequal to everything, so the total-order merge key `(ts, hub_id, seq)` stopped being
  a total order: two hubs folding the same events in different receive orders could sort them
  differently and diverge. The codec now rejects a non-finite `ts` as a malformed body
  (`MultiHubWireError`), the same contract it already applies to every other bad field. Found by
  fault-injection of the wire codec.
- A malformed federation peer bundle no longer crashes the import or the hub. A numeric field in an
  out-of-band bundle — a peer's `expires_at` or a record's `provenance.imported_at` — was converted
  with a bare `float()`, so a hostile or corrupt value (a string, a mapping, a list, or a non-finite
  `nan`/`inf`) raised a raw `TypeError`/`ValueError`. Every caller catches only `FederationStoreError`
  (a `ValueError` subclass, which never matched the `TypeError` cases), so such a bundle escaped as an
  unhandled traceback — crashing `synapse federation import` on a peer's bundle and `synapse hub
  --federation-store` at startup on a corrupt store. Both numeric fields now parse through a guarded
  conversion that raises `FederationStoreError` naming the field, and rejects `nan`/`inf` (a `nan`
  expiry would defeat the `now >= expires_at` check and leave a peering that never expires). Found by
  fault-injection of the federation bundle parser.
- The hub no longer floods its log with full ERROR tracebacks for benign aborted handshakes. A
  load-balancer TCP health check, a port scan, or a client that drops before completing the WebSocket
  handshake previously logged `opening handshake failed` with a full traceback each time — on a
  production hub, frequent, benign, and enough noise to bury real errors and grow the log without bound.
  A `HandshakeAbortFilter` on the log handler now drops exactly those records (a handshake failure whose
  cause chain is a plain connection abort — EOFError/ConnectionError/TimeoutError, matched through
  websockets' wrapping exception) while keeping every other log, including a genuine handshake error from
  a completed-but-invalid request. Found by live fault-injection testing of the hub.

### Added
- An **API and wire stability** policy (`docs/api-stability.md`): what counts as a stable surface, the
  test that guards each one against accidental change (the public `__all__`, the complete wire
  `MessageType` vocabulary, the federation primitives out-of-tree consumers import, and the tiered CLI),
  the decoupled wire-protocol version, the stability tiers, and the deprecation policy. The wire message
  vocabulary is now frozen in full by `tests/test_wire_surface_freeze.py` — previously only the count
  was pinned, blind to a rename that keeps the count constant.
- The hub advertises a wire-protocol version. `WIRE_PROTOCOL_VERSION` (an integer, baseline `1`,
  decoupled from the package version so it changes only on a wire-incompatible change) now rides in the
  `welcome` handshake as `protocol_version` and in `/health`, and a client captures the peer's version
  as `hub_protocol_version` on connect. This gives a consumer that syncs across possibly version-skewed
  hubs a compatibility signal to read on connect instead of inferring from a separate query. It is
  advertise-only: a client records the peer's version but no compatibility policy is enforced yet
  (the mismatch behaviour is a contract to be agreed with the wire's downstream consumers first). A hub
  or client that predates the field reads it as absent, so the addition is backward-compatible.
- `synapse approvals` makes the two-person relay quorum operable. The approval ledger is per-hub live
  state that enforced a second operator but exposed no way to see which relays were pending, so the
  quorum was invisible between the first request and the second approval. The pending set now rides in
  the hub's state snapshot (the same one the dashboard and cockpit read) as `pending_relay_approvals`,
  and the new read-only `synapse approvals` query prints it — oldest first, naming each pending action,
  its namespace and task, and the first requester a second, different operator must join to reach
  quorum. It holds only what the ledger holds (never a message body).

## [0.98.4] - 2026-07-06

### Added
- Dead-letter escalation can now forward across hubs, end to end. When a blackholed directed
  target's namespace is owned by a peer hub — resolved through the same namespace-ownership and
  relay routes the operator relay uses — an escalation forwards a pointer to that owning hub (the
  target and its undelivered count, never a message body, so re-delivery stays impossible by
  construction) over the federation transport and records a durable `dead_letter_forwarding` audit
  event. The owning hub, behind the same deny-by-default serving policy and namespace-ownership gate
  the operator relay uses, records a matching inbound audit (naming the verified sending peer) and
  broadcasts the pointer to its own operators, so the hub that can actually reach the missing reader
  learns of the gap. The two audits reconcile through a `direction` field (`out` on the origin, `in`
  on the owner). New `core.dead_letter_forwarding` holds the honesty-bound notice and its codec,
  `core.dead_letter_forwarding_transport` the fire-and-forget sender (the hub's default), and
  `handlers.dead_letter_forwarding` the peer-side receiver.
- `synapse federation rotate` keeps a domain's own trust bundle fresh: it pushes the expiry
  forward, unions new signing keys or certificate pins alongside the existing ones for a grace
  window (an old key stays valid until a later rotation retires it, so a peer that has not
  re-fetched keeps verifying), rewrites the bundle in place, and keeps the prior bundle as a
  backup. It mints no keys of its own — the added ids are generated through the tooling that
  already manages the domain's keys. New `core.federation_rotation` holds the rotation policy.
- The WASM sandbox can be confined to operator-approved workspace roots. `synapse sandbox run
  --workspace-root DIR` (repeatable) refuses, fail-closed, any preopen that resolves outside every
  approved root before the tool runs, and `synapse sandbox validate --workspace-root DIR`
  pre-flights the same verdict without running anything. With no root given the constraint is
  inert, so the policy is opt-in.
- `synapse sandbox validate --check-paths` pre-flights a manifest's filesystem grants against the
  live filesystem — the same host-path resolution the runner performs — and reports each grant as
  accepted (with its canonical directory) or refused (a symlink redirect or a missing directory)
  without running the tool, returning exit `1` when the manifest is valid but a grant would be
  refused here.
- Dead-letter blackholes can now escalate. A hub started with a
  `dead_letter_escalation_threshold` broadcasts a one-line `dead_letter_escalation` notice to every
  connected socket and journals an audit event when a target's undelivered directed-message count
  reaches the threshold, and again at each further multiple — turning the ledger's passive
  visibility into an active signal for a blackhole that keeps growing. It never re-delivers a
  message (the ledger holds counts and names, not bodies), so escalation points a human or an
  orchestrator at the problem rather than silently re-sending; the default of `0` disables it,
  leaving the ledger unchanged. New `core.dead_letter_escalation` holds the threshold policy.

## [0.98.3] - 2026-07-06

### Added
- The armed auto-action policy is now durable, so the terminal and a live orchestration loop share
  one source of truth. `synapse auto-action arm compact,log` and `disarm log` add or remove
  actions in a JSON policy file in the coordination home (`$SYN_HOME` or `~/synapse`, overridable
  with `--store PATH`), `clear` disarms everything, and `show` renders the persisted posture; the
  bare command still previews the static model and touches no files. New `participants.auto_action_store`
  (`load_policy`, `save_policy`) is the seam an orchestration harness loads to build its dispatch,
  so what the operator arms is what the loop would fire. Persisting a policy still fires nothing —
  an armed action fires only when its signal is raised at runtime and a handler was supplied.

### Security
- The cross-hub operator relay can now require two-person approval: a hub started with
  `require_two_person_relay` records an authorised relay pending instead of applying it, and
  carries it out only when a second, different operator relays the same action, namespace, and
  task. The same operator repeating the request cannot approve their own relay (it stays pending),
  and both the pending request and the approval are audited, so a governed cross-hub force-release
  under this policy names two distinct operators in the log. The `RelayActionResult` gains a
  `pending` field and `synapse federation relay` a new exit code `3` for a recorded-pending
  verdict; both default off, so a single-operator hub and an older initiator read exactly as
  before. The quorum lives in the new `core.operator_relay_approval` ledger (bounded, in-memory),
  completing the operator-relay policy rituals begun with reason-required receipts and break-glass
  tagging; break-glass does not bypass the quorum.
- The cross-hub operator relay now carries a `reason` and a `break_glass` tag, recorded in the
  `operator_relay` audit on both the originating and the owning hub, so a governed force-release
  across hubs leaves an auditable why and an emergency override stands apart from routine
  governance in the log. `synapse federation relay` gains `--reason` and `--break-glass`, and a
  hub started with `require_relay_reason` refuses a relay that carries no reason (reason-required
  receipts) — deny-by-default, checked in `authorise_relay` after the peer and action gates. Both
  wire fields default empty for backward compatibility. (Two-person approval of a relay is a
  larger stateful workflow, tracked as a follow-up.)
- The AES-GCM per-key message limit can now be enforced across restarts, not just within one
  process. `AtRestCipher` takes an optional `counter`, and the new `core.at_rest_counter`
  provides a crash-safe `PersistentMessageCounter` that persists the count to a sidecar file by
  reserving a batch ahead of use — so a long-lived encrypted store resumes a key's cumulative
  lifetime count after a restart or crash (over-counting by less than a batch and rekeying early,
  never under-counting and risking a fresh nonce colliding with an old one). The default remains
  the per-process `InMemoryMessageCounter`, byte-identical to before. `AtRestCipher.from_key_file`
  and `from_wrapped_key_file` accept the counter so a store can opt in.
- The WebAssembly sandbox now canonicalises a filesystem grant's host path before it
  preopens it, and refuses the run fail-closed if the path resolves through a symlink or
  is not an existing directory. A host path is resolved on disk at run time, so a symlink
  swapped into a granted path between manifest authoring and execution could have
  redirected a preopen to a directory the operator never granted; the sandbox now preopens
  the resolved real directory and records it in the run receipt's new `preopened_paths`
  field, so the run reaches exactly the directory the receipt shows and no moving target.
  New `core.sandbox_paths` (`resolve_preopen_host`, `harden_preopens`).
- `SECURITY.md` no longer describes at-rest encryption as unimplemented. The at-rest
  encryption runtime (envelope encryption of SQLite stores, WAL/SHM sidecars, relay
  logs, A2A state, archives, and backups; scrypt/PKCS#11/TPM2 key-encryption backends;
  migration/rekey) has shipped, so the security posture doc now states it accurately,
  with the transparent live-database (SQLCipher-class) boundary kept honest as the
  remaining gap. Private channels are likewise no longer listed as future work.
- Dashboard operator writes now require `Content-Type: application/json`, closing a
  local cross-site-request-forgery hole. A cross-origin web page can POST a body to
  the loopback dashboard without a CORS preflight only with a "simple" content type
  (`text/plain`, form-encoded, multipart); the operator write path parsed JSON from any
  content type, so a malicious page a local operator visited could drive `/message`,
  `/task`, and `/task/update` in `synapse dashboard --operator` without reading the
  response. Requiring `application/json` forces a preflight the surface never answers
  with cross-origin allow headers, so the browser blocks the write; a non-JSON operator
  write is refused `415`. The read-only dashboard and the same-origin cockpit (which
  already sends `application/json`) are unaffected. The misleading docstring that claimed
  loopback writes require the bearer token is corrected.

## [0.98.2] - 2026-07-05

### Added
- Cross-hub operator relay — `synapse federation relay release` relays a governed
  operator action to a peer hub over the existing federation transport, the first
  being a force-release of a stuck lease the peer holds. The peer authorises the
  relay deny-by-default (mutual TLS + the peering's bounded scope granting the
  action's verb in the namespace + the peer must own the namespace) and refuses an
  unverified peer or an unregistered action fail-closed. An applied release is
  journalled twice on the acting hub: a standard `release` for state reconstruction
  and a new audit-only `operator_relay` event carrying the cross-hub provenance a
  release never records — the verified peer, the asserting operator and origin hub,
  and the previous holder — and the hub's own agents are told the lease was revoked.
  New `core.operator_relay` (deny-by-default policy + relayable-action registry),
  `core.operator_relay_wire`, `core.operator_relay_transport`, and a serving handler,
  plus `SynapseState.force_release`. Relayable actions are an explicit allowlist, so a
  new cross-hub capability is a deliberate registry entry, never an accident of the wire.
- Origin-side routing for the cross-hub operator relay — an operator can target their
  own hub, and a hub configured with a relay-peer route to the namespace's owner
  forwards the relay on their behalf and relays the verdict back, so the operator never
  needs the owning hub's credentials (the origin-side counterpart of claim forwarding).
  The relay is now audited on **both** hubs: the origin hub records an outbound
  `operator_relay` event (a new `direction` field distinguishes it from the owning hub's
  inbound one) naming the requester and the destination owner, and stamps its own id as
  the forwarded request's origin so the owner attributes the relay to the hub that
  relayed it, never a value the requester asserted. Routing is deny-by-default: a relay
  for a namespace the hub neither owns nor has a route to is refused fail-closed. New
  `core.operator_relay_routing` (pure route resolution) and `core.operator_relay_forwarding`
  (the origin-side gate), plus `SynapseHub(relay_peers=…, relay_forwarder=…)`, a route map
  kept separate from the claim-forwarding peers because relaying a force-release is more
  privileged than forwarding a claim.
- TPM 2.0 hardware key-encryption-key backend for at-rest wrapped keys (optional
  `synapse-channel[tpm2]` extra). A decrypt-only RSA-2048 key-encryption key is derived
  from the device's storage seed and a fixed template — the identical key every process,
  so no handle is persisted — and wraps and unwraps the data key with RSA-OAEP; the RSA
  private key is generated inside the TPM and never leaves it.
  `synapse encrypt-key generate-wrapped-tpm2` writes such a file (`--tcti` / `TPM2_TCTI`,
  defaulting to the in-kernel resource manager `device:/dev/tpmrm0`), recording only the
  template version — never a device path. New `core.at_rest_tpm2`
  (`Tpm2KeyEncryptionKey`, `generate_wrapped_key_file_tpm2`,
  `cipher_from_wrapped_key_file_tpm2`) implementing the `KeyEncryptionKey` protocol over
  the same wrapped-key file format. CI installs swtpm so the backend is exercised, not
  skipped. This completes the pluggable hardware backend family (passphrase, PKCS#11, TPM).
- PKCS#11 hardware key-encryption-key backend for at-rest wrapped keys (optional
  `synapse-channel[pkcs11]` extra). A key-encryption key held on a PKCS#11 token — a
  YubiKey PIV, a cloud or network HSM, or SoftHSM for tests — wraps and unwraps the
  data key on the device via RFC 3394 AES key wrap (`C_WrapKey` / `C_UnwrapKey`), so
  the token key never leaves the hardware. `synapse encrypt-key generate-wrapped-pkcs11`
  writes such a file (`--pkcs11-module` / `PKCS11_MODULE`, `--token-label`,
  `--key-label`, `--no-create-kek`; PIN from `PKCS11_PIN` or a prompt), recording only
  the token and key labels — never the PIN or module path. New `core.at_rest_pkcs11`
  (`Pkcs11KeyEncryptionKey`, `generate_wrapped_key_file_pkcs11`,
  `cipher_from_wrapped_key_file_pkcs11`) implementing the `KeyEncryptionKey` protocol
  over the same wrapped-key file format. CI installs SoftHSM2 so the backend is
  exercised, not skipped.
- Envelope-encrypted (KEK-wrapped) at-rest key files with a pluggable
  key-encryption-key backend, the foundation for hardware-backed keys (PKCS#11 /
  TPM / YubiKey / cloud HSM). A random data key does the bulk AES-GCM while a
  key-encryption key wraps it with RFC 3394 AES-KW; the wrapped-key file records
  which `backend` produced it (`passphrase-scrypt` today, hardware backends as
  optional extras next) so a fresh process rebuilds the matching key. `synapse
  encrypt-key generate-wrapped` writes one and `synapse encrypt-key rewrap` rotates
  its passphrase **without re-encrypting any data**, because only the
  key-encryption key changes and the data key underneath is unchanged. New
  `KeyEncryptionKey` protocol and `PassphraseKeyEncryptionKey`, `wrap_data_key` /
  `unwrap_data_key` / `generate_wrapped_key_file` / `rewrap_wrapped_key_file`, and
  `AtRestCipher.from_wrapped_key_file` in `core.at_rest`. The optional PKCS#11 / TPM
  / YubiKey key-encryption-key backends implement the same protocol and plug into
  this same wrapped-key format.
- `synapse auto-action` gives the opt-in auto-action reactor a discoverable CLI
  surface. The reactor (which turns the session advisor's per-round signals into
  automatic compact/log/handover actions) was previously reachable only in-process
  through `react_to_advice`, so an operator could not see what it does. The command
  prints the signal-to-action map, the signals that deliberately map to no action,
  and — with `--arm`/`--all` — a preview of a policy's armed posture. It reads the
  static model only (starts nothing, fires nothing), and states honestly that
  arming happens in the orchestration loop, not through a hub-side toggle. New
  read-only `describe_auto_actions`/`auto_action_report_to_json`/
  `render_auto_action_report` in `participants.auto_action`.
- `synapse encrypt-key generate --from-passphrase` derives the at-rest key from a
  passphrase (prompted twice) via scrypt instead of random bytes, with the scrypt
  cost tunable through `--scrypt-n` (a power of two), `--scrypt-r`, and `--scrypt-p`
  for a security/performance trade-off. A fresh random salt is drawn per derivation
  and discarded — the written file is a normal owner-only 32-byte key of record,
  protected exactly like a random one, and the passphrase alone cannot reconstruct
  it. The default remains a random key; the passphrase path (and its `scrypt`
  parameters, previously reachable only through the `AtRestCipher.from_passphrase`
  library API) is now exposed on the CLI. New `generate_key_file_from_passphrase`.

### Security
- `AtRestCipher` now enforces the AES-GCM per-key safety bound for random 96-bit
  nonces. It counts the messages it seals (exposed as `encrypted_count`), logs a
  one-time rekey warning once it passes fifteen-sixteenths of the `GCM_MESSAGE_LIMIT`
  (`2**32`), and raises the new `AtRestKeyExhausted` rather than encrypt past it —
  so a key is rotated before the nonce-collision probability can rise past the
  `2**-32` bound. The count is per cipher instance and resets when the cipher is
  rebuilt, guarding a single long-running process rather than a key's cumulative
  lifetime across restarts.

## [0.98.1] - 2026-07-05

### Fixed
- The CLI-launched hub now reports a real `config_epoch`. `synapse hub`
  constructs `SynapseHub(...)` directly rather than through `from_config`, so the
  configuration-posture fingerprint added in 0.98.0 stayed empty on the actual
  deployed hub — leaving the pinning indicator inert (`/health`, the `who`
  snapshot, and `/snapshot.json` all reported an empty `config_epoch`, and a
  cockpit's drift chip never lit). The command now regroups its assembled
  arguments with `HubConfig.from_kwargs` (a new inverse of `HubConfig.to_kwargs`
  that fills defaults for the partial keyword set the CLI passes) and stamps the
  fingerprint onto the hub, so a CLI-deployed hub pins the same way a
  `from_config` hub does. A gap the 0.98.0 fleet deploy surfaced: the library
  `from_config` path was tested, the CLI construction path was not.

## [0.98.0] - 2026-07-05

### Added
- Hub pinning indicator — the hub now reports a `config_epoch` alongside its
  `version`, a short deterministic fingerprint of its configuration posture (the
  scalar limits and the armed/disarmed state of each optional subsystem: auth,
  ACL, per-message auth, metrics, multi-hub, federation). It appears in the hub's
  `/health` response, in the `who` snapshot, and in the dashboard's
  `/snapshot.json` (as `hub_version` and `config_epoch`), so a cockpit can badge
  which hub build and configuration it is watching and notice a deploy or a config
  drift. Honest scope: it fingerprints posture, not secrets — object-valued
  settings enter only as a presence marker, so rotating a key or editing an ACL
  rule within the same posture does not change it. A hub built without a grouped
  config (an ad-hoc `SynapseHub()`) reports an empty `config_epoch`.
- `/waits.json` dashboard store feed (with `--feeds-db`) — the pending
  coordination gates reconstructed from the durable plan: each non-terminal task
  blocked on a dependency that has not reached a terminal status, with `who` is
  waiting (the task's suggested owner, or whoever declared it), `on_what`
  dependency ids block it, and `since` when it was declared, plus a `wait_count`.
  The "what is the fleet stuck behind" panel. Store-derived and deterministic
  (dependency satisfaction judged from the log's own recorded task statuses),
  available with the hub down; 404 without `--feeds-db`, 503 on an unreadable
  store. Transient socket waiters are not journalled and are omitted — this is the
  coordination gates the plan can prove, not who holds a socket open.
- `/sessions.json` dashboard store feed (with `--feeds-db`) — the opt-in
  `session_metric` telemetry the fleet left in the durable log, in the same JSON
  `synapse participants costs` renders: per-session token counts, cost, latency,
  and error/abstention rates, plus `totals` aggregated across sessions. Every
  record carries the `seq` of the snapshot it was read from and the coordination
  `task_id` from the note body, so a cockpit joins a session's cost straight to
  its causal cone (via `/causality.json`) — "this session spent N tokens on task
  T42", not merely "on session S". Same posture as the other store feeds:
  store-derived and deterministic (available with the hub down), 404 without
  `--feeds-db`, 503 on an unreadable store; a log with no session notes reports
  empty `sessions` and zeroed `totals`, never a fabricated cost.
- Operator write-path for the dashboard (opt-in) — `synapse dashboard --operator`
  arms three write routes so the cockpit can act on the fleet rather than only
  observe it: `POST /message` (`{"to","text"}`) relays a chat message, `POST /task`
  (`{"id","title","depends_on"?}`) declares a board task, and `POST /task/update`
  (`{"id","status"?,"note"?}`) changes a task's status and/or appends a progress
  note. Off by default: without the flag every route is a 404, indistinguishable
  from an unknown path, and the dashboard stays a read-only observer. When armed, a
  write still requires the dashboard bearer token, is rate-limited, and is sent
  under an explicit `operator:<name>` identity that never impersonates an agent.
  The relay reimplements neither authorisation nor auditing — the hub ACL-checks
  the relayed frame and records it in the durable log, so every operator action is
  authorised at the hub and appears in replay, `/state-at`, and the signal stream.
  Responds `200` when delivered, dead-lettered, or applied, `403` on ACL refusal,
  `409` when the blackboard refuses a task on its own terms, and `503` when the hub
  is unreachable.

- CycloneDX SBOM (`synapse-channel-<tag>-sbom.cdx.json`) is generated from the
  built wheel's dependency closure and attached to every GitHub Release alongside
  the distributions, so the published software bill of materials is a first-class
  release artifact. The generator (`cyclonedx-bom`) is hash-locked in a dedicated
  `requirements-sbom.txt`.
- `all` convenience extra bundling every runtime feature library (cryptography,
  WASM, OTel, MCP) — `pip install synapse-channel[all]` for a full-feature install
  without naming each extra. A packaging drift guard keeps `all` exactly the union
  of the feature extras, keeps the runtime floor a single dependency, and imports
  every feature-consuming module to prove the base import surface never
  hard-requires an optional library.

### Changed
- Removed the hub's `register` / `unregister` / `_authenticate_or_close` methods —
  thin wrappers over the `HubConnection` collaborator that no live path or test
  reached once the socket lifecycle moved behind the `handler` entry point. The
  collaborator owns these steps and is tested on them directly; the hub keeps only
  the `handler`, `_send_welcome`, and `_install_signal_handlers` delegators, each
  with its own hub call site (`serve`, the withheld-welcome path, and shutdown
  wiring). No behaviour change — a redundant indirection is gone. `core/hub.py`
  drops from 1003 to 978 lines.
- Extracted the hub's durable-state seeding into `core/hub_state_seed.py`
  (`seed_hub_state`): the decision to replay the event log — resuming live leases,
  chat history, the shared blackboard, and the ledger-guard seed (the message-id
  high-water mark, per-actor finding counts, and the idempotency cache) — or build an
  empty registry, together with the one-off compaction hint a hub emits when opened on
  an oversized log, now lives in one pure function returning a `SeededHubState` the
  constructor binds, instead of a ~50-line branch inlined in `__init__`. It holds no
  hub reference, so the resume-versus-fresh behaviour is testable without a live hub.
  No behaviour change — a restart resumes exactly as before and the compaction hint
  fires on the same threshold. Fourth slice of the resumed hub decomposition, taking
  `core/hub.py` from 1037 to 1003 lines (1294 at the start of the arc, with four
  single-responsibility collaborators peeled). 100% line+branch on the new module.
- Extracted the hub's frame-authorisation gates into `core/hub_frame_gates.py`
  (`HubFrameGates`): verifying required per-message authentication (an HMAC frame
  signature or an Ed25519 signed-event signature), authorising a mutating frame
  against the ACL, and routing a claim by namespace ownership — granting locally,
  forwarding to the owning peer hub and relaying its verdict, or refusing fail-closed
  with the owner named — now live in one class the hub holds, with
  `_verify_per_message_auth` / `_authorise_acl` / `_authorise_claim_ownership` left as
  thin delegating wrappers (the `handle_message` pipeline keeps one entry point per
  gate) and the internal `_observed_asserting_hubs` / `_forward_remote_claim` folded
  into the collaborator. Routing itself stays on the hub, since a handler is invoked
  with the hub as its first argument; the gates take the hub's per-socket send and
  system-message factory as injected callbacks and capture their policy inputs at
  construction, so the collaborator carries no back-reference to the hub. No behaviour
  change — the auth verdicts, the ACL denials, and the claim grant/forward/refuse
  decisions are identical. Third slice of the resumed hub decomposition, taking
  `core/hub.py` from 1202 to 1037 lines. 100% line+branch on the new module.
- Extracted the hub's socket-connection lifecycle into `core/hub_connection.py`
  (`HubConnection`): admitting a socket against the capacity, per-host, and
  unauthenticated-burst ceilings; welcoming it (on connect for an open hub, or only
  after the first frame authenticates on a secured one); reading the authenticated
  first frame under the auth deadline; pumping later frames into the routing
  pipeline; releasing the agent name and broadcasting the departure on disconnect;
  and the `SIGTERM`/`SIGINT` graceful-shutdown wiring — now live in one class the
  hub holds, with `register` / `unregister` / `handler` / `_send_welcome` /
  `_authenticate_or_close` / `_install_signal_handlers` left as thin delegating
  wrappers, so `serve` and every test keep one entry point. Frame routing itself
  stays on the hub and is handed in as an injected callback, so the collaborator
  carries no back-reference to the hub; `_process_request` also stays on the hub as
  the HTTP-endpoint renderer. No behaviour change — connection admission, the
  withheld-welcome timing on a secured hub, and disconnect cleanup are identical.
  Second slice of the resumed hub decomposition, taking `core/hub.py` from 1258 to
  1202 lines. 100% line+branch on the new module.
- Extracted the hub's pre-route ingress guards into `core/hub_ingress.py`
  (`HubIngress`): authenticating a socket's first frame against the shared-secret
  token, binding the claimed sender name (with optional takeover), keying the remote
  host for per-host rate limiting, closing a socket, and refusing — or, when
  overridden, warning about — an exposed bind now live in one class the hub holds,
  with `_authorise` / `_resolve_sender` / `_exposure_problems` / `_guard_exposure`
  left as thin delegating wrappers and `_close_socket` / `_remote_host` kept as
  class-callable staticmethods (the handler call surface is unchanged). It reads the
  live socket registry and takes the hub's per-socket send and system-message factory
  as injected callbacks, so it carries no back-reference to the hub. No behaviour
  change; the token gate, name resolution, and exposure refusal are identical. First
  slice of the resumed hub decomposition, taking `core/hub.py` from 1294 to 1258
  lines. 100% line+branch on the new module.
- `synapse hub --paranoid` is now the full production secure preset: besides the
  token, durable log, and per-message authentication it already required, it now
  also requires ACL enforcement (`--require-acl` with an `--acl-policy`) and native
  WSS (`--tls-certfile`/`--tls-keyfile`), and still disables metrics query tokens
  and the insecure off-loopback override. A paranoid start without ACL or TLS now
  fails closed with a specific message. The missing-hooks report drops ACL and
  signed events (now enforced) and names what genuinely remains — mutual-TLS
  client-certificate verification and cryptographic per-agent identity.

### Fixed
- A dashboard store feed (`/state-at.json`, `/merkle-proof.json`, `/events.json`,
  `/causality.json`) crashed with an unhandled `OverflowError` when a `?seq=` or
  `?limit=` query carried an integer beyond SQLite's signed 64-bit range — an
  arbitrarily large value parsed as an unbounded Python int, then overflowed
  inside the store query. The feeds now bound the parsed integer and answer `400`
  instead of a `500`. Found by the new query-feed fuzz test.

### Tests
- Fuzz coverage for the dashboard store-feed query parsers: hostile `?seq=`,
  `?task=`, `?direction=`, and `?limit=` values (non-numeric, negative, huge,
  duplicated, percent-encoded, multi-kilobyte) are thrown at every feed and the
  handler must answer a deliberate status (`200`/`400`/`404`/`503`), never an
  unhandled `500`.
- Property-based coverage (Hypothesis) for the coordination invariants a
  correctness bug would break silently: claim-scope overlap is symmetric and
  agrees with per-path overlap (no file collision slips a non-conflicting scope),
  a whole-worktree claim conflicts with any other, the idempotency cache replays
  the most recent response for a key and never exceeds its bound, and a task never
  takes a forbidden lifecycle transition — least of all out of a terminal state.

### Documentation
- `SECURITY.md` gains a deployment-profile matrix (local-dev, single-user
  workstation, team LAN, internet-exposed) mapping each profile to its required
  controls, plus a capability→extra map, and its paranoid-mode summary is updated
  for the now-required ACL and TLS. `CONTRIBUTING.md` gains a definition of done
  (changelog fragment, backward-compat statement, threat-model delta) and a
  `core/*` hot-path ownership note.

### Security
- Every dashboard and cockpit HTTP response now carries browser-hardening headers:
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, and a same-origin `Content-Security-Policy`
  (`frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'`; inline
  script/style retained for the server-rendered pages). The dashboard is
  self-contained, so the policy blocks injected remote resources at no cost.
- The metrics query-string token (`--metrics-query-token-ok`) is now loopback-only.
  Binding a non-loopback host with it set is refused with `InsecureBindError` (like
  the other exposure guards, downgradable with `--insecure-off-loopback`), because a
  `?token=` value leaks into proxy access logs, browser history, and shell history.
  On a loopback bind it remains a legitimate local debug aid; off loopback the token
  belongs in the `Authorization` header.

## [0.97.0] - 2026-07-04

### Added
- `SynapsePersistentDeadLetters` Prometheus alert — fires when
  `synapse_dead_letter_targets` stays above zero for an hour, catching a name
  left blackholed and undrained where `SynapseDeadLettersGrowing` catches only
  the passing miss. Meaningful now that the ledger ages quiet names out: a
  still-counted target is a genuine persistent gap. The honest live-path answer
  to "alert on dead letters" — the ledger is not journalled, so this rides the
  exported gauge, not the log-derived causal-health surface.
- Session telemetry can name the coordination task it advanced — `orchestrate_session`,
  `BusOrchestration.run`, and `emit_session_metric` gained an optional `task_id`
  that rides each durable `session_metric` snapshot's body (the note's own
  task-id slot carries the session id, so the coordination task rides the body,
  omitted when empty). `synapse participant costs` and the report reader surface
  it, so a session's turns/tokens/spend can be read against the claim or board
  task it was working, not only its session. Backward compatible: empty leaves
  the body and every reader unchanged.
- `/health-anomalies.json` dashboard feed — the honest hub-side alert surface:
  the orphaned, dangling, and stale coordination anomalies the causality graph
  makes visible (`core.causality_health.run_causal_health`), in the same JSON
  shape `synapse causality --health` emits, with an `anomaly_count` a cockpit
  alerts badge can show. Fired alerts stay collector-side off `/metrics`
  (Prometheus/Alertmanager); this is only what the durable log can prove —
  store-derived, deterministic (ages measured against the log's own final
  timestamp), available with the hub down; same `--feeds-db` posture as the
  other store feeds.
- `synapse dead-letters` — a terminal view of the hub's dead-letter ledger
  (directed messages delivered to no live connection), worst blackhole first,
  with the exact `syn inbox --as NAME` drain remedy the doctor's addressee
  check emits. The ledger already rode in the state snapshot for the dashboard
  and cockpit; this brings it to a terminal operator too. Read-only, reuses the
  state request.
- Dead-letter ledger age bound — `DeadLetterLedger` gained an optional
  `max_age_seconds`, and the hub applies a seven-day default: a target that has
  gone quiet past the bound is forgotten (expired on both `record` and
  `snapshot`) so a stale slot no longer shows as a live blackhole. A target
  that keeps drawing directed traffic refreshes and never ages out; the library
  default stays unbounded so existing callers are unchanged.
- `/merkle-proof.json?seq=N` dashboard feed — serves an RFC 6962 Merkle
  inclusion proof for one event so a cockpit row's *verify* button can
  confirm the row is committed to the attested log's tree root, in the same
  JSON shape `synapse debug merkle` emits. Store-derived, deterministic, and
  available with the hub down; a `seq` the committed log does not hold returns
  `{"present": false}` with a note rather than a fabricated proof; same
  `--feeds-db` posture as the other store feeds.
- `/state-at.json?seq=N` dashboard feed — reconstructs coordination state
  (claims + board) as of any event sequence by bounded replay of the
  durable log (`core.journal.replay(up_to_seq=)`), in the live-snapshot
  shape plus `as_of_seq` and `log_end_seq`. Store-derived and
  deterministic (judged against the bounded event's own timestamp, never
  the wall clock), so a cockpit can time-travel the whole fleet, not just
  the event log. Honest scope: presence/roster is not journalled and is
  omitted; `seq` is clamped into range; same `--feeds-db` posture as the
  other store feeds.
- The dashboard's `--cockpit-dist` static server now serves
  `.webmanifest` (`application/manifest+json`) and `.webp`, so the
  cockpit can ship a web-app manifest and modern icons — the server-side
  enabler for an installable, mobile-responsive cockpit PWA.

### Changed
- Commercial pricing documentation reconciled to a single value-ladder
  (Community, Commercial Licence, Pro, Team, Business / Enterprise) priced in
  USD; the retired pay-what-you-want tier is dropped from `docs/commercial.md`,
  `COMMERCIAL-LICENSE.md`, and the README.


## [0.96.0] - 2026-07-04

### Fixed
- The CLI no longer aborts with `UnicodeEncodeError` on a non-UTF-8
  console (Windows `cp1250` and friends): the entry point reconfigures
  stdout/stderr to UTF-8, so the arrows, bullets, and sparkline glyphs
  the commands print survive a legacy code page instead of crashing the
  whole command. Found on the first Windows run of `synapse doctor`;
  harmless where the streams are already UTF-8.


### Added
- `synapse causality health --since TS` — bound the scan to recent
  events on a large log, mirroring the trust graph's `--since`; a task
  whose entire recorded lifecycle predates the window is not assessed,
  and a window-straddling task is judged on the window's evidence only.
- `synapse benchmark --trend --export-csv FILE` — the stored history as
  long-format CSV (one row per metric value, context columns on every
  row) for spreadsheets and external monitors.
- The README now points at the cockpit build instructions and documents
  the state snapshot's `dead_letters` section.
- Log-derived signals reach Prometheus through the node_exporter
  textfile collector: `synapse reliability --textfile FILE` and
  `synapse causality health --textfile FILE` write the reliability
  findings and causal-health anomalies as valid labelled exposition
  (`synapse_reliability_findings{kind}`, `synapse_causal_health_anomalies
  {shape}`), deterministic over a given log and parsed back by the
  Prometheus client in the suite so node_exporter never rejects one.
  Two alert rules ship with the observability bundle, and
  `docs/observability.md` documents the analytics plane landing beside
  the live counters — evidence gauges, not grades.
- The WASM sandbox gained an adversarial proof battery and run
  attestation. `tests/test_wasm_sandbox_escapes.py` drives a hostile
  module past every limit — memory bomb, fuel bomb, wall-clock runaway,
  a reach for a host syscall, a reach for the network — and asserts each
  is contained by a mechanism (an undefined import cannot link; a grow
  past the cap is refused; the epoch timer interrupts a fuel-free loop),
  not by the tool's good behaviour. `synapse sandbox run --attest DB`
  appends the run receipt to a durable event store as a `sandbox_run`
  event, auditable through `synapse event-query` and replay without the
  tool's bytes ever entering the log. `docs/sandbox-threat-model.md`
  states what is denied by what mechanism and what is out of scope.
- An observability provisioning bundle under
  `integrations/observability/`: a Prometheus scrape job, six alerting
  rules over the decision counters (hub down, dead letters growing,
  denials outpacing grants, auth failures, takeover quarantine,
  federation denials), and a committed Grafana dashboard — import,
  pick the datasource, done. A drift-guard test pins every metric name
  the bundle references to the registry, so a renamed metric fails the
  suite instead of silently emptying a panel. `docs/observability.md`
  walks the five-minute setup and states the plane boundary: `/metrics`
  is the live process deciding, the store feeds are log analytics.
- The hub's `/metrics` endpoint grew from 8 to 21 metrics: decision
  counters wired at the decision sites — claims granted/denied, releases,
  directed and broadcast chat, per-message auth failures, rate-limit
  rejections, federation-gate denials, waiter takeovers and their
  quarantines — plus live gauges for connected `-rx` waiters and the
  dead-letter ledger (targets and letters). Each increment is one integer
  addition in the message path and a scrape stays I/O-free, so an alert
  rule can now see the hub *deciding*, not just existing.
- `/metrics.json` dashboard feed — store-attested log metrics for the
  cockpit's metrics panel (total and per-kind event counts, plus the same
  split over trailing hour/day windows), measured against the log's own
  final timestamp so the document is deterministic and replayable; same
  `--feeds-db` posture as the other store feeds (available with the hub
  down, 404 unconfigured, 503 fail-visible), and the document itself
  states that the live process registry remains the hub's own `/metrics`
  endpoint.
- `synapse doctor --notify-cmd CMD` — pipe any warn/fail findings to an
  operator sink command (one line per finding with the remedy attached,
  hub URI in `SYNAPSE_DOCTOR_URI`), turning diagnostics into proactive
  alerts; a healthy run sends nothing, `--fix` pages the post-repair
  state, `--json` composes (stdout stays one document), and the sink is
  best-effort under the same no-shell contract as
  `cross-repo --notify-cmd`.
- `synapse lock --release-timeout SECONDS` — tune how long the exit is
  held for the hub's release confirmation on slower links; the wait
  stays bounded either way and the lease TTL remains the backstop.
  Distinct from `--wait-timeout`, which bounds acquiring the lease.

## [0.95.0] - 2026-07-03

### Added
- The cockpit reads the raw event tail in two requests on any log size
  and states why a causality trace is empty, riding the `since=latest`
  shortcut and the absence notes below.
- The hub now remembers directed chats that reached no live connection:
  a bounded per-target ledger (biggest blackhole first, stalest target
  evicted beyond 200, cleared the moment the addressed name connects)
  rides the state snapshot as `dead_letters`, so the dashboard and the
  cockpit can show "N messages, nobody listening" instead of leaving
  the blackhole invisible. Honest scope: an entry attests only that no
  live connection matched at send time — feed-history draining remains
  the doctor's addressee check.
- Directed-message blackholes are now visible and drainable. `syn inbox
  --as NAME` (repeatable; standing set via the comma-separated
  `$SYN_ALIASES`) drains additional identities — a role name like
  `project/coordinator` alongside the resolved one — each under its own
  cursor, so no reader ever consumes another's delta. And `synapse
  doctor` gained an addressee check: recent directed traffic whose
  target has no inbox cursor, no live waiter, and no live connection is
  reported with message counts and the drain command, because a message
  nobody reads otherwise waits for a human to relay it — the exact
  failure the bus exists to remove.
- `/events.json` accepts `since=latest` — the tail shortcut that starts
  at the log's end instead of walking a large history to catch up.
- A `present: false` causality-feed answer now carries a `note` naming
  which absence it is: an event recorded but outside the coordination
  causal graph (chatter carries no causal edges), or no event at that
  sequence at all.

## [0.94.0] - 2026-07-03

### Added
- The cockpit grew from a read-only viewer into a query surface: the
  activity spine is brushable from mouse and keyboard (arrows seed and
  move the window, brackets resize), a brushed window correlates the
  panels, hovered events carry an inspector, log rows hop to their
  causal cone through the causality feed, the spine consumes the
  hub-attested event tail (real sequences and timestamps instead of
  poll-quantised derivation), a capped board renders "N of M tasks" —
  never a page masquerading as the whole plan — and the fonts are
  self-hosted so the page loads without third-party requests.
- Three new dashboard feeds off the durable stores, closing the cockpit's
  server-side asks: `/events.json?since=SEQ&limit=N` (the raw event-log
  tail past a cursor in the exact multihub snapshot shape — real
  sequences and timestamps instead of poll-quantised derivation),
  `/causality.json?seq=N|task=ID&direction=causes|effects` (one causality
  query in the CLI's exact `--json` shape, with `task=ID` resolving to
  the task's most recent recorded event), and `/federation.json`
  (imported peerings with provenance and ceremony fingerprints; namespace
  outcomes are hub-runtime state and ship absent with the reason stated).
  The event-store flag is now named `--feeds-db` with `--reliability-db`
  kept as the same flag's original name, and `--cockpit-dist DIR` serves
  a built cockpit single-page app read-only under `/cockpit/` with path
  traversal and unrecognised suffixes refused.

- `synapse hub --board-task-cap N` — bound the tasks served per board
  snapshot, because a long-running fleet's full board eventually
  outgrows a websocket frame (field-observed around a thousand tasks).
  Live tasks are kept ahead of terminal ones, the newest `updated_at`
  wins inside each class when trimming, the reply carries `total_tasks`
  and `truncated`, and the `ready` id list always stays complete. The
  default serves the full board unchanged; the ledger itself is never
  trimmed — the cap bounds one reply, not the plan.

### Fixed
- The docs workflow retries its GitHub Pages deployment once after a
  five-minute wait: the Pages backend intermittently refuses a first
  attempt with "Deployment failed, try again later" and accepts a
  delayed retry, which previously cost a manual rerun on almost every
  push. The job fails only when both attempts fail.
- `synapse lock` now waits (bounded) for the hub's release confirmation
  before exiting. The release frame itself is fire-and-forget and the
  hub persists the release before broadcasting the grant, so previously
  a follow-up step could read the event log — or contend for the lease —
  before the release landed; the process now exits only after the lease
  is durably gone. A hub that never confirms costs only the bounded
  wait, with the lease TTL remaining the backstop.

### Security
- The chat backend client refuses a `base_url` whose scheme is not
  `http`/`https` at construction — a `file://` or custom scheme smuggled
  in through configuration is a `ValueError`, not a silently opened
  request.
- Bandit now gates CI: the lint job runs `bandit -r src -c
  pyproject.toml` (it was configured but never invoked). Every prior
  finding was triaged in place: one unparsed suppression fixed (bandit
  1.9 reads `# nosec B603 B607`, not the comma form), the placeholder-only
  SQL construction and status-string/argv false positives annotated with
  their reasons, and the subprocess imports documented as fixed-argv
  surfaces.

## [0.93.0] - 2026-07-03

### Added
- `synapse fleet-init` — empty machine to working fleet in one command,
  bundling the existing first-run pieces in their right order: the real
  `doctor` (optionally `--fix`; a failing report is a printed remedy,
  never an abort), a persistent coding-fleet workspace scaffold
  (`./synapse-fleet` by default, `--force` to refresh), a probe of every
  registered provider CLI without taking a turn (`--seat PROVIDER`
  declares intended seats; an unavailable declared seat is warned about
  and kept in the plan), the packaged no-collision demo smoke
  (`--no-smoke` to skip), and a printed next-steps plan — waiter arming,
  per-provider `worker-session` seat commands, `git-init`, dashboard —
  with the workspace's project name filled in. No new dependency and no
  new daemon: everything it starts is what the bundled commands start.
- The hub can feed its own partition detection: `synapse hub
  --multihub-watch PEER=URI` (repeatable) runs a standing follower that
  polls each named peer's event log on a bounded interval and folds the
  observed claims into the asserting-owners view the namespace-ownership
  gate consumes, so a namespace a watched peer is seen contesting
  resolves as partitioned and refuses to grant until the contest clears.
  Companion flags wire the ownership map from the CLI: `--hub-id` (the
  hub's stable id) and `--namespace-owner NS=HUB_ID` (repeatable,
  deny-by-default claim routing; requires `--hub-id`, and the watch
  requires the map). Naming a peer is the operator confirmation for the
  always-on outbound connection; a failed poll keeps the last successful
  observation, so an outage errs on the refusing side; the watch task
  lives exactly as long as the server. Validated live on two hubs: a
  claim held on the watched peer flips the namespace to partitioned
  refusals and the peer's release clears it on the next poll.

- `synapse benchmark --ascii` — renders the `--trend` block in printable
  ASCII for consoles and CI log viewers without UTF-8: the sparkline ramp
  becomes `._-=+*#%@` and the arrow and dash punctuation degrade to `->`
  and `--`. Requires `--trend`; the stored history and the `--json`
  document are byte-identical either way.

- `synapse cross-repo --suggest-resolution` can now name a concrete pin:
  when a version inside an odd-one-out's remainder range is already
  declared by one of the remaining consumers in an inclusive bound
  (`==`, `>=`, `<=`), the advice appends "`X.Y would satisfy them all
  (a version REPO already declares)`" and the JSON gains `suggested_pin`
  and `pin_source`. Evidence-based only: the version is lifted from a
  manifest, never invented — the scanner has no package index, so
  exclusive fence-post bounds are never candidates and whether an index
  publishes the version is not claimed.

- Federation peering age is now visible and enforceable: `synapse
  federation list` shows each peering's age since its confirmed import
  and renders a peering whose bundle expiry has passed as `[expired]`;
  `--max-age DAYS` flags active peerings imported longer ago than the
  threshold as stale and exits `1`, so a scheduled job can hold the
  fleet to a re-ceremony cadence. `federation import --max-age DAYS`
  applies the same policy at import time, warning (the import still
  succeeds) when the incoming bundle never expires or expires further
  out than the threshold.

- Added the end-to-end exchange-ceremony walkthrough to the federated
  trust model design doc: two operators, both fingerprint blocks
  captured from a real two-hub run of `federation offer`, `hub
  --federation-offer`, `federation fetch`, and the confirmed
  `federation import` — including the `--max-age` expiry grading at
  import time.

- `synapse causality health --watch` — the lifecycle-anomaly assessment
  becomes a standing coordination-health monitor: the store is reread
  and re-assessed every `--interval` seconds, the first tick prints the
  full report as the baseline, and every later tick prints only the
  anomaly transitions (`+ fact` new, `- fact` cleared, identity facts
  that deliberately omit the ever-growing ages), so a steady fleet
  stays quiet and the scrollback reads as a timeline. `--json` streams
  one full report per tick as NDJSON; a failing tick stops the watch
  with exit `2`; a bounded watch exits with the last tick's anomaly
  signal.

- `synapse benchmark --alert` — a deterministic statistical drift gate
  over the `--trend` history: every probe metric's latest value is
  measured in sigma distances from the sample mean of its same-context
  predecessors (same package version, CPU model, and governor as the
  latest run — the fields the context breaks annotate), and a value
  beyond `--alert-sigma` (default 3) exits `1`. A series with fewer
  than `--alert-min-samples` same-context samples (default 5, floor 3)
  is reported as insufficient and never silently gated; a flat
  baseline has no sigma, so any deviation from it is flagged as such.
  `--json` gains a `drift` object; composes with `--compare`.

- The SYNAPSE-protected badge — a repository whose CI gates on the
  `anulum/synapse-channel` policy-check action (or `synapse policy-check
  --enforce` directly) may declare it with a static badge; the policy
  engine page documents exactly what the badge claims, the three
  eligibility conditions (enforcement on, gating the protected path,
  committed policy file), and how a reader verifies the claim in the
  repository itself — an honest self-declaration with no hosting, the
  first slice of the managed GitHub App build order.

- `synapse dashboard --reliability-db HUB.DB` — the dashboard serves
  `/reliability.json`, the same audit-signal report as `synapse
  reliability` ("audit signals, not scores"), read from the durable
  event store rather than the live hub so it stays available when the
  hub is down. Without the flag the endpoint answers 404 (the cockpit
  reliability panel treats that as the feed being honestly absent); an
  unreadable store answers 503 rather than an empty report. Behind the
  same dashboard bearer token as every other path.

- A read-only web cockpit under `clients/cockpit/` (React + TypeScript +
  Vite single-page app over the dashboard's `/snapshot.json`): a live
  activity spine plotting real coordination transitions, fleet roster
  with per-path claims and presence honesty, worst-first risk rail,
  claims board, shared-plan deck, federation row, signal log, and a
  reliability panel fed by `/reliability.json` that renders audit
  signals — never a score.

### Security
- The last four unpinned tool installs in CI are now hash-locked: the
  pre-commit, release, publish, and reuse workflow jobs install
  `pre-commit`, `build`, `twine`, and `reuse` from a new
  `.github/requirements/requirements-tools.txt` (`uv pip compile
  --universal --generate-hashes`, installed with `--require-hashes`),
  closing the remaining supply-chain gap the dev and audit lockfiles
  already closed for every other job.

## [0.92.0] - 2026-07-03

### Added
- Federation-bundle exchange over the network, replacing the out-of-band
  file copy while keeping the trust decision with the operator: a hub
  started with `--federation-offer FILE` serves its own operator-authored
  bundle material over the ordinary websocket surface (token-gated,
  re-read per request so rotated material republishes without a
  restart); `synapse federation fetch URI --out FILE` pulls it, prints
  the fingerprint block, and never imports; `synapse federation offer
  FILE` validates the offering side's material and prints the identical
  block, so both operators compare like for like out-of-band before the
  explicit `federation import --confirmed-by` (whose `--source` records
  the fetch URI as the peering's provenance). The bundle fingerprint is
  a SHA-256 over the whole canonical bundle, so an in-path alteration of
  any policy content — namespaces and scope grants as much as keys and
  pins — changes the value the operators read to each other; there is no
  trust-on-first-use. Two new wire message types
  (`federation_offer_request`/`federation_offer`); every transport
  failure fails the fetch closed with nothing written.
- `synapse cross-repo --watch --notify-cmd CMD` runs an operator command
  whenever the coordination facts — live claims joined to the graph and
  provable version conflicts — change between two consecutive watch
  refreshes, with the delta on stdin (`+ fact` appeared, `- fact`
  cleared) and the scanned root in `SYNAPSE_CROSS_REPO_ROOT`. Fires on
  transitions only (never on the baseline refresh or a steady state);
  the command is shlex-split and run without a shell, and a failing or
  hanging sink is reported without stopping the watch. The sink is
  generic by design — a desktop notifier, `synapse send`, or anything
  else — keeping the scanner decoupled from any live hub.
- `synapse benchmark --trend STORE.db` appends each finished scorecard
  to a local SQLite history and renders per-metric sparkline trend
  lines across every stored run — first and latest values, the observed
  range, and the series shape — so a slow regression no single
  `--compare` gate trips stays visible. Host or package context changes
  between consecutive runs (CPU model, governor, version) are annotated
  as explicit breaks rather than silently connected; unlike
  `--compare`, a differing CPU model is annotated, not refused, since a
  history legitimately spans upgrades. The JSON document gains a
  `trend` object, and the flag composes with `--results` and
  `--compare`.
- Version-conflict detection now compares direct-URL requirements in the
  one case the conservative model can honestly claim: two references to
  the same base URL pinned at two hex revisions of which neither
  prefixes the other are provably two different commits and conflict;
  identical revisions overlap. Every other URL shape — different bases,
  branch or tag revisions (mutable), revision-less URLs, or a URL
  against a version range — remains uncompared, exactly as before.
- `synapse cross-repo --suggest-resolution` turns each detected version
  conflict into actionable advice: for every provably conflicting
  package it intersects all consumers' declared ranges (the same bounded
  interval model detection uses, so the two never disagree) and names
  which single repository's declaration is the odd one out, with the
  range the remaining consumers reconcile at. When no single declaration
  is the outlier the advice says the constraints split into mutually
  disjoint camps; declarations outside the bounded model are listed as
  unassessed. Advisory text only — nothing rewrites a manifest. The
  JSON report gains a `resolutions` list; the flag does not combine
  with `--watch` or `--dot`.
- `synapse causality health` flags three lifecycle-anomaly shapes in the
  coordination-causality graph: orphaned claims (a claim is its task's
  last recorded event), dangling dependencies (a declared `depends_on`
  whose task never completed — the same completion predicate the
  dependency-edge derivation uses), and stale claims (claimed, never
  released, silent longer than `--stale-after` seconds, default 3600).
  Ages are measured against the log's own final timestamp, never the
  wall clock, so a report is deterministic and replayable; exit `1`
  signals at least one anomaly, and every signal is an operator hint
  derived from recorded events, not a verdict.
- Federated causality queries gain `--dot`: the answer renders as a
  Graphviz digraph with one cluster per hub, so an edge inside a cluster
  is same-hub causality and an edge crossing cluster boundaries is a
  `federation` edge — coloured and labelled with its basis. The rendered
  edges are the query's induced subgraph (every merged-graph edge whose
  endpoints both belong to the answer), now also carried in the JSON
  output as `edges`; the queried node is double-bordered and a
  counterfactual's unsupported descendants are dashed. `--dot` requires
  `--peer` and excludes `--json`.
- `synapse causality otel` gains three projection controls:
  `--service-name NAME` overrides the `service.name` resource on the
  exported spans so several hubs can share one observability tenant;
  `--filter TASK_ID` (repeatable) projects only the named tasks' traces,
  refusing a task the log does not record, keeping cross-task links into
  excluded tasks (the deterministic ids resolve against any export that
  carried the other task) and counting the exclusions in the summary;
  and an event recording the task lifecycle's failure terminal
  (`failed`) — or a task whose final recorded status is it — now
  projects OpenTelemetry span status `ERROR`, making failed
  coordination visible in trace viewers. Everything else stays
  `UNSET`: the log records progress, not success verdicts. The JSON
  span records carry the new `status` and `filtered_out_tasks` fields.
- `synapse causality otel --watch` re-projects and re-exports every
  `--interval` seconds until `--count` ticks ran (0 = until Ctrl-C) —
  live coordination observability on a fixed cadence. The store is
  reread each tick, so newly recorded events appear in the next export,
  and the deterministic span ids make re-exports idempotent on the
  collector side; a failing tick stops the watch with its exit code.

### Fixed
- `A2ATaskEvents.has_subscribers` reports whether a live local
  subscription is registered for a task, so a publisher can sequence an
  update after a subscriber is known to be listening instead of racing
  the registration — the race a slow CI runner exposed in the
  subscription lifecycle test, which now synchronises on it.

### Security
- The hub federation gate now denies a frame signed with a peered key whose
  live certificate pin fails to resolve to a single peered domain (reason
  `peer_domain_unresolved`), matching the existing refusal of a peered key
  on a connection with no pinnable certificate. Previously such a frame —
  a stale or foreign certificate, credentials split across peerings, or an
  ambiguous pair two peerings both claim — degraded to local processing
  with only an operator warning. A frame signed with an unpeered key still
  takes the local path unchanged, and the misconfiguration diagnosis is
  still logged for the operator.

### Documentation
- New README "Security posture" section: the loopback-first default and
  the opt-in, deny-by-default runtime controls (connect and per-message
  authentication, Ed25519 signature trust, mTLS pins, ACL policy,
  paranoid mode) alongside the supply-chain gates (two-layer gitleaks,
  hash-locked CI toolchain, SHA-pinned actions, digest-pinned images,
  per-push pip-audit, CodeQL, Scorecard). The stale "Known limitations"
  bullet claiming no signature trust, ACL enforcement, or mTLS trust
  bundle exists is corrected to what actually remains out of scope: no
  key exchange or automatic trust distribution, and declared (not
  cryptographic) per-agent identity.

## [0.91.0] - 2026-07-02

### Added
- `synapse causality otel` projects the coordination-causality graph onto
  OpenTelemetry spans: one trace per task (a root span covering the task's
  recorded lifetime, one child span per coordination event) with cross-task
  `dependency`/`contention` edges carried as span links — "this claim
  proceeded because that release freed its paths" renders as a first-class
  link in any trace viewer. Ids are deterministic derivations of the task id
  and event sequence, so re-exporting the same log yields identical spans.
  `--out FILE` writes the span records as JSON with no new dependency;
  `--endpoint URL` pushes OTLP over HTTP through the official exporter
  behind the new optional `otel` extra. A failed push exits non-zero with
  the exporter's verdict, and taskless events are counted in the summary
  rather than silently dropped.
- `synapse causality` traces coordination causality across federated hubs:
  `--peer HUB=PATH` (repeatable) merges the named hubs' event logs in the
  deterministic multi-hub order, events are addressed as `HUB:SEQ`
  (`--hub-id` names the primary log), and an edge whose endpoints two
  different hubs authored is tagged `federation` with the recorded
  relation it derives from as its basis. Cross-hub precedence is
  clock-ordered evidence — hubs share no sequence — and the queries stay
  read-only and observe-only, like the multi-hub fold. `contention`
  remains single-hub and refuses `--peer`.

## [0.90.0] - 2026-07-02

### Added
- CI installs its dev, benchmark, and docs toolchain from a hash-locked
  requirements file (`--require-hashes`), making every workflow's
  dependency set byte-reproducible; the universal resolution carries
  markers for all supported interpreters, and the regeneration command is
  documented in the file header.
- Secret scanning joins the commit gate: a gitleaks pre-commit hook
  refuses a staged secret, and the pre-commit workflow gained a
  digest-pinned full-tree sweep so a secret already in the checkout
  cannot hide behind an empty staging area. A repository `.gitleaks.toml`
  allowlists the one false positive (docstring type annotations naming
  `Ed25519PrivateKey`) and the gitignored build artefacts that mirror it.
- The deployment guide's exposure section gained a worked reverse-proxy
  example: a Caddyfile terminating TLS in front of a loopback hub, the
  client invocation through `wss://`, and the trust-store and per-host-cap
  considerations — validated end to end against a real proxy.
- `synapse cross-repo --watch` rescans the checkout tree and rejoins live
  claims every `--interval` seconds (`--count` bounds the refreshes): a
  TTY clears and redraws the report in place, piped output separates
  refreshes with a `---` divider, `--json --watch` streams NDJSON, and the
  exit code reports the last refresh's `--repo` signal.
- `synapse benchmark --compare BASELINE.json` gates a run against a
  scorecard saved with `--results`: throughput and latency-percentile
  drift beyond `--tolerance` (default 25%, sized for shared-workstation
  noise) exits `1`, ungated context metrics never gate, a baseline from a
  different CPU model is refused, and softer host drift (governor,
  interpreter, package version) is reported as loud warnings. Under
  `--json` the document gains a `comparison` object beside the scorecard.
- `synapse cross-repo` flags declared version constraints that can never be
  satisfied together: every package two or more scanned repositories
  consume — external packages included — is checked pairwise, and a
  `version_conflict` edge (red in DOT output) appears when the constraints
  are provably disjoint. The comparison models PEP 440 specifier sets,
  Cargo requirements, and npm semver ranges over plain numeric release
  versions; anything outside that bounded model — pre-release or epoch
  segments, direct URL references, `go.mod` requirements — never claims a
  conflict, and dependency-edge evidence now carries the declared
  constraint text.

### Fixed
- `--token-file` naming a missing or unreadable file now fails with a clean
  `cannot read token file` message and exit code `2` instead of an unhandled
  traceback.

## [0.89.0] - 2026-07-02

### Added
- `synapse benchmark` measures the installed package on the operator's
  machine: probes for durable event-store appends, journal replay, lite
  relay encoding, and `who` plus claim-to-grant round-trips over a real
  loopback WebSocket hub, each reporting throughput and p50/p95 latency.
  The scorecard carries the host context — package version, interpreter,
  CPU model and governor, load averages before and after — and an explicit
  shared-workstation isolation label, so the numbers read as regression
  evidence, not as isolated-core production claims. `--probe` selects a
  subset, `--iterations` overrides defaults, `--json` emits data, and
  `--results FILE` writes the scorecard to disk.
- `synapse cross-repo` widens coordination from one repository to a whole
  checkout tree: it scans every repository under a root directory for
  dependency manifests (`pyproject.toml`, `Cargo.toml`, `package.json`,
  `go.mod`) and CODEOWNERS files, composes them into a graph of `dependency`
  and `shared_owner` edges, and joins the live claims of a hub event log onto
  it (a claim's `worktree` is its repository). With `--repo` the exit code
  becomes a coordination signal — `1` when a live claim exists in a
  repository connected to the focus by a dependency edge — and `--json` and
  `--dot` emit the graph as data or a Graphviz digraph. Manifests that exist
  but cannot be parsed are reported as problems rather than silently
  skipped. Declaration-level, advisory evidence only.
- `synapse trust-graph` queries the durable event log as an evidence graph,
  realising the agent-trust-graph design's read-only projection: typed edges
  between agent and task nodes — positive release receipts, stale claims,
  declared failed checks, broken handoff candidates, and one agent-to-agent
  edge per reconstructed conflict pair — each carrying the event-log sequence,
  timestamp, and evidence fields that created it. `--agent`, `--task`, and
  `--since` (a decay window) focus a review; `--json` emits the graph as data
  and `--dot` as a Graphviz digraph. Evidence with provenance, not scores: no
  ranking, no grades, no authorisation.
- `HubConfig` groups the forty-odd `SynapseHub` keyword parameters into typed,
  frozen family records — `HubLimits` (every enforced ceiling),
  `TakeoverDamping`, `HubAuthConfig` (connection, per-message, and ACL
  enforcement), `HubMetricsConfig`, `MultiHubConfig`, and `FederationConfig` —
  and `SynapseHub.from_config(config)` builds a hub from the record.
  Behaviour is identical by construction: every field name and default
  mirrors its keyword parameter, contract tests pin the flattened record
  against the live signature so the two surfaces cannot drift, and the flat
  keyword surface and every CLI flag remain unchanged.

### Changed
- `import synapse_channel` now resolves its public names lazily (PEP 562):
  the submodule behind a name is imported on first attribute access, cutting
  the bare package import from roughly one second to under ten milliseconds
  while keeping `__all__`, every re-exported object, and type-checker
  visibility identical.
- The `synapse` CLI registers subcommands lazily: `main` reads the requested
  command off `argv` and imports only the module family that owns it, so a
  short call such as `synapse who` or `synapse merkle root` no longer pays
  the import cost of the whole surface (local commands start in roughly a
  quarter of the previous time). `--help`, `--version`, and unknown commands
  still build the full parser, and contract tests pin every registration
  unit to the exact commands it provides and to help output identical to the
  full build.

### Added
- `synapse status --watch` refreshes the one-line hub summary every
  `--interval` seconds (default 2) as an operator dashboard. Each refresh
  opens its own probe connection, so a hub restart shows as an honest offline
  line; a TTY rewrites the line in place while piped output appends one line
  per refresh, and `--json --watch` streams one JSON object per line (NDJSON).
  `--count N` bounds the refreshes; Ctrl-C stops an unbounded watch cleanly
  with exit `0`, and the bounded form exits with the last observed state.
- `synapse workflow contention` joins a declarative workflow to the durable
  log: it compiles the workflow to its task ids, runs the same offline
  yield-advice analysis as `synapse causality contention`, and keeps only the
  overlapping live-claim pairs a workflow task is party to — whether it keeps
  or yields. Pairs outside the workflow are counted in a trailing note; the
  exit code signals scoped collisions only (`0` none, `1` at least one, `2` on
  an invalid workflow, a missing store, or the node ceiling).
- `synapse participant convene --dry-run` prints the convocation plan without
  taking a single turn: the resolved mode, its round count, and each seat's
  identity, readiness, planned turns, and estimated cost from an
  operator-supplied `--pricing` table under printed per-turn token assumptions
  (`--est-input-tokens`/`--est-output-tokens`). Seats without a price line are
  reported unpriced and excluded from the total; with `--budget-usd` the report
  states whether the estimate fits. Exit `0` when every seat is ready, `1` when
  any is unavailable, `2` for a refused configuration.
- The repository root now ships a composite GitHub Action (`action.yml`)
  wrapping `synapse policy-check`, so a repository can gate CI on a release
  receipt — optionally recomputing the Merkle commitment and requiring a
  trusted hub signature — with a single `uses:` step. Inputs reach the shell
  through environment variables, never script interpolation; the decision
  report is exposed as the `report` step output.
- The `synapse causality contention` documentation gained a worked two-agent
  example with real command output, showing how downstream weight picks the
  yielder and how a tie falls back to first-come precedence.
- `synapse participant costs` reads opt-in session telemetry back from a hub
  SQLite event store — offline, like `synapse accounting report` — and prints
  the latest cumulative snapshot per `(agent, session)` (turns, errors,
  abstentions, token pressure, metered spend, mean latency, highest rate-limit
  utilisation seen) plus fleet totals, or the machine-readable report with
  `--json`. Where the accounting report answers what models cost, this answers
  how participant sessions are going and what they spent; a missing store
  refuses with exit `2`.

## [0.88.0] - 2026-07-02

### Added
- The Participant Fabric gained its operator surface: `synapse participant list`
  reports each registered provider driver's readiness (claude, codex, kimi, ollama,
  ollama-api, grok) without taking a turn, and `synapse participant ask` runs exactly
  one turn against one provider and prints the answer — or the full typed turn result
  with `--json`. Grok turns are refused while its stream schema remains unverified
  against a real binary.
- The participant surface gained the Fabric's deliberation layers:
  `synapse participant exchange` runs an opener turn and a reactor turn that sees the
  opener's result only as fenced peer data, and `synapse participant convene` fans a
  question out to a panel named as `PROVIDER[:MODEL]` seats, runs the conversation
  mode's cross-critique rounds (`--mode auto` selects colloquy, roundtable, or
  symposium from the panel shape), and in a symposium ends with the moderator's
  synthesis. Both print each turn as it is produced — or the full typed transcript
  with `--json` — and honour a cumulative `--budget-usd` ceiling.
- Release receipts' coordination-log commitments can now carry hub-key provenance:
  `synapse merkle keygen` generates the hub deployment's Ed25519 receipt-signing
  keypair (owner-only private key, distributable `.pub` whose `key_id` is derived
  from the key material), `synapse verify-release --signing-key` signs the Merkle
  commitment into `verification.merkle_signature`, and `synapse policy-check
  --trusted-signing-key` adds a `merkle_signature` decision so a verifier holding
  only the receipt and the `.pub` file learns which hub attested that exact log
  state — no access to the live log required. Verification is deny-by-default: a
  tampered root, an untrusted or transplanted key, a malformed envelope, and a
  signature with no commitment to cover all fail; only an unsigned receipt reads
  `not_applicable`.
- `synapse causality contention` weighs every pair of overlapping live claims —
  different owners, same worktree, intersecting path scopes — by what each
  contender's task gates downstream (causal descendants of its recorded events
  plus pending declared dependents, transitively) and recommends which agent
  yields; on an equal count the later claim yields. Advisory only: no claim is
  preempted, and the exit code doubles as a collision signal (`0` no overlap,
  `1` at least one pair).

- `synapse status --json` and `synapse doctor --json` emit their counts and
  verdicts as machine-readable JSON for monitoring scripts and CI health gates;
  `doctor --json` is a plain diagnostic and refuses the mutating and checklist
  flags so stdout stays one document. The install guide now surfaces
  `synapse completions` and `synapse install-shell-hook`.

### Fixed
- Both multi-hub transports now decode peer-hub replies with the same
  depth-bounded JSON loader the hub applies to its own inbound frames, so a
  deeply nested reply from a malicious or compromised peer fails the poll (or
  refuses the forwarded claim) instead of recursing through an unbounded parse.
- The federation gate no longer downgrades a frame signed with a peered key to
  local processing when the connection presents no pinnable certificate — a
  plaintext socket or a certificate read that fails now denies such a frame
  outright, because the cross-domain authority its key claims can only be bound
  by a live pin. Frames signed with purely local keys are unaffected.

## [0.87.0] - 2026-07-02

### Added
- `syn reap --stale` sweeps every shell-hook pidfile and reaps the verified waiters
  whose owner shell or terminal process is dead (recorded `--owner-pid`, or the
  terminal PID embedded in the identity), keeping live and unjudgeable ones and never
  signalling a process whose command line is not this Synapse waiter; `--dry-run`
  reports the verdicts without acting.

### Fixed
- `synapse who` and `synapse status` no longer count `-rx` wake-listener sidecars as
  agents: the roster reads `N agents · M waiters` with the waiters listed apart, so a
  workstation's agent count matches the terminals actually running instead of every
  presence socket ever armed.
- Shell-hook waiters are now leashed to the shell that armed them: `synapse arm` gained
  `--owner-pid`, the bash/zsh/fish hooks pass their shell pid, and a waiter disarms
  itself the moment its terminal exits instead of holding a hub connection for days.
- A waiter displaced by a takeover now yields instead of fighting for the name back:
  `synapse wait` reports the eviction as its own exit code (`4`) and `synapse arm` ends
  its loop on it, so two waiters for one identity no longer steal the connection from
  each other until the hub quarantines the name.

## [0.86.0] - 2026-07-02

### Fixed
- The on-channel model worker now awaits the survivor task it cancels on shutdown, so
  stopping the worker no longer leaks a pending task into event-loop teardown.
- A lock release that fails during waiter teardown is now logged at debug level instead
  of being silently suppressed.

## [0.85.0] - 2026-07-01

### Added
- Release receipts can commit the coordination log: `synapse verify-release --merkle-db`
  embeds the log's RFC 6962 Merkle root (root, tree size, sequence range) into the receipt
  as both machine detail and an evidence line, binding the release to the exact
  coordination history behind it. `synapse policy-check --merkle-db` re-verifies the
  commitment later — it recomputes the committed log prefix, which append-only growth
  never disturbs, and adds a `merkle_commitment` decision that fails (and can gate with
  `--enforce` under an enforcement policy) when the prefix was rewritten, truncated, or
  renumbered since the receipt.

### Changed
- Building the causality graph is now bounded-memory: `synapse causality` streams only the
  coordination event kinds off the store cursor — the kind filter runs inside SQLite, so
  bulk chat on a long-lived hub never reaches Python — and folds them under a fail-closed
  ceiling (default 250 000 coordination events; `--max-nodes` raises it, `0` lifts it) that
  errors with a `synapse compact` remedy instead of exhausting memory.
- Committing the event log to a Merkle root is now bounded-memory: `synapse merkle root`
  (and `run_root`) streams events off a new lazy event-store cursor (`iter_events`) into a
  running commitment that holds only the `O(log n)` subtree peaks, so a multi-year log
  commits without loading into RAM. The root is bit-identical to the previous whole-log
  computation; building an inclusion proof still materialises the committed leaves.

## [0.84.0] - 2026-07-01

### Added
- `synapse completions <shell>` prints a static tab-completion script for bash, zsh, or
  fish. The script is generated from the installed CLI's live argument parser — top-level
  subcommands, nested subcommands, and long options — so it cannot drift from the surface
  it completes, needs no extra dependency, and starts no process per keystroke. Install it
  where the shell looks for completions (or evaluate it inline) and re-run the command
  after an upgrade to refresh it.

### Changed
- `synapse doctor --fix` now auto-repairs the safely repairable findings instead of only
  printing setup commands: when the default local hub does not answer or the identity's
  waiter is missing, it installs and starts the local hub, presence, and wake-arming user
  services, then re-runs the checks so the exit code reports the post-repair state. The
  repair is gated to the default loopback hub the generated services manage — a remote or
  non-default hub is never touched; its findings keep printed guidance, as do identity,
  exposure, and disk findings.
- `synapse hub --federation-store` now refuses to start when the store's peerings grant
  cross-domain scope but `--require-message-auth` is not set: without per-message
  authentication no signing key is ever verified, so the granted scope could never be
  enforced and every cross-domain frame would be silently refused. A store whose peerings
  grant no enforceable scope still starts with the existing warning, and the new
  `--federation-observe-only` flag declares the intent to load a scope-granting store for
  diagnostics and deny-closed refusal only; combining it with `--require-message-auth`,
  or passing it without a store, is refused as contradictory.

## [0.83.0] - 2026-07-01

### Added
- `synapse status` prints a one-line hub summary — online agents and active claims (and live resource
  offers when any exist) — sized for a shell prompt or a tmux status bar. It draws the roster from the
  live connection set rather than the cumulative last-seen ledger, and its exit code doubles as a prompt
  signal: zero when the hub answers, non-zero when it is down.
- The federation gate now logs a warning when a signed frame arrives over a pinned connection but
  resolves to no peered domain because a peering's signing key or certificate pin is missing, stale,
  split across peerings, or ambiguous. The frame is still handled locally, unchanged; the warning is the
  operator signal a misconfigured peering previously lacked. An ordinary local frame — neither credential
  enrolled — stays silent.
- Documented the connect-once versus per-frame trust model and when to enable `--require-message-auth`
  (multiple parties, attributable authorship, or federation, which requires it) in the per-message
  authentication guide.

### Fixed
- `docker compose up` now starts a working hub. A container must bind `0.0.0.0` for its published port to
  reach it, which the hub refuses without a token, so the shipped compose command crash-looped on
  "Refusing to bind". The command now passes `--insecure-off-loopback` — safe because the port is
  published on loopback only — and a new CI compose smoke waits for the container to report healthy so the
  default cannot regress unnoticed.

## [0.82.0] - 2026-07-01

### Added
- `synapse commands` prints every subcommand grouped by its stability tier (stable core, adapters,
  read-only analysis, advisory governance, experimental) with a one-line summary of each tier, so the
  surface can be scanned by responsibility instead of read as one flat `synapse --help` list.

### Fixed
- The federation gate now degrades to the local frame path when reading the peer's live certificate
  raises, instead of letting the exception crash the connection's frame handler. A certificate read can
  fail on a socket that has closed or never completed its TLS handshake; such a frame is now handled
  exactly as an absent certificate is.

## [0.81.0] - 2026-07-01

### Changed
- The CLI reference now lists every subcommand in its command table and adds worked examples for the
  setup and integration commands (`init`, `install-shell-hook`, `shell-hook`, `arm`, `adapters`,
  `worker-session`), the advisory governance commands (`identity audit`, `acl shadow`, `policy-check`,
  `federation`, `encrypt-key`), and the experimental `sandbox` and `workflow` surfaces. `synapse health`
  is documented as silent by design (it reports through its exit code), contrasted with `synapse doctor`.

## [0.80.0] - 2026-07-01

### Added
- `synapse merkle verify --json` writes a `{"valid", "seq", "root"}` verdict to stdout (with a
  `reason` when the proof is rejected), giving offline proof verification the same machine-readable
  stdout payload that `merkle root` and `merkle prove` already carry. Without the flag, verification
  still reports through its exit code and a stderr line.

## [0.79.0] - 2026-07-01

### Added
- `SYNAPSE_URI` selects the hub for every CLI command. An operator working against a non-default
  hub — a remote coordinator, or a second local hub on another port — now sets it once instead of
  repeating `--uri` on each command. An explicit `--uri` still overrides it for a single call, and
  a blank or unset variable falls back to the loopback default `ws://localhost:8876`.

### Fixed
- The "Coordinate from code" quickstart example started a hub in the same process and connected an
  agent to it without waiting for the server to bind, so the agent could abandon a refused
  connection and every following verb would act on a closed one. The example now connects to a
  separately started hub and stops with a clear error when the hub is unreachable.

## [0.78.0] - 2026-07-01

### Added
- `synapse merkle root|prove|verify ./hub.db` commits the durable event log to a Merkle root: a
  single SHA-256 fingerprint of every event, so two operators — or two federated hubs — holding
  the same log derive the same root and a mismatch proves the logs differ. `merkle prove SEQ`
  emits an O(log n) inclusion proof for one event, and `merkle verify proof.json` checks that
  proof offline against a trusted root with no event store, the light-client verification a
  follower runs (`--expect ROOT` pins the root; `--through SEQ` commits only up to a sequence).
  The tree follows RFC 6962 with distinct leaf and interior-node domain-separation prefixes, so a
  leaf hash cannot be forged as an interior node. It commits what the log contains — integrity and
  inclusion — complementing the per-task `reproduce` digest with a log-wide, incrementally
  provable commitment. It is read-only and contacts no live hub.

## [0.77.0] - 2026-07-01

### Added
- `synapse debug ./hub.db --fork-at SEQ` forks a task's reconstructed state at a sequence
  point: it folds the durable log back into the exact claim state the task held there — owner,
  status, declared paths, and the saved resume checkpoint — and prints the resume manifest an
  agent would pick up if the task were rewound to that point, beside the events that really
  happened next. The task is inferred from the snapshot at the sequence or named with `--task`,
  and `--set FIELD=VALUE` overrides a resume field on the manifest only. It is read-only
  inspection over the log: the hub runs no task, so nothing is executed or changed.
- `synapse reproduce ./hub.db TASK` fingerprints a task's authoritative history into a stable
  SHA-256 digest of its claim snapshots and releases, so the same history yields the same digest
  on every machine. `--expect DIGEST` gates on a known-good value and exits non-zero on any
  divergence, the way a release receipt is verified.
- `synapse causality causes|effects|counterfactual ./hub.db SEQ` traces coordination causality
  over the event log. It folds the durable events into a directed acyclic graph of three recorded
  relations — a task's own lifecycle, a declared `depends_on` satisfied by the dependency's
  completion, and a release that let a later, path-overlapping claim proceed — and answers against
  an event sequence: the events upstream of it, the events it enabled downstream, or the downstream
  events whose recorded cause traces back through it. Every edge is backed by a concrete event;
  the counterfactual is a structural what-if over the inferred graph, not statistical causal
  discovery. It is read-only and contacts no live hub.

## [0.76.0] - 2026-06-30

### Added
- A hub can now load its federation policy from an imported store at startup with
  `synapse hub --federation-store FILE`, so a peering imported with `synapse federation
  import` takes effect on the next start. The store's peerings — including revoked or expired
  ones, which authorise nothing — are composed into the live frame authorisation. Federation
  binds authority only alongside `--require-message-auth`; a store without it logs a warning
  that no cross-domain frame will be honoured, and a malformed store is reported and refused.
  With no store the live path is unchanged.
- Wired the federated trust policy into the live authorisation of agent frames, opt-in and
  deny-closed. A hub configured with a federation bundle now recognises a frame from a peered
  remote domain — identified only from its verified signing key and the live certificate pin,
  never a self-declared field — and authorises it against that peering's bounded scope, composed
  with mutual TLS, the event signature, and the mapped scope. A frame any layer refuses is
  refused with the reason named; a cross-domain frame on a hub that does not require per-message
  authentication is refused, since its authority cannot be bound. An allowed cross-domain frame
  is routed without the local access policy, which a remote subject has no identity in. A hub with
  no federation bundle is unchanged: every frame takes the local path exactly as before.
- Added a scope check that authorises a remote subject's frame against a peering's bounded
  scope, evaluated exactly as a local subject's frame is against the local access policy. Each
  access the frame requires is mapped to a verb in the remote subject's namespace, and every one
  must be granted by the peering's scope; a subject inherits no local default, so a frame with no
  granted verb, an empty scope, or no mapped access at all is denied rather than allowed. This
  keeps one authorisation vocabulary across local and cross-domain frames — only the policy they
  are evaluated against differs. Pure building block; not yet wired into the live frame path.
- Added a resolver that identifies which peered domain a frame belongs to from verified
  credentials alone. Given the Ed25519 signing-key id taken from a frame's verified signature and
  the certificate pin read off the live connection, it returns the single peered domain that
  accepts both, or nothing when no peering accepts both or more than one does. A key accepted by
  one domain presented over another domain's connection resolves to neither, and an ambiguous
  configuration is refused rather than guessed, so a frame's issuing domain is never taken from
  self-declared content. This is a pure building block; the live frame path is unchanged until it
  is wired in.

## [0.75.0] - 2026-06-30

### Added
- Added runtime partition detection to claim routing. The ownership gate now consults an optional
  feed of the hubs observed asserting authority over a namespace, so a partition — a peer seen
  holding a claim in a namespace this hub also believes it owns — refuses every grant until
  ownership is re-established, even on the hub's own local grant path. `multihub_fold`'s
  `asserting_owners` derives that feed from a follower's observed claims (the hub id that holds a
  claim is observed owning the claim's namespace), and a hub wired with it through the opt-in
  `observed_asserting_hubs` source refuses a contested claim as `partitioned`. With no feed
  configured, ownership resolves from the static map alone, exactly as before.
- Closed the cross-hub claim-routing loop: a non-owning hub now forwards a claim for a namespace
  it does not own to the hub that does and relays the verdict to the claimant. A hub configured
  with `claim_peers` — a route to each owning hub — forwards a remote-owned claim automatically;
  the claimant sees the owner's authentic `claim_granted` (with the real lease) or its denial,
  just as for a local claim. The route is opt-in and fails closed: a hub with no route for the
  owner, or one whose owner is unreachable, ungoverned, or contested, refuses the claim and names
  the owner, exactly as before, so an unreachable owner never lets a claim be believed granted.
  Two hubs that each own their own namespaces can now coordinate claims across a connection
  without a shared filesystem or a global leader.
- Added the forwarding half of cross-hub claim routing: a network client that asks a namespace's
  owning hub to grant a claim and returns its authoritative verdict. It opens an on-demand
  connection to the owning hub, sends the forwarded claim, and decodes the result the owning
  hub's handler replies with — holding no standing outbound connection between claims. Every
  transport failure (a refused or dropped connection, an error frame, a malformed or absent
  result, or a timeout) fails closed as a single error, so a caller relays a real verdict or,
  on failure, falls back to refusing the claim and naming the owner — an unreachable owner or a
  split never lets a claim be believed granted. Wiring this into the non-owning hub's claim gate,
  so a remote-owned claim is forwarded automatically, is the remaining slice.
- Added the serving half of cross-hub claim forwarding: an owning hub now grants a claim
  forwarded from another hub and relays the authoritative verdict back. When a non-owning hub
  forwards a claim, the owning hub applies it through the same authoritative grant path a direct
  claim uses — so the lease it produces is identical however the claim was routed — and answers
  with whether it granted, the owning hub's id, and the grant fields the forwarding hub relays to
  its client. Because a forwarded claim mutates lease state on a remote agent's behalf, the gate
  fails closed at every step: the peer must be authorised by the hub's serving policy (a hub with
  no policy accepts no forwarded claim at all), this hub must authoritatively and uncontestedly
  own the namespace, and a malformed request grants nothing. Reaching out to the owning hub from
  the non-owning side is the remaining slice; until then a non-owner still refuses and names the
  owner.
- Added the wire codec for forwarding a claim to the hub that owns its namespace. It names the
  two shapes that exchange uses — a request carrying the namespace, the claimant the grant is made
  under, the task id, and the original claim body the owning hub re-applies, and a result carrying
  whether the owner granted, the owning hub's id, a human-readable detail, and the authentic grant
  fields the forwarding hub relays back to its client. The codec is pure, with no network, clock,
  or hub dependency, and decoding is defensive: a malformed request or result raises rather than
  yielding a half-built shape, so a forwarding hub that catches it refuses the claim and relays no
  grant it cannot trust. This is the first step toward granting a routed claim on the owning hub
  rather than only telling the caller where to route it.
- Added namespace-ownership resolution and its local enforcement on the claim grant path, the
  first half of routing claims across hubs without merging them. A claim is mutual exclusion, not
  a mergeable value, so claims are routed by namespace ownership: each namespace has exactly one
  authoritative owning hub. `NamespaceOwnership` resolves a namespace to local, remote, ungoverned,
  or partitioned (the last two fail closed); a hub configured with such a map refuses a claim whose
  namespace — derived from the agent identity, as the ACL derives it — it does not own, naming the
  owning hub in the `claim_denied` so the caller can route the claim there. The gate is opt-in: a
  hub with no map grants every namespace, exactly as a single hub does today. Forwarding the refused
  claim to the owning hub over a connection is not yet built; the caller is told the owner.
- Added serving-side enforcement of the deny-by-default multi-hub pull gate, the counterpart of
  the gating the following side already applies. A hub configured with a `MultiHubServingPolicy`
  reads the certificate the peer presents on the live mutual-TLS connection and runs the same
  federation-and-mutual-TLS composition before serving its event log: a peer with no operator
  grant, a connection presenting no client certificate, or a certificate whose pin the policy
  does not accept is answered with an empty snapshot — the same shape as "no new events", so the
  refusal discloses neither the log nor whether the peer or its grant exists. The gate is
  opt-in: a hub with no policy serves every peer as before, so no existing deployment changes.
  The federation/mTLS pull gate is now enforced on both sides of a cross-host pull.

## [0.74.0] - 2026-06-30

### Added
- Added `synapse multihub follow`, the network counterpart of `synapse multihub observe`. Where
  `observe` reads a peer hub's event-store file, `follow` pulls the peer's log over a real
  connection (`--peer-uri ws://… | wss://…`), folds it through the same read-only follower, and
  prints the observed board, progress, and advisory claims (or `--json`). It grants nothing, like
  `observe`, and accepts `--token`, `--limit`, and `--timeout`; deny-by-default federation/mTLS
  gating remains available in the library. This makes the cross-host transport usable from the
  command line for a peer reachable over the network rather than a shared filesystem.
- Added deny-by-default authorisation for a multi-hub pull, so a follower only pulls from a peer
  an operator has explicitly granted. A single decision composes the federation policy with
  mutual-TLS peer verification through the existing composition law — a pull is permitted only
  when every layer permits it, and federation never widens a check. It is fail-closed: an
  unknown, revoked, or expired peering, a namespace the peering does not grant, an unaccepted
  certificate pin, or a certificate file that cannot even be loaded all refuse the pull, and the
  gate re-evaluates a peering's expiry and revocation on every poll. The network fetcher accepts
  this gate and consults it before each fetch connects, failing closed without connecting when
  the peer is not authorised. (Wiring the same decision into the serving hub from the live mTLS
  connection is a deployment follow-up.)
- Added the wire codec for a cross-host multi-hub event-log pull. It names the two shapes one
  hub uses to ask another for the events past a cursor — a request carrying an exclusive
  `after_seq` and an optional batch `limit`, and a snapshot carrying the batch of events plus a
  `next_cursor` to resume from — and converts them to and from the JSON-object wire bodies. The
  codec is pure, with no network, clock, or hub dependency, and decoding is defensive: a
  malformed body raises rather than yielding a half-built batch, so the fetching follower can
  fail the poll and leave the peer's cursor unadvanced. This is the first step toward following a
  peer hub over a real connection rather than only over a shared filesystem.
- Added the serving half of the multi-hub event-log pull: a hub now answers a peer's
  `multihub_log_request` (an `after_seq` cursor and optional `limit`) with a private
  `multihub_log_snapshot` carrying the events past the cursor and a `next_cursor` to resume from,
  read through the durable event log's existing cursor. The handler is read-only — it mutates
  nothing and the access layer leaves it ungated like the other read snapshots — and forgiving of
  a malformed request (it answers with an empty snapshot rather than an error); a hub running
  without persistence serves an empty snapshot anchored at the requested cursor. This is the
  network counterpart of the follower's shared-filesystem reader.
- Added the fetching half of the multi-hub event-log pull, so a hub can follow a peer over a real
  connection rather than only over a shared filesystem. `network_fetcher` returns a follower
  `EventFetcher` that opens a connection to a peer hub, requests the events past a cursor, and
  decodes the snapshot reply — dropping into the existing follower with no change to its seam.
  Each fetch uses a fresh connection and holds no state between polls, and every failure mode (a
  refused or dropped connection, a hub error frame, a malformed or absent snapshot, or a timeout)
  is raised as a single error type, so the follower advances a peer's cursor only on a clean fetch
  and leaves it unadvanced otherwise — the fail-closed posture extended across the network.
- Added an opt-in step that turns the deliberation advisor's per-round signals into automatic
  actions. The advisor stays purely advisory; this separate reactor lets an orchestrator arm a
  chosen subset of signals (`compact-soon`, `log-now`, `high-error-rate`) to trigger a compact,
  log, or handover via caller-supplied handlers. Every axis is opt-in — an action fires only when
  its signal is present, the action is armed, and a handler is supplied — so the default does
  nothing and the concrete side effects stay the operator's. The routed deliberation loop and its
  bus binding both accept this dispatch and record the actions taken per round.

### Changed
- Clarified the Grok participant's support status: the driver is built and unit-tested, so the
  integration is ready to enable, but it is not recommended until xAI ships a stable Grok CLI.
  The CLI is not yet stable, so its streaming-json output schema could not be captured at source
  and stays unverified; the schema must be re-verified against a stable Grok CLI before the
  gated real smoke is trusted.
  (Note, 2026-07 update: June 2026 escalations documented the Grok CLI as heavy/unreliable on
  the target workstation with repeated freezes and memory pressure. As of 0.2.91+ the binary
  is present and reported stable; those specific workstation issues are no longer observed.
  The schema-verification gate remains.)

### Fixed
- Fixed the multi-hub network fetcher not catching a fetch timeout on Python 3.10, where the
  timeout error is a distinct type from the built-in. A timed-out fetch now fails closed
  uniformly across supported Python versions.

## [0.73.0] - 2026-06-30

### Added
- Made the Participant Fabric's session telemetry durable. A session's running operational
  metrics (turns, errors, abstentions, cumulative tokens, spend, latency, and the highest
  rate-limit utilisation) can now be recorded to the progress ledger as an opt-in
  `session_metric` note and read back across processes and sessions. `emit_session_metric`
  mirrors the usage-note bridge — it is opt-in, default off, skips an empty session, and never
  raises into the turn it observes — and `run_session_metric_report` /
  `build_session_metric_report` reduce those notes to the latest cumulative snapshot per
  session and total across sessions, rendering both human text and a stable JSON shape. The
  hub core remains a no-telemetry substrate: the snapshots ride the existing progress-ledger
  channel, introduce no new wire message or stored-event kind, and are descriptive evidence,
  not an enforcement gate.
- Added a routed, telemetered deliberation loop (`orchestrate_session`) that brings the
  Participant Fabric's Phase 5 pieces together at run time. It generalises a fixed-order
  conversation: each round the router picks which provider should answer now, the loop drives
  that participant, folds the result into the running session metrics, and reads the advisor's
  verdict. A turn's reported rate-limit utilisation is fed back before the next routing
  decision, so load steers away from a provider nearing its limit. The advisor stays advisory
  with one bounding exception that mirrors the existing budget guard — an over-budget signal
  halts the run — and, when a poster is supplied, each round persists a durable `session_metric`
  snapshot. The hub core is untouched.
- Bound the routed deliberation loop onto a live hub with `BusOrchestration`, the orchestration
  counterpart to `BusConversation` and `BusConvocation`. A connected bus identity publishes every
  routed turn to the room as a topic-stamped chat message; with `emit_metrics` enabled it also
  persists a durable `session_metric` snapshot to the hub after each round. Both emissions stay
  opt-in and default off, so the bus binding honours the no-telemetry stance.

## [0.72.0] - 2026-06-30

### Added
- Added the Participant Fabric (`synapse_channel.participants`) — an optional layer, on top
  of the bus and never in core, that drives a provider CLI session as a uniform bus
  participant. A `Participant` answers a typed `TurnRequest` with a typed `TurnResult`
  (answer, disclosed rationale, abstain/error state, provider resume token, metered cost),
  so a multi-hop conversation exchanges structure rather than re-summarised prose. This first
  release covers the headless channel: `HeadlessClaudeParticipant` runs
  `claude -p … --output-format stream-json` and parses its event stream, injecting shared
  context through `--append-system-prompt` so peer text never arrives as the user prompt.
  `conduct_exchange` runs a two-participant loop — one answers, a second reacts to the first's
  result — and `BusExchange` publishes each result to a live hub. Every participant output
  that becomes another's input passes through a prompt-injection boundary that fences it as
  data and forbids obeying instructions inside it. A provider failure becomes an error result,
  never a raised exception. The layer adds no new dependency and is not imported by the bus
  core; it drives the external `claude` binary at runtime. 100% line+branch on the new modules.
- Added session continuity and multi-round conversations to the Participant Fabric. A
  `ContinuitySeat` wraps any participant and gives it memory across turns by threading the
  provider session resume token, so a later turn resumes the earlier one; an errored or
  session-less turn never overwrites a good thread. `conduct_conversation` runs a bounded
  multi-round deliberation that cycles through participants — each round reacting to the
  previous turn's result through the injection boundary, each participant remembering its own
  earlier turns — under a hard round cap and an optional cumulative cost budget that halts the
  run early and records that it did (a bounded run never reads as a completed one).
  `BusConversation` publishes such a conversation to a live hub. 100% line+branch.
- Added a second Participant Fabric provider: a headless Codex driver. `CodexParticipant`
  runs `codex exec --json` (and `codex exec resume <id>` for continuity) under a read-only
  sandbox by default, and parses its JSONL event stream into the same typed `TurnResult` the
  Claude driver produces — so the two compose as uniform peers with no provider-specific code
  in the orchestration. Two contract differences are handled and documented: Codex has no
  system-prompt channel, so the shared context (including any fenced peer contribution) is
  prepended to the prompt under a separator; and Codex reports token usage but no monetary
  cost, so its turns carry `cost_usd` of 0 and a conversation's cost budget cannot bound them
  (only the round cap can). A `ContinuitySeat` gives a Codex session memory across turns the
  same way it does a Claude one. 100% line+branch; the headless turn, real `--resume`
  continuity, and a cross-provider exchange (a Claude turn and a Codex turn in one
  conversation) are each covered by gated real smoke tests.
- Added the multi-party conversation layer to the Participant Fabric — the part that
  multiplies reasoning rather than relaying it. A conversation is run in one of three modes,
  selected for the session: a `Colloquy` (a small, deep exchange), a `Roundtable` (equal
  participants, one broad refinement pass), or a `Symposium` (a larger gathering whose
  moderator synthesises a final answer). `convene` runs any mode through one shape: an opening
  fan-out where every participant answers concurrently, then the mode's cross-critique rounds
  where each refines having seen the whole panel's answers as fenced data, then a moderator
  synthesis when the mode uses one. `select_mode` picks the mode from the panel size and
  whether a moderator is available. Every paid turn is bounded — a capped number of critique
  rounds and an optional cumulative cost budget that halts the convocation between rounds and
  records that it did. A peer's answer reaches another participant only through the injection
  boundary, so the multiplication layer has no injection hole. `BusConvocation` publishes a
  convocation to a live hub. 100% line+branch.
- Added a third Participant Fabric provider: a headless Kimi driver. `KimiParticipant` runs
  `kimi --print --output-format stream-json` (adding `-r <id>` for continuity) and parses its
  JSONL message stream into the same typed `TurnResult` the other drivers produce, so all
  three compose as uniform peers with no provider-specific code in the orchestration. Three
  contract differences are handled and documented: Kimi has no system-prompt channel, so the
  shared context (including any fenced peer contribution) is prepended to the prompt under a
  separator; its print mode auto-approves tool calls, so a reasoning participant runs in
  read-only plan mode by default and cannot modify the workspace; and it reports no monetary
  cost, so its turns carry `cost_usd` of 0 and a conversation's cost budget cannot bound them
  (only the round cap can). The resume token is read from the provider's stderr, where Kimi
  reports it, and a `ContinuitySeat` gives a Kimi session memory across turns the same way it
  does the others. 100% line+branch; the headless turn and real session resume are covered by
  gated real smoke tests.
- Added a fourth Participant Fabric provider: a headless Ollama driver — the one provider that
  runs entirely locally, so it is free, offline, and has no account or terms-of-service gate.
  `OllamaParticipant` runs `ollama run <model>` and distils the model's plain-text reply into
  the same typed `TurnResult` the other drivers produce, so all four compose as uniform peers
  with no provider-specific code in the orchestration. Unlike the others, Ollama's `run` mode
  emits no JSON event stream, no session token, and no cost, so a local turn carries an empty
  session and `cost_usd` of 0, and its continuity comes from the conversation's fenced context
  rather than provider-side memory; a thinking-capable model's reasoning is suppressed so it
  cannot pollute the reply. A model name is required, as `ollama run` always names one. 100%
  line+branch; the local turn is covered by a gated real smoke test.
- Added a fifth Participant Fabric provider: a headless Grok driver, built for completeness but
  not run here. `GrokParticipant` builds `grok --single <prompt> --output-format streaming-json
  --permission-mode plan`, routing shared context through Grok's `--rules` system-prompt append
  and resuming a session via `--resume`. The argv is verified against `grok --help` (Grok
  0.2.64); the *stream schema* was not captured at source at addition time (GROK_SCHEMA_VERIFIED=False).
  Parser targets assumed Claude-Code-family convention. Real smoke gated pending capture+verification against stable grok. (Note 2026-07: prior CLI reliability issues resolved per operator reports; grok 0.2.91+ stable and detected; the then-remaining schema gate is resolved by the real 0.2.93 capture recorded under [Unreleased].) 100% line+branch.
- Added the bus-mediated turn relay, the foundation for the Participant Fabric's PTY and MCP
  channels. Where a headless participant spawns a fresh process and reads its stdout, a
  long-lived peer instead receives the turn over the bus and answers over the bus; `relay_turn`
  publishes a turn request to the peer, runs an injected wake hook to nudge it, and awaits the
  reply. Reply correlation is a hybrid: it prefers a typed `turn_result` matched by topic id
  (what a peer running the forthcoming responder returns) and falls back, after a short grace,
  to wrapping a plain-text reply as a degraded answer, so a peer without the responder still
  participates. A hub that never becomes ready, or a turn with no reply, becomes an error
  result rather than a raised exception. The turn request now has a symmetric wire envelope
  (`turn_request_to_payload` / `turn_request_from_payload`) beside the existing turn result.
  No new dependency; 100% line+branch.
- Added the peer-side turn responder, the other half of the bus-mediated relay. A
  `TurnResponder` wraps a local participant and connects one bus identity; for each turn
  request addressed to it, it runs the participant and publishes a typed `turn_result` back to
  the requester, re-stamped with the responder's own identity and channel so the envelope
  records who answered on the bus rather than the inner driver. This is the structured side of
  the relay's hybrid correlation — a peer running the responder returns a full typed result,
  while a peer without one still answers through the relay's degraded free-text fallback. Turns
  are served one at a time, and a payload that is not a turn request, or that carries no usable
  sender, takes no turn; an unready hub ends serving without answering. No new dependency;
  100% line+branch.
- Added the two bus-mediated participant channels on top of the relay. A `PtyParticipant` fronts
  a terminal agent reading from a tmux pane: it relays the turn over the bus and supplies the
  relay's wake hook by injecting the fixed, payload-free wake prompt into the pane, so the task
  travels as bus data and only the routing nudge touches the terminal. An `McpParticipant` fronts
  a peer already listening on the bus through its own waker and the Synapse MCP tools, so it
  relays with no wake at all. Both front exactly one peer — the seat's identity is that peer's bus
  identity, which the relay addresses and matches the reply by, while the relay connects under a
  separate sender identity. A peer running the responder answers with a typed result; a peer
  without one still answers through the degraded free-text fallback. No new dependency;
  100% line+branch.
- Added a channel selector that chooses how to drive a provider. `select_channel` reads a small
  capabilities descriptor — whether the peer is reachable over MCP, the name of its headless
  binary, whether a tmux session is configured — and returns the most robust available channel in
  the `MCP > HEADLESS > PTY` order, with the headless rung counting only when its binary resolves
  on `PATH`. A provider that exposes no usable channel selects nothing, so a caller reports it as
  undrivable rather than guessing. 100% line+branch.
- Captured the model token usage the Participant Fabric had been discarding, and added an opt-in
  bridge to the existing usage accounting. A turn outcome now carries the provider-reported input
  and output token counts (read from the Claude result `usage` block and the Codex `turn.completed`
  usage), and a turn request and result carry the model the turn is attributed to — the operator's
  declared model on the request, restamped by a driver that knows the model it actually ran. A new
  opt-in helper formats these into the canonical `usage` accounting note and posts it to the
  progress ledger, so a bus-bound exchange or conversation run with usage emission enabled becomes
  visible in the existing cost/token report; emission is off by default, keeping the no-telemetry
  default. The hub core is unchanged and no dependency is added. 100% line+branch.
- Added an API channel and a first participant for it: an Ollama REST driver. Instead of spawning
  a CLI, `OllamaApiParticipant` POSTs to a model server's `/api/generate` endpoint and reads the
  JSON reply, capturing the API-reported token counts straight into the usage accounting. The
  transport is the Python standard library, so no dependency is added, and the request is made
  through an injectable poster so the path is tested without the network. A new `api` channel value
  joins the selection order as `MCP > API > HEADLESS > PTY` — a direct HTTP call is more robust than
  spawning a subprocess — and the channel selector gains an API rung. A model name is required, the
  endpoint is stateless (continuity rides the conversation's fenced context), and a local turn has
  no cost; a transport failure or malformed body becomes an error result. 100% line+branch, with a
  gated real smoke against a running local server.
- Captured the rate-limit signal the Claude parser had been discarding. A turn outcome and result
  now carry the provider's last reported rate-limit utilisation (or none when unreported), read
  from the `rate_limit_event` the parser previously ignored, with the latest event winning and a
  malformed one dropped rather than coerced. The signal travels on the turn result so a router can
  read a provider's headroom and deprioritise one close to its limit, instead of the awareness
  being thrown away. 100% line+branch.
- Added a provider/model router that chooses which model should answer a task. Where the channel
  selector answers how to drive one provider, `select_provider` answers which to drive: from a task
  profile (required capability tags, expected token sizes) and a set of candidate models, it keeps
  the candidates that are drivable and carry every required capability, then ranks the survivors by
  rate-limit headroom (a candidate at or over its limit is dropped, so the captured rate-limit
  signal steers load away from a throttling provider), then estimated cost (a local unpriced model
  ranks free), then channel robustness. It returns the winning candidate with its channel and the
  cost it was ranked on, or nothing when the task is unroutable. The router is pure and selects but
  never constructs a participant, leaving that to the caller. 100% line+branch.
- Added session telemetry and an operational advisor. A running `SessionMetrics` total folds each
  finished turn — its tokens, cost, latency, error and abstention counts, the highest rate-limit
  utilisation seen, and the current context size (the last turn's input tokens, since the
  cumulative figure overcounts a re-sent history). From those metrics and a small set of
  thresholds, `assess_session` reports advisory operational signals: compact a filling context, log
  on a turn cadence, stop against a budget, ease off a provider near its rate limit, or investigate
  a high error rate. The advice is descriptive evidence, not an action and not a gate — the
  function never logs, compacts, or stops a run; it returns recommendations with reasons for a
  human or a higher layer to act on. The fold is pure (the caller measures latency and passes it
  in) and the assessment is pure over the metrics, so both are deterministic and tested without a
  clock. The token figures are the driven participants' pressure, the honest signal this layer can
  see; the orchestrator's own remaining context is a harness metric it does not observe. 100%
  line+branch.
- Added the WASM sandbox getting-started guide (`docs/wasm-sandbox-getting-started.md`):
  an operator walkthrough from a tool's source to a capability-limited run — compile a Rust
  tool to `wasm32-unknown-unknown`, compute its digest and write a deny-by-default manifest,
  `validate` the manifest, `test` (pre-flight) the tool, and `run --approve` it for an audit
  receipt. Every command and its output were captured from a real end-to-end run; the guide
  uses a digest placeholder (each build differs) rather than a fixed digest. Linked from the
  nav and README, with a doc test that keeps its commands parseable by the live CLI and its
  documented verbs in sync. (KIMI v0.71.0 gap closed.)
- Added `synapse sandbox test` — a dry-run pre-flight that loads a `.wasm` tool and verifies
  it against its manifest *without running it*: `core/wasm_sandbox.py` compiles the module
  (validating its structure) and reads its exported functions but never instantiates or
  calls it, so no fuel is spent and a runaway tool still pre-flights instantly. The bounded
  `PreflightReport` (`core/sandbox_receipt.py`) records whether the module is well-formed,
  whether the `--entrypoint` (default `run`) is an exported function, whether the module
  matches its manifest digest, and what it would be granted, with a single `ok` verdict the
  CLI maps to exit `0` (ready), `1` (pre-flight ran, tool not ready), or `2` (could not
  pre-flight). A cheap gate before `sandbox run --approve`. Behind the optional `[wasm]`
  extra; 100% line+branch on the new code. (KIMI v0.71.0 gap closed.)
- Added the live Studio command centre `/studio/command` (Studio Stage B): the operator
  view that reads `/studio.json` and renders it in the instrument-panel design system. Its
  signature instrument is the **Coordination Clock** — a radial gauge where every claim is a
  segment around the dial, coloured by lease health (green fresh, amber ageing, red stale),
  conflicts marked on the rim, a slow radar sweep, and the verdict and live claim count at
  the centre — surrounded by the verdict pill, headline counters, and agents/claims/tasks/
  risk panels. The shell is hub-independent (it loads and shows an offline state with no hub,
  then fills in as it polls) and honours `prefers-reduced-motion` (the sweep stills and a
  claims-table fallback appears). Vanilla HTML + the `studio.css` tokens + dependency-free
  ES — no build step, no external request. 100% line+branch.
- Added the Studio snapshot endpoint `/studio.json` (Studio Stage A): `studio_snapshot.py`
  projects the read-only dashboard payload into the command-centre shape — a single risk
  **verdict** (the reserved red/amber/green signal), a row of headline counters, and the
  agents, claims, tasks, conflicts, and risk behind them. It is a pure dict-to-dict reshape
  of the existing `/snapshot.json` read model, so Studio adds no new hub call; every
  headline count is derived from the list it summarises (so the instrument and its rows
  cannot drift apart), and a partial payload from a degraded hub still projects to a
  renderable snapshot. 100% line+branch.

### Changed
- Extracted the hub's idempotency cache, durable-finding quota, and message-id counter
  into `core/hub_ledger_guard.py` (`HubLedgerGuard`): the at-most-once replay guard, the
  per-agent finding quota, and the strictly increasing message id now live in one class
  the hub seeds from a durable-log replay, with `_next_msg_id` / `_remember` /
  `reserve_finding_slot` / `_maybe_replay_duplicate` left as thin delegating wrappers
  (the handler call surface is unchanged) and `_idempotency` / `_message_seq` still
  readable off the hub. No behaviour change; the restart-survival of the at-most-once and
  quota guarantees is identical. Final slice of the bounded hub decomposition, which took
  `core/hub.py` from 1127 to 1009 lines and left it as the connection and message-routing
  coordination core. 100% line+branch on the new module.
- Removed four dead HTTP wrapper methods from the hub (`_http_ok`, `_http_unauthorized`,
  `_request_metrics_token`, `_metrics_authorised`) — superseded by the free functions in
  `core/hub_http.py` and with no remaining callers — and collapsed the redundant
  `_http_endpoint_response` indirection into the `_process_request` websockets hook, which
  now calls `http_endpoint_response` directly. No behaviour change; the `/metrics` and
  `/health` endpoints and their token enforcement are unchanged. Third slice of the bounded
  hub decomposition.
- Extracted the hub's outbound messaging into `core/hub_broadcast.py`
  (`HubBroadcaster`): sending one frame to a socket, fanning a broadcast out to every
  client (mirroring to the relay first), addressing a named agent, and composing a
  presence update now live in one class the hub holds, with `_send_json` / `_broadcast`
  / `_broadcast_presence` / `_send_to_agent` left as thin delegating wrappers (the
  handler call surface is unchanged). It reads the live socket registry and takes the
  hub's system-message factory and online-agents roster as injected callbacks, so it
  carries no back-reference to the hub. No behaviour change. Second slice of the bounded
  hub decomposition. 100% line+branch on the new module.
- Extracted the relay-log mirroring out of the hub into `core/hub_relay.py`
  (`RelayMirror`): the append, lite encoding, and self-trimming that bound the file
  now live in a single-responsibility class the hub holds, leaving `_mirror_to_relay`
  a thin delegating wrapper. No behaviour change — the relay log, its trimming, and the
  no-log no-op are identical. First slice of the bounded hub decomposition. 100%
  line+branch on the new module.

## [0.71.0] - 2026-06-29

### Added
- Added the `synapse sandbox` CLI (experimental) — the operator face of the WebAssembly
  sandbox. `sandbox validate <manifest>` checks a capability manifest and prints its
  normalised, deny-by-default grants; `sandbox run <tool.wasm> --manifest <m> [--input
  <f>] --approve` binds the manifest to the exact module by content digest (a swapped
  module is refused), requires an explicit `--approve` so a capability-bearing run is
  always an operator decision, executes the tool capability-limited, and prints the bounded
  run receipt. Without the `[wasm]` extra it reports the install hint. With this the
  capability-limited WebAssembly sandbox is usable end-to-end; the design doc is updated to
  reflect the shipped sandbox, with the marketplace remaining the gated next step. 100%
  line+branch.
- Added the WebAssembly sandbox runtime (`core/wasm_sandbox.py` + `core/sandbox_receipt.py`)
  behind the optional `[wasm]` extra — a real capability-limited execution sandbox.
  `run_sandboxed` executes an untrusted `.wasm` tool under exactly the manifest's grants:
  a memory cap, a fuel (instruction) budget, a wall-clock epoch backstop, WASI-preopened
  filesystem paths, and no network (WASI preview1 exposes no sockets, so a tool reaches the
  network only through a host import that is never linked). It returns a bounded
  `RunReceipt` — exit status, fuel used, input/output digests, and granted capabilities. A
  fuel bomb traps `out_of_fuel`; a wall-clock runaway is interrupted (`epoch_deadline`). The
  runtime is `wasmtime`, imported only behind the extra so the single-dependency core stays
  import-clean; the manifest→config derivation is pure. 100% line+branch.
- Added the sandbox capability-manifest policy core (`core/sandbox_policy.py`), the first
  slice of the capability-limited WebAssembly sandbox ([design](docs/sandboxed-tools-and-marketplace.md)):
  deny-by-default `FilesystemGrant`/`NetworkGrant`/`ResourceGrant` bundled in a
  `CapabilityManifest` bound to a `.wasm` content digest; `authorise(manifest, request)`
  returns the first failing reason or the granted manifest; `to_acl_rules()` expresses a
  tool's filesystem/network grants as ACL rules so they flow through the same
  deny-by-default `evaluate_access` — one authorisation model, not a parallel one (added
  the `sandbox` permission verb). Pure and I/O-free; the WASM runtime that enforces a
  manifest follows behind the optional `[wasm]` extra. 100% line+branch.
- Added a sustained-write benchmark (`benchmarks/sustained_write_benchmark.py`):
  profiles the durable event store under sustained write load on a real on-disk WAL
  database — write-latency distribution and throughput for the `synchronous=NORMAL`
  commit and the `durable=True` fsync path, the `read_since(0)` replay cost as the log
  grows, and how compaction lowers read cost. Committed results, `make bench` wiring, a
  README section, and focused tests. (KIMI v0.70.0 surfaced this gap — the existing
  harnesses measure coordination/replay, not sustained durable-write latency.)
- Added a two-hub "observe a peer" walkthrough to the
  [multi-hub docs](docs/multi-hub-sync.md): run two hubs with separate event stores,
  coordinate on each, and read the other's observed board and claims with
  `synapse multihub observe` — including how a peer's claim shows as advisory and where
  cross-host (network-transport) observation stops.
- Added `synapse multihub observe` ([docs](docs/multi-hub-sync.md)): the operator-facing
  read of the multi-hub follower. It opens a peer hub's event store, folds its log through
  `MultiHubFollower`, and prints the *observed* board, progress count, and claim view
  (advisory — claims are never granted across hubs), or `--json`. Read-only by
  construction — it reads the peer store through the same `read_since` seam (SQLite WAL
  allows a concurrent reader beside the live peer hub) and exits. Classified `analysis` in
  the surface taxonomy; 100% line+branch. (KIMI v0.70.0 surfaced this as a gap — the
  follower was library-only.)
- Added `synapse federation import/list/revoke` ([docs](docs/federated-trust-model.md)):
  the operator-facing layer over the federation policy bundle. `import` reads an
  out-of-band peer-domain bundle, requires a `--confirmed-by` operator, records the
  provenance (source, time, confirmer), and persists the peering; `list` shows the
  imported peerings with their provenance; `revoke` marks a peering revoked so it fails
  authorisation while keeping its audit record. No auto-discovery and no
  trust-on-first-use — every peering is auditable to a human decision. Serialisation and
  the store live in `core/federation_store.py` (pure; deny-by-default on omissions),
  with a thin CLI shell. Classified `governance` in the surface taxonomy; 100%
  line+branch on both modules.
- Added the federated trust **policy bundle** ([docs](docs/federated-trust-model.md)),
  the first slice of the federated trust model. `core/federation.py` extends the
  single-host trusted-peer notion to trusted peer *domains*: a `FederationPeer` records,
  per remote domain, the local namespaces it may address, the accepted certificate pins
  and event-signing key ids, the bounded local scope (`ScopeGrant`) its subjects map to,
  and an expiry plus revocation. `FederationBundle.authorise` returns a deny-by-default
  decision (unknown domain → revoked → expired → namespace → key → pin, in order), and
  `compose_cross_domain` joins it with the external mutual TLS, signature, and ACL
  results so a frame any layer rejects is rejected. Pure and crypto-free — it composes
  the existing primitives and adds no trust root. 100% line+branch. The federation
  runtime (bundle exchange, remote identity resolution, frame-path wiring) remains
  research.

## [0.70.0] - 2026-06-29

### Added
- Added the A2A bridge [validation receipts](docs/a2a-validation-receipts.md) template:
  the community A2A validation track is now a set of reproducible receipts that survive
  the bridge boundary — discovery, task lifecycle, webhook, proxy/TLS, replay, and
  threat-model — rather than a single pass/fail, separating protocol compatibility from
  operational safety. Adopted from a community contribution by Armorer Labs.
- Added the read-only multi-hub follower ([docs](docs/multi-hub-sync.md)): the third
  CRDT slice. `core/multihub_follower.py`'s `MultiHubFollower` tracks a per-peer `seq`
  cursor, fetches a peer's events past it through an injected transport (`store_fetcher`
  reads a peer `EventStore` over the `read_since` ingest seam — a network transport slots
  in the same way), folds the accumulated union, and returns the observed view. Polling is
  incremental and idempotent. Observe-only by construction: it grants no claim, and on
  losing a peer it simply stops advancing that cursor — the fail-closed posture. With the
  merge and fold slices this completes the read-side CRDT layer; the cross-host mTLS
  transport and the namespace-ownership claim protocol remain research. 100% line+branch.
- Added the multi-hub observed-state fold ([docs](docs/multi-hub-sync.md)): the second
  CRDT slice. `core/multihub_fold.py` folds a merged multi-hub log into the mergeable
  view — the board (last-writer-wins per task), the grow-only progress ledger, and the
  **observed claim** view. The claim view is the safety-critical part: it records the
  latest claim each peer reports, tagged with the authoring hub and marked observed
  (advisory), and **never grants a claim** — a release clears it, and a follower routes a
  real claim request to the namespace's owning hub. Pure and deterministic; 100%
  line+branch. The network follower is the remaining slice.
- Added the multi-hub event-log union ([docs](docs/multi-hub-sync.md)), the first
  CRDT-shaped slice of multi-hub sync: `core/multihub_merge.py` tags each durable
  event with its authoring hub (`HubEvent`), merges several hubs' logs into a grow-only
  set keyed by `(hub_id, seq)` — duplicates collapse, a conflicting reused id keeps the
  first — replays them in the deterministic `(ts, hub_id, seq)` total order, and reports
  the per-hub high-water cursor a follower resumes from. Pure and I/O-free; it folds no
  state and grants no claims (claims are mutual exclusion, never merged). 100%
  line+branch. The state fold and the network follower are the remaining slices.
- Added the Studio design system (A0) and its reference page ([docs](docs/studio.md)):
  the dashboard begins growing from a read-only cockpit into an operator Studio. A new
  dependency-free `dashboard_assets/studio.css` carries the instrument-panel language —
  an ink-navy base, an indigo-violet brand hue, and red/amber/green reserved for
  verdicts — as CSS custom properties plus a component kit (panels, cards, status dots,
  verdict pills, mono data rows, the nav rail, an indigo focus ring; motion stilled
  under `prefers-reduced-motion`). It is served at `/studio.css`, and `/studio` renders
  a self-contained reference page exercising every component with no live data, so it
  works with the hub offline and is the visual reference the live command centre builds
  on. 100% covered; no new dependency.
- Added `synapse adapters list/install/uninstall` ([docs](docs/cross-agent-adapter-kits.md)),
  the cross-agent adapter installer: it detects the coding tools on a machine (Claude
  Code, Codex, Cursor, Aider, Copilot, Windsurf, Gemini CLI) and writes a thin
  claim-aware adapter — "claim before edit, release on commit, reach the hub" — into
  each tool's native config. Two write shapes follow each tool's convention: a
  dedicated file Synapse owns, or a marker-wrapped block appended to a shared file;
  installs are idempotent (re-install replaces, never duplicates) and `uninstall`
  removes exactly what was added, leaving the tool's other config intact. Persona- and
  framework-neutral; adds no new coordination primitive — it only routes existing
  tools to the claims, releases, and presence that already exist. Pure catalogue +
  planning in `adapters.py`, thin I/O shell in `cli_adapters.py`, 100% line+branch.

## [0.69.0] - 2026-06-29

### Fixed
- The hub now damps a **takeover oscillation**: two waiters launched for the same
  identity each take the name back from the other about once per cooldown, an
  eviction war the short cooldown only rate-limited rather than ended. When one name
  is taken over more than `takeover_oscillation_threshold` times within
  `takeover_oscillation_window` seconds, the hub quarantines it — pinning the current
  owner and refusing further takeovers for `takeover_quarantine` seconds, logged once
  as `takeover quarantine … reason=oscillation` instead of a per-second stream. The
  live owner stays connected (messages keep arriving) instead of being evicted ~1 Hz.
  New `SynapseHub` knobs default to 5 takeovers / 30 s → 60 s quarantine; see
  [troubleshooting](docs/troubleshooting.md). 100% line+branch on `hub_clients`.

## [0.68.0] - 2026-06-29

### Added
- Added workflow fan-out / map-join ([docs](docs/workflows.md)): a step with a
  `for_each` list compiles to one parallel task per item (`<workflow>/<step>#<item>`),
  and any dependency on that step expands to a join over every expanded task — a map
  (the parallel tasks) and a join (a downstream step waiting on all of them) out of
  the plain dependency primitive. Fan-out composes with conditional edges (the
  condition carries onto every join edge) and with capability routing; expansion is
  bounded to 64 tasks per step and is a pure authoring-time rewrite, so the board and
  driver see only the expanded graph of ordinary tasks. 100% line+branch covered.
- Added conditional (branching) workflow edges ([docs](docs/workflows.md)): a
  dependency may now be written as `{"step": "test", "on": "done"}` to wait for a
  specific terminal outcome (`done` or `cancelled`) rather than mere completion, so
  a workflow can branch on result (run one step on success, another on failure). The
  condition is enforced by the driver, not the board — the board still sees a plain
  `depends_on` edge; the driver classifies a task whose conditional edge can never be
  met as `skipped` and retires it on the board (cancels it), keeping the graph
  moving. `derive_state` gains a `skipped` bucket and the run loop cancels skipped
  branches. Unconditional edges keep their meaning (any terminal status satisfies).
  100% line+branch covered.
- Added `synapse workflow run` ([docs](docs/workflows.md)), the autonomous live
  loop around the planner: it connects to the hub, posts a compiled workflow's
  tasks once, then on every board reading re-derives the state and routes the ready
  steps by writing each task's `suggested_owner`. Routing is advisory (workers stay
  free to choose), idempotent (a task already advising the chosen agent is not
  re-written), resumable (it routes from the live board, so a restarted driver
  continues), and bounded by both `--max-in-flight` and `--deadline`. The decision
  logic is the pure planner; `run` adds only the connect-post-read-assign shell
  (`core/workflow_run.py`). 100% covered.
- Added the workflow driver's planning core (`core/workflow_driver.py`) and a
  `synapse workflow plan` command: given a compiled workflow and a board snapshot,
  it buckets tasks into done/in-flight/ready/blocked (readiness recomputed from
  dependencies) and plans which ready tasks to hand to which capable agents,
  bounded by `--max-in-flight` and one task per agent per round. A pure,
  deterministic function over the workflow and the board — the autonomous live
  loop wraps it. 100% covered.
- Added a declarative workflow layer (`core/workflow.py`, [docs](docs/workflows.md)):
  a workflow is a plain JSON artifact (a name and steps with `depends_on` edges)
  that compiles to ordinary blackboard tasks, so the board's existing ready/blocked
  derivation executes it — no new runtime, no new dependency. Validation rejects
  duplicate ids, dangling deps, self-dependencies, and cycles before anything is
  posted; compilation namespaces task ids by workflow and emits them in dependency
  order. New `synapse workflow validate` and `synapse workflow compile [--json]`
  offline authoring commands. This is the first slice of the declarative
  orchestration layer; a workflow driver follows.

## [0.67.0] - 2026-06-29

### Added
- Added the [managed GitHub App design](docs/managed-github-app.md) for hosted
  cross-PR file-scope conflict prediction. It pins the boundary: the prediction
  reuses the existing local-core conflict finder, while webhooks, GitHub auth, the
  checks API, and hosting stay out of the local core as a separate managed layer.
  Advisory only, not implemented, and gated on a local adoption signal.
- Added a VS Code / Cursor extension stub (`clients/vscode/`, separate from the
  core Python package): a status bar with hub health and own-claim count,
  claim/release-current-file commands, a board tree view, and overview-ruler marks
  for claimed files. The editor-agnostic logic (`fleetModel.ts`) is Vitest-tested;
  `extension.ts` is the thin editor glue. CI builds, type-checks, and tests it.
- Added a public-surface taxonomy (`surface_taxonomy.py`, [docs](docs/public-surface.md)):
  every CLI subcommand is classified into a stability tier — stable core, adapters,
  read-only analysis, advisory governance, or experimental — and design-preview
  documentation pages are tracked separately. A regression test asserts the
  taxonomy and the live parser agree, so a new subcommand cannot ship unclassified
  and a removed one cannot linger. Makes the daily-safe surface obvious while
  keeping the pre-1.0 honesty.
- Added an operator risk view to the dashboard (`dashboard_risk.py`): the
  `snapshot.json` now carries a `risk` section, and the cockpit shows a Risk panel
  with a red/amber/green verdict, a priority-ordered signal list (stale leases and
  advisory branch conflicts as red, blocked tasks as amber), and a safe-next-work
  queue drawn from the ready set. It is derived strictly from the existing fleet
  snapshot — it invents no new signal — and stays read-only and local-first.
- Added a bounded streaming-response path (`core/streaming.py`) for incremental
  worker replies and long-running progress: an `open`/`chunk`…/`done` (or `abort`)
  frame sequence carried over the existing WebSocket chat path, tagged with one
  stream id. A `StreamBounds` ceiling (chunk count, per-chunk and total bytes,
  TTL) is enforced by both the producer (`StreamProducer`, `agent.stream_reply`)
  and the consumer (`StreamConsumer`), so a runaway stream is refused at the
  source and a malformed or oversized one is rejected on receipt. Streams are
  transient, not durable task state; the retention boundary is documented. See
  [docs/streaming.md](docs/streaming.md).

### Fixed
- `synapse send` (and `syn say`), `synapse accounting record`, and `synapse
  approval request`/`decide` no longer silently drop their message when the sender
  name conflicts with a live identity. The hub accepts the welcome handshake and
  only then closes a name-conflicting socket (close code 4009), so a "ready"
  connection could already be doomed and the message was written into a dying
  socket and lost with no error — which read as "messages between terminals never
  arrive". A shared `connect_failures.closed_after_ready` now detects the
  post-welcome close so every one-shot send and emit reports the conflict with an
  actionable message instead of failing silently. (Operator note: a waiter must
  arm as `<identity>-rx`, never the bare `<identity>`, or an agent's own sends
  conflict with its own presence.)

### Added
- Added the [sandboxed tools and marketplace research](docs/sandboxed-tools-and-marketplace.md)
  design: a capability-limited WebAssembly sandbox (no ambient authority;
  deny-by-default filesystem, network, and resource grants as ACL scopes) as the
  precondition for any tool marketplace, which would gate on signed capability
  cards, a declared permission manifest, and run receipts. Not implemented; no
  untrusted code runs without the sandbox, and no executable marketplace ships
  before all preconditions exist.
- Added the [multi-hub sync (CRDT) research](docs/multi-hub-sync.md) design that
  asks whether several hubs could synchronise state while keeping claim safety and
  local-first. Its honest core: the append-only event log, presence, and progress
  merge conflict-free, but claims are mutual exclusion and not a CRDT — they are
  routed by single-owner-per-namespace and fail closed on a partition. Not
  implemented; adds no cross-hub service to the local core.
- Added a [cross-agent adapter kits](docs/cross-agent-adapter-kits.md) design: a
  planned `synapse adapters` step that detects installed coding tools (Claude
  Code, Codex, Cursor, Aider, Copilot) and writes a thin claim-aware adapter into
  each tool's native config, plus thin client shims for Python frameworks.
  Adapters carry only "claim before edit, release on commit, reach the hub";
  Synapse stays persona-neutral and adds no new coordination primitive. Not
  implemented yet.
- Added a [federated trust model](docs/federated-trust-model.md) design that pins
  how independent operator-managed domains could peer — out-of-band,
  deny-by-default bundle exchange composing identity, signed events, mutual TLS,
  ACLs, and receipts across a domain boundary. It is a design boundary only: not
  implemented, not a certificate authority, and unchanged local-first default.
- Added the [Agent Air Traffic Control architecture](docs/agent-air-traffic-control.md)
  document that names how the shipped parts compose into one control loop —
  separation (claims), merge-risk radar (conflicts), evidence-gated completion
  (receipts, policy-check, approval), post-incident replay (postmortem,
  reliability), and memory (the ingest seam). It is an architecture, not a
  scheduler: only claims gate a mutation, everything else is read-only or advisory.

### Changed
- `synapse event-query` now reads selectively instead of loading the whole event
  store for every query: each query pushes its sequence/time window and required
  event kinds into SQLite (`EventStore.read_window`), so memory is bounded by the
  query window rather than the log size. Results are unchanged — the loaded
  window is always a superset of the events a query keeps. Added `--limit N` to
  cap printed output to the most recent N records and conflict pairs.

## [0.66.0] - 2026-06-29

### Added
- Added signed-event trust bundles and mutual-TLS enforcement for multi-host hub
  deployments: operator trust bundles verify event signatures, certificate pins,
  project scope, replay windows, and signing-key ids, with explicit
  verification-result strings.
- Completed the at-rest encryption runtime to the full local storage profile:
  SQLite event stores and WAL/SHM sidecars, relay logs, A2A state files, archive
  outputs, key-file permission checks, and a migration/rekey flow with backup,
  recovery, and failure-safe startup notes.
- Added the private-channel runtime completion tranche: `synapse channel
  history` returns bounded member-only live history, channel chat is journalled
  and relay-mirrored with explicit channel ids, `synapse relay --channel` /
  `--public-only` / `--channel-metadata` filter projections, and
  `synapse event-query "channel <id> between seq <start> <end>"` returns
  metadata-only channel evidence.
- Added endpoint-side encrypted chat payloads: `synapse send --encrypt-key-file`
  writes an AES-256-GCM payload envelope with route-bound AAD, `synapse listen
  --decrypt-key-file` decrypts locally, and `synapse channel key-check`
  validates payload key files while keeping key discovery and rotation out of
  scope.
- Added opt-in model cost/token accounting. `synapse accounting record` posts a
  `usage`-kind progress note carrying a canonical token/cost body, and `synapse
  accounting report` aggregates those notes from a hub SQLite event store into
  per-agent and per-model totals with optional `--pricing` cost estimates and
  `--budget` evidence. Synapse calls no model provider and collects no telemetry,
  so usage exists only when recorded; budgets are evidence, not an enforcement
  gate. The canonical note format is documented so non-Python clients can record
  the identical body.
- Added human-in-the-loop approval gates. `synapse approval request` puts a
  subject (a held task or policy-gated release) in `awaiting_approval`, `synapse
  approval decide --approve|--reject` records a decision, and `synapse approval
  status` replays the `approval`-kind ledger notes into the current decision
  state per subject (latest event wins, so a re-request re-opens the gate). It is
  advisory evidence and an audit trail, not a hard runtime gate; an approved
  subject can be cited in a release receipt via `synapse release --approval`.
- Rebuilt `synapse dashboard` as a live fleet nerve-center cockpit. The page now
  polls `/snapshot.json` and updates in place instead of reloading on a full-page
  meta refresh: a heads-up vitals bar, a fleet graph that clusters online agents
  by project and colours each by waiter health, board lanes, an active-claims
  panel, a live progress stream, release receipts, and the capability manifest.
  It stays loopback-only and read-only, ships its CSS/JS as package data with no
  runtime dependencies, and keeps a server-rendered `<noscript>` fallback.

### Changed
- Event-signing and mutual-TLS modules import `cryptography` lazily, so the base
  client, hub, and CLI install and import with only the `websockets` runtime
  dependency; signing, mTLS, at-rest, and payload encryption pull the optional
  `encryption` extra only when those features are used.

### Security
- Updated the JS client dev toolchain (vitest 3.x, vite 7.x, esbuild 0.28.x) to
  clear five npm advisories in `clients/js`, including a critical vitest UI
  arbitrary-file read/execute and a high vite `server.fs.deny` bypass on Windows.

## [0.65.0] - 2026-06-29

### Added
- Added an outbound MCP client so a Synapse operator can call tools on an
  external MCP server, the independent counterpart to the inbound `synapse mcp`
  server. `synapse mcp-tools <server> --config <file>` lists and `synapse mcp-call
  <server> <tool> --config <file> --arg k=v` invokes tools named in a
  deny-by-default JSON allowlist — a server or tool that is not allowlisted is
  refused before the server is contacted. Uses the optional `synapse-channel[mcp]`
  extra, imported only when a call is made. Per-agent ACLs over outbound MCP
  remain a later tranche.

## [0.64.0] - 2026-06-28

### Added
- Added an official typed TypeScript/JavaScript WebSocket client in `clients/js`
  (npm `@anulum/synapse-channel`). Unlike the read-only Go client it speaks the
  mutation protocol — chat, claims, releases, board reads, presence, and receipts
  — with typed envelopes, a connect/welcome handshake, keepalive heartbeats, and
  inbound dispatch by message type. It runs unchanged in the browser and in
  Node 20+ with no runtime dependencies, is verified by a dedicated CI job, and
  is a separate package that does not ship inside the Python distribution.

## [0.63.0] - 2026-06-28

### Added
- Added opt-in identity/ACL runtime enforcement. `synapse hub --acl-policy
  <file> --require-acl` maps each mutating frame (chat, claim, release, task
  update, handoff, checkpoint, board, finding) to the structured ACL accesses it
  needs and refuses it with an error before routing when the authenticated
  sender's identity is not allowed by the deny-by-default policy — the same
  evaluation `synapse acl shadow` reports. The identity namespace is the resolved
  `project/agent` sender; authentication remains the per-message-authentication
  layer and this is the authorisation layer. Off by default; ungated verbs, read
  surfaces, a missing policy, and shared-token local hubs are unchanged.
  Identity-bound credentials, rotation/revocation, durable audit-event journaling,
  and read-surface ACLs remain design targets in `docs/identity-and-acl`.

## [0.62.0] - 2026-06-28

### Added
- Added an identity/ACL shadow-mode tranche (observe-only, non-blocking).
  `synapse identity audit --identities <file>` inventories declared agent
  identities and flags rollout blockers (duplicate audit subjects, missing
  credentials, shared seats). `synapse acl shadow --policy <file> --requests
  <file>` evaluates candidate accesses against a deny-by-default ACL with
  structured target patterns (kind plus glob, scoped to a project namespace) and
  records the would-allow/would-deny decision each would receive — with the
  matching rule and reason — without ever blocking a frame. Identity-bound
  credentials and in-hub enforcement remain design targets in
  `docs/identity-and-acl`.

## [0.61.0] - 2026-06-28

### Added
- Added `synapse verify-release`, which runs declared verification commands,
  records observed stdout/stderr digests, artifact hashes, Git state, and writes
  receipt JSON for `synapse release --receipt`.
- Added an advisory policy engine and `synapse policy-check TASK --policy
  <file> --receipt-json <file>`, which evaluates a release receipt against a
  small JSON/TOML policy and prints deterministic pass/warn/fail/not_applicable
  decisions (required tests, strict typing evidence, owner approval, evidence
  freshness, receipt presence, known-failure acknowledgement, generated-artifact
  parity), each with the evidence it used and a next action. Advisory by default;
  `--enforce` exits non-zero only when an enforcement-mode policy has a failing
  rule. Pairs with `verify-release` receipts.

## [0.60.0] - 2026-06-28

### Added
- Added a first tranche of private channels: audience-scoped recipient sets that
  deliver a chat only to a channel's online members instead of broadcasting it.
  `synapse channel create/join/leave/list` manages membership and
  `synapse send --channel <id>` (or `SynapseAgent.chat(..., channel=<id>)`)
  routes to members only — a non-member sender is refused and a non-member never
  receives the body, which is also kept out of the public chat history and relay
  log. Join is open in this tranche (audience scoping, not a security boundary);
  per-channel history, retention, and channel-filtered queries remain design
  targets in `docs/private-channels`.
- Added a foundation tranche of at-rest encryption: an AES-256-GCM envelope with
  scrypt passphrase derivation, owner-only key files, and atomic encrypted-file
  helpers in `synapse_channel.core.at_rest`, plus `synapse encrypt-key
  generate/check` to manage key files. The AES-GCM primitive uses the optional
  `cryptography` dependency (`pip install synapse-channel[encryption]`); the
  package still imports without it. Storage-surface wiring (relay log, A2A state,
  archives) and live SQLite event-store encryption remain design targets in
  `docs/at-rest-encryption`.

## [0.59.0] - 2026-06-28

### Added
- Added opt-in HMAC-SHA256 per-message authentication for selected mutating hub
  frames after WebSocket connect authentication. `synapse hub --message-auth-key
  KEY_ID:SECRET:SENDER[,SENDER...] --require-message-auth` now enforces signed
  claims, releases, task updates, handoffs, checkpoints, and resource offers
  with canonical-frame verification, fail-closed sender binding, timestamp
  windows, bounded in-memory nonce replay detection, and explicit
  verification-result errors.
- `synapse hub --paranoid` now requires per-message authentication enforcement
  in addition to token-protected access and durable event-log replay.

## [0.58.1] - 2026-06-28

### Fixed
- The shell hook no longer collides with a worker-session tmux waker on the
  `<identity>-rx` name. The prompt auto-arm and the interactive provider's own
  tmux waker both tried to own that waiter; the passive one won the name while
  the injecting one was locked out, so a terminal agent (Codex, Kimi K2) never
  auto-woke on a directed message. The prompt auto-arm now yields when a live
  worker-session tmux waker is present, and the provider wrapper releases the
  passive waiter before launching the provider, so the injecting waker owns the
  name. Re-run `synapse install-shell-hook` to pick up the change.

## [0.58.0] - 2026-06-28

### Added
- Generalised the tmux wake transport to any terminal coding agent — Codex,
  Kimi K2, Claude Code — through `synapse agent-tmux {start,wake,status,wait}`
  with `--agent-command`. The pane-activity probe now derives the agent binary
  from the launch command instead of hard-coding Codex, so a non-Codex agent
  running under a shell is detected correctly. `synapse codex-tmux` remains as a
  Codex-defaulted alias (`--codex-command`); `codex_tmux` stays importable as a
  compatibility surface over the new `agent_tmux` module.

### Fixed
- The connection-failure classifier now disambiguates the close codes the hub
  reuses. Code `4010` is emitted for both a takeover (`superseded`) and an
  authentication refusal (`auth denied`/`auth required`), and `4014` for both a
  takeover cooldown and the unauthenticated-socket cap; the classifier keyed on
  the code alone, so a bad token was reported as a takeover. It now reads the
  reason text, and recognises the auth-timeout (`4012`) and per-host-cap (`4015`)
  closes as well.
- The agent wake loop's retry backoff now adds bounded random jitter so a fleet
  of wakers that all lose the hub at once — a hub restart — does not reconnect in
  one synchronised burst.

### Repository hygiene
- The PyPI publish workflow now triggers on the release tag push instead of
  `release: published`. A GitHub Release created by `GITHUB_TOKEN` does not fire
  downstream workflows, so the publish never ran automatically and each release
  had to be pushed to PyPI by hand; the tag push fires it directly.

## [0.57.0] - 2026-06-28

### Fixed
- The Codex tmux wake transport now types the wake prompt and presses Enter as
  two separate `tmux send-keys` calls with a configurable `--submit-delay`
  pause. A single combined call left the prompt unsent in the Codex input
  buffer, so injected wakes were silently dropped until a human pressed Enter.
- `synapse codex-tmux wait` no longer exits on the first failed `synapse wait`.
  It retries with capped exponential backoff and only gives up after
  `--max-wait-failures` consecutive failures (unbounded by default), so a brief
  hub restart or eviction no longer kills the waker permanently.
- Connection failures from the command-line verbs now distinguish a hub that is
  full, in a takeover cooldown, or rejecting a duplicate name from a hub that is
  simply absent. A full hub previously printed the same `Could not reach hub`
  line as an offline one, masking a capacity ceiling as an outage.
- `synapse git-claim`, `synapse lock`, and `synapse release` now name the real
  reason a request got no reply instead of printing `no response from hub` or
  `timed out`. Claiming or locking under a name another live session already
  holds — a common slip when reusing a waiter's identity — now reports the name
  conflict (close 4009), and a full hub reports its capacity (close 4013). They
  also stop waiting as soon as the hub closes the socket rather than polling out
  the full window.

### Changed
- Raised the default hub connection ceiling (`--max-clients`) from 64 to 256 so
  a multi-project fleet, where each terminal holds a command socket and a
  persistent waiter, does not exhaust the table and reject new connections with
  close code 4013.

## [0.56.0] - 2026-06-28

### Added
- Added first-class semantic selector ergonomics to `synapse git-claim`:
  `--module`, `--symbol`, `--api`, `--source`, `--test`, `--generated`, and
  `--migration` resolve locally into ordinary claim paths, while
  `--semantic-evidence-json` writes receipt-ready selector evidence.
- Added dashboard-local bearer authentication for `synapse dashboard`: loopback
  dashboards remain unauthenticated by default, explicit `--dashboard-token`
  protects browser and JSON requests, and non-loopback dashboard binds receive
  a generated startup token when the operator does not provide one.
- Added `synapse hub --paranoid` as a fail-closed local hub profile that
  requires token-protected access, a durable event log, metrics bearer-token auth
  when metrics are enabled, disables relaxed metrics/off-loopback switches, and
  prints the missing hardening hooks it does not implement.
- Added the official read-only Go client for ops and CI tools to fetch dashboard
  JSON snapshots without implementing WebSocket mutation flows.
- Added a committed five-agent coding fleet benchmark that measures local claim
  conflict rate, claim latency, release cleanup, and replay recovery evidence.
- Added branch-conflict candidates to the dashboard fleet view, derived from
  active git-scoped claim metadata without running git from the dashboard.
- Added a read-only dashboard task-dependency graph derived from blackboard task
  edges and exposed through both HTML and `/snapshot.json`.
- Added fleet visibility to `synapse dashboard` and `/snapshot.json`, including
  live agents, `-rx` waiters, missing waiters, active and stale claims, ready
  and blocked tasks, release receipt notes, and optional persisted A2A task
  counts via `--a2a-state-file`.
- Added the public agent trust graph design for evidence-linked routing review
  over reliability signals, release receipts, capability observations, handoff
  outcomes, conflict history, provenance references, decay windows, policy
  inputs, and explicit non-scoring boundaries before any graph runtime ships.
- Added the public differential-privacy blackboard design for redacted and noisy
  multi-organisation board projections, privacy budgets, cohort thresholds,
  privacy-ledger audit evidence, and explicit raw-log/encryption/authorization
  boundaries before any privacy runtime ships.
- Added the public signed capability cards design for tamper-evident capability
  advertisements, manifest/card digests, verification results, replay controls,
  credential rotation, revocation, trust bundles, and advisory-discovery
  boundaries before any signing runtime ships.

## [0.55.0] - 2026-06-28

### Added
- Added `synapse hub` blackboard retention controls:
  `--max-progress`, `--max-progress-per-author`, `--max-progress-per-task`, and
  `--max-findings-per-agent`. The hub applies the same bounds during live
  operation and durable replay.
- Added a commercial licence evaluation path and checker coverage so public docs
  keep the AGPL/commercial boundary, self-service plans, and custom-contact
  requirements aligned.
- Added prototype Datalog-like and Cypher-like aliases for `synapse event-query`
  while preserving the existing read-only event-log execution model.
- Added the public policy-engine design, covering advisory local release rules
  for required tests, type checks, owner approval, evidence freshness, generated
  artifact parity, and no-merge-without-receipt.
- Added the public paranoid-mode design for one future operator switch that
  enables strict local settings and reports missing hardening hooks without
  claiming encryption, identity, ACL, or exposed-deployment guarantees.
- Added the public at-rest encryption design for optional local storage
  encryption scope, key lifecycle, rotation, backup recovery, and local-first
  tradeoffs before any encryption flag ships.
- Added the public end-to-end encrypted channels design for selected encrypted
  payloads, per-project/per-worktree keys, recipient sets, key rotation, member
  removal, and hub-visible metadata boundaries.
- Added the public private-channels design for project, worktree, task, and
  direct channel namespaces, membership lifecycle, history visibility, retention
  boundaries, relay filtering, and event-query filtering.
- Added the public signed-events and mTLS design for selected event signatures,
  key rotation, replay protection, verification results, trust bundles,
  certificate pinning, and trusted multi-host peer boundaries.
- Added the public per-message authentication design for authenticated frames,
  canonical frames, sender binding, replay cache bounds, key rotation,
  revocation, and verification-result boundaries after WebSocket connect
  authentication.
- Added the public identity and ACL design for per-agent identity,
  identity-bound credentials, project namespace permissions, allowed verbs,
  target patterns, metrics/A2A/dashboard/release privileges, deny-by-default
  authorization, credential rotation, revocation, and shared-token migration.

## [0.54.0] - 2026-06-28

### Added
- Added `synapse dashboard` for a loopback-only read-only HTML/JSON view of the
  live roster, claims, board tasks, progress notes, and capability cards.
- Added native hub `wss://` support with `--tls-certfile` and `--tls-keyfile`
  while preserving token requirements for off-loopback binds.
- Added declarative capability contracts on capability cards, preserving
  per-task-class input/output schemas and optional pre/postconditions in the
  manifest, A2A metadata, CLI counts, and dashboard snapshots.
- Added a read-only capability directory that joins capability cards and
  resource offers for discovery-only CLI and MCP surfaces.
- Added advisory semantic task routing for board tasks via `synapse route-task`
  and `synapse_route_task`, using deterministic local capability-card signals
  without claiming work or assigning owners.
- Added optional observed capability evidence for routing from positive
  release-receipt assessment notes in a local event store, preserving source
  task and sequence provenance without grading agents.
- Added `synapse memory-recall` and MCP `synapse_memory_recall` for
  deterministic local recall over durable findings, checkpoints, and handoffs
  with matched-token and source-sequence provenance.
- Added `synapse resource-bids` and MCP `synapse_resource_bids` for
  deterministic read-only ranking of live resource offers against board tasks
  without reserving capacity or authorizing execution.
- Added read-only MCP resource templates for single task, single agent, and
  resource-kind views while keeping the hub protocol unchanged.

## [0.53.0] - 2026-06-27

### Changed
- Added explicit scalability benchmark indexing-decision metadata and refreshed
  the committed scan evidence for when to keep or revisit the linear
  scope-conflict scan.
- Added `synapse ttl-advice` for read-only adaptive lease TTL advice from
  durable event-log samples while preserving explicit manual TTL control.
- Added `synapse reliability` for evidence-only reliability memory over the
  durable event store, tracking stale claims, declared failed-check evidence,
  broken handoff candidates, and conflict pairs without producing scores.
- Added `synapse postmortem` for replayable task postmortems from the durable
  event store, including timeline, owners, releases, evidence notes,
  reconstructed conflicts, and candidate unanswered messages.
- Added a public integration demo matrix with bounded CLI, MCP, and local A2A
  walkthroughs that list supported behavior and keep external validation open.
- Refreshed the public comparison page with concrete, locally verifiable
  differences for file-scope claims, Git hooks, durability, metrics, MCP, A2A,
  receipts, and local-first operation.
- Added `synapse doctor --redeploy-checklist` so post-release local fleet
  restarts have copyable package, service, roster, durable-state, and git-hook
  verification steps without the diagnostic command mutating services.
- Added `tools/audit_dependency_tooling.py --check` to keep the local preflight,
  action pinning, Dependabot ecosystems, and PyPI publish/download metadata
  surfaces from drifting silently.

## [0.52.0] - 2026-06-27

### Added
- Added historical-cadence stall detection to the LLM-free supervisor, with
  operator controls for disabling or tuning the predictive supplement.
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
