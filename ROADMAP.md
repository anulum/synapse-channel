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

This roadmap describes direction, not promises. The authoritative record of
released changes is [`CHANGELOG.md`](CHANGELOG.md); the
[API and wire stability contract](docs/api-stability.md) defines which current
surfaces integrators may rely on.

## How to read this roadmap

SYNAPSE CHANNEL is still in its pre-1.0 line. Features below are labelled by
their real maturity:

- **Shipped** means the implementation, CLI or adapter, documentation, and
  repository tests exist in the current open-source package tree.
- **Experimental** means a working shipped surface is intentionally outside the
  stable pre-1.0 contract.
- **Active priority** means hardening or validation work, not a delivery date.
- **Research candidate** means no commitment to ship; it must earn its
  complexity and evidence.

## Ecosystem direction: a governed AI development loop

SYNAPSE CHANNEL is one component of a broader local-first development system.
The long-term direction connects six independently bounded products into a
continuous evidence loop:

```mermaid
flowchart LR
    O["Human owner<br/>intent, policy, and release authority"]
    D["DIRECTOR-AI<br/>response grounding"]
    C["DIRECTOR-CLASS-AI<br/>action policy"]
    S["SYNAPSE CHANNEL<br/>claims, mailbox, plan, and receipts"]
    F["SYNAPSE CHANNEL FLEET<br/>multi-host coordination operation"]
    W["Candidate change<br/>tests and evidence"]
    R["RIGOR-FOUNDRY<br/>verification and admission"]
    L["Accepted change<br/>with provenance"]
    M["REMANENTIA<br/>bounded verified memory"]

    O --> D
    O --> R
    D <--> C
    D --> S
    S <--> F
    F --> W
    W --> R
    R -->|accept| L
    R -->|reject with findings| D
    L --> M
    M --> D
    M --> S
```

The intended responsibilities are deliberately separate:

- Configured owner/agent orchestration supplies work decomposition and routing.
  **DIRECTOR-AI** checks response grounding;
  **DIRECTOR-CLASS-AI** supplies action-policy control.
- **SYNAPSE CHANNEL** coordinates identities, claims, tasks, handoffs, and
  receipts; **SYNAPSE CHANNEL FLEET** extends that coordination across seats,
  hosts, and model providers.
- **RIGOR-FOUNDRY** admits or rejects candidate changes against explicit,
  reproducible gates. A model's confidence is not admission evidence.
- **REMANENTIA** preserves bounded, attributable context with separate verification
  requirements so later
  cycles can learn without treating every generated output as truth.
- The **human owner** retains policy, release, and irreversible authority.

The target is **self-evolving, never self-authorising**: agents may propose,
implement, challenge, and improve the system, while independent roles and
fail-closed evidence gates decide what becomes durable state. "Slop-free" is an
engineering objective measured through provenance, review separation,
reproducible tests, and explicit admission criteria—not an absolute claim that
automation cannot fail.

This diagram states an ecosystem direction, not a shipped cross-project
contract. Each product's own roadmap remains authoritative for its current
interfaces, maturity, and release commitments.

## Shipped foundation

### Coordination, durability, and recovery

- File- and worktree-scoped **work claims** with overlap detection, expiring
  leases, epochs, optimistic-concurrency versions, staged-claim enforcement,
  Git hooks, and provider edit guards. See
  [Git-native claims](docs/git-claims.md).
- Contract conflicts between branches that share no file: each dependent of a
  declared contract records the spec digest it was built from, and
  `synapse contracts check` reports a stale dependent or a spec change whose
  dependents were not regenerated. The check is advisory and claims or blocks
  nothing. See [contract conflicts](docs/contract-conflicts.md).
- A typed task lifecycle and dependency-aware blackboard, append-only progress,
  atomic handoff, stalled-task supervision, resumable checkpoints, deadlock
  detection, and release receipts. A progress note's text is bounded at 1 MiB
  of UTF-8 (`MAX_PROGRESS_NOTE_BYTES`), the same for the hub and every writer.
- A SQLite WAL event journal with admitted retained replay, supported idempotency keys,
  reconnect cursors, bounded histories and buffers, archive/compaction tools,
  and optional SQLCipher protection for the live store. See
  [at-rest encryption](docs/at-rest-encryption.md).
- Identity-scoped mailbox, waiter, tmux, shell-hook, and worker-session
  ergonomics for keeping provider processes reachable without making presence
  itself a wake loop. A mailbox's pending count is stored beside the journal
  and kept exact with each chat, acknowledgement and deletion, so `who` never
  reads the chat history to answer. Antigravity CLI (`agy`) is a provider
  command like the others: the shell hook wraps it, its tmux pane takes
  directed wakes, and `synapse participant ask agy` drives it headlessly.
  `synapse wait --no-takeover` registers only a free name and never displaces
  a live holder, and a new tmux server keeps only a fixed set of global
  variables, so one seat's environment does not reach the others.
- Seats that survive a crash or a reboot: `synapse seat-hold` runs an agent
  only while its seat's dedicated name is held, and never displaces a live
  holder. A per-host session ledger (`synapse ledger show` and `snapshot`)
  records each agent session on a Linux host by boot, process and start time,
  and `synapse ledger restore` starts again the seats a crash or a reboot cut,
  behind the holder and from an empty environment, one at a time within the
  host's headroom. A restored seat's token file ends with its holder, and the
  next restore sweeps the file a killed holder left. See the
  [CLI reference](docs/cli.md).
- The event log records its store format: a release refuses a log written by a
  newer format before it reads or creates any table, and `synapse hub` exits 78,
  which the hub units do not restart on. Offline readers, `synapse event-query`,
  the dashboard's feeds and the analysis reports open the log read-only through
  `EventLogReader`, so they never write the live journal or block the hub's
  writer. See [deployment](docs/deployment.md).
- A new mailbox waiter starts at the hub's current sequence instead of
  replaying old personal traffic; its first arm says how many earlier messages
  are addressed to it, and `--since 0` replays them. See the
  [CLI reference](docs/cli.md).
- A stop finishes, and an attempt reconciles, whichever order the attempt's end
  and its marker-bearing verification arrive in: a kept `applied` stop read-back
  ends its attempt, a verification that names an attempt's marker binds to that
  attempt alone, and seals, closing read-backs and independent reconciliation
  draw on reserved capacity an executor's 32-row limit cannot exhaust. See the
  [protocol](docs/protocol.md).
- The app task queue and the spend ledger migrate an earlier layout only by a
  declared migration that `synapse verify-result migration` confirms against a
  published schema digest, and a changed layout is refused unchanged. See
  [app tasks](docs/app-tasks.md).
- A chat is never an approval, a verdict or a declaration, and no operation can
  declare a physical or equipment effect. See the [security policy](SECURITY.md).
- A connected seat releases its own claims through a `<seat>-release` sidecar
  connection. The hub judges it as the seat; it never takes over the seat's
  socket or creates an identity pin, and on a shared-token or open hub it may
  release only a claim of the commit or merge trigger it presents. See the
  [security policy](SECURITY.md).
- An opt-in approval-load policy (`synapse hub --approval-load-policy`) limits
  how fast approval requests reach an approver and flags rubber-stamping for
  audit. See the [security policy](SECURITY.md).
- Opposing `applied` and `sealed` marker records on one attempt surface as a
  contradiction in either arrival order. See the [protocol](docs/protocol.md).
- The dashboard's Fleet mirror reads the advisory export the Fleet sync daemon
  writes, including its matching `writer_generation` record; any other extra
  field is still refused. See the [studio guide](docs/studio.md).
- Mail that no session will collect can be found, handed to a successor or
  closed (`synapse mailbox orphans`, `handover`, `close` and `drain`, and
  `synapse doctor --mailbox-orphans`), and mail addressed to a role waits in
  the role's own mailbox. See the [CLI reference](docs/cli.md).
- Work state that a receiver can check, never authority: `synapse work-state
  build` pins input files by SHA-256 and repositories by commit. Uncertain
  wakes are brought to an operational end with `synapse waker uncertain`. See
  the [coordination model](docs/coordination-model.md) and
  [wake relay](docs/wake-relay.md).
- Recovery evidence: `synapse demo --recovery` runs one reproducible recovery
  story on a real local hub, and the [delivery matrix](docs/delivery-matrix.md)
  lists every request type a hub accepts with its delivery and retry key,
  backed by a whole-hub `kill -9` test.

### Agent, protocol, and editor integrations

- An optional **MCP server** over stdio or
  [authenticated private HTTPS](docs/mcp-http.md) and a fail-closed outbound MCP client
  path, while the hub and core install remain MCP-agnostic. See the
  [MCP guide](docs/mcp.md). A remote client that keeps only one static secret
  reaches the HTTPS listener through `synapse mcp-gate`, which checks a
  stable bearer on every request and forwards it with the seat's current
  issuer token to the pinned listener; each gated seat lists the tools it may
  hold. See [MCP bearer gate](docs/mcp-gate.md) and the
  [remote Grok connector](docs/grok-remote.md).
- History read queries answered only to the identity they name, an inbox page
  and an owned-work listing on the MCP stdio bridge, and remote MCP mutations
  that carry their operation identifier as a tool argument, with lease fences
  that survive the adapter's restarts. See the [MCP guide](docs/mcp.md) and
  the [protocol](docs/protocol.md).
- An **A2A Agent Card and HTTP+JSON bridge** with a published local support
  matrix and reproducible validation receipts. This is partial validation, not
  external A2A certification. See
  [A2A conformance](docs/a2a-conformance.md).
- An `interop` workflow that drives the official MCP and A2A client libraries
  against real `synapse hub`, `synapse mcp` and `synapse a2a-serve` processes
  and judges each journey by the hub's own journal. See the
  [official-client interop lane](docs/interop.md). The
  [protocol conformance tests](docs/protocol-conformance-tests.md) page names
  the test behind each protocol version and feature it lists.
- An [integrator contract](docs/integrator-contract.md) that names one command
  or call for each of the six steps an integrator needs: register an executor,
  propose an operation, obtain authority, execute, follow the result, and
  recover after an unknown outcome.
- Provider-specific adapters and guarded mutation paths. The covered native
  tools of each provider are listed in
  [provider mutation claim hooks](docs/claim-guard-hooks.md); configuration
  adapters are not universal write interception.
- A pinned **OpenCode** integration plus ACP-driven real-client paths for Zed,
  JetBrains, Neovim, and Emacs. See the
  [OpenCode integration guide](docs/opencode.md).
- Official Python and JavaScript clients, a read-only Go HTTP client, Git and
  shell integrations, and editor/IDE packaging surfaces.

### Governance, evidence, and operator visibility

- Human-in-the-loop approval request/decision/status flows, policy evaluation,
  access-control and role surfaces, universal receipts, release verification,
  Merkle evidence, postmortem, reliability, accounting, and causality tools.
- **Operation records** bound to a claimed task: the live claim holder
  declares an operation and its effect (a file, a Git publication, a
  deployment or a migration) before acting, and its outcome report and
  verification records follow, each one journal event
  (`synapse operation declare`, `outcome` and `verification`).
  `synapse verify-result` checks the requested result
  itself: a file's exact SHA-256 and the change's tests, a commit on a remote
  branch, a deployment's health report or its generation chain and service
  start record, or a migration. `synapse generation` keeps a hard-linked
  generation chain for conditional deployments. See
  [execution outcomes](docs/execution-outcomes.md).
- Catalogue operations run end to end with `synapse ops run`, along one
  checked path: the exact target and executable fingerprint, the qualification
  gate of the named profile, the operator's grants read again just before the
  run, then an outcome record and an optional result verification. With
  `--hub` (opt-in) a run also goes through the hub's governed operations: a
  declaration with an intent built from the plan, an approval by another
  identity, one attempt, and the published outcome. See
  [operations](docs/operations.md).
- One operation's whole chain without the hub: `synapse operation report`
  prints the declaration with its intent digest checked, the approval notes,
  the hub's answers, the executor's reports, the verifications and the seals.
  An executor's report is never shown as a success without a verification.
  See the [CLI reference](docs/cli.md).
- Attempts and seals for declared operations: `synapse operation declare
  --intent` fixes an authorisation intent before the effect,
  `synapse operation attempt` asks the hub to open an attempt for that exact
  intent, `synapse operation seal` ends an attempt whose executor fell silent,
  and `synapse verify-result git` reads an attempt's marker. See
  [execution outcomes](docs/execution-outcomes.md).
- Version 0.1 of the effect-safety suite: `synapse effect-suite run` runs
  cases 1 to 4 in twenty-one variants against this package's own stack. See
  [effect suite](docs/effect-suite.md).
- Evidence that leaves the hub: `synapse export` writes a hub store's journal
  and AEF receipts as plain JSON Lines with a manifest. Signed tree heads
  (`synapse aef sth`) tell a complete export from a cut-off or rolled-back
  one, `synapse aef verify` checks chain continuity, and an optional hybrid
  countersignature (Ed25519 plus ML-DSA-65) covers a tree head. See
  [export](docs/export.md) and
  [verifying AEF evidence offline](docs/aef-verification.md).
- Signed, versioned policies for `policy-check` (`synapse policy sign` and
  `verify`), verdicts that turn stale when an input they read is corrected,
  compliance export, retention and report over one read-only journal
  snapshot, and a `reputation` ranking of recorded evidence for human triage.
  See the [policy engine](docs/policy-engine.md), [verdicts](docs/verdicts.md),
  [compliance](docs/compliance.md) and [reputation](docs/reputation.md).
- The running artefact names itself: `synapse --version` and `synapse doctor`
  print the source commit, the wheel digest and the declared profile. See
  [build identity](docs/build-identity.md). A generated
  [guarantee register](docs/guarantee-register.md) shows, for each
  capability, the last stage it has reached, checked against the tree.
- The cockpit as the dashboard's front door, built into the wheel.
  `/capabilities.json` lists each optional surface and whether this start
  serves it, `/findings.json` serves the findings agents recorded with the
  hub, and a `scope` narrows the fleet to one project's part. See
  [cockpit](docs/cockpit.md).
- Governed operations in the cockpit: an operations tab over
  `/operations.json` shows each operation's chain link by link, and an
  approval view checks the recorded intent digest against the one the
  dashboard recomputes. With `--operator`, a login that holds `task_update`
  records an approval note there (`POST /approval/decide`); the hub judges it
  when an attempt is asked. The attention queue names refused operations and
  silent attempts. See [cockpit](docs/cockpit.md).
- Scopes beyond one project: `synapse dashboard --scope-groups FILE` declares
  groups of namespaces (`scope=group:<name>`), the five event-backed feeds and
  the live stream take the scope, and `--observed-peer HUB=URI` serves an
  observed hub as an advisory scope of its own (`scope=peer:<HUB>`) with
  writes off. See [cockpit](docs/cockpit.md) and the
  [CLI reference](docs/cli.md).
- Setup from the dashboard, opt-in (`--setup-apply` with `--operator`, on
  loopback, for an access file's admins): `GET /setup/preflight.json` gives
  each requirement of the command line's `local-single-user` profile, and
  `POST /setup/plan` issues a plan bound to the command line's own plan, with
  a receipt for every answer. `POST /setup/apply` applies an issued plan once
  through the command line's executor after the host is inspected again,
  `GET /setup/receipts.json` reads the receipt chain, and the cockpit's setup
  assistant offers an Apply step only after an explicit confirmation.
  `synapse setup apply` names a directory it refuses, with its mode and one
  safe fix. See the [CLI reference](docs/cli.md).
- Every dashboard write needs exactly one `Origin`, the dashboard's own
  (`http://<Host>`); a missing, repeated or foreign `Origin` is answered 403
  and relays nothing. See [cockpit](docs/cockpit.md).
- A dashboard and Studio projection for live claims, tasks, conflicts, security
  posture, receipts, governed operator actions, and observed peer state. See
  [Studio](docs/studio.md).
- Event-log ingestion and recall as the memory seam, rather than a second
  authoritative memory database.
- OpenTelemetry projection of causality evidence through an optional exporter;
  telemetry remains outside the core dependency set. With `--correlate`,
  `synapse causality otel` places recorded model usage, claim denials and
  operation records into the task traces. See
  [observability](docs/observability.md).
- Capability cards, signed advisory cards, directories, manifests, and
  evidence-preserving local routing. Cards and routing recommendations do not
  grant authority. Provider routing ranks declared local candidates first and
  uses a hosted priced candidate only with an explicit `metered_ref`, and
  `ollama` and `tiered` workers reach only the operator's declared local
  endpoints.
- Stopping governed operations: `synapse operation stop` stops a scope and,
  with `--seal`, seals each attempt it listed and records what it read back,
  also the attempts a standing stop still lists;
  every stop reports its state and the profile it claims, and a resume needs
  `operation-resume`. Autonomy levels per scope: `synapse operation level`
  sets a scope's level, a judged and recounted record raises it one step, and
  with `--ladder` a missed deadline, an overlong stop or an incorrect
  acceptance lowers it in the same transaction. A component's own state is a
  governed effect: Core applies a read row's projection, `synapse ops run
  --hub` changes the state after a second read, and `synapse verify-result
  component` verifies it. The cockpit offers the Fleet mirror as an advisory,
  read-only scope to its observers. See [operations](docs/operations.md).

### Security and multi-host primitives

- Shared-secret connect authentication, exposure guards, bounded rate policy,
  private runtime directories, owner-only secret loading, dashboard Host
  defence, webhook egress pinning, and shared-host attack regressions.
- Rotating credentials: `synapse hub --token-set` admits connections from an
  owner-only set of identified tokens with validity windows, and can require
  expiry, cap a token's lifetime and close a session when its token ends;
  with a journal it records audit rows (`synapse token-set new`, `retire`,
  `list` and `status`). `/metrics` and `/health` take a token set of the same
  format, per-message HMAC keys come from a key set the hub reads again, and
  outbound peer tokens rotate without a restart. See
  [secure mode](docs/secure-mode.md) and
  [per-message authentication](docs/per-message-authentication.md).
- Machine-key trust-on-first-use identity pins, operator identity bundles,
  optional ACL and role enforcement, per-message authentication, signed-event
  verification primitives, mTLS contexts, and certificate-pin trust bundles.
  The packaged profile and managed key lifecycle remain narrower than the
  underlying primitives. See [identity and ACL](docs/identity-and-acl.md) and
  [signed events and mTLS](docs/signed-events-mtls.md).
- A hub can require every frame after registration to be signed by the key
  that proved the connection (`--require-signed-frames`; the Python and
  JavaScript clients sign when it is required) and refuse a name that proves
  no key (`--require-identity-proof`). Signing connections are closed once
  their key stops being trusted, and the hub journals each change in the
  roles a connection binds. See
  [per-message authentication](docs/per-message-authentication.md).
- Multi-hub observation, claim forwarding, protocol-version negotiation,
  federation bundle offer/fetch/import/rotate/revoke flows, and guarded relay
  primitives. Federation does not turn observed peer state into local
  authority. See [multi-hub sync](docs/multi-hub-sync.md) and the
  [federated trust model](docs/federated-trust-model.md). Out-of-tree
  federation consumers import from one package, `synapse_channel.federation_api`
  (see [API stability](docs/api-stability.md)). It also carries the read-only
  event-log reader types, the per-task operation record projector, the Fleet
  mirror contract and owner-only path validation, and a multi-hub event pull
  carries the peer's journal instance.
- An owning hub serves a relayed release, a forwarded claim or a dead-letter
  pointer only for a peer authorised for the request's namespace, with a scope
  grant and uncontested ownership, and binds the claim it frees or takes to
  that namespace. An edge hub forwards an operator relay only for a sender
  granted `operator-relay`, in its own name. An enforced message ACL authorises
  every part of a chat target, and a secured hub fans frames out only to
  sockets that authenticated. See [identity and ACL](docs/identity-and-acl.md).
- A multi-hub follower stops on a peer whose journal instance changes under its
  cursor and quarantines it until `recover_peer`. An owning hub keeps a forwarded
  claim while the forwarding peer hub is connected, and a repeat through a peer
  hub renews the lease. The multi-hub log serves only the public view of a claim,
  and a claim whose lease lapsed on its own hub stops asserting its namespace.
  See [multi-hub sync](docs/multi-hub-sync.md).
- An inclusion proof must carry 32-byte hashes and exact integer positions, in
  Python and in the cockpit. The multi-hub log withholds every event kind no
  follower consumes, and outbound MCP tools can be pinned by descriptor. The
  Claude Code claim hook refuses a tool call when the hook itself fails.

## Shipped experimental surfaces

These surfaces work today but remain explicitly experimental until their
contracts and external evidence mature:

- A capability-limited WebAssembly tool sandbox with deny-by-default grants,
  bounded resources, approval support, and run receipts. It is not a general
  host or container isolation claim. See the
  [sandbox getting-started guide](docs/wasm-sandbox-getting-started.md).
- Declarative workflows compiled to the existing blackboard DAG, participant
  deliberation, semantic routing, adaptive TTL advice, automated-action advice,
  resource bidding, and other lab-tier analysis/governance helpers.
- Advanced federation, trust, encryption, and hardware-backed key adapters
  whose stable operator profiles are still being consolidated.
- A forward event feed for memory consumers (`synapse feed read`, `ack` and
  `bind`, behind the `feed-read` grant): findings and marked undirected chat
  after a cursor bound to the hub and its journal instance, with the consumers'
  acknowledgements holding compaction until the operator releases them. See
  [the forward event feed](docs/protocol.md#the-forward-event-feed).
- Linux protected-execution primitives: one trusted writer API over live
  kernel checks and signed authority status, root-owned installation bytes
  retained in sealed memory files, cgroup ancestry and process-subtree
  observations, and a protected frontend control channel over native Unix
  channels. They expose observations and refuse on mismatch; they install no
  writer, supervisor or service and grant no recovery authority. See the
  [coordination specification](docs/coordination-spec.md#durable-protected-execution-evidence).

## On `main`, not yet released

Changes that reach the `main` branch after a release are listed here, in no
release yet, until a release carries them. The "Unreleased" section of the
[changelog](CHANGELOG.md) records them. At 0.99.67 nothing on `main` is
outside a release.

## Active pre-1.0 priorities

1. **Delivery integrity and secure outcomes.** Eliminate stale or misdirected
   wake traffic, finish outcome-oriented secure profiles, keep exposed hubs
   bounded by default, and extend replay-resistant durable trust state.
2. **A smaller golden path.** Make install → connect → claim → conflict refusal
   → handoff → verified receipt understandable without navigating the full
   advanced CLI surface.
3. **Protocol and platform evidence.** Freeze the 1.0 wire/API compatibility
   contract, exercise version-negotiation fixtures, and keep real Linux, macOS,
   and Windows filesystem semantics in CI.
4. **Operational recovery.** Exercise backup, restore, migration, corruption
   recovery, long-running soak, and measured capacity limits rather than
   inferring readiness from unit tests.
5. **External validation.** Publish reproducible successes and failures from
   independent fleets and security review. The project does not claim external
   certification, a completed pentest, or universal production suitability
   before that evidence exists.
6. **Stable boundaries.** Keep coordination core, integrations, security
   profiles, and labs visibly separated without replacing the blackboard with
   a second orchestration runtime.

The [0.x to 1.0 migration guide](docs/migration-1.0.md) records the concrete
freeze and release-cut checks.

## Research candidates

Each candidate remains optional and must arrive with a threat model, bounded
dependencies, real boundary tests, and a reversible adoption path:

- An **agent change transaction** joining a declared intent, scoped authority,
  an approval bound to that exact intent, a single application of the approval,
  and independently verifiable receipts. The intent is fixed before approval
  and checked again at the moment of use. A change whose result has a content
  address, such as a Git commit, is prepared first, approved by that address,
  and then published in one step.
- **Consistency proofs between signed tree heads**, so that two holders of
  exported evidence can detect a forked or rolled-back log offline, without the
  receipts.
- An **executor profile** that states how an executor is isolated, with a
  per-run record a verifier can check, so that an isolation level is evidence
  and not a label.
- A **runnable effect-safety test set** for agent executors: a denied operation
  leaves no effect, a changed operation is refused, a repeated delivery has one
  effect, and an unknown outcome stays unknown.
- Semantic claims beyond literal paths and Python symbols: exported APIs,
  OpenAPI operations, protobuf messages, database objects and migrations,
  Terraform resources, manifests, and CI/public contracts.
- Pre-execution read/write-set simulation and conflict-aware scheduling.
- Causal replay and counterfactual task-order analysis without exposing
  secrets.
- Per-operation, non-transferable capability tokens instead of shared mutation
  credentials.
- Managed workload identity such as OIDC or SPIFFE after demonstrated
  deployment demand.
- Broader managed end-to-end encrypted-channel profiles after identity, key
  lifecycle, recovery and metadata-leakage qualification. The existing opt-in
  chat-payload encryption remains its narrower shipped surface.

## Explicit limits and non-goals

To keep the bus inspectable and local-first, SYNAPSE CHANNEL does not aim to
be:

- an internal consensus protocol, distributed lock manager, or CRDT system;
- a standalone graph database or a second authoritative task/memory store;
- an opaque hosted control plane required for the open-source core to work;
- an autonomous authority that grants permissions from capability cards,
  routing scores, trust rankings, model output, or dashboard state;
- a claim that agents are contained merely because they coordinate through the
  bus; or
- a substitute for operating-system isolation, code review, CI, independent
  security assessment, or operator judgement.
