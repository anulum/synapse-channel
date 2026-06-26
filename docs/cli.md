# CLI reference

The `synapse` command exposes the following subcommands.

| Command | What it does |
| --- | --- |
| `synapse hub` | Run the coordination hub. |
| `synapse demo` | Run a self-contained local coordination demo and print a success marker. |
| `synapse quickstart-coding` | Create a coding-fleet workspace, run the no-collision demo, and print a success marker. |
| `synapse new coding-fleet` | Scaffold a runnable two-agent coding demo workspace. |
| `synapse health` | Probe the hub; exit `0` if reachable, `1` if not (wired as a container healthcheck). |
| `synapse worker` | Run a model worker that answers on the channel. |
| `synapse team` | Launch a hub plus one or two local workers in one shot. |
| `synapse mcp` | Serve the hub to MCP-compatible agents over stdio (see [MCP server](mcp.md)). |
| `synapse a2a-card` | Print an Agent2Agent Agent Card projected from the live capability manifest. |
| `synapse a2a-serve` | Run the stdlib HTTP+JSON Agent2Agent bridge. |
| `synapse send` | Connect, send one message, optionally await replies, and exit. |
| `synapse wait` | Block until a message addressed to you arrives, then exit (a wake trigger). |
| `synapse listen` | Connect and stream channel messages until interrupted. |
| `synapse relay` | Decode and print a lite relay log a hub mirrored to a file. |
| `synapse board` | Print the shared task/progress blackboard. |
| `synapse supervisor` | Run an LLM-free supervisor that re-offers stalled tasks. |
| `synapse manifest` | Print the capability manifest of advertised agents. |
| `synapse who` | List the agents currently online, optionally for one project or this identity with `--me`. |
| `synapse state` | Print active claims and their checkpoints (a resume view). |
| `synapse doctor` | Check for common coordination misconfigs (identity, exposure, hub, waiter); exit non-zero on a failure. |
| `synapse git-init` | One-step claim-aware setup: install the hooks and write a `.synapse/` conventions guide. |
| `synapse git-claim` | Claim work scoped to the current git branch (see [Git-native claims](git-claims.md)). |
| `synapse git-hook` | Install post-commit/post-merge hooks that auto-release a commit's claims. |
| `synapse git-release` | Release the claims whose paths a commit or merge just touched. |
| `synapse conflicts` | Predict cross-branch merge conflicts between overlapping claims; exit non-zero on a hit. |
| `synapse lock` | Hold a lease while running a command, to serialise it across agents. |
| `synapse release` | Manually drop a claim you own (e.g. an `--auto-release-on manual` claim). |
| `synapse task` | Declare and update the shared task plan. |

## First 60 seconds

The installed CLI has a source-checkout-free validation path:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
```

`synapse doctor` reports local wiring issues. On a fresh machine, a missing hub or
waiter can be a warning before services are installed. `synapse demo` starts an
ephemeral local hub, drives a planner/worker flow, and is successful when it
prints:

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

## Recovery: picking up after a restart

Nothing is lost when a terminal or session goes down — the feed, the plan, and the
event log are durable. On return, catch up everything for your repo regardless of
the instance id you now run as:

```bash
synapse relay ./feed.ndjson --project quantum --cursor ./quantum.cursor  # missed messages
synapse board                                                           # the current plan
synapse state --owner quantum                                           # your claims + resume checkpoints
synapse who --project quantum                                           # who is live now
```

A lapsed claim keeps its checkpoint, so re-claiming the task resumes from it rather
than restarting.

## Identities and groups

An identity is a name; when several agents share a project they use composite
names `<project>/<agent>`, e.g. `quantum/claude-7f3a` and `quantum/codex-2b40`.
A `target` is then a name, a comma list, a **group glob** (`quantum/*` for every
agent on the project, `quantum/claude-*` for one role), or `all`. List who is live:

```bash
synapse who                       # every agent online
synapse who --project quantum     # only quantum/... instances
synapse who --name quantum/codex-2b40 --me  # this identity plus its -rx waiter status
syn who --me                      # same check using the resolved syn identity
syn reap                          # list this identity's shell-hook waiter pidfile
syn reap --pid 1234               # clean up only that verified identity waiter PID
syn locks                         # list this project's leases, scopes, ages, and release commands
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

`synapse git-claim` accepts the task id either positionally (`synapse git-claim
TASK-1 --paths src`) or as a named field (`synapse git-claim --task-id TASK-1
--paths src`) for generated argv. Use one form, not both. `synapse git-release`
is hook-invoked and does not take a task id; when a manual drop is needed, use
`synapse release <task> --name <owner>`.

Use `syn locks` for the operator view before releasing or asking another owner to
release. It queries the live state snapshot as `<identity>-locks`, filters to the
resolved project by default, and prints the task id, holder, scope, age, remaining
lease time, checkpoint, git branch context, and the exact `synapse release ...`
command. `syn locks --all` removes the project filter; `syn locks --owner <name>`
shows one owner or project namespace; `syn locks --json` emits the same rows as
JSON.

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
```

If a one-shot send accidentally uses a waiter name such as `api-dev-rx`, the
command sends as `api-dev` instead. That keeps the persistent wake socket online
and avoids the hub's duplicate-name refusal for the short-lived sender.

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
synapse hub --host 0.0.0.0 --token-file ./tok      # token from a file, not argv (ps-safe)
synapse hub --host 0.0.0.0 --insecure-off-loopback # bind off-loopback WITHOUT a token (refused otherwise)
```

Binding a non-loopback host without a token (and, with `--metrics`, a metrics
token) is **refused** by default — the hub will not start exposed by accident;
`--insecure-off-loopback` downgrades that to a warning for a trusted private
network. `--max-connections-per-host` is a connection-count cap keyed by the
remote host; it is separate from `--host-rate`, which meters inbound frames from
that host. Supply the token with `--token-file` or the `SYNAPSE_TOKEN`
environment variable rather than `--token`, which is visible in `ps`. The hub
drains on `SIGTERM`/`SIGINT`, so a container stop shuts it down cleanly. `synapse
health` is a liveness probe — exit `0` when the hub answers, `1` otherwise —
wired as the Docker `HEALTHCHECK`:

```bash
synapse health                       # exit 0 if the local hub is reachable
synapse health --uri ws://host:8876
```

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
synapse a2a-card --endpoint-url https://agent.example.com/a2a/v1
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --bearer-auth --a2a-token "$A2A_TOKEN" --state-file ./a2a-state.json
synapse a2a-serve --endpoint-url http://127.0.0.1:8877 --task-timeout 300 --subscribe-timeout 1
synapse relay ./feed.ndjson --cursor ./feed.cursor
```

## Agent2Agent bridge

`synapse a2a-card` projects the live SYNAPSE capability manifest into an A2A
Agent Card. `synapse a2a-serve` runs the local HTTP+JSON bridge and keeps A2A at
the edge of the system; the hub remains WebSocket-native.

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

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
`manifest`, `a2a-card`, `a2a-serve`, and `task`.

Run any command with `--help` for its full set of options.
