# Python API reference

Everything public lives on the top-level `synapse_channel` package surface (70
exported names). This page starts with the handful you actually call, then the
full generated reference follows. For the compatibility promise on every symbol
here, see [API and wire stability](api-stability.md).

## Two entry points

Almost every integration uses one of two classes:

- **`SynapseAgent`** — the client. Connect an agent to a running hub, then issue
  coordination verbs (claim, release, task updates, messaging, checkpoints). This
  is what most callers reach for.
- **`SynapseHub`** — the hub itself. Run the authoritative coordination process,
  usually from the `synapse hub` CLI, but embeddable in-process for tests or
  bundled deployments. Configure it with **`HubConfig`**.

```python
from synapse_channel import SynapseAgent, SynapseHub, HubConfig
```

## The client in one flow

A minimal `SynapseAgent` session — connect, wait for the hub's welcome, claim a
file scope, update the task, and release — is the whole daily loop in code:

```python
import asyncio

from synapse_channel import SynapseAgent


async def main() -> None:
    agent = SynapseAgent("ALPHA", uri="ws://localhost:8876")
    session = asyncio.create_task(agent.connect())  # one long-lived session

    # Wait for the hub's welcome before issuing verbs; fail loudly if it is down.
    if not await agent.wait_until_ready():
        raise RuntimeError("could not reach the hub — is `synapse hub` running?")

    # A file-scope claim refuses an overlap before two agents edit the same file.
    await agent.claim("refactor-parser", note="splitting the tokenizer", paths=["src/parser"])
    await agent.save_checkpoint("refactor-parser", "step=2")
    await agent.update_task("refactor-parser", status="working")
    await agent.release("refactor-parser")

    agent.running = False
    session.cancel()


asyncio.run(main())
```

Pass `on_message_callback=` to `SynapseAgent(...)` to react to inbound frames
(chat, task events, release grants). The full worked example — with an event
callback that waits on checkpoint and release confirmations — is in the
[quick start](quickstart.md).

## The verbs you will use most

Grouped by what they coordinate (all are `async` methods on `SynapseAgent`):

- **Work claims** — `claim(task_id, paths=..., note=...)` and `release(task_id)`:
  file-scope mutual exclusion, the one thing that gates a mutation.
- **Task lifecycle** — `update_task(task_id, status=...)` drives the typed task
  state on the shared blackboard.
- **Checkpoints** — `save_checkpoint(task_id, data)` records resumable progress
  that survives a restart.
- **Messaging** — send to everyone, a named group (`A,B`), or one agent; an idle
  agent catches up from its durable inbox on reconnect.

For the exact signatures of every method, read the generated reference below.

## Embedding a hub

To run the hub in-process (tests, a bundled tool), construct `SynapseHub` from a
`HubConfig`. `HubConfig().to_kwargs()` maps one-to-one onto the `SynapseHub`
constructor — a contract the test suite enforces — so config built one way is
always accepted by the hub.

```python
from synapse_channel import SynapseHub, HubConfig

hub = SynapseHub(**HubConfig().to_kwargs())
```

## Supporting surfaces

The remaining exports fall into a few families you reach for as needed:

- **Model workers** — `SynapseLLMWorker`, `OpenAIChatClient`, `TieredChatClient`,
  and the offline `RuleBasedClient` let agents reply on-channel through any
  OpenAI-compatible endpoint with a deterministic fallback.
- **Team helpers** — `plan_team(...)` / `run_team(...)` script a small fleet.
- **Coordination primitives** — `Blackboard`, `EventStore`, `TaskClaim`,
  `TaskStatus`, `MessageType`, and the `*Config` types.
- **Pure predicates** — `paths_overlap`, `scopes_conflict`, `would_create_cycle`,
  `is_directed`, `is_recipient`, and friends: no I/O, safe to call anywhere.

Everything is re-exported from the package root, so `from synapse_channel import X`
works for any name below.

## Full generated reference

The reference below is generated from the source docstrings for every public
symbol.

::: synapse_channel
    options:
      show_root_heading: true
      show_source: false
      members_order: source
