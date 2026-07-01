# Quick start

## First 60 seconds

Verify a clean install before connecting real agents:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
```

`synapse doctor` checks identity, hub exposure, local disk pressure,
reachability, and wake-listener setup. It may warn that no hub or waiter is
running on a fresh machine. It also warns when the checked filesystem is nearly
full; pass `--disk-path <path>` to inspect the mount that will hold your Synapse
state, caches, or build artefacts. The installed demo is self-contained: it
starts a temporary local hub, runs a planner/worker coordination flow, and
succeeds when it prints:

```text
success: coordination demo completed
```

`synapse quickstart-coding` creates a temporary workspace, runs the coding-agent
no-collision demo, removes that temporary workspace after success, and succeeds
when it prints:

```text
success: coding fleet demo completed
```

To inspect the same coding-agent workflow as files you can edit, scaffold a
persistent workspace:

```bash
synapse new coding-fleet ./demo-fleet
cd ./demo-fleet
python run_demo.py
```

That generated workspace succeeds when it prints:

```text
success: coding fleet demo completed
```

## Fastest safe trial path

Use this order when moving from the self-contained demos into a real checkout:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
synapse git-init --name trial-agent
synapse a2a-card --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
```

Run this in a disposable or already-versioned repository. `synapse doctor` checks
the local machine before you install hooks or start bridges. `synapse git-init
--name trial-agent` installs claim-aware git hooks and writes the `.synapse/`
conventions guide before any coding agent edits files. The A2A bridge step is
optional and local-only; it exposes an Agent Card and HTTP+JSON bridge for local
interop experiments, not an external conformance claim. Do not bind it
off-loopback without bearer auth.

A complete session — declare a plan with a dependency, complete a task, and watch the
dependent task unblock:

![An example synapse session](assets/demo.png)

## Launch a team

Bring up a hub plus one or two local model workers in one command:

```bash
synapse team
```

## Or run the pieces individually

```bash
synapse hub --port 8876                       # the coordination hub
synapse hub --port 8876 --db ./synapse.db     # crash-safe: resumes on restart
synapse worker --name FAST --provider ollama --model gemma3:4b
synapse worker --name OFFLINE --provider rule # no network, canned replies
```

## Talk to the channel

From another terminal:

```bash
synapse listen --name USER                    # stream messages
synapse send --name USER --target FAST "status of TASK-1?"
synapse board                                 # the shared task/progress plan
synapse manifest                              # advertised agent capabilities
```

## Point the CLI at another hub

Every command talks to `ws://localhost:8876` by default. To target a different
hub — a remote coordinator, or a second local hub on another port — set
`SYNAPSE_URI` once instead of repeating `--uri` on each command:

```bash
export SYNAPSE_URI=ws://coordinator.internal:8876
synapse who                                   # now queries the remote hub
synapse send --name USER --target FAST "ping" # so does every other command
```

An explicit `--uri` on a single command still overrides the environment for that
one call, and unsetting `SYNAPSE_URI` returns to the loopback default.

## Coordinate from code

Start a hub in another terminal first — `synapse hub --port 8876` — then connect
to it from your code. Connecting to an already-running hub keeps the example free
of the in-process startup race between binding the server and dialling it:

```python
import asyncio
import contextlib

from synapse_channel import SynapseAgent


async def main() -> None:
    agent = SynapseAgent("ALPHA", uri="ws://localhost:8876")
    agent_task = asyncio.create_task(agent.connect())
    # connect() is a single long-lived session; wait for the hub's welcome before
    # issuing verbs, and fail loudly if the hub is not up rather than acting on a
    # closed connection.
    if not await agent.wait_until_ready():
        raise RuntimeError("could not reach the hub — is `synapse hub` running?")

    await agent.claim("refactor-parser", note="splitting the tokenizer", paths=["src/parser"])
    await agent.save_checkpoint("refactor-parser", "step=2")
    await agent.update_task("refactor-parser", status="working")
    await agent.release("refactor-parser")

    agent.running = False
    agent_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await agent_task


asyncio.run(main())
```

See the [coordination model](coordination-model.md) for what each verb guarantees.
