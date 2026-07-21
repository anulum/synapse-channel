# Tutorial: watch two agents coordinate

The fastest way to understand SYNAPSE CHANNEL is to watch it stop a collision and
hand work over cleanly — then read what each step taught you. This tutorial uses
the shipped self-contained demo (no repository, no configuration) so you can see
the whole coordination model in about a minute, then points you at doing it for
real.

## Step 1 — run the demo

```bash
python -m pip install synapse-channel
synapse demo
```

`synapse demo` starts its own local hub, seats two agents ("Claude" and "Codex"),
and drives a short coordination flow. It succeeds when it prints:

```text
success: coordination demo completed
```

Along the way you will see lines like these (abridged):

```text
• MUTATION DENIED: Synapse claim ownership is missing or ambiguous for: 'src/shared.py'.
• HANDOFF: Claude atomically transferred src/shared.py authority to Codex
• VERIFIED RECEIPT: real unittest and git diff checks passed; both changed files were SHA-256 recorded
```

## Step 2 — read what happened

Those three lines are the whole product in miniature:

1. **A claim gates the mutation.** When an agent tried to change `src/shared.py`
   without holding a claim on it, the hub refused the mutation — *before* the
   edit landed. This is the one guarantee everything else builds on: a
   file-scope claim is the thing that authorises a change, so two agents can
   never edit the same file at once.
2. **Handoff moves authority atomically.** Instead of "release the file, hope
   nobody grabs it, reclaim it," Claude handed the held scope, status, and
   checkpoint to Codex in one step — no window where the file is unowned and up
   for grabs.
3. **The result is evidence, not a promise.** The receipt records that real
   checks ran and hashes the changed files, so the coordination is auditable
   after the fact rather than taken on trust.

No database, no cloud, no consensus protocol was involved — one local hub owned
the state and both agents connected to it.

## Step 3 — do it for real

The demo ran a scripted flow. To coordinate your own agents, follow the
**[multi-seat golden path](quickstart.md#multi-seat-golden-path-5-minutes)** —
the canonical "zero to two coordinated agents" walkthrough. In short:

```bash
synapse doctor                       # check local setup
synapse hub --db ~/synapse/hub.db    # a durable hub (one owns the state)
synapse git-init --name my-repo      # claim-aware git hooks in your checkout
synapse dashboard                    # watch claims, tasks, and risk in one view
```

Then seat your existing agents: any MCP host (Claude Code, Codex, Cursor,
Claude Desktop) connects through `synapse mcp` with no new code — see the
[MCP guide](mcp.md).

## Step 4 — drive it from Python

To coordinate from your own code, the client is a few `async` calls. The one
guarantee from Step 2 — a claim refuses an overlap before two agents edit the
same file — is one method:

```python
import asyncio

from synapse_channel import SynapseAgent


async def main() -> None:
    agent = SynapseAgent("ALPHA", uri="ws://localhost:8876")
    session = asyncio.create_task(agent.connect())
    if not await agent.wait_until_ready():
        raise RuntimeError("could not reach the hub — is `synapse hub` running?")

    await agent.claim("refactor-parser", note="splitting the tokenizer", paths=["src/parser"])
    await agent.update_task("refactor-parser", status="working")
    await agent.release("refactor-parser")

    agent.running = False
    session.cancel()


asyncio.run(main())
```

See the [Python API reference](api.md) for the full client surface, and the
[getting-started notebook](https://github.com/anulum/synapse-channel/blob/main/notebooks/getting-started.ipynb)
for a runnable version of this flow.

## Where to go next

- [Why SYNAPSE CHANNEL](why-synapse.md) — what it is and why it matters.
- [Quick start](quickstart.md) — the full golden path and coding-agent wiring.
- [Coordination model](coordination-model.md) — claims, tasks, handoff, and
  recovery in depth.
- [Use cases](use-cases.md) — when it fits, when it is overkill.
