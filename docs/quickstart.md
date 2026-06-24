# Quick start

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

## Coordinate from code

```python
import asyncio
from synapse_channel import SynapseHub, SynapseAgent


async def main() -> None:
    hub = SynapseHub()
    asyncio.create_task(hub.serve("localhost", 8876))

    agent = SynapseAgent("ALPHA", uri="ws://localhost:8876")
    task = asyncio.create_task(agent.connect())
    await agent.wait_until_ready()

    await agent.claim("refactor-parser", note="splitting the tokenizer", paths=["src/parser"])
    await agent.save_checkpoint("refactor-parser", "step=2")
    await agent.update_task("refactor-parser", status="working")
    await agent.release("refactor-parser")

    agent.running = False
    task.cancel()


asyncio.run(main())
```

See the [coordination model](coordination-model.md) for what each verb guarantees.
