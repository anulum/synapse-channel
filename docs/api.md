# Python API reference

The main coordination API lives on the top-level `synapse_channel` package
surface. Host observation helpers use explicit module imports, documented
below. This page starts with the main entry points, then renders their source
docstrings. See [API and wire stability](api-stability.md) for the compatibility
policy.

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

    # The hub refuses a request that overlaps another live file-scope claim.
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

Each `connect()` call is one connection attempt that owns its state. When the
attempt ends — the hub closed the socket, the network dropped, or the callback
cleared `running` — the agent is no longer ready (`wait_until_ready()` returns
`False`), its heartbeat task has been cancelled and awaited, and
`last_close_code`/`last_close_reason` describe that attempt. Calling `connect()`
again on the same agent starts a fresh attempt: readiness is cleared, `running`
is re-armed and the diagnostics are reset, while the mailbox cursor and any
owner lease carry over. A second `connect()` while an attempt is still active
raises `RuntimeError` instead of racing the live listener.

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

For an embedded disposable hub, pass port `0` and wait for the live address;
this keeps socket selection and binding atomic:

```python
import asyncio

from synapse_channel import SynapseHub


async def start_embedded_hub() -> None:
    hub = SynapseHub(hub_id="embedded")
    server = asyncio.create_task(hub.serve("127.0.0.1", 0))
    try:
        host, port = await hub.wait_until_serving()
        print(f"ws://{host}:{port}")
        # Connect embedded clients here.
    finally:
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)
```

`bound_address` is `None` before the bind and after shutdown. A failed bind
unblocks the readiness waiter without publishing a false address.

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

These coordination helpers are re-exported from the package root, so
`from synapse_channel import X` works for names in the package reference.

## Full generated reference

The package reference below is generated from the source docstrings.

::: synapse_channel
    options:
      show_root_heading: true
      show_source: false
      members_order: source

## Read-only host observations

Host monitoring uses explicit module imports; it does not add process-control
methods or top-level package exports:

```python
import os

from synapse_channel.host_sessions import HostSessionMonitor

monitor = HostSessionMonitor(pids=(os.getpid(),))
observation = monitor.snapshot()
print(observation.process_status)
print(observation.to_json().decode("utf-8"))
```

This example requests no directory or context disclosure. Process collection
requires Linux procfs; an unsupported platform reports `unavailable` rather
than a successful empty scan. tmux and coordination each report their own
availability. A standalone monitor has no coordination source unless the
caller supplies a bounded reader.

`snapshot(paths=True, context=True)` opts into pathname metadata for local
callers. It reads no transcript body. HTTP consumers must use the dashboard's
explicit principal grants; constructing a monitor does not authorise disclosure
to another viewer. Terminal and dashboard options are documented under
“Local host-session observation” in `clients/cockpit/README.md` in the source
checkout.

Reuse one monitor to share its one-second cache across callers. Observation
timestamps describe collection, not agent activity. Each row also carries
`started_at`, the kernel start time derived from the boot time in `/proc/stat`
and the process start ticks (about one-second resolution); `observed_at -
started_at` is the process runtime age, which is distinct from observation age
and proves neither activity nor responsiveness. When the boot reference is
unreadable, `started_at` is null and `started_at_status` is `unavailable`.
Check source and field statuses before interpreting missing values.

### Shared collector and wire records

::: synapse_channel.host_sessions
    options:
      members: [HostSession, HostObservation, HostSessionMonitor]
      show_root_heading: true
      show_source: false

### Kernel process metadata

::: synapse_channel.host_sessions_proc
    options:
      members: [ProcessIdentity, ProcessMetadata, KernelClock, observe_process, discover_processes, process_metadata, kernel_clock]
      show_root_heading: true
      show_source: false

### tmux pane metadata

::: synapse_channel.host_sessions_tmux
    options:
      members: [PaneMetadata, observe_tmux]
      show_root_heading: true
      show_source: false

### Dashboard disclosure and terminal rendering

::: synapse_channel.dashboard_host_sessions
    options:
      members: [load_host_grants, host_session_response]
      show_root_heading: true
      show_source: false

::: synapse_channel.cli_pid_monitor
    options:
      members: [render_host_observation, format_runtime]
      show_root_heading: true
      show_source: false
