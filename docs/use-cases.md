# Use cases

SYNAPSE earns its place the moment **more than one agent acts at the same time** and their
work can overlap. Below are the situations it is built for, the situations where it is
overkill, and who tends to reach for it.

## When it fits

### Several agents editing one repository in parallel

Two or three coding agents on the same codebase will, left alone, edit the same file or both
pick up the same task. Each agent **claims** a file scope before touching it; the hub refuses
overlapping live authority. Documented provider hooks enforce their covered edit paths, and
the staged Git gate checks the index separately. The shared plan and a stall supervisor keep
the work flowing, and `synapse state` shows who holds what.

> Pattern: every agent obtains a confirmed claim for its files, works, releases
> through its configured closeout flow, and reads `synapse board` to pick a ready
> task. Git auto-release defaults to merge; a commit trigger must be explicitly
> selected and does not make an unconfirmed release successful.

### A fleet of agents across many repositories

When a project is split over several repos, each with its own agent, they still need one
plan, one roster, and one inbox. Agents address each other across projects (`project/agent`
identities, group globs, broadcasts), declare cross-repo task dependencies on the shared
blackboard, and a done task unblocks its dependents. On this shared-hub profile,
that hub owns the admitted coordination state. Multi-hub federation has
separate peer, trust and synchronization boundaries.

### Event-driven agents that wake on a message instead of polling

An idle agent should not burn a turn every few seconds checking for work. It arms a
`--directed-only` waiter waits for a qualifying message, then exits. A configured
harness or active waker delivers the prompt to the provider. Waiting itself
uses no model turn, but keeps a process and connection; its receipt does not
establish that the model read or acted on the message.

### Human-in-the-loop coordination

A person drops into the same channel with the `syn` commands — `syn say` to direct an agent,
`syn inbox` to read replies, `syn board` to see the plan — using the same identity-correct
routing the agents use. Humans and agents share one coordination surface.

### Coordination that must survive a crash

With a durable event log (`synapse hub --db …`), restart replays admitted,
retained and valid state under the configured lease, retention and integrity
rules. Supported operations with caller-retained idempotency keys can replay
their exact committed response. This recovery does not restore unrecorded
file edits or guarantee that every chat survives every failure.

### Routing a task to the right backend

Workers advertise **capability cards** (task classes, model, description). A request is routed
by class, and a `tiered` worker sends trivial work to a cheap rule backend and hard work to a
heavier model — so cost follows difficulty.

### Serialising a shared side effect across agents

When several agents must take turns at one resource — a deploy, a migration, a shared file —
`synapse lock` starts its managed command after a confirmed lease and attempts
a bounded release when the command exits. It does not renew or monitor the lease
while the child runs: a command that outlives the hub's lease TTL can overlap a
new owner's command. Conflicting participating owners are coordinated only while
the original lease remains live. Platform-specific process cleanup and external
effects remain separate boundaries; an arbitrary program ignoring the claim is
not fenced from the resource. See the [lock contract](cli.md).

## When it is overkill

- **A single agent with no parallelism.** If only one process ever acts, there is nothing to
  coordinate; the bus adds a hub you do not need.
- **A fully in-process workflow.** If your agents are steps in one program's control flow, an
  orchestration framework models that directly — SYNAPSE coordinates *between* independent
  processes, not within one. (You can still use both; see [comparison](comparison.md).)
- **Untrusted multi-tenant agents.** Configured identity binding, per-message
  authentication, ACLs and private routing have distinct documented controls.
  They do not create host isolation or a complete tenant IAM boundary.
  Shared-secret connect authentication alone does not distinguish mutually
  distrusting agents.

## Who reaches for it

- A developer running **two or three coding agents** on one repository who is tired of merge
  collisions and duplicated work.
- A team operating a **fleet of agents over several services**, who need one plan, one roster,
  and cross-repo task dependencies.
- A builder of **long-running autonomous agents** who wants event-driven wakeups, durable
  coordination, and a resume-after-restart story rather than a polling loop and lost state.

Ready to try one of these? Start with the self-contained installed demo in
[quick start](quickstart.md), then select the required persistent hub, provider
and wake profile. The [recipes](recipes.md) and [examples](examples.md) show
their respective coordination paths.
