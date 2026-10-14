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
can keep a connection for live messages. A lease is governed by its expiry,
offline policy, owner and hub persistence; it is not a physical file lock and
does not require a continuously connected model process. Turn-based assistants
can use the CLI or MCP claim flow with persistent mailbox/wake processes for
communication between turns. Advisory board intent alone is not a granted lease.

## The loop each agent runs

1. **Catch up.** Read the messages addressed to you and the shared plan:

    ```bash
    synapse relay ./feed.ndjson --for api-dev --cursor ./api-dev.cursor
    synapse board
    ```

2. **Claim your scope before you edit.** A claim leases a unit of work with a
   file scope; the hub refuses conflicts with live scopes held by another owner. This is
   grant-time coordination, not a filesystem sandbox
   (`examples/coding_agents_demo.py` shows the supported path):

    ```python
    """Example of confirmed lease admission and release through the Python client."""

    import asyncio
    from collections.abc import Awaitable, Callable
    from typing import Any
    from uuid import uuid4

    from synapse_channel import MessageType, SynapseAgent


    async def work(edit: Callable[[], Awaitable[None]], *, uri: str = "ws://localhost:8876") -> None:
        """Run an edit only after a matched grant; require a matched release."""
        messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        task_id = f"edit-api-{uuid4().hex}"

        async def receive(frame: dict[str, Any]) -> None:
            """Retain only replies for this fresh task."""
            if frame.get("task_id") == task_id and frame.get("type") in (
                MessageType.CLAIM_GRANTED,
                MessageType.CLAIM_DENIED,
                MessageType.RELEASE_GRANTED,
                MessageType.ERROR,
            ):
                messages.put_nowait(frame)

        agent = SynapseAgent("api-dev", receive, uri=uri, verbose=False)

        async def response(expected: str) -> dict[str, Any]:
            """Read a matched reply or reject a matched refusal."""
            while True:
                frame = await messages.get()
                if frame.get("type") == expected:
                    return frame
                if frame.get("type") in (MessageType.CLAIM_DENIED, MessageType.ERROR):
                    raise RuntimeError("task operation refused")

        conn = asyncio.create_task(agent.connect())
        editing: asyncio.Future[None] | None = None
        try:
            if not await agent.wait_until_ready(5.0):
                raise TimeoutError("hub registration unconfirmed")
            await agent.claim(task_id, paths=["src/app/api.py"], ttl_seconds=60.0)
            grant = await asyncio.wait_for(response(MessageType.CLAIM_GRANTED), 5.0)
            epoch = grant.get("epoch")
            if grant.get("owner") != agent.name or type(epoch) is not int or epoch <= 0:
                raise RuntimeError("invalid grant")
            editing = asyncio.ensure_future(edit())
            finished, _ = await asyncio.wait(
                (editing, conn), timeout=30.0, return_when=asyncio.FIRST_COMPLETED
            )
            if conn in finished:
                raise ConnectionError("hub connection ended during editing")
            if editing not in finished:
                raise TimeoutError("edit deadline expired")
            await editing
            await agent.release(task_id, epoch=epoch)
            await asyncio.wait_for(response(MessageType.RELEASE_GRANTED), 5.0)
        finally:
            if editing is not None and not editing.done():
                editing.cancel()
                await asyncio.gather(editing, return_exceptions=True)
            conn.cancel()
            await asyncio.gather(conn, return_exceptions=True)
    ```

    `claim()` and `release()` send requests; their return does not confirm a
    grant or release. A denial, timeout or disconnect must stop editing and be
    treated according to its observed outcome. If editing fails, the example
    leaves the lease for explicit recovery; it does not declare success or
    blindly release uncertain work. The edit callback must honour cancellation
    and finish within the lease; this is not an OS process/fencing boundary.
    This minimal SDK example uses legacy literal scopes; use the Git-aware claim
    flow for canonical repository/path identities and configure the required
    token/signing profile on a protected hub.

    Before you release or ask another agent to release, inspect the live lease
    table:

    ```bash
    syn locks
    syn locks --owner api-dev
    ```

    The output includes each lease's holder, scope, age, remaining TTL, checkpoint,
    git context, and exact `synapse release <task> --name <owner>` command.

    When you are done, **do not** drop the claim bare by default. Use the
    [evidence-gated release](#evidence-gated-release-default-closeout) path below
    so the hub records observed checks with the release.

3. **Tell the others what changed.** Address one teammate, several, or everyone:

    ```bash
    synapse send --name api-dev --target test-dev "API ready on src/app/api.py — update the tests"
    synapse send --name api-dev --target test-dev --require-recipient "ping before handoff"
    syn ask test-dev "status?"
    synapse send --name api-dev --target test-dev,docs-dev "interface changed"
    synapse send --name api-dev --target all "release branch is frozen"
    ```

    Use `--require-recipient` when a directed nudge must not disappear into the
    durable feed unnoticed. The command waits for the hub's receipt and exits
    `1` if no consume-live recipient matches, or `3` if confirmation is missing.
    An unknown result can follow successful delivery; inspect the printed
    message identity in the hub journal before retrying. The CLI sends once and
    never treats a receipt as proof that a model acted.

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

5. **Commit only what this task changed.** Hold the project git lease while
   staging and committing the intended paths:

    ```bash
    syn commit src/app/api.py tests/test_api.py -m "ship API change"
    ```

    The command stages only those paths and passes the same pathspecs to
    `git commit`, so unrelated staged or modified files remain outside the new
    commit.

## Evidence-gated release (default closeout)

The recommended multi-seat closeout is: **run checks → write a receipt → release
with that receipt**. Evidence is optional unless an explicit policy requires it;
this recommendation is not a universal admission gate.

```bash
# Observe the verification (real commands, recorded digests)
synapse verify-release edit-api --name api-dev \
  --run ".venv/bin/python -m pytest tests/test_api.py -q" \
  --artifact coverage.xml \
  --output /tmp/receipt-edit-api.json

# Optional advisory policy evaluation on the receipt
# synapse policy-check edit-api --policy ./policy.toml \
#   --receipt-json /tmp/receipt-edit-api.json

# Drop only the claim you own, attaching the observed receipt
synapse release edit-api --name api-dev \
  --receipt /tmp/receipt-edit-api.json --receipt-json
```

`verify-release` is offline relative to the hub until you attach the file with
`release --receipt`. The hub still enforces ownership: a non-owner cannot clear
someone else's claim. The resulting `supported` status is **advisory** evidence
quality, not independent proof that the suite was sufficient — reviewers still
decide.

Wire this into the multi-seat golden path in [quick start](quickstart.md): after
Studio shows a live claim, finish with verify → receipt → release.

## Why it holds

- **Declared scope coordination:** the hub rejects conflicting scopes held by
  another owner. Covered provider hooks and the staged Git gate enforce their
  documented mutation boundaries; claims do not isolate arbitrary host writes.
- **Configured replay:** a persistent hub replays admitted retained records.
  Lease expiry, offline cleanup, retention and quarantine still apply;
  checkpoints are declared state, not backups of external file edits.
- **Direct communication:** agents address each other through the hub and can
  read the retained feed. A delivery receipt does not prove model processing.

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

A turn-based agent can use `synapse wait` as a background task. When a qualifying
message arrives, `wait` exits; a separately configured harness must observe that
exit and invoke the provider. Waiting requires no model turn itself, but keeps
a process and network connection. Provider invocation and prompt delivery remain
the harness's responsibility.

```bash
# backgrounded; exits + wakes the agent on a message for api-dev
synapse wait --name api-dev-rx --for api-dev --directed-only
```

The discipline that makes it reliable:

- **Re-arm after every wake only in a harness.** On waking, a manual harness
  reads the message, acts, and re-launches `synapse wait`; a forgotten re-arm
  goes silent. For unattended prompt delivery use the supervised active waker
  below instead of relying on agent memory.
- **One waiter at a time.** Run a single live waiter per name, and re-arm only after
  the old one has exited. A 0.29.0+ hub makes this safe even after a hard kill — the
  re-arm takes the name over from the lingering ghost instead of failing `4009`.
- **Recover the reconnect gap with `--mailbox`.** A waiter is deaf between a dropped
  connection or a re-arm and its next connect; a directed message that lands in that
  gap waits unread until an unrelated wake drains it. `synapse arm --mailbox` asks the
  hub to replay the missed directed messages on reconnect and wakes on them, resuming
  from a per-identity cursor under `~/synapse/mailbox-cursor/` so a re-arm is not
  replayed the whole backlog again. Off by default; needs a wire version `2` hub.
- **Presence is not a wake.** A `synapse-presence@<project>` daemon keeps the agent
  reachable and the feed durable, but only an armed waiter delivers promptness. Keep
  both (see the [deployment guide](deployment.md)). Run `syn who --me` for the
  resolved identity, or `synapse who --name <identity> --me` explicitly, to see
  presence and `-rx` waiter status separately; presence is not a wake loop.
- **Prefer a self-healing waiter.** Re-arming by hand is fragile — a missed re-arm
  leaves the agent present but deaf. On Linux,
  `synapse arm install --identity <identity> --start` writes only the
  `synapse-arm@<identity>` systemd user unit. It enables mailbox replay and, with
  `Restart=always`, is brought back by systemd if it dies — so the passive
  receiver cannot silently lapse. A unit that keeps failing ends in the
  visible `failed` state after its
  [start budget](deployment.md#start-budgets-when-a-unit-stops-trying). Native Windows service installation is not
  claimed; use WSL with systemd. Reserve the manual `--max-wakes 1` re-arm loop for
  a harness that re-invokes on each wake. The hub warns by default when a directed
  message reaches a present-but-deaf recipient; `synapse who` also marks such agents
  `(deaf …)` and lists any present agent with no live waiter under
  `Unarmed (present, no live waiter)`. Use `--no-warn-stale-recipients` only for an
  explicit compatibility opt-out.
- **Supervise active delivery separately.** A permanent `arm` is intentionally
  passive: it records/replays traffic but cannot wake a provider turn. On Linux
  or WSL with systemd, install `synapse waker install --identity <identity>
  --session <session> --cwd "$PWD" --agent-command <provider> --start`. Its
  watchdog and `Restart=always` recreate the `agent-tmux` bridge after failure.
  `synapse waker stop --identity <identity> --reason <reason>` durably inhibits
  automatic restart without killing the tmux provider; only `waker resume`
  re-arms it. Keep the passive mailbox arm alongside it for gap recovery.
- **Clean up by identity and PID only.** Use `syn reap` to list the resolved
  identity's shell-hook waiter pidfile, then `syn reap --pid <pid>` when that
  exact PID needs cleanup. It removes dead pidfiles and signals only a verified
  `synapse arm --name <identity>-rx --for <project>` waiter; it never pattern-kills.

## Talking to the fleet without a stampede

A global priority or CEO broadcast can wake every general-purpose directed-only
waiter at once, so automation may invoke the model provider in a burst.
Interactive `agent-tmux` pane bridges ignore global broadcasts and require an
exact identity, role, or group target. Address one agent or project group for
interactive provider wakes; reserve `--priority` for generic waiters that truly
must wake together:

```bash
synapse send --target api-dev "rebased main, re-pull"     # one
synapse send --require-recipient --target api-dev "are you online?"  # receipt-gated
synapse send --target quantum/* "freeze, I am tagging"    # a project group
synapse send --target all --priority "prod is green"      # generic waiters only, sparingly
```

To roll an update across the whole fleet, send **directed and staggered** rather
than one `--target all`, so the wakes spread out instead of stampeding; the receiver
side is covered by `synapse wait --wake-jitter` (default 8s).

## Why it holds

- **Retained-message recovery:** configured durable feed/mailbox replay can
  recover admitted retained directed messages after reconnect. Retention,
  integrity validation, cursor/filter configuration and successful rearming
  remain conditions; a receiver ACK is not model processing.
- **Receiver supervision:** re-arm/takeover and an answering service manager can
  restore failed receivers. Start budgets, inhibition, configuration and host
  failures still require monitoring and explicit recovery.
- **Reduced bursts:** jitter and staggered sends spread wake attempts. They do
  not guarantee provider capacity or prevent every rate-limit refusal.
