# CLI reference

The `synapse` command exposes the following subcommands.

For end-to-end examples that combine these commands with existing agent tools,
see the [Integration demos](integration-demos.md).

| Command | What it does |
| --- | --- |
| `synapse hub` | Run the coordination hub. |
| `synapse commands` | List every subcommand grouped by stability tier — the quickest map of the surface. |
| `synapse completions` | Print a static tab-completion script for bash, zsh, or fish, generated from the installed CLI. |
| `synapse demo` | Run a self-contained local coordination demo and print a success marker. |
| `synapse quickstart-coding` | Create a coding-fleet workspace, run the no-collision demo, and print a success marker. |
| `synapse new coding-fleet` | Scaffold a runnable two-agent coding demo workspace. |
| `synapse health` | Probe the hub; exit `0` if reachable, `1` if not (wired as a container healthcheck). |
| `synapse worker` | Run a model worker that answers on the channel. |
| `synapse worker-session` | Run a provider command with `SYN_PROJECT`/`SYN_IDENTITY` set and a waiter armed around it. |
| `synapse team` | Launch a hub plus one or two local workers in one shot. |
| `synapse mcp` | Serve the hub to MCP-compatible agents over stdio (see [MCP server](mcp.md)). |
| `synapse mcp-tools` / `synapse mcp-call` | List and call allowlisted tools on an external MCP server (outbound). |
| `synapse sandbox` | Validate a capability manifest and pre-flight or run a `.wasm` tool against it (`validate`/`test`/`run`). |
| `synapse adapters` | Detect coding tools and wire them to the hub with a claim-aware adapter (`list`/`install`/`uninstall`). |
| `synapse a2a-card` | Print an Agent2Agent Agent Card projected from the live capability manifest. |
| `synapse a2a-serve` | Run the stdlib HTTP+JSON Agent2Agent bridge. |
| `synapse channel` | Manage private-channel membership and member-visible history; pair with `synapse send --channel`. |
| `synapse encrypt-key` | Generate and check at-rest encryption key files (needs the `encryption` extra to encrypt). |
| `synapse agent-tmux` | Wake an existing terminal-agent tmux session (Codex, Kimi, …) with a fixed safe prompt. |
| `synapse codex-tmux` | Codex-defaulted alias of `agent-tmux`. |
| `synapse dashboard` | Serve a loopback-only read-only live cockpit (fleet graph, board, claims, stream, receipts) over hub snapshots, plus `/snapshot.json`. |
| `synapse route-task` | Recommend agents for a board task using local capability signals. |
| `synapse resource-bids` | Rank live resource offers for a board task without reserving capacity. |
| `synapse memory-recall` | Recall matching durable memory records from a local event store. |
| `synapse send` | Connect, send one message, optionally await replies, and exit. |
| `synapse wait` | Block until a message addressed to you arrives, then exit (a wake trigger). |
| `synapse listen` | Connect and stream channel messages until interrupted. |
| `synapse arm` | Keep a waiter armed, re-arming automatically after each wake so a terminal stays reachable. |
| `synapse relay` | Decode and print a lite relay log a hub mirrored to a file. |
| `synapse ingest` | Stream durable event-store records since a sequence cursor. |
| `synapse event-query` | Query a hub SQLite event store for temporal task and coordination history. |
| `synapse multihub` | Observe or follow a peer hub's event log and print its board and claims (see [Multi-hub sync](multi-hub-sync.md)). |
| `synapse participant` | Probe or drive Participant Fabric providers: `list` reports each driver's readiness, `ask` runs one turn, `exchange` and `convene` run multi-party deliberations. |
| `synapse federation` | Import, list, and revoke out-of-band operator-signed peer-domain bundles (`import`/`list`/`revoke`). |
| `synapse compact` | Apply event-store retention and optionally write an HTML archive report. |
| `synapse postmortem` | Build a replayable task postmortem from a hub SQLite event store. |
| `synapse debug` | Fork a task's reconstructed state at a sequence point (read-only what-if). |
| `synapse reproduce` | Fingerprint a task's authoritative history into a deterministic digest. |
| `synapse causality` | Trace coordination causes, effects, or counterfactuals over the event log. |
| `synapse merkle` | Commit the event log to a Merkle root and prove event inclusion. |
| `synapse reliability` | Build evidence-only reliability memory from a hub SQLite event store. |
| `synapse accounting` | Record and report opt-in model cost/token usage from a hub SQLite event store. |
| `synapse approval` | Request, decide, and replay human-in-the-loop approval gates from a hub SQLite event store. |
| `synapse ttl-advice` | Build read-only lease TTL advice from a hub SQLite event store. |
| `synapse board` | Print the shared task/progress blackboard. |
| `synapse supervisor` | Run an LLM-free supervisor that re-offers stalled tasks. |
| `synapse manifest` | Print the capability manifest of advertised agents. |
| `synapse directory` | Print a read-only capability directory from live agent cards (discovery only). |
| `synapse who` | List the agents currently online, optionally for one project or this identity with `--me`. |
| `synapse status` | Print a one-line hub summary (online agents, active claims) for shell prompts and tmux status bars; exit non-zero when the hub is down. |
| `synapse state` | Print active claims and their checkpoints (a resume view). |
| `synapse doctor` | Check for common coordination misconfigs (identity, exposure, hub, waiter); exit non-zero on a failure. `--fix` auto-repairs a down default local hub or missing waiter by installing and starting the user services. |
| `synapse init` | Print or install the local user services (hub, waiter, presence) as systemd units. |
| `synapse install-shell-hook` | Install auto-arming shell integration into Bash, Zsh, and Fish (idempotent, guarded block). |
| `synapse shell-hook` | Print the shell code that auto-arms terminals and wraps agent commands, for manual sourcing. |
| `synapse git-init` | One-step claim-aware setup: install the hooks and write a `.synapse/` conventions guide. |
| `synapse git-claim` | Claim work scoped to the current git branch (see [Git-native claims](git-claims.md)). |
| `synapse git-hook` | Install post-commit/post-merge hooks that auto-release a commit's claims. |
| `synapse git-release` | Release the claims whose paths a commit or merge just touched. |
| `synapse conflicts` | Predict cross-branch merge conflicts between overlapping claims; exit non-zero on a hit. |
| `synapse verify-release` | Run declared verification commands and write an observed release receipt JSON; `--merkle-db` commits the coordination log's Merkle root into it. |
| `synapse policy-check` | Evaluate a release receipt against a policy file; advisory by default, `--enforce` to gate. |
| `synapse identity` | Inventory and audit declared agent identities for enforcement-rollout blockers. |
| `synapse acl` | Shadow-mode (non-blocking) deny-by-default ACL evaluation of candidate accesses. |
| `synapse lock` | Hold a lease while running a command, to serialise it across agents. |
| `synapse release` | Manually drop a claim you own (e.g. an `--auto-release-on manual` claim). |
| `synapse task` | Declare and update the shared task plan. |
| `synapse workflow` | Validate and compile a declarative workflow into blackboard tasks (`validate`/`compile`/`plan`/`run`). |

## First 60 seconds

The installed CLI has a source-checkout-free validation path:

```bash
python -m pip install synapse-channel
synapse commands   # a map of every subcommand, grouped by stability tier
synapse doctor
synapse demo
synapse quickstart-coding
```

`synapse commands` prints the whole surface grouped into its five stability tiers
(stable core, adapters, read-only analysis, advisory governance, experimental), so
you can find the daily-safe core without scrolling the flat `synapse --help` list.

`synapse doctor` reports local wiring issues, including identity, hub exposure,
root-filesystem pressure, hub reachability, and the current identity's waiter. On
a fresh machine, a missing hub or waiter can be a warning before services are
installed. `synapse doctor --fix` repairs the safely repairable findings: when the
default local hub does not answer or the waiter is missing, it installs and starts
the local hub, presence, and wake services, then re-runs the checks so the exit
code reflects the repaired state. Findings the services cannot repair — identity,
exposure, disk pressure, or any non-default hub — are reported with a remedy but
never touched. `synapse demo` starts an ephemeral local hub, drives a planner/worker
flow, and is successful when it prints:

For post-release local fleet restarts, `synapse doctor --redeploy-checklist`
prints package, service, roster, durable-state, and git-hook checks. It does not
restart services by itself; it gives operators copyable verification commands
for the installed executable, `systemd --user` units, live roster, SQLite event
log, and claim-aware hooks.

```text
success: coordination demo completed
```

`synapse quickstart-coding` creates a temporary workspace, runs the packaged
two-agent coding demo, removes the temporary workspace after success, and is
successful when it prints:

```text
success: coding fleet demo completed
```

For a generated workspace with editable files and a runnable no-collision coding
scenario:

```bash
synapse new coding-fleet ./demo-fleet
cd ./demo-fleet
python run_demo.py
```

The generated demo succeeds when it prints `success: coding fleet demo completed`.

## Fastest safe trial path

Use this order when moving from install validation into a real repository:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
synapse git-init --name trial-agent
synapse a2a-card --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
```

Run this in a disposable or already-versioned repository. `synapse git-init
--name trial-agent` installs hooks and writes the local `.synapse/` conventions
guide before agents edit files. The A2A bridge step is optional and local-only:
it validates the HTTP+JSON bridge shape for local tools, but it is not an
external conformance claim. Do not bind it off-loopback without bearer auth.

For a stricter local hub profile, use `synapse hub --paranoid --db <path>
--token-file <path>`. Paranoid hub mode requires a shared-secret token, durable
event log, and metrics bearer token when metrics are enabled; it disables metrics
query tokens and the insecure off-loopback override while printing the hardening
hooks that are still missing.

## Recovery: picking up after a restart

Nothing is lost when a terminal or session goes down — the feed, the plan, and the
event log are durable. On return, catch up everything for your repo regardless of
the instance id you now run as:

```bash
synapse relay ./feed.ndjson --project quantum --cursor ./quantum.cursor  # missed messages
synapse board                                                           # the current plan
synapse state --owner quantum                                           # your claims + resume checkpoints
synapse who --project quantum                                           # who is live now
synapse dashboard --port 8765                                           # local read-only HTML/JSON view
```

A lapsed claim keeps its checkpoint, so re-claiming the task resumes from it rather
than restarting.

`synapse dashboard` binds to `127.0.0.1` by default and reads roster, state,
board, and manifest snapshots from the live hub. It serves `/` for the browser
view and `/snapshot.json` for local tooling. The snapshot also includes a derived
`fleet` section for live agents, `-rx` waiters, missing waiters, active and stale
claims, a task-dependency graph from blackboard task edges, branch-conflict candidates
from live git-scoped claims, ready and blocked board tasks, release receipt
notes, and optional A2A task counts. Pass `--a2a-state-file <path>` to
summarise a persisted `synapse a2a-serve --state-file <path>` store in that
section. The task-dependency graph is read-only and does not mutate the
blackboard. Dashboard branch conflicts use the same declared-claim metadata as
`synapse conflicts`; they do not run git or apply `--check-diff` refinement. Use
`--allow-non-loopback` only behind trusted local network controls because the
page exposes agent names, claim scopes, branch names, and task text. Pass
`--dashboard-token <token>` to require `Authorization: Bearer <token>` on `/`
and `/snapshot.json`; when `--allow-non-loopback` exposes the dashboard and no
token is supplied, Synapse generates and prints a startup token.

## Identities and groups

An identity is a name; when several agents share a project they use composite
names `<project>/<agent>`, e.g. `quantum/claude-7f3a` and `quantum/codex-2b40`.
A `target` is then a name, a comma list, a **group glob** (`quantum/*` for every
agent on the project, `quantum/claude-*` for one role), or `all`. List who is live:

```bash
synapse who                       # agents online, with -rx waiter sidecars counted apart
synapse who --project quantum     # only quantum/... instances
synapse who --name quantum/codex-2b40 --me  # this identity plus its -rx waiter status
syn who --me                      # same check using the resolved syn identity
syn reap                          # list this identity's shell-hook waiter pidfile
syn reap --pid 1234               # clean up only that verified identity waiter PID
syn reap --stale                  # reap all verified waiters whose owner shell is dead
syn reap --stale --dry-run        # report the sweep verdicts without acting
syn locks                         # list this project's leases, scopes, ages, and release commands
syn ask <target> <message>        # send, require an online recipient, and wait for replies
syn commit <paths> -m <message>   # hold the project git lease and commit only those paths
synapse send --target quantum/* "rebasing main now"   # the whole project team
```

`synapse who --me` queries as `<name>-who`, then reports `<name>` and
`<name>-rx`, so the check does not create the presence it describes. It keeps the
output honest: presence is not a wake loop, and a missing `-rx` waiter means
directed messages will not wake that terminal promptly.

`synapse wait --directed-only` suppresses *routine* broadcasts: it wakes on messages
that name you (or a group you are in), but still wakes on a **priority broadcast**
(`synapse send --priority`) and on any message from **`CEO`** — so an `all` that
genuinely matters reaches a quiet waiter promptly while peer chatter is left for the
next `synapse relay`/inbox read. Use `--priority` sparingly, for announcements that
must reach everyone immediately.

When several agents share a repo, serialise the operations that must not overlap —
above all commits — by wrapping them in a lease. The hub grants one live lease per
id, so the others wait their turn instead of clobbering each other:

```bash
synapse lock quantum:git -- git push          # holds quantum:git while pushing
synapse lock quantum:git --wait-timeout 0 -- git push   # fail fast if someone holds it
```

A lock is a named mutex keyed by its id: `quantum:git` and `physics:git` are
independent, so one repo's push-lock never blocks another's. The lease is held only
for the wrapped command and dropped when it exits. A claim that no commit or merge
will auto-release — a `git-claim --auto-release-on manual` — is dropped by its owner
with `synapse release <task> --name <owner>`.

Add receipt fields when the release is also the closeout record. The hub echoes
the receipt on `release_granted`; if any evidence field is present, it records the
same receipt as an `assessment` progress note on the board. Use `--receipt-json`
when another tool should consume the hub-confirmed receipt:

```bash
synapse release BUILD --name api-dev \
  --evidence "pytest tests/test_feature.py -q: passed" \
  --evidence "mypy src/synapse_channel/feature.py: passed" \
  --changed-file src/synapse_channel/feature.py \
  --generated-artifact docs/_generated/capability_manifest.json \
  --artifact coverage.xml \
  --approval "reviewed-by=owner" \
  --known-failure "none" \
  --confidence medium \
  --freshness-seconds 60 \
  --receipt-json
```

The receipt records the releasing owner's submitted evidence; it does not certify
that the evidence is complete or sufficient. It also includes advisory
`epistemic_status` and `epistemic_reasons` fields derived from the submitted
evidence, known failures, and `--freshness-seconds`: fresh positive evidence is
`supported`, positive evidence without freshness is `needs_freshness`, old
positive evidence is `stale`, declared known failures are `degraded`, and no
positive evidence is `unsupported`.

Use `synapse verify-release` when the closeout record must include observed
command execution instead of hand-written evidence. The command runs each
declared `--run` argv, records exit codes plus stdout/stderr SHA-256 digests,
hashes named `--artifact` files, captures Git `HEAD`, tree, and changed files,
and writes receipt JSON consumable by `synapse release --receipt`:

```bash
synapse verify-release BUILD --name api-dev \
  --run ".venv/bin/python -m pytest tests/test_feature.py -q" \
  --run ".venv/bin/python -m mypy --strict src/synapse_channel/feature.py" \
  --artifact coverage.xml \
  --merkle-db ~/synapse/hub.db \
  --output verified-release.json

synapse release BUILD --name api-dev \
  --receipt verified-release.json \
  --receipt-json
```

`--merkle-db` additionally commits the coordination log's RFC 6962 Merkle root
into the receipt, binding the release to the exact coordination history behind
it. Because the log is append-only, anyone can later re-verify the commitment —
`synapse policy-check --merkle-db` recomputes the committed log prefix and adds
a `merkle_commitment` decision that fails when that prefix was rewritten,
truncated, or renumbered since the receipt (and passes as the log grows):

```bash
synapse policy-check BUILD --policy ./policy.json \
  --receipt-json verified-release.json \
  --merkle-db ~/synapse/hub.db          # re-verify the committed log prefix
```

The generated receipt is still advisory coordination evidence. A `supported`
status means the submitted checks produced fresh positive evidence; it does not
mean Synapse independently verified correctness, reviewed the commands, or
proved the artifacts sufficient.

`synapse git-claim` accepts the task id either positionally (`synapse git-claim
TASK-1 --paths src`) or as a named field (`synapse git-claim --task-id TASK-1
--paths src`) for generated argv. Use one form, not both. `synapse git-release`
is hook-invoked and does not take a task id; when a manual drop is needed, use
`synapse release <task> --name <owner>`.

`synapse git-claim` also accepts semantic selector flags for the same local
resolver exposed by `tools/semantic_claims.py`: `--module`, `--symbol`, `--api`,
`--source`, `--test`, `--generated`, and `--migration`. The command resolves the
selectors against the local git root, merges the derived source/test/generated
paths with any explicit `--paths`, and sends only ordinary file-scope paths to
the hub. Add `--semantic-evidence-json semantic-evidence.json` to write
receipt-ready selector evidence under the git root.

Claim paths are coordination scopes, not filesystem reads. Normal relative paths
such as `src/auth.py` stay narrow. Absolute paths and any path containing `..`
are treated as traversal-like declarations and widen to the whole worktree, so a
suspicious scope may block more work but cannot miss a conflict.

Use `syn locks` for the operator view before releasing or asking another owner to
release. It queries the live state snapshot as `<identity>-locks`, filters to the
resolved project by default, and prints the task id, holder, scope, age, remaining
lease time, checkpoint, git branch context, and the exact `synapse release ...`
command. `syn locks --all` removes the project filter; `syn locks --owner <name>`
shows one owner or project namespace; `syn locks --json` emits the same rows as
JSON.

Use `syn commit <paths> -m <message>` for the common commit workflow. It resolves
the current identity, acquires the `<project>:git` lease, runs `git add -A --`
only for the supplied paths, and runs `git commit -m <message> --` for the same
paths. Unrelated staged or modified files stay outside that commit. The command
rejects empty, absolute, parent-traversal, and `.git` paths before contacting the
hub.

## Getting woken on a message

A turn-based assistant cannot hold a socket between turns, so it learns of a
message only when it checks. `synapse wait` turns that into a push: it blocks on
the connection and exits the instant a message addressed to you arrives. Run it as
a background task — when it exits, the message has landed (and a harness that
re-invokes an agent on background completion wakes you). On wake, read the message,
act, and re-launch `synapse wait`. It costs nothing while it waits.

```bash
synapse wait --name api-dev-rx --for api-dev   # blocks; prints + exits on a message for api-dev
synapse wait --for api-dev --timeout 60        # give up after 60s (exit 2) instead of waiting forever
```

When the shell hook launches an interactive provider command, `worker-session`
automatically starts or attaches a persistent tmux session and keeps a directed
wake bridge alive. The user still types the provider command normally, for
example `codex` or `claude`; the provider process starts with `SYN_PROJECT` and
`SYN_IDENTITY` already set.

Use `synapse agent-tmux` as the manual diagnostic/admin surface for that tmux
wake path. It works for any terminal coding agent — Codex, Kimi K2, Claude Code —
selected with `--agent-command`. It starts or targets a named tmux session and
injects only a fixed instruction; the Synapse message body stays in the inbox and
the agent reads it itself. `synapse codex-tmux` is a Codex-defaulted alias kept
for backward compatibility (`--codex-command` instead of `--agent-command`).

```bash
# Generic form — choose the agent with --agent-command (defaults to codex):
synapse agent-tmux start  --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"
synapse agent-tmux wait   --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"
synapse agent-tmux status --identity api-dev/kimi --session api-dev-kimi --agent-command kimi --cwd "$PWD"

# Codex alias (equivalent to --agent-command codex):
synapse codex-tmux wait --identity api-dev/codex-main --session api-dev-codex --cwd "$PWD"
```

A terminal agent does not wake its own idle pane on a Synapse message: its
`synapse wait` is a foreground tool call whose turn ends, so the message lands in
the inbox but the pane never re-engages. `agent-tmux wait` is the external bridge
that closes that gap — it blocks on `synapse wait` for the identity and, on each
directed message, types the wake prompt into the pane and presses Enter.

`wait` types the fixed prompt and presses Enter as two steps separated by
`--submit-delay` seconds, because the agent UI ignores a submit key that arrives
in the same keystroke batch as the pasted line. It retries a failed
`synapse wait` with backoff instead of exiting, giving up only after
`--max-wait-failures` consecutive failures (unbounded by default), so a hub
restart does not permanently stop the waker.

When a broadcast (`--target all`, or a `--priority`/`CEO` message that reaches a
`--directed-only` waiter) wakes *every* terminal at the same instant, their agents
all re-invoke and call the model provider at once. That synchronised burst trips the
**provider's** request-rate limiter — not a synapse limit: Anthropic's API, for
instance, answers *"Server is temporarily limiting requests"* (a request-rate
throttle, distinct from your usage quota). `synapse wait --wake-jitter <seconds>`
(default 8) spreads the broadcast wakes over `0..jitter` so each agent reacts
without the stampede; a
one-to-one directed message has no herd and still wakes immediately. Set `0` to
disable for a latency-critical single-waiter setup.

The shell hook records its background waiter in an identity-scoped pidfile under
`$XDG_RUNTIME_DIR/synapse-shell` (or `/tmp/synapse-shell`). Use `syn reap` to list
the pidfile for the resolved identity. If the pidfile points at a dead PID,
`syn reap --pid <pid>` removes only that pidfile; if the PID is live, it sends
SIGTERM only after the command line verifies as this exact `synapse arm --name
<identity>-rx --for <project>` waiter. It refuses unrelated PIDs instead of
searching or pattern-killing.

`syn reap --stale` sweeps every pidfile in the runtime directory and reaps the
verified waiters whose owner is demonstrably dead — the recorded `--owner-pid`,
or the terminal PID embedded in a `…/terminal-<pid>` identity. A waiter whose
owner is alive, or that names no checkable owner, is kept; a live process whose
command line is not this Synapse waiter is reported and never signalled. Add
`--dry-run` to see the verdicts without acting.

```bash
synapse wait --for api-dev                  # default: broadcast wakes jitter 0–8s
synapse wait --for api-dev --wake-jitter 0  # disable the jitter
```

The same herd from the *sending* side: to push a fleet-wide update, do **not**
`--target all` a fleet of waiters at once — send directed, spaced a few seconds
apart, so the wakes (and re-invocations) do not stampede the provider.

## Messaging: broadcast, several, or one

Every message carries a `target`. The hub broadcasts each message to all
connected clients and records it in history and the relay log; the `target`
selects who it is *for*:

```bash
synapse send --target all "deploy is green"              # everyone (the default)
synapse send --target SCPN-CONTROL "kernel built, run the control tests"  # one agent
synapse send --target SCPN-CONTROL,REMANENTIA "you two: rebase on main"   # several
synapse send --require-recipient --target SCPN-CONTROL "ping"             # fail if nobody online matches
```

If a one-shot send accidentally uses a waiter name such as `api-dev-rx`, the
command sends as `api-dev` instead. That keeps the persistent wake socket online
and avoids the hub's duplicate-name refusal for the short-lived sender.

Use `synapse send --require-recipient` for directed sends that should be
observable. The sender asks the hub for a delivery receipt; the hub replies with
`delivery_receipt`, including `delivered`, `message_target`, `message_id`, and
the matched online `recipients`. The CLI prints `delivered to ...` and exits `0`
when at least one online recipient matches `--target`; it prints `delivery
failed: no online recipient matched ...` and exits `1` when the message would
otherwise be only a silent durable-feed entry.

For selected sensitive bodies, `synapse send --encrypt-key-file` replaces the
plain payload with an AES-256-GCM envelope whose authenticated data binds the
visible sender, target, channel, task id, and recipient set. The hub routes the
ciphertext and metadata; it does not receive the plaintext:

```bash
synapse send --target SCPN-CONTROL \
  --encrypt-key-file ./payload.key \
  --encrypt-key-id project-main-v1 \
  --encrypt-recipient SCPN-CONTROL \
  "private handoff note"
synapse listen --name SCPN-CONTROL --for SCPN-CONTROL \
  --decrypt-key-file ./payload.key
synapse channel key-check ./payload.key
```

The key file is a local 32-byte owner-only file. This first runtime tranche does
not discover, rotate, revoke, or escrow keys.

Private channels scope delivery without encrypting payloads:

```bash
synapse channel create ops --name alice
synapse channel join ops --name bob
synapse send --name alice --channel ops "operator note"
synapse channel history ops --name bob --limit 20
synapse relay ./feed.ndjson --channel ops
synapse relay ./feed.ndjson --public-only
synapse relay ./feed.ndjson --channel ops --channel-metadata
synapse event-query ./synapse.db "channel ops between seq 1 999999"
```

The hub retains a bounded live history per channel for current members and
journals channel chat with a visible channel id. Relay filtering can select one
private channel or the default public lane. Event-query channel results expose
metadata and payload byte length, not private payload bodies.

For the common question workflow, use `syn ask <target> <message>`. It resolves
the same identity as `syn say`, dispatches to `synapse send` with
`--wait-seconds 30 --require-recipient`, and prints replies during that wait
window. Override the window with `syn ask --wait 10 <target> <message>`. Use
`--no-require-recipient` only for broadcasts or durable-feed-only asks.

A reader sees only the messages addressed to it with `--for`, which also drops
presence noise and other agents' cross-talk — a per-agent inbox. Because the
relay log is durable, an agent that was offline still catches up on its next read:

```bash
synapse relay ./feed.ndjson --for SCPN-CONTROL --cursor ./control.cursor
synapse listen --name SCPN-CONTROL --for SCPN-CONTROL    # live inbox
```

## Hub options

```bash
synapse hub --port 8876
synapse hub --port 8876 --db ./synapse.db          # crash-safe persistence
synapse hub --port 8876 --rate 5 --burst 20        # per-agent rate limiting
synapse hub --port 8876 --relay-log ./feed.ndjson  # mirror the channel to a file
synapse hub --max-clients 32 --max-msg-kb 256      # cap connections and frame size
synapse hub --max-connections-per-host 4           # cap simultaneous sockets from one host
synapse hub --max-progress-per-task 500            # cap retained board progress per task id
synapse hub --max-findings-per-agent 200           # cap durable findings admitted per agent
synapse hub --shutdown-close-timeout 5             # bound active socket close handshakes
synapse hub --tls-certfile ./hub.crt --tls-keyfile ./hub.key  # native wss://
synapse hub --host 0.0.0.0 --token-file ./tok      # token from a file, not argv (ps-safe)
synapse hub --host 0.0.0.0 --insecure-off-loopback # bind off-loopback WITHOUT a token (refused otherwise)
```

Binding a non-loopback host without a token (and, with `--metrics`, a metrics
token) is **refused** by default — the hub will not start exposed by accident;
`--insecure-off-loopback` downgrades that to a warning for a trusted private
network. `--max-connections-per-host` is a connection-count cap keyed by the
remote host; it is separate from `--host-rate`, which meters inbound frames from
that host. Native `wss://` uses `--tls-certfile` plus `--tls-keyfile`; it protects
the transport but does not replace `--token` for off-loopback binds. Supply the
token with `--token-file` or the `SYNAPSE_TOKEN`
environment variable rather than `--token`, which is visible in `ps`. The hub
drains on `SIGTERM`/`SIGINT`, so a container stop shuts it down cleanly. `synapse
health` is a liveness probe — exit `0` when the hub answers, `1` otherwise —
wired as the Docker `HEALTHCHECK`. `--shutdown-close-timeout` bounds the
WebSocket close handshake during stop; accepted mutations are durable at append
time when `--db` is enabled, not deferred to process exit:

Takeover and identity-conflict paths are logged for auditability without message
payloads: accepted takeovers, cooldown refusals, name conflicts, and name-switch
denials include the sender name, remote host, and close reason.

```bash
synapse health                       # exit 0 if the local hub is reachable
synapse health --uri ws://host:8876
```

`synapse health` prints nothing by design — it reports only through its exit code,
so an empty response is a pass, not a failure. Read `$?` (or rely on the container
healthcheck) rather than the output. When you want a human-readable account of what
is or is not wired — identity, hub exposure, waiters — run `synapse doctor` instead;
`health` answers "is the hub up?", `doctor` answers "is my setup right?".

## Selecting the hub

Every client command talks to `ws://localhost:8876` by default. To point the
whole CLI at another hub — a remote coordinator, or a second local hub on another
port — set `SYNAPSE_URI` once instead of passing `--uri` to each command:

```bash
export SYNAPSE_URI=ws://coordinator.internal:8876
synapse who            # queries the hub named by SYNAPSE_URI
synapse board          # so does every other command
```

An explicit `--uri` on a single command overrides the environment for that call,
and unsetting `SYNAPSE_URI` (or leaving it blank) restores the loopback default.
The companion `SYNAPSE_TOKEN` supplies the shared secret for a secured hub the
same way.

## Worker options

```bash
synapse worker --name FAST --provider ollama --model gemma3:4b
synapse worker --name OFFLINE --provider rule
synapse worker --name TIER --provider tiered --model small --heavy-model big
synapse worker --prefix remanentia/ --name FAST --provider rule
```

A `tiered` worker classifies each request and routes trivial requests to a cheap
rule path and hard requests to the heavy model.

`--prefix` is prepended to `--name` to form the identity the worker registers
under (here `remanentia/FAST`), so the same role can run under several projects on
one hub without a name clash. `synapse team --prefix remanentia/` namespaces a
whole team the same way; address a namespaced worker by its full identity, for
example `synapse send --target remanentia/FAST "status?"`.

## Observing

```bash
synapse listen --name USER
synapse board
synapse manifest
synapse directory --task-class chat --json
synapse route-task TASK-1 --limit 3 --event-store ./synapse.db --json
synapse resource-bids TASK-1 --resource-kind gpu --json
synapse memory-recall ./synapse.db "transport handoff" --json
synapse a2a-card --endpoint-url https://agent.example.com/a2a/v1
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --bearer-auth --a2a-token "$A2A_TOKEN" --state-file ./a2a-state.json
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --task-timeout 300 --subscribe-timeout 1
synapse relay ./feed.ndjson --cursor ./feed.cursor
synapse compact ./synapse.db --all --max-checkpoints-per-task 3 --archive-report ./compact-report.html
synapse event-query ./synapse.db "task TASK-1 timeline"
synapse event-query ./synapse.db "conflicts at seq 120" --json
synapse event-query ./synapse.db "channel ops between seq 1 999999"
synapse event-query ./synapse.db 'timeline("TASK-1").'
synapse event-query ./synapse.db 'MATCH (task:TASK {id:"TASK-1"}) RETURN timeline'
synapse postmortem ./synapse.db TASK-1
synapse debug ./synapse.db --fork-at 142
synapse debug ./synapse.db --task TASK-1 --fork-at 142 --set status=blocked --json
synapse reproduce ./synapse.db TASK-1
synapse reproduce ./synapse.db TASK-1 --expect 9f2c… --json
synapse causality causes ./synapse.db 142
synapse causality effects ./synapse.db 118 --json
synapse causality counterfactual ./synapse.db 96
synapse merkle root ./synapse.db
synapse merkle prove ./synapse.db 142 --json > proof.json
synapse merkle verify proof.json --expect 9f2c…
synapse merkle verify proof.json --json
synapse reliability ./synapse.db
synapse accounting record --name alpha --task TASK-1 --model claude-opus-4-8 --input-tokens 1200 --output-tokens 300
synapse accounting report ./synapse.db --pricing pricing.json --budget budget.json
synapse approval request --name dev --subject TASK-1 --reason "needs human sign-off"
synapse approval decide --name ceo --subject TASK-1 --approve --reason "ship it"
synapse approval status ./synapse.db --pending
synapse ttl-advice ./synapse.db
synapse ingest ./synapse.db --cursor ./ingest.cursor    # drain new events as JSON lines, resumable
synapse multihub observe --peer-db ./peer.db --json     # fold a peer hub's log offline
synapse multihub follow --peer-uri ws://peer:8876       # pull a peer's board over a connection
synapse supervisor --idle-seconds 300 --history-multiplier 3
```

`synapse manifest` prints live capability cards advertised by connected agents.
When a card carries capability contracts, the readable output includes the
contract count while JSON surfaces such as the MCP manifest resource, A2A Agent
Card metadata, and dashboard snapshot retain the full `contracts` entries with
`task_class`, `input_schema`, `output_schema`, `preconditions`, and
`postconditions`.

`synapse directory` builds a read-only capability directory from the live
manifest plus resource offers from the state snapshot. It supports `--agent`,
`--task-class`, `--skill`, `--resource-kind`, and `--json`. The directory is
discovery metadata only: it helps route or review work, but it does not reserve
capacity, authorize execution, or certify trust.

`synapse route-task TASK-1` fetches the board, manifest, and state snapshots and
returns advisory routing recommendations for that board task. The scorer is
local and deterministic: task-class matches rank first, skill matches and card
description overlap add explainable evidence, and cards with contracts carry a
small evidence bonus. `--event-store ./synapse.db` optionally adds observed
capability evidence from positive release-receipt assessment notes in the
durable log, preserving the source task id and event sequence for review.
`--include-zero` shows unmatched agents for diagnostics; no route recommendation
claims the task, changes `suggested_owner`, reserves capacity, grades an agent,
or certifies trust.

`synapse resource-bids TASK-1` fetches the same board, manifest, and state
snapshots, then ranks live resource offers for the board task. The scorer is
local and deterministic: requested resource kind, capacity, provider task-class
matches, skill tags, provider description overlap, resource kind/name overlap,
and matching metadata all appear as reason codes in the output. `--resource-kind`
filters offers before scoring, `--include-zero` keeps diagnostic candidates, and
`--json` prints the stable report. A resource bid is a marketplace-style
directory hint only: it does not reserve capacity, authorize execution, mutate
the board, or certify provider trust.

`synapse memory-recall DB QUERY` reads the same local SQLite event store and
returns deterministic recall hits over durable memory records. The projection
uses findings, checkpoints, and handoffs, ignores recall-query telemetry, and
keeps each hit tied to its source sequence, timestamp, event kind, source field,
task id, actor, evidence reference, score, and matched tokens. `--since-seq`
limits the read to records above a cursor, `--limit` bounds output, and `--json`
prints the stable machine-readable report. This is local event-log projection:
it does not create external embeddings, contact a service, certify truth, or
mutate hub state.

`synapse supervisor` watches the shared board and re-offers stalled plan tasks.
The fixed `--idle-seconds` threshold remains the operator ceiling. By default the
supervisor can lower the effective threshold when completed tasks on the board
show enough faster progress cadence; `--history-multiplier`,
`--min-history-samples`, and `--min-predictive-idle-seconds` tune that heuristic,
and `--no-predictive-stall` disables it. This is local board evidence, not a
claim that a worker process has failed.

`synapse compact` is an offline maintenance command for the SQLite event store
created by `synapse hub --db`. It needs either `--floor-seq <seq>` (the lowest
sequence every read-side consumer has ingested) or `--all` (only when the whole
log is settled). `--archive-report PATH` writes an owner-only static HTML report
from the pre-compaction event snapshot, then records the actual checkpoint and
finding removal counts from the compaction run. The report includes event-kind
counts, board tasks, release receipt notes, and a bounded coordination timeline;
`--archive-report-limit N` controls the row cap for bounded sections.

`synapse event-query` is a temporal event-log query command for the same SQLite
event store. It supports `task <id> timeline`, `task <id> at seq <n>`,
`task <id> at time <seconds>`, `path <path> between <start> <end>`,
`channel <id> between seq|time <start> <end>`, and `conflicts at seq|time <n>`.
Channel queries return metadata-only records so private-channel bodies are not
printed by this forensic path. It also accepts prototype aliases over the same
model: Datalog-like `timeline("TASK").`, `state("TASK", seq, 120).`,
`touches("src/auth.py", 0, 9999999999).`, `channel("ops", seq, 1, 99).`,
`conflicts(seq, 120).`, plus
Cypher-like `MATCH (task:TASK {id:"TASK"}) RETURN timeline` and related
`AT`/`BETWEEN` forms. It is read-only forensic evidence: it reconstructs what
the event log said at a sequence or timestamp, but it does not contact the live
hub or certify that a merge is safe.

Queries read selectively rather than loading the whole store. Each query pushes
its sequence/time window and the event kinds it needs into SQLite, so a query
deserialises only candidate rows even as a dogfood event log grows large — the
result is identical to a full scan because the loaded window always contains
every event the query keeps. Memory is bounded by the query window, not the log
size: a point-in-time query (`at seq|time`) never loads events after its cutoff,
and a range query (`between`) loads only its window. `--limit N` additionally
caps the printed output to the most recent `N` records (and conflict pairs); it
bounds output, not the reconstruction, so task-state and conflict results stay
correct.

`synapse postmortem ./synapse.db TASK-1` builds a replayable postmortem from the
same event store. The Markdown or `--json` report lists the task timeline,
observed owners, release events, assessment evidence, reconstructed path-overlap
conflicts, and candidate unanswered messages that mention the task id. Candidate
unanswered messages are an audit signal only: the log proves the directed chat
and the absence of a later matching chat reply, not intent or off-channel
communication.

`synapse debug ./synapse.db --fork-at 142` forks a task's reconstructed state at
a sequence point. It folds the durable log back into the exact claim state the
task held as of that sequence — owner, status, declared paths, and the saved
resume checkpoint — then prints the resume manifest an agent would pick up if the
task were rewound there, alongside the events that really happened after the fork
point. The task is inferred from the snapshot at the sequence, or named with
`--task`; `--set FIELD=VALUE` overrides a resume field (`owner`, `note`,
`status`, `data_ref`, `worktree`, `checkpoint`) on the manifest only. It is a
read-only what-if: the hub runs no task, so nothing is executed or changed. Exit
status is `0` when the task held a live claim at the fork point, `1` when it did
not (released or never claimed — nothing to fork), and `2` on a bad argument or a
missing store.

`synapse reproduce ./synapse.db TASK-1` fingerprints a task's authoritative
history. Because hub state is a pure fold of an append-only log, a task's claim
snapshots and releases must replay to the same state on every machine; this
command canonicalises that slice and hashes it into a stable SHA-256 digest, so
two operators — or two federated hubs — holding the same history derive the same
digest. `--expect DIGEST` gates on a known-good value (exit `1` on mismatch, like
a release-receipt check); a task with no authoritative events exits `2`.

`synapse causality causes ./synapse.db 142` traces coordination causality over
the event log. It folds the durable log into a directed acyclic graph of three
recorded relations — a task's own lifecycle (claim before update before release),
a declared `depends_on` satisfied by the dependency's completion, and a release
that let a later, path-overlapping claim proceed — then answers against an event
sequence: `causes` returns the events upstream of it, `effects` the events it
enabled downstream, and `counterfactual` the downstream events whose recorded
cause traces back through it and so would lose all support if it were removed.
This is coordination causality inferred from recorded scheduling semantics, not
statistical causal discovery and not program-trace causality; every edge is
backed by a concrete event, and the counterfactual is a structural what-if over
the inferred graph, not a claim that the work would never have happened another
way. Exit status is `0` when the sequence names a coordination event, `1` when it
does not, and `2` on an unknown direction or a missing store.

`synapse merkle root ./synapse.db` commits the durable event log to a single
Merkle root: a 32-byte fingerprint of every event, so two operators — or two
federated hubs — holding the same log derive the same root and a mismatch proves
the logs differ (`--expect ROOT` gates on a trusted value, `--through SEQ` commits
only up to a sequence). `synapse merkle prove ./synapse.db 142` emits an `O(log
n)` inclusion proof — the sibling hashes that reconstruct the root from one
event's leaf — and `synapse merkle verify proof.json` checks such a proof offline
with no event store, the light-client verification a follower runs against a
trusted root (`--expect ROOT` also pins the root). The tree follows RFC 6962
(Certificate Transparency): leaves and interior nodes carry distinct
domain-separation prefixes, so a leaf hash cannot be forged as an interior node.
The commitment proves what the log contains — integrity and inclusion — not the
semantic correctness of the coordination it records. `root`/`prove` exit `2` on a
missing store and `prove` exits `1` when no event has that sequence; `verify`
exits `0` valid, `1` on a bad proof or root mismatch, `2` on an unreadable file.
`verify` reports through its exit code and a stderr line by default; pass `--json`
to get a `{"valid", "seq", "root"}` verdict on stdout instead (with a `reason` when
invalid), matching the `--json` stdout payload that `root` and `prove` already
carry.

`synapse reliability ./synapse.db` builds evidence-only reliability memory from
the same event store. It counts stale claims, declared failed-check evidence,
broken handoff candidates, and reconstructed conflict pairs per owner. The
output is audit signals, not scores: it does not rank agents, assign trust
grades, or prove intent.

`synapse accounting` records and reports opt-in model cost/token usage. Synapse
never calls a model provider and collects no telemetry, so token and cost figures
exist only when an agent or operator records them: `synapse accounting record`
posts one usage note (a `usage`-kind progress note carrying a canonical
`key=value` body) onto the shared ledger, and `synapse accounting report
./synapse.db` reads those notes back into per-agent and per-model totals. Pass
`--pricing pricing.json` (model → `{input_per_1k, output_per_1k}`) to estimate
cost from tokens, and `--budget budget.json` (agent → ceiling) for budget
evidence. Budgets are evidence, not an enforcement gate: the report states spend
against a ceiling, it does not block work. Non-Python clients can record usage by
posting the identical note body.

`synapse approval` runs a human-in-the-loop approval gate over the same ledger.
`synapse approval request --name <actor> --subject <id>` posts an
`approval`-kind note that puts the subject in `awaiting_approval`; `synapse
approval decide --subject <id> --approve|--reject [--reason ...]` records the
decision; and `synapse approval status ./synapse.db [--subject <id>] [--pending]
[--json]` replays the notes into the current decision state per subject (the
latest event wins, so a fresh request after a decision re-opens the gate). It is
advisory evidence and an audit trail, not a hard runtime gate — nothing blocks a
hub mutation. An approved subject can be cited in a release receipt via `synapse
release --approval "<id>: approved by <actor>"`.

`synapse ttl-advice ./synapse.db` builds read-only adaptive lease TTL advice from
the same event store. It derives completed-task duration samples, active
live-claim counts, and stale-claim counts, then prints an advisory default and
optional owner-specific rows when enough samples exist. The command does not
change the hub default, and explicit manual TTL values remain the control path.

`synapse multihub` reads a *peer* hub's event log rather than the local one.
`multihub observe --peer-db ./peer.db` folds a peer's log file offline into its
board and claims; `multihub follow --peer-uri ws://peer:8876` pulls the same
snapshot from a live peer over a connection. Both are read-only observations
tagged with a peer id and neither mutates the local hub. See
[Multi-hub sync](multi-hub-sync.md) for the federation and trust model.

## Agent2Agent bridge

`synapse a2a-card` projects the live SYNAPSE capability manifest into an A2A
Agent Card. `synapse a2a-serve` runs the local HTTP+JSON bridge and keeps A2A at
the edge of the system; the hub remains WebSocket-native.
The bridge is an interop surface for A2A-shaped clients, not a replacement for
orchestration frameworks, coding agents, or the native SYNAPSE hub protocol.

Supported local subset:

- `GET /.well-known/agent-card.json` and `/agent-card.json` for discovery.
- `POST /message:send` to create a bridge task and forward text/data/file parts
  into SYNAPSE chat.
- `POST /message:stream` for an immediate Server-Sent Events task snapshot.
- `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}:cancel`, and
  `POST /tasks/{id}:subscribe` for bridge-local task lifecycle operations.
- `POST|GET|DELETE /tasks/{id}/pushNotificationConfigs[/config_id]` for stored
  push-notification configuration.
- `POST /rpc` for JSON-RPC 2.0 dispatch to the same operations.

Operational boundaries:

- Bearer auth is opt-in with `--bearer-auth --a2a-token "$A2A_TOKEN"` and applies
  to protected bridge routes. The public Agent Card remains public discovery.
- `--state-file` persists bridge tasks and push configs. Corrupt state files fail
  fast; non-terminal persisted tasks recover as failed on restart; failed writes
  roll back the in-memory task/config view.
- Terminal task states are immutable: cancel and late SYNAPSE replies do not
  reopen or rewrite completed, failed, canceled, or rejected tasks.
- `--task-timeout` marks open tasks failed when no correlated SYNAPSE reply arrives
  within the configured window.
- `--subscribe-timeout` bounds one in-process subscription wait. Subscriptions
  emit bounded local replay frames for the current bridge process, then at most
  one queued update for that wait. Persisted task recovery restores task
  snapshots only; it does not rebuild durable subscription streams across
  restarts or bridge replicas.
- Caller-supplied `taskId` and `contextId` values are restricted to bridge-safe
  characters. Duplicate caller task ids are rejected.
- Webhook URLs must be HTTP(S), include a host, omit embedded credentials, and not
  target localhost, loopback, private, or link-local IP literals.

State-file durability matrix:

| Case | Behavior | Focused coverage |
| --- | --- | --- |
| Clean restart | Tasks and push configs reload from `--state-file`. | `test_task_store_persists_tasks_and_push_configs` |
| Corrupt JSON | Startup fails fast with `Invalid A2A state file`. | `test_task_store_reports_corrupt_state_file` |
| Atomic write | Writes go through an owner-only temp file, fsync the file, replace the state file, and best-effort fsync the parent directory. | `test_a2a_task_store_fsyncs_state_file_and_parent_directory` |
| Failed write | In-memory task/config changes roll back; the previous committed state file is left intact. | `test_a2a_task_store_keeps_committed_state_file_when_temp_write_fails` |
| Stale in-flight task | Persisted non-terminal tasks recover as failed on restart. | `test_state_file_recovery_fails_stale_working_tasks` |
| Push config recovery | Push configs persist, reload, list, get, delete, and roll back failed writes/deletes. | `test_a2a_task_store_push_config_get_list_delete_paths` |

Bounded local soak coverage:

| Path | Local evidence | Limit |
| --- | --- | --- |
| Network handler churn | Sixteen real localhost `POST /message:send` requests through one stdlib HTTP server persist and reload from a state file. | This is not a latency or throughput benchmark. |
| Persistence churn | The same run exercises repeated fsynced state writes under a fixed task cap. | It does not simulate power loss or filesystem faults beyond focused write-failure tests. |
| Webhook failure pressure | Twelve task completions continue while configured webhook deliveries raise timeout errors. | It uses an injected failing deliverer, not a remote receiver. |
| Subscriber fanout | Twelve concurrent subscribers receive the terminal update and the bridge clears subscriber queues. | It is bounded local thread pressure, not multi-process soak. |

Unsupported or externally gated:

- No claim is made here about third-party A2A conformance until remote CI,
  independent interoperability/conformance tests, real webhook receiver tests,
  deployment threat-model review, and operator sign-off complete.
- The bridge does not make SYNAPSE itself an A2A-native hub; it is a separate edge
  process translating between A2A-shaped HTTP operations and SYNAPSE chat/tasks.
- Subscription replay is local process memory. It is not a durable event log shared
  across bridge restarts or multiple bridge replicas, and terminal recovered
  tasks reject subscription with a problem response.

## Managing the task plan

`synapse task` lets a human drive the shared blackboard from the command line —
the persistent plan, not the live leases (claiming/holding a lease belongs to a
running agent, since a lease is released when its holder disconnects):

```bash
synapse task declare BUILD --title "Compile the package"
synapse task declare TEST --title "Run the suite" --depends-on BUILD
synapse board                                  # BUILD ready, TEST blocked on it
synapse task update BUILD --status done        # TEST now unblocks
synapse task progress TEST "started" --kind note
syn ack TEST --evidence "pytest tests/test_feature.py -q" --artifact coverage.xml
```

The hub bounds the in-memory blackboard and memory-admission surfaces with
operator-set limits: `--max-progress` for the total retained progress notes,
`--max-progress-per-author` for one author, `--max-progress-per-task` for one
task id, and `--max-findings-per-agent` for durable findings admitted by one
agent. These limits apply on live writes and on `--db` replay; the append-only
event log still retains accepted events until `synapse compact` removes safe
history.

`syn ack <task>` is the ergonomic closeout path for a completed board task. It
requires at least one `--evidence` or `--artifact` value, writes those values as an
`assessment` progress note from the resolved `syn` identity, waits for the hub's
progress confirmation, marks the task `done`, and waits for the task-update
confirmation before printing success. Use repeated flags when a task has several
proof points:

```bash
syn ack TEST \
  --evidence "pytest tests/test_feature.py -q" \
  --evidence "mypy src/synapse_channel/feature.py" \
  --artifact coverage.xml \
  --note "ready for release"
```

## Setup and terminal integration

These commands wire a machine and its terminals into the bus once, so agents
arm themselves and coding tools claim work without per-session setup. `init`
prints or installs the local user services; `install-shell-hook` adds an
auto-arming block to your shell rc (idempotent — re-running it replaces the
guarded block rather than appending); `shell-hook` prints that block for manual
sourcing; `arm` keeps a single waiter re-armed; and `adapters` wires detected
coding tools to the claim-aware hooks.

```bash
synapse init --project my-repo                          # print the hub/waiter/presence user units
synapse init --project my-repo --install-user-services  # install them under systemd --user
synapse install-shell-hook                              # auto-arm Bash, Zsh, and Fish on new terminals
synapse install-shell-hook --shell fish                 # only the Fish integration
synapse shell-hook                                      # print the block instead of installing it
synapse completions bash > ~/.local/share/bash-completion/completions/synapse   # tab completion
synapse completions fish > ~/.config/fish/completions/synapse.fish              # (re-run after upgrades)
synapse arm --name my-repo --for "my-repo,my-repo/*"    # keep a waiter armed, re-arming after each wake
synapse arm --name my-repo --owner-pid $$               # leash the waiter: disarm when this shell exits
synapse worker-session --identity my-repo -- codex      # run a provider CLI with SYN_PROJECT/SYN_IDENTITY set
synapse adapters list                                   # detect coding tools and report adapter status
synapse adapters install --project my-repo              # write the claim-aware adapter into each tool
```

## Governance and integrity

Governance commands are advisory by default — they report, they do not block —
so a rollout can observe before it enforces. `identity audit` inventories
declared identities for enforcement-rollout blockers; `acl shadow` evaluates
candidate accesses deny-by-default without denying anything live; `policy-check`
scores a release receipt against a policy (`--enforce` to gate); and `federation`
manages out-of-band, operator-signed peer-domain bundles for cross-hub trust.

```bash
synapse identity audit --identities ./identities.json          # audit declared identities for blockers
synapse identity audit --identities ./identities.json --json
synapse acl shadow --policy ./acl.json --requests ./requests.json   # non-blocking deny-by-default evaluation
synapse policy-check --policy ./policy.json --receipt-json ./receipt.json   # advisory; --enforce to gate
synapse federation import ./peer-domain.json --confirmed-by ceo # trust an operator-signed peer bundle
synapse federation list --store ./federation.json              # imported peer domains and their provenance
synapse federation revoke example.org --store ./federation.json
synapse encrypt-key generate ./synapse.key                     # write a fresh owner-only 32-byte key file
synapse encrypt-key check ./synapse.key                        # verify its ownership, mode, and length
```

## Experimental surfaces

Newer, advisory surfaces whose shape may still change before 1.0. `sandbox`
validates a capability manifest and pre-flights or runs a `.wasm` tool against
it (running needs the `wasm` extra); `workflow` validates a declarative workflow
and compiles it into the blackboard tasks the board would execute; `participant`
is the operator surface over the Participant Fabric — `list` probes each
registered provider driver (claude, codex, kimi, ollama, ollama-api, grok)
without taking a turn, and `ask` runs exactly one turn against one provider and
prints the answer, or the full typed turn result with `--json`. Grok turns stay
refused while its stream schema is unverified against a real binary. `--model`
is required for `ollama` and `ollama-api` (their drivers configure no default).

The deliberation subcommands drive the Fabric's multi-party layers from the same
surface, naming each seat as `PROVIDER[:MODEL]` (the model part may itself hold
colons, so `ollama:gemma3:1b` works). `exchange` runs an opener turn and then a
reactor turn that sees the opener's result only as fenced peer data; `convene`
fans a question out to a whole panel, runs the mode's cross-critique rounds
(`--mode` defaults to `auto`, choosing colloquy, roundtable, or symposium from
the panel size and whether `--moderator` was given), and in a symposium ends
with the moderator's synthesis. A repeated provider gets numbered seats
(`participant/claude`, `participant/claude-2`, …). Both print each turn as it is
produced, or the full typed transcript with `--json`, and exit `0` only when
every turn answered and the run completed — an unavailable seat, a degraded
turn, or a `--budget-usd` halt exits `1`; a refused configuration exits `2`.

```bash
synapse sandbox validate ./manifest.json                # validate a capability manifest
synapse sandbox test ./tool.wasm --manifest ./manifest.json    # pre-flight without running
synapse sandbox run ./tool.wasm --manifest ./manifest.json --input ./in.json
synapse workflow validate ./workflow.json               # parse and validate
synapse workflow compile ./workflow.json --json         # compile into blackboard tasks
synapse workflow plan ./workflow.json                   # show the tasks and their dependency order
synapse participant list                                # readiness of every provider driver
synapse participant ask ollama "summarise this diff" --model llama3   # one turn, print the answer
synapse participant ask claude "review src/foo.py" --context "be terse" --json
synapse participant exchange "is this design sound?" claude codex   # opener + reviewing reactor
synapse participant convene "how should we cache this?" claude codex ollama:gemma3:1b \
    --mode symposium --moderator claude --budget-usd 2.50          # panel + moderated synthesis
```

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
`manifest`, `release`, `a2a-card`, `a2a-serve`, and `task`.

Run any command with `--help` for its full set of options.
