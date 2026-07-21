# Coordination model

The hub composes a handful of independent mechanisms into one coordination plane.

## 1. Plan

Any agent declares work on the shared blackboard. A declared task has an id, a
title, a description, and optional dependencies. The hub refuses dependency
cycles, so the set of *ready* tasks (open, with every dependency finished) is
always well-defined.

The blackboard keeps recent progress bounded by total note count, author, and
task id. These caps are live hub settings and are also applied when replaying a
durable event log, so a restart does not resurrect an unbounded in-memory board
view.

The planned
[differential-privacy blackboard design](differential-privacy-blackboard.md)
defines future redacted and noisy projections for multi-organisation board
views. It is not implemented yet; the current local board remains exact for the
operator.

A declared task is the **plan**; a claim is the **lease** on doing it. The two
share a task id but stay independent, so the simple claim flow keeps working with
no plan entry at all.

## 2. Claim

An agent leases a task by id. The claim may declare a **file scope** — a
`worktree` and a set of `paths`. The hub refuses a claim whose file scope
overlaps another agent's live claim; agents in different worktrees never contend.

Git-aware clients retain those fields as human-readable displays and add a
versioned `path_identity` derived locally from the canonical worktree, Git-index
spelling, resolved filesystem path, actual case policy, Unicode NFC, and an
existing object's device/object key. The hub validates and compares the supplied
identity without accessing Git or the filesystem. This makes symlink, hard-link,
junction, case, Unicode-equivalent, and Windows short-name aliases contend while
preserving distinct case-sensitive paths on filesystems such as Linux/ext4.
Malformed or display-misaligned identities are refused before state mutation.
Legacy claims remain readable; an identity-aware peer applies its known case
policy to a legacy display, while two legacy peers keep literal comparison.

Every lease:

- **expires**, so a crashed agent never holds a claim forever;
- carries an **epoch**, so a paused or superseded agent cannot act on a dead
  claim;
- carries a **version** for optimistic concurrency, so a stale update is refused.

## 3. Work

The owner moves the task through a typed lifecycle
(`claimed → working → input_required → done`/`failed`); the hub rejects an
illegal transition. The owner can save a **checkpoint** — an opaque resume token
that survives lease expiry.

When the owner manually releases a claim as the closeout record, the release can
carry a receipt with evidence, artifacts, changed files, generated artifacts,
approvals, known failures, confidence, and evidence freshness. The hub echoes the
receipt and records it as a board assessment note when evidence is present. The
receipt's `epistemic_status` is advisory metadata derived from those fields, not
proof that the release is safe to merge: fresh positive caller evidence is
`unverified`, `needs_freshness` means freshness was not supplied, `stale` means
the evidence is older than one hour, `degraded` means known failures were
declared, and `unsupported` means no positive evidence was attached. The
observed `verify-release` path may apply `supported` after executing the declared
checks itself. The offline signature verifier reports a successful historical
commitment check separately as `VALID_LEGACY`; it never promotes advisory
`epistemic_status` to a verification verdict or silently treats the historical
receipt as an AEF receipt.

The separate `core.aef_verification` boundary verifies native AEF v0.1 receipt
identity, Ed25519 signatures, key policy, freshness, and signed-tree-head
inclusion under explicit trust and caller-supplied time. Its conformance fixture
covers all ten accepted receipt and inclusion vectors; the source vector token
`INVALID_EXPIRED` is recorded as a source correction to the normative `EXPIRED`
verdict. `AefReceiptIndex` remains the explicit ephemeral batch boundary;
`AefDurableReceiptIndex` persists accepted `(log_id, seq, receipt_id)` identities
with a FULL-synchronous atomic transaction, so replays and conflicting sequence
claims remain detectable across restarts and concurrent verifier processes. Its
table may coexist in the hub database but never reads or writes legacy event
rows. Native AEF log emission remains separate from the historical event log and
its Merkle tree; neither serializer is reinterpreted as the other.

`AefReceiptLog` is the native-emission boundary for the next migration step. It
assigns an independent AEF sequence and `prev_receipt` chain, signs canonical
receipt bytes with the existing hub Ed25519 key type, validates every receipt
before a FULL-synchronous append, and can bind the frozen legacy root only in
the AEF genesis receipt. A supplied `legacy_seq` is stored as reconciliation
evidence, never as an AEF sequence or Merkle leaf. The native tables may coexist
in the hub database without touching legacy event rows.

`legacy_event_to_aef` is the explicit compatibility mapper for the first
runtime evidence families: lease grants and minimized claim denials,
digest-only guard denials, sandbox executions, and durable multi-hub
partition/heal transitions. It converts the historical float timestamp through
the AEF integer-time boundary and carries the legacy sequence only into signed
reconciliation evidence. Minimized identifiers remain visibly digest-labelled;
the mapper does not reconstruct deleted plaintext. Unsupported kinds remain
legacy-only and malformed supported rows fail closed. This mapper still does
not make two separate commits atomic; the durable outbox below is the recovery
boundary between them.

The durable outbox boundary queues selected legacy event sequences in the same
SQLite transaction as their unchanged event rows. `drain_aef_outbox` consumes
those rows in legacy order. If the process stops after native AEF emission but
before acknowledgement, restart lookup by the signed/indexed `legacy_seq`
verifies that the existing receipt is the same deterministic projection and
settles the cursor without emitting a duplicate. A mismatch, unsupported queued
kind, malformed row, or receipt without identity stops the drain fail-closed.
The outbox remains opt-in. A hub enables the live route only when the operator
supplies `--db`, a stable `--hub-id`, and an owner-only Ed25519
`--aef-signing-key`. Startup reconciles the complete pending backlog before the
hub accepts traffic. Live reconciliation runs on a dedicated worker thread with
fresh SQLite connections, so it never shares the hub connection across threads;
failure leaves the durable cursor pending, logs the condition, and retries at
the bounded `--aef-drain-interval`. Shutdown signals and joins that worker before
the journal closes. Omitting the signing-key flag preserves the legacy-only
runtime exactly.

Before closeout, `python tools/test_ownership_map.py --check` can map changed
source files to likely owning tests. The map uses AST imports and a conservative
test-filename fallback, so it is useful evidence for picking focused tests and
receipt `changed_file` entries without pretending to be an approval system.

For generated artefacts, `python tools/generated_dependency_claims.py --claim-args
--source <path>` maps source paths to generated outputs that can go stale, such as
the README capability inventory and `docs/_generated/capability_manifest.json`.
Use those generated paths in the same file-scope claim and receipt. This
generated-output dependency map is a coordination aid; freshness still comes from
the owning generator's check command.

`python tools/semantic_claims.py --selector <kind:value> --claim-args` is the
semantic claim resolver for local planning. It accepts module, symbol, API,
source, test, generated, and migration selectors. Symbol and API selectors emit
a canonical synthetic descendant such as
`src/pkg/worker.py/.synapse-symbol/Worker/run`; the other selectors and owning
test/generated companions remain whole-file paths. The hub enforces all of them
with its existing path ancestry rule, so symbol separation needs no parser or
wire change on the server.

For day-to-day claims, `synapse git-claim` accepts the same selector kinds as
first-class flags (`--module`, `--symbol`, `--api`, `--source`, `--test`,
`--generated`, and `--migration`). It resolves them after discovering the local
git root, merges the derived paths with explicit `--paths`, and can write the
selector evidence with `--semantic-evidence-json`.

Function-level inference from actual edits is also client-side. Install
`synapse-channel[semantic]`, then use `python tools/semantic_diff_claims.py
--base main --claim-args` or `synapse git-claim TASK --diff-base main`. The
resolver maps zero-context Git hunks on both old and new source sides to the
smallest named tree-sitter declaration for Python, JavaScript, TypeScript, Rust,
and Go. Any incomplete mapping — including module-level changes, unsupported or
invalid syntax, add/delete/rename statuses, or unavailable source content —
widens to the whole file. Optional `--diff-head`, repeatable `--diff-path`, and
`--semantic-evidence-json` keep the comparison and receipt evidence explicit.
No parser is downloaded at runtime; the hub still stores only canonical paths
and branch metadata.

Those symbol paths participate in the complete local enforcement chain.
Precise provider edit tools may provisionally use a symbol claim for its source
only when the exact worktree/branch has no competing semantic owner; full-file
writes and patch tools require a literal file claim. At commit time,
`git-claim-check --staged` compares `HEAD` with the authoritative index and
checks the exact declarations touched. Incomplete evidence widens to the file
and parser failure denies. Post-commit/post-merge auto-release repeats the
committed semantic projection and releases only the exact proven symbol; it
retains ambiguous symbol claims for manual release. Parallel sibling-symbol
work should use isolated Git worktrees because the underlying physical file
cannot safely host independent pre-edit mutations in one shared checkout.

`python tools/import_merge_risk.py --changed <path> --claimed <path> --check` is
the import graph merge-risk radar for pre-merge and handoff checks. It combines
explicit changed paths or `--base main --head HEAD` branch diffs with claimed
paths, package-local Python import edges, CODEOWNERS, and the test ownership map.
It is advisory: a non-zero `--check` result means review the overlap before
merge, not that the hub has rejected anything.

`synapse event-query ./synapse.db "task <id> timeline"` is the temporal
event-log query surface for post-hoc reconstruction. It reads the SQLite event
store directly and can show a task timeline, task state at a sequence or
timestamp, path touches between timestamps, or historical claim conflicts. The
query is read-only and does not change hub state. Prototype Datalog-like aliases
such as `timeline("TASK").` and Cypher-like aliases such as
`MATCH (task:TASK {id:"TASK"}) RETURN timeline` normalize into that same small
query model over journal snapshots.

`synapse postmortem ./synapse.db TASK-1` turns those same durable events into a
replayable postmortem. It reconstructs who claimed the task, release points,
assessment evidence that existed in the board progress stream, path-overlap
conflicts involving the task, and candidate unanswered messages. The unanswered
message section is deliberately conservative: it reports directed chats that
mention the task id and have no later matching chat reply in the log; it does
not prove intent or off-channel response.

`synapse reliability ./synapse.db` aggregates evidence-only reliability memory
from the event log. It tracks stale claims, declared failed-check evidence,
broken handoff candidates, and merge-conflict frequency as audit signals, not
scores. The report is suitable for handovers and routing review, but it does not
rank agents or assign trust grades.

The [agent trust graph](agent-trust-graph.md) connects those audit signals
with positive release receipts as traceable graph evidence, queryable with
`synapse trust-graph` (filter by agent, task, or time window; text, JSON, or
Graphviz DOT). It stays advisory: it does not rank agents, authorize
execution, or replace review.

`synapse cross-repo` extends the same read-side evidence across a whole
checkout tree: dependency manifests and CODEOWNERS files become edges between
repositories, and the live claims of the event log join onto the graph so an
agent can see, before starting a cross-cutting change, whether anyone is
working in a repository its repository depends on — or one that depends on
it. Declared version constraints that are provably disjoint — two
repositories pinning the same package to ranges no version satisfies — are
flagged as `version_conflict` edges; a constraint the bounded comparison
cannot model never claims a conflict. Advisory and declaration-level, like
every other analysis surface.

The planned [policy engine](policy-engine.md) builds on those same release
receipts and event-log projections. Its first mode is advisory: required tests,
strict type checking, owner approval, evidence freshness, generated artifact
parity, and no-merge-without-receipt rules are evaluated against local evidence
without changing hub state or merging code.

`synapse ttl-advice ./synapse.db` evaluates adaptive lease TTL inputs from the
event log. It uses completed-task duration samples and live-claim load to print
an advisory default, but it does not mutate hub configuration and explicit
manual TTL choices remain authoritative.

## 4. Hand off and recover

- **Atomic handoff** transfers a held task to another *online* agent in one step,
  with no release/re-claim window for a third agent to grab it. Scope, status,
  and checkpoint move with it. The move honours the same file-scope mutual
  exclusion as a direct claim: it is refused if the moved scope would collide
  with a live claim held by an agent other than the recipient, so a handoff can
  never leave two agents holding the same files.
- An **LLM-free supervisor** watches the plan and re-offers tasks that stall (no
  progress while in progress, or blocked with every dependency finished). Its
  in-progress rule keeps the fixed idle threshold as the operator ceiling and
  can optionally supplement it with completed-task progress cadence from the
  same board.
- A task taken over after its lease lapses **resumes from its last checkpoint**
  rather than restarting.

## 5. Route

Workers advertise **capability cards** describing their skills and the task
classes they can take; the hub aggregates them into a manifest. Cards may also
include declarative capability contracts with per-task-class `input_schema`,
`output_schema`, preconditions, and postconditions. A request can be classified
into a task class and routed to the matching backend, reserving heavy models for
the genuinely hard requests, while the contract metadata remains reviewable
discovery evidence rather than executable trust.

For board work, `synapse route-task <task-id>` and the MCP
`synapse_route_task` tool provide the same read-only recommendation payload.
They join the board task with live capability cards and score structured
task-class matches, skill tags, card description overlap, and contract evidence
locally. An optional event-store path adds observed evidence from positive
release-receipt assessment notes, preserving source task ids and durable event
sequences so a human or policy layer can audit why the hint exists. The result
is a routing hint only: it does not claim the task, mutate the board, reserve
capacity, grade an agent, or certify an agent.

For resource selection, `synapse resource-bids <task-id>` and the MCP
`synapse_resource_bids` tool rank live resource offers from the same directory
against a board task. The output keeps resource id, provider, kind, name,
capacity, score, and reason codes so humans or policy layers can inspect why an
offer ranked. The result is advisory only: it does not reserve capacity,
authorize execution, mutate the board, or certify provider trust.

MCP resource templates provide narrower read-only context retrieval for hosts
that support them: `synapse://task/{task_id}` for one board task,
`synapse://agent/{agent}` for one agent's card and resources, and
`synapse://resource-kind/{kind}` for matching resource offers. These templates
read the same hub snapshots as the static resources; they do not stream updates,
chain tools, reserve resources, assign work, or change the hub protocol.

For local durable memory, `synapse memory-recall <db> <query>` and the MCP
`synapse_memory_recall` tool project findings, checkpoints, and handoffs from
the SQLite event store into deterministic token matches. Recall hits keep the
source sequence, event kind, source field, task id, actor, evidence reference,
and matched tokens so downstream use remains auditable. The projection is local
and read-only: it does not create external embeddings, call a service, certify
truth, or mutate hub state. The live hub also caps durable findings admitted per
agent before journalling, preventing one producer from dominating the local
memory spine.

## Durability and reconnection

With `--db`, the hub records every authoritative mutation to an append-only
SQLite event log (WAL) and rebuilds its state by replaying it on start-up. A
reconnecting agent uses an idempotency key (so a retried claim is applied once)
and a resume cursor (to fetch exactly the messages it missed).
