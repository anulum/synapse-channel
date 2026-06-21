<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Recipe: parallel coding agents on one repository

The case the bus is built for: several agents (or developers) editing the same
repository at once without stepping on each other, coordinating directly instead
of through a human relay.

## The shape

Run one hub for the repo (see the [deployment guide](deployment.md)). Each worker
is a process that holds a connection while it works — that is what lets it hold a
file-scope lease. A turn-based assistant that cannot hold a socket between turns
uses the persistent blackboard for advisory intent instead (declare what you are
about to touch, check the board before you start), and a small connector process
can hold a strict lease on its behalf.

## The loop each agent runs

1. **Catch up.** Read the messages addressed to you and the shared plan:

    ```bash
    synapse relay ./feed.ndjson --for api-dev --cursor ./api-dev.cursor
    synapse board
    ```

2. **Claim your scope before you edit.** A claim leases a unit of work with a
   file scope; the hub refuses any claim whose paths overlap a live one, so two
   agents never edit the same files (`examples/coding_agents_demo.py` shows this):

    ```python
    import asyncio
    from synapse_channel import SynapseAgent

    async def work() -> None:
        agent = SynapseAgent("api-dev", uri="ws://localhost:8876")
        conn = asyncio.create_task(agent.connect())
        await agent.wait_until_ready()
        await agent.claim("edit-api", paths=["src/app/api.py"])  # refused if overlapping
        # ... edit the files you claimed ...
        await agent.release("edit-api")
    ```

3. **Tell the others what changed.** Address one teammate, several, or everyone:

    ```bash
    synapse send --name api-dev --target test-dev "API ready on src/app/api.py — update the tests"
    synapse send --name api-dev --target test-dev,docs-dev "interface changed"
    synapse send --name api-dev --target all "release branch is frozen"
    ```

4. **Keep the plan current.** Declare work with dependencies so a finished task
   unblocks the next; a stall supervisor re-offers anything that goes quiet:

    ```bash
    synapse task declare ship-api --title "Implement + test the API" --depends-on edit-api
    synapse task update edit-api --status done
    synapse supervisor --idle-seconds 300
    ```

## Why it holds

- **No collisions:** overlapping file scopes are rejected at claim time, in one
  place, before any edit happens.
- **No lost work:** with `--db` the hub replays its event log on restart, so a
  crash does not drop live leases; a lapsed lease hands its checkpoint to whoever
  claims the task next.
- **No human relay:** messages, the plan, and claims live in the hub, so agents
  address each other directly and an idle agent catches up from the feed.

Run the worked example end-to-end:

```bash
python examples/coding_agents_demo.py
```
