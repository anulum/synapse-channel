<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Declarative workflows

Synapse is an orchestration layer *of sorts* — not a workflow engine, but a way
to declare a multi-step plan and let the existing blackboard run it. The
blackboard already executes a task graph: a task with unmet `depends_on` edges is
blocked, and it becomes ready when every dependency reaches a terminal status. A
**workflow** is the authoring layer on top: a plain JSON artifact that compiles to
those blackboard tasks. There is no new runtime and no new dependency — the
board's own ready/blocked derivation is the executor.

## The artifact

A workflow is a `name` and a list of `steps`. Each step has an `id`, a `title`, an
optional `task_class` (a routing hint), an optional `description`, and a list of
`depends_on` step ids:

```json
{
  "name": "release",
  "steps": [
    { "id": "build", "title": "Build the wheel", "task_class": "ci" },
    { "id": "test", "title": "Run the suite", "depends_on": ["build"] },
    { "id": "publish", "title": "Publish", "task_class": "release", "depends_on": ["test"] }
  ]
}
```

## Validate and compile

```bash
synapse workflow validate release.json
synapse workflow compile release.json          # human summary
synapse workflow compile release.json --json   # task declarations as JSON
```

`validate` parses and checks the artifact. `compile` turns it into the blackboard
task declarations the board would execute:

```text
3 blackboard tasks:
  release/build [ci] <- (none)
  release/test <- release/build
  release/publish [release] <- release/test
```

Each step becomes one task whose id is namespaced by the workflow name
(`release/build`), with `depends_on` remapped to the namespaced ids. Tasks are
emitted in dependency order, so a step always appears after the steps it waits on.
The `task_class` is carried through compilation as a routing hint for a driver; it
is not stored on the blackboard task itself.

## Validation is strict

A workflow is rejected at authoring time — before anything reaches the board — if
it cannot make progress:

- a **duplicate** step id;
- a step that **depends on itself**;
- a `depends_on` that references an **unknown** step;
- a **cycle** in the dependency graph (a workflow with a cycle would deadlock the
  board, so it is refused, naming a step on the cycle).

## Conditional edges — branching on outcome

A plain dependency waits for a step to *finish*: a task is ready once every
dependency reaches a terminal status (`done` **or** `cancelled`). A **conditional**
edge waits for a specific outcome instead, so a workflow can branch on result. Write
a dependency as an object with an `on` (`done` or `cancelled`) rather than a bare id:

```json
{
  "name": "release",
  "steps": [
    { "id": "test", "title": "Run the suite" },
    { "id": "deploy", "title": "Deploy", "depends_on": [{ "step": "test", "on": "done" }] },
    { "id": "rollback", "title": "Roll back", "depends_on": [{ "step": "test", "on": "cancelled" }] }
  ]
}
```

Here `deploy` runs only if `test` finishes `done`, and `rollback` only if `test` is
`cancelled` — the two are mutually exclusive branches. `compile` shows the condition
on the edge:

```text
release/deploy <- release/test:done
release/rollback <- release/test:cancelled
```

The condition is **enforced by the driver, not the board**: the board still sees a
plain `depends_on` edge (so it gates on terminal-ness), while the driver checks
whether the recorded outcome actually matches. When a branch can never fire — `test`
finished `done`, so `rollback`'s `on: cancelled` is unreachable — the driver retires
that step by cancelling it on the board, which keeps the graph moving and lets any
downstream steps resolve. An unconditional edge keeps its original meaning: any
terminal status of the dependency satisfies it.

## Driving a workflow

Given a board snapshot, `synapse workflow plan` works out what to do next: which
tasks are done, in flight, ready, blocked, or skipped (a branch not taken), and
which ready tasks to hand to which agents.

```bash
synapse workflow plan release.json \
  --status status.json \    # {"release/build": "done"} — board task statuses
  --agents agents.json \    # {"alice": ["ci"], "bob": []} — agents and the classes they handle
  --max-in-flight 4
```

```text
state: 1 done, 0 in flight, 1 ready, 0 blocked
assignments:
  release/test -> alice
```

The planner recomputes readiness from dependencies (a task is ready only when all
of its dependencies are terminal), routes each ready task to a free agent that
advertises its `task_class` (an unclassified task can go to anyone), and never
exceeds the in-flight budget. It is a pure function over the compiled workflow and
the board snapshot, so it is deterministic and replayable.

## Running a workflow live

`synapse workflow run` is the autonomous loop around that planner. It connects to
the hub, posts the compiled tasks once, then on every board reading re-derives the
state and routes the ready steps by writing each task's `suggested_owner` — an
*advice*, never a forced assignment. It stops as soon as every task is terminal, or
once the deadline passes.

```bash
synapse workflow run release.json \
  --agents agents.json \      # {"alice": ["ci"], "bob": []} — the candidate worker pool
  --max-in-flight 4 \
  --poll-interval 1.0 \       # seconds between board readings
  --deadline 120              # seconds to keep driving before giving up
```

```text
workflow complete after 3 board reads
assignments made:
  release/build -> alice
  release/test -> alice
```

The loop is **advisory and idempotent**: it only suggests owners, so workers stay
free to pick up whatever they choose, and a task already advising the chosen agent
is never re-written. It is **resumable** — it routes from whatever the board
currently reports, so a driver restarted mid-run simply continues from the live
state. And it is **bounded** twice over: by `--max-in-flight` (how much work it will
advise at once) and by `--deadline` (how long it will run). The decision logic is
the pure planner above; `run` adds only the connect-post-read-assign shell.

## Boundaries

- **The blackboard is the executor.** A workflow compiles to ordinary tasks with
  `depends_on` edges; the planner only decides assignments. It adds no scheduler
  and no new transport.
- **Single-dependency, local-first.** The artifact is plain JSON parsed with the
  standard library; nothing new is pulled into the core.
- **Bounded routing.** The planner hands out at most `--max-in-flight` tasks and
  one task per agent per round — work-handing, never a flood.
