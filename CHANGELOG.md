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
  0.2.64); the *stream schema* is not, because the Grok CLI is heavy and unreliable on this
  machine and its output was not captured at source. The parser therefore targets the assumed
  Claude-Code-family streaming-json convention (it delegates to the Claude parser) and is
  flagged as such by `GROK_SCHEMA_VERIFIED = False`; the real smoke is triple-gated and stays
  skipped until the schema can be verified against a usable Grok. 100% line+branch on both new
  modules under that assumption.
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
