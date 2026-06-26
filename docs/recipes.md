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

    Before you release or ask another agent to release, inspect the live lease
    table:

    ```bash
    syn locks
    syn locks --owner api-dev
    ```

    The output includes each lease's holder, scope, age, remaining TTL, checkpoint,
    git context, and exact `synapse release <task> --name <owner>` command.

3. **Tell the others what changed.** Address one teammate, several, or everyone:

    ```bash
    synapse send --name api-dev --target test-dev "API ready on src/app/api.py — update the tests"
    synapse send --name api-dev --target test-dev --require-recipient "ping before handoff"
    synapse send --name api-dev --target test-dev,docs-dev "interface changed"
    synapse send --name api-dev --target all "release branch is frozen"
    ```

    Use `--require-recipient` when a directed nudge must not disappear into the
    durable feed unnoticed. The command waits for the hub's receipt and exits
    non-zero if no online recipient matches the target.

4. **Keep the plan current.** Declare work with dependencies so a finished task
   unblocks the next; a stall supervisor re-offers anything that goes quiet:

    ```bash
    synapse task declare ship-api --title "Implement + test the API" --depends-on edit-api
    syn ack edit-api --evidence "pytest tests/test_api.py -q" --artifact coverage.xml
    synapse supervisor --idle-seconds 300
    ```

    `syn ack` keeps the closeout evidence next to the plan: it posts the evidence
    and artifacts as an `assessment` progress note from the resolved identity, then
    marks the task `done` after the hub confirms the note.

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

Or run the same installed workflow in a temporary workspace:

```bash
synapse quickstart-coding
```

To keep editable source and test files after the run, generate a workspace:

```bash
synapse new coding-fleet ./demo-fleet
cd ./demo-fleet
python run_demo.py
```

# Recipe: a fleet of turn-based agents

The other shape: not long-running worker processes but *turn-based assistants* — the
kind that run in a terminal and cannot hold a socket open between turns. Several of
them, across several projects, coordinating on one hub. (This is how SYNAPSE itself
is built.)

## The wake loop

A turn-based agent cannot block waiting for a message, so it turns waiting into a
*push*. It runs `synapse wait` as a background task; the moment a message addressed
to it lands, `wait` exits and the harness re-invokes the agent — no polling, no cost
while it waits.

```bash
# backgrounded; exits + wakes the agent on a message for api-dev
synapse wait --name api-dev-rx --for api-dev --directed-only
```

The discipline that makes it reliable:

- **Re-arm after every wake.** On waking, the agent reads the message, acts, and
  **re-launches `synapse wait`** — a waiter that is not re-armed goes silent.
- **One waiter at a time.** Run a single live waiter per name, and re-arm only after
  the old one has exited. A 0.29.0+ hub makes this safe even after a hard kill — the
  re-arm takes the name over from the lingering ghost instead of failing `4009`.
- **Presence is not a wake.** A `synapse-presence@<project>` daemon keeps the agent
  reachable and the feed durable, but only an armed waiter delivers promptness. Keep
  both (see the [deployment guide](deployment.md)). Run `syn who --me` for the
  resolved identity, or `synapse who --name <identity> --me` explicitly, to see
  presence and `-rx` waiter status separately; presence is not a wake loop.
- **Clean up by identity and PID only.** Use `syn reap` to list the resolved
  identity's shell-hook waiter pidfile, then `syn reap --pid <pid>` when that
  exact PID needs cleanup. It removes dead pidfiles and signals only a verified
  `synapse arm --name <identity>-rx --for <project>` waiter; it never pattern-kills.

## Talking to the fleet without a stampede

A broadcast wakes every waiter at once, so their agents all re-invoke together and
the model **provider** rate-limits the burst — Anthropic's API, for one, answers
*"Server is temporarily limiting requests"*. Address one agent, a project group, or
— when it truly must reach everyone — use `--priority`, which wakes even
`--directed-only` waiters:

```bash
synapse send --target api-dev "rebased main, re-pull"     # one
synapse send --require-recipient --target api-dev "are you online?"  # receipt-gated
synapse send --target quantum/* "freeze, I am tagging"    # a project group
synapse send --target all --priority "prod is green"      # everyone, sparingly
```

To roll an update across the whole fleet, send **directed and staggered** rather
than one `--target all`, so the wakes spread out instead of stampeding; the receiver
side is covered by `synapse wait --wake-jitter` (default 8s).

## Why it holds

- **No missed messages:** the durable feed means an agent that was mid-turn when a
  message arrived still catches it on its next read; the waiter only adds promptness.
- **No dark agents:** exit-on-drop + re-arm + takeover mean a crashed or restarted
  waiter comes back rather than silently lapsing.
- **No provider stampede:** jitter on the receiver and staggered directed sends on
  the sender keep a fleet-wide wake from tripping the provider's rate limiter.
