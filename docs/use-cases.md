# Use cases

SYNAPSE earns its place the moment **more than one agent acts at the same time** and their
work can overlap. Below are the situations it is built for, the situations where it is
overkill, and who tends to reach for it.

## When it fits

### Several agents editing one repository in parallel

Two or three coding agents on the same codebase will, left alone, edit the same file or both
pick up the same task. Each agent **claims** a file scope before touching it; the hub refuses
an overlapping claim, so their working sets never intersect. The shared plan and a stall
supervisor keep the work flowing, and `synapse state` shows who holds what.

> Pattern: every agent `claim`s its files, works, `release`s on commit (via the git hooks),
> and reads `synapse board` to pick the next ready task.

### A fleet of agents across many repositories

When a project is split over several repos, each with its own agent, they still need one
plan, one roster, and one inbox. Agents address each other across projects (`project/agent`
identities, group globs, broadcasts), declare cross-repo task dependencies on the shared
blackboard, and a done task unblocks its dependents. One hub is the federation's source of
truth.

### Event-driven agents that wake on a message instead of polling

An idle agent should not burn a turn every few seconds checking for work. It arms a
`--directed-only` waiter that **blocks** until a message addressed to it arrives, then exits —
which re-invokes the agent. No polling, no wasted cycles; the connection holds presence while
it waits.

### Human-in-the-loop coordination

A person drops into the same channel with the `syn` commands — `syn say` to direct an agent,
`syn inbox` to read replies, `syn board` to see the plan — using the same identity-correct
routing the agents use. Humans and agents share one coordination surface.

### Coordination that must survive a crash

With a durable event log (`synapse hub --db …`), a hub restart **resumes** active leases, the
plan, and history rather than losing them — so a long multi-agent run is not undone by a
process bounce. Reconnect-safe idempotency means a replayed claim or release does not
double-apply.

### Routing a task to the right backend

Workers advertise **capability cards** (task classes, model, description). A request is routed
by class, and a `tiered` worker sends trivial work to a cheap rule backend and hard work to a
heavier model — so cost follows difficulty.

### Serialising a shared side effect across agents

When several agents must take turns at one resource — a deploy, a migration, a shared file —
`synapse lock` holds a lease for the duration of a command, so only one runs it at a time.

## When it is overkill

- **A single agent with no parallelism.** If only one process ever acts, there is nothing to
  coordinate; the bus adds a hub you do not need.
- **A fully in-process workflow.** If your agents are steps in one program's control flow, an
  orchestration framework models that directly — SYNAPSE coordinates *between* independent
  processes, not within one. (You can still use both; see [comparison](comparison.md).)
- **Untrusted multi-tenant agents.** The bus trusts the agents it coordinates; it does not
  sandbox them or provide cryptographic per-agent identity. Do not use the shared-secret
  connect auth as an isolation boundary between mutually distrusting parties.

## Who reaches for it

- A developer running **two or three coding agents** on one repository who is tired of merge
  collisions and duplicated work.
- A team operating a **fleet of agents over several services**, who need one plan, one roster,
  and cross-repo task dependencies.
- A builder of **long-running autonomous agents** who wants event-driven wakeups, durable
  coordination, and a resume-after-restart story rather than a polling loop and lost state.

Ready to try one of these? The [quick start](quickstart.md) launches a hub and two workers in
one command; the [recipes](recipes.md) and [examples](examples.md) show each pattern end to end.
