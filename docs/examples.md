<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Examples

Three self-contained, runnable demos live in the `examples/` directory. Each one
starts its own in-process hub, so nothing needs to be running first:

```bash
pip install synapse-channel
python examples/coordination_demo.py
python examples/llm_team_demo.py
python examples/coding_agents_demo.py
```

## `coordination_demo.py`

A narrated walk through the whole coordination plane: a planner declares a plan on
the blackboard, a dependent task stays blocked, a worker claims a file scope, an
overlapping claim is refused, the plan task finishes so the dependent unblocks,
and the task is handed off.

## `llm_team_demo.py`

Ask an on-channel model worker a question and print its reply. It uses a local
Ollama model when one is reachable (a real answer) and the deterministic offline
backend otherwise, so it runs anywhere.

## `coding_agents_demo.py`

The [parallel coding agents recipe](recipes.md) in code: two agents lease disjoint
file scopes, the hub refuses the overlapping claim so they never touch the same
file, and the first messages the second directly when the API is ready.

Each demo exposes a `run_demo(port, ...)` coroutine that the test-suite drives, so
the examples stay correct as the library evolves.
