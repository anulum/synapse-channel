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

## Boundaries

- **The blackboard is the executor.** A workflow compiles to ordinary tasks with
  `depends_on` edges; it adds no scheduler and no new transport.
- **Single-dependency, local-first.** The artifact is plain JSON parsed with the
  standard library; nothing new is pulled into the core.
- **Authoring only, today.** `validate` and `compile` are offline tools. Driving a
  compiled workflow — posting it and routing each ready task to a capable agent —
  is the workflow driver, a separate step in this lane.
