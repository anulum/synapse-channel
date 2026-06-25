# CLI reference

The `synapse` command exposes the following subcommands.

| Command | What it does |
| --- | --- |
| `synapse hub` | Run the coordination hub. |
| `synapse health` | Probe the hub; exit `0` if reachable, `1` if not (wired as a container healthcheck). |
| `synapse worker` | Run a model worker that answers on the channel. |
| `synapse team` | Launch a hub plus one or two local workers in one shot. |
| `synapse mcp` | Serve the hub to MCP-compatible agents over stdio (see [MCP server](mcp.md)). |
| `synapse a2a-card` | Print an Agent2Agent Agent Card projected from the live capability manifest. |
| `synapse send` | Connect, send one message, optionally await replies, and exit. |
| `synapse wait` | Block until a message addressed to you arrives, then exit (a wake trigger). |
| `synapse listen` | Connect and stream channel messages until interrupted. |
| `synapse relay` | Decode and print a lite relay log a hub mirrored to a file. |
| `synapse board` | Print the shared task/progress blackboard. |
| `synapse supervisor` | Run an LLM-free supervisor that re-offers stalled tasks. |
| `synapse manifest` | Print the capability manifest of advertised agents. |
| `synapse who` | List the agents currently online, optionally for one project. |
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
synapse send --target quantum/* "rebasing main now"   # the whole project team
```

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
synapse hub --host 0.0.0.0 --token-file ./tok      # token from a file, not argv (ps-safe)
synapse hub --host 0.0.0.0 --insecure-off-loopback # bind off-loopback WITHOUT a token (refused otherwise)
```

Binding a non-loopback host without a token (and, with `--metrics`, a metrics
token) is **refused** by default — the hub will not start exposed by accident;
`--insecure-off-loopback` downgrades that to a warning for a trusted private
network. Supply the token with `--token-file` or the `SYNAPSE_TOKEN` environment
variable rather than `--token`, which is visible in `ps`. The hub drains on `SIGTERM`/`SIGINT`,
so a container stop shuts it down cleanly. `synapse health` is a liveness probe —
exit `0` when the hub answers, `1` otherwise — wired as the Docker `HEALTHCHECK`:

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
synapse relay ./feed.ndjson --cursor ./feed.cursor
```

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
```

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
`manifest`, `a2a-card`, and `task`.

Run any command with `--help` for its full set of options.
