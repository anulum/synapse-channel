<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Examples

Runnable, self-contained demos. Each one starts its own in-process hub, so
nothing needs to be running first. After installing the package, the source-checkout-free
first 60 seconds path is:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
```

The installed demo succeeds when it prints:

```text
success: coordination demo completed
```

`synapse quickstart-coding` creates a temporary coding-agents workspace, runs it,
removes the temporary workspace after success, and prints `success: coding fleet
demo completed`.

From a source checkout, the same demo code is also available as scripts:

```bash
python examples/coordination_demo.py
python examples/llm_team_demo.py
```

To generate the same coding-agents demo as an editable workspace after install:

```bash
synapse new coding-fleet ./demo-fleet
cd ./demo-fleet
python run_demo.py
```

## `coordination_demo.py`

A narrated walk through the coordination plane: a planner declares a plan on the
blackboard, a dependent task stays blocked, a worker claims a file scope, an
overlapping claim is refused, the plan task finishes so the dependent unblocks,
and the task is handed off — all against a live hub. Expected output:

```
• Two agents are online: PLANNER and WORKER.
• PLANNER declared BUILD and TEST (TEST depends on BUILD).
• Board ready set: ['BUILD']  (TEST waits on BUILD)
• WORKER claimed BUILD with a file scope over src/.
• PLANNER's overlapping claim on src/app.py was refused: ...
• WORKER finished BUILD (checkpoint saved); board ready set: ['TEST']
• WORKER handed TEST off to PLANNER with no release/re-claim gap.
```

## `llm_team_demo.py`

Ask an on-channel model worker a question and print its reply. It uses a local
Ollama model when one is reachable (a real answer) and falls back to the
deterministic offline backend otherwise, so the demo runs anywhere.

## `coding_agents_demo.py`

Two agents edit one repository in parallel: one leases the API source, the other
the tests; the hub refuses the overlapping claim so they never touch the same
file, and the first messages the second directly when the API is ready. This is
the worked version of the [parallel coding agents recipe](../docs/recipes.md).

Each demo exposes a `run_demo(port, ...)` coroutine that the test-suite drives, so
the examples stay correct as the library evolves.
