# Python API reference

The main coordination API lives on the top-level `synapse_channel` package
surface. Host observation helpers use explicit module imports, documented
below. This page starts with the main entry points, then renders their source
docstrings. See [API and wire stability](api-stability.md) for the compatibility
policy.

## Retained-log observation readers

Local retained-log readers are documented in [record observations](compliance.md).
They use explicit imports and contact no hub:
`core.record_keeping.build_export`, `core.record_keeping_source.iter_record_source`,
`core.record_retention.assess_retention` and `core.compliance.build_report`.
Bundle and retention functions are also re-exported by `core.compliance`.
Existing keys, inclusive sequence ceilings and explicit complete-export budgets
apply to both the CLI and these Python APIs.

::: synapse_channel.core.record_keeping

::: synapse_channel.core.record_keeping_source

::: synapse_channel.core.record_retention

::: synapse_channel.core.compliance

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

This loop connects, waits for a matched claim grant, records a checkpoint,
updates the task and confirms release. Each operation has a bounded wait:

```python
"""Confirm a claim, checkpoint, status update and release on a local hub."""

import asyncio
from typing import Any
from uuid import uuid4

from synapse_channel import MessageType, SynapseAgent


async def main(*, uri: str = "ws://localhost:8876") -> None:
    """Run each task operation only after the preceding acknowledgement."""
    task_id = "refactor-parser-" + uuid4().hex
    replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    accepted = (
        MessageType.CLAIM_GRANTED,
        MessageType.CLAIM_DENIED,
        MessageType.CHECKPOINT_SAVED,
        MessageType.CHECKPOINT_DENIED,
        MessageType.TASK_UPDATED,
        MessageType.RELEASE_GRANTED,
        MessageType.RELEASE_DENIED,
        MessageType.ERROR,
    )

    async def receive(frame: dict[str, Any]) -> None:
        """Queue only operation replies for this fresh task."""
        if frame.get("task_id") == task_id and frame.get("type") in accepted:
            replies.put_nowait(frame)

    async def confirmed(expected: str) -> dict[str, Any]:
        """Require the next matched reply to acknowledge the requested verb."""
        frame = await asyncio.wait_for(replies.get(), 5.0)
        if frame.get("type") != expected:
            raise RuntimeError("task operation refused or unexpected reply")
        return frame

    agent = SynapseAgent("ALPHA", receive, uri=uri)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(5.0):
            raise TimeoutError("hub registration unconfirmed")
        await agent.claim(task_id, paths=["src/parser"], ttl_seconds=60.0)
        grant = await confirmed(MessageType.CLAIM_GRANTED)
        epoch = grant.get("epoch")
        if grant.get("owner") != agent.name or type(epoch) is not int or epoch <= 0:
            raise RuntimeError("invalid claim grant")
        await agent.save_checkpoint(task_id, "step=2", epoch=epoch)
        saved = await confirmed(MessageType.CHECKPOINT_SAVED)
        version = saved.get("version")
        if type(version) is not int or version <= 0:
            raise RuntimeError("invalid checkpoint acknowledgement")
        await agent.update_task(task_id, status="working", epoch=epoch, expected_version=version)
        await confirmed(MessageType.TASK_UPDATED)
        await agent.release(task_id, epoch=epoch)
        await confirmed(MessageType.RELEASE_GRANTED)
    finally:
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
```

The callback matches this fresh task's replies. A refusal, disconnect or timeout
aborts the sequence; reconcile uncertain outcomes before retrying. The example
uses an open loopback hub and literal scope comparison. Configure token/signing
for a protected hub and use Git-aware claims for canonical repository paths.
It edits no file and supplies no verification receipt. The
[quick start](quickstart.md#coordinate-from-code) runs the same confirmed loop.

Each `connect()` call is one connection attempt that owns its state. When the
attempt ends — the hub closed the socket, the network dropped, or the callback
cleared `running` — the agent is no longer ready (`wait_until_ready()` returns
`False`), its heartbeat task has been cancelled and awaited, and
`last_close_code`/`last_close_reason` describe that attempt. Calling `connect()`
again on the same agent starts a fresh attempt: readiness is cleared, `running`
is re-armed and the diagnostics are reset, while the mailbox cursor and any
owner lease carry over. A second `connect()` while an attempt is still active
raises `RuntimeError` instead of racing the live listener.

The MCP adapters own their retry lifetime separately through
`mcp.connection.maintain_connection`. It serially reuses the same agent after
normal transport loss, with bounded backoff and jitter, and propagates owner
cancellation. Application operations retain their existing confirmation and
retry contracts; the single-attempt `SynapseAgent.connect()` API is unchanged.

::: synapse_channel.mcp.connection

The Python client limits each decompressed inbound hub message to 8 MiB
(`MAX_HUB_MESSAGE_BYTES`). This also applies to CLI and dashboard clients and
allows accumulated board snapshots above 1 MiB. Responses exceeding the bound
close the connection with code `1009`. A locally initiated size refusal is
recorded with the fixed reason `received hub message exceeds the client size
limit` when no peer close frame arrived. A received peer close frame retains its
own diagnostic. Readiness is cleared when either connection attempt ends.

## The verbs you will use most

Grouped by what they coordinate (all are `async` methods on `SynapseAgent`):

- **Work claims** — `claim(task_id, paths=..., note=...)` and `release(task_id)`:
  requests for file-scope ownership and release. Await matched grants before
  relying on ownership; these send helpers do not gate external file edits.
- **Task lifecycle** — `update_task(task_id, status=...)` drives the typed task
  state on the shared blackboard.
- **Checkpoints** — `save_checkpoint(task_id, data)` records resumable progress
  that can be replayed after restart when retained in the configured durable
  journal. The send helper alone does not confirm persistence.
- **Messaging** — send to everyone, a named group (`A,B`), or one agent; an idle
  receiver configured for mailbox recovery can request retained pending messages
  on reconnect. Ordinary `SynapseAgent` construction defaults to `mailbox=False`;
  retention and quotas still bound recovery.

For the exact signatures of every method, read the generated reference below.

`prepare_release(task_id, idem_key=..., ...)` is a synchronous preparation helper:
it returns the exact release envelope, including its remembered fencing epoch,
without sending. Retain a unique key and the canonical semantic SHA-256 digest
before sending. `ReleaseIntent.from_request(request)` from
`synapse_channel.core.release_confirmation` retains the owner, task, key and
digest. Send with the prepared epoch and original receipt fields; `release(...)`
remains an asynchronous
send helper; returning from it does not prove the hub committed the mutation.

`request_release_confirmation(task_id, operation_id, request_digest, request_id)`
sends a read-only query. Match the private `state_snapshot` to the hub identity,
your own target and exact request id, then accept only a confirmed projection
with the original task, owner, key, digest and receipt. Missing records, invalid
proofs and ordinary legacy snapshots stay unknown. This never replays a release
or proves a newer lease is absent. The [wire contract](protocol.md#release-receipts)
and [manual CLI recovery](cli.md) define the bounded operator workflow.

## Embedding a hub

To run the hub in-process (tests, a bundled tool), use the composition factory
or pass a `HubConfig` directly to `SynapseHub`. Family records own defaults;
`hub.configuration` contains the
normalized settings. Existing writable names such as `hub.max_clients` update
that hub's record without changing a record shared with another hub.

```python
from synapse_channel import HubConfig
from synapse_channel.core.hub_factory import build_hub

hub = build_hub(HubConfig())
```

Existing keyword calls such as `SynapseHub(max_clients=32)` remain supported.
Supply a record or keyword options; combining them raises `TypeError`.
`HubConfig.to_kwargs()` remains available for legacy embedding subclasses.
`SynapseHub.from_config(config)` preserves their keyword-only constructors.
Record construction and `from_config` stamp `config_epoch`; bare and legacy
keyword construction retain an empty epoch.

`build_hub(config)` constructs the complete collaborator graph outside the
hub, then installs it once. The base `SynapseHub.from_config` and `synapse hub`
use this factory. Direct record and legacy keyword construction delegate to
the same composition function. No protocol vocabulary or CLI flags change.

Embedders can pass a typed `component_factory=` to `build_hub`. Its inputs are
the requested `HubConfig` and a `HubComponentCallbacks` record bound to the
allocated target; its result is a complete `HubComponents` graph. Build with
`build_components(config, callbacks)` and replace a family or service as
needed. Keep shared registries consistent and defer every target callback until
installation completes. The receiving constructor accepts that graph through
its existing first argument, rejects another target's graph and refuses a
second installation. The factory also refuses a graph built for another
configuration record. Supplied component factories own their acquisitions
until they return; the factory releases the returned target's checkpoint if
installation fails, and leaves a foreign target's checkpoint untouched.

The hub owns its checkpoint connection. After stopping and awaiting serving,
call `hub.close()` to anchor and release it, including when a constructed hub
was never served. A failed constructor releases its checkpoint automatically.
Supplied journal, attachment and replay stores remain caller-owned.

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
        hub.close()
```

`bound_address` is `None` before the bind and after shutdown. A failed bind
unblocks the readiness waiter without publishing a false address.

## Supporting surfaces

The remaining exports fall into a few families you reach for as needed:

- **Model workers** — `SynapseLLMWorker`, `OpenAIChatClient` and `TieredChatClient`
  let configured workers reply on-channel through a compatible endpoint.
  `RuleBasedClient` is a separately selected deterministic offline backend,
  not an automatic fallback for every provider failure.
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

## Durable storage method details

Construct `EventStore` and call its public methods. Their complete parameter,
return and transaction documentation is maintained with the owning table
implementations below; the facade retains the same signatures and runtime
docstrings. These classes organise the reference and share an existing owner
connection rather than opening independent stores.

`EventStore.event_metrics(window_seconds)` aggregates retained counts and time
bounds through that store's selected plaintext or SQLCipher connection without
decoding event bodies. Named windows are measured from the log's final timestamp.
The result describes stored metadata; it does not authenticate payloads or prove
that a vendor processed them.

::: synapse_channel.core.persistence_events.EventWriter
    options:
      members: [append_batch, pending_aef_events, mark_aef_delivered, aef_delivery]
      show_source: false

::: synapse_channel.core.persistence_event_reads.EventReader
    options:
      members: [iter_events, read_since, read_window, corrupt_rows, count, event_metrics, max_seq, latest_at_or_before]
      show_source: false

::: synapse_channel.core.persistence_event_authentication.EventAuthentication
    options:
      members: [has_row_macs, enable_row_mac, row_quarantine]
      show_source: false

::: synapse_channel.core.persistence_event_maintenance.EventMaintenance
    options:
      members: [delete, vacuum]
      show_source: false

::: synapse_channel.core.persistence_operations.OperationTable
    options:
      members: [get_operation, read_operations, pending_operation_outbox_count, pending_operation_intents, mark_operation_intent_delivered, commit_operation]
      show_source: false

::: synapse_channel.core.persistence_receipts.ReceiptTable
    options:
      members: [commit_delivery_receipt_request, commit_delivery_receipt_transition, delivery_receipt_aggregate, pending_delivery_notifications, mark_delivery_notification_attempt]
      show_source: false

## Read-only delivery receipts

Use the explicit storage module for one directed chat's durable receipt:

```python
from synapse_channel.core.persistence_receipt_view import read_delivery_receipt

view = read_delivery_receipt("hub.db", message_seq=42)
if view is not None:
    print(view.state, view.receipt)
```

The sequence is the durable chat cursor, rather than the hub's in-memory
message id. The reader joins the aggregate to exactly its referenced event in
one consistent read transaction and verifies their message and state binding.
Each call sees live WAL commits. It opens with `mode=ro`, never instantiates
`EventStore`, and performs no bootstrap, migration, backfill or chmod. A missing
database and its parent remain absent.
For an existing WAL database in a writable directory, SQLite may create or
reuse its WAL/SHM coordination sidecars during a read. The database's contents
and file mode remain unchanged.

`None` means unavailable: absent, busy, unknown schema, damaged or unverified.
Consumers must treat it as uncertain, never accepted or refused. MAC-column
stores need the additive `event_row_mac_idx` installed by the owning writer;
the reader does not install it or scan an old journal to guess enrollment.
For an authenticated source, supply its trusted `row_mac=RowMacKey(...)`;
the exact event must authenticate, including events predating enrollment.
The reader does not create or automatically load row keys.

For SQLCipher, supply `key_file=` or raw `key=`; raw key takes precedence.
`ReceiptProjectionError` reports unavailable optional encryption or rejected
key setup. Its stable code is `receipt_projection`. Catch that named error
when rendering an uncertain outcome. The reader authenticates the selected
event, rather than auditing all history. Transport delivery never establishes
that a model read or acted on the message.

::: synapse_channel.core.persistence_receipt_view
    options:
      show_root_heading: true
      show_source: false

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

## Pinned pi participant

`PiParticipant` implements the typed Participant Fabric interface over a
subprocess RPC session. Its default is model-only; an explicit `PiClaimBinding`
loads the separately packaged extension and binds file tool calls to a live
claim. See the [operator guide](pi.md) for installation and the cooperative
hook boundary.

::: synapse_channel.participants.headless_pi
    options:
      members: [PiParticipant, PiClaimBinding]
      show_root_heading: true
      show_source: false

## Local account and quota evidence

These APIs operate on an owner-local ledger, separate from hub events. See the
[operator guide](entitlements.md) for event fields and privacy boundaries.

::: synapse_channel.core.entitlements
    options:
      members: [EntitlementError, parse_time, parse_quantity, event_target, validate_event, active_events]
      show_root_heading: true
      show_source: false

::: synapse_channel.core.entitlement_store
    options:
      members: [EntitlementStoreError, default_entitlement_store, append_event, read_events]
      show_root_heading: true
      show_source: false

::: synapse_channel.core.entitlement_forecast
    options:
      members: [WindowForecast, forecast_window]
      show_root_heading: true
      show_source: false

::: synapse_channel.core.entitlement_view
    options:
      members: [entitlement_view]
      show_root_heading: true
      show_source: false

Git claim outcome handling uses `reply_timeout` and `confirm_only` on the native
runner and MCP bridge/tool. Unknown CLI outcomes exit `3`; confirmation persists
only the fence of an exact live lease and never replays the mutation.
See [claim outcomes and recovery](git-claims.md#claim-outcomes-and-recovery).


## Coordination handler stages

`core.handlers.messaging.route_chat` retains its existing signature and result
record. Semantic-response validation precedes hub stamping; retry suppression
precedes durable ingress charging. Channel and peer forwarding keep their own
branches. `core.handlers.chat_delivery` owns local recipient resolution, history,
durable receipt requests, fanout and final feedback. The early negative receipt
for stale recipients remains before fanout; only completed recipient writes
permit retry suppression. Observer or sender echoes do not prove delivery.

Lease entry points remain in `core.handlers.leasing`.
`core.handlers.leasing_mutations` applies the already parsed owner fields to the
candidate state; `core.handlers.leasing_drafts` prepares exact response/event
bundles without publishing them. The facade retains late-bound journal recorder
seams, epoch guards, wait publication and transport ordering. Handoff progress
precedes its grant; release grants precede assessment progress; checkpoints
reply privately. Replay/conflict responses return without another mutation,
and an inserted operation settles its outbox after the original notifications.

These are internal implementation stages; public handler signatures, wire
vocabulary, CLI flags and package exports remain unchanged.

::: synapse_channel.core.handlers.chat_delivery

::: synapse_channel.core.handlers.leasing_mutations

::: synapse_channel.core.handlers.leasing_drafts

## HTTP and pane wake ownership

`a2a_http.build_a2a_handler`, `dashboard.start_dashboard_server` and
`agent_tmux.inject_wake` remain the entry points for callers. Each server binds
its own bridge or dashboard readers; request-time snapshot collection retains
the facade's current async collector. HTTP capacity admission precedes request
work. Protected dashboard pages and feeds check the configured principal grants;
the built React cockpit's static bootstrap shell and assets have their separate
public route. Loading that shell grants no access to protected live data.

The A2A handler owns the existing body-size cap, read deadline, JSON decoding
and authored error responses. Its route owner retains version negotiation and
the discovery, task, message and push-configuration lifecycle. The dashboard
owners prepare shells/snapshots, emit bounded NDJSON frames and bind bounded
host coordination. Optional unavailable feeds remain explicitly absent while
snapshot delivery continues. A live-stream client that leaves ends its stream
quietly on every platform; Windows reports that departure as an aborted
connection, which is now treated like a reset one.

Pane wake preparation binds the exact configuration's existing probes and
registry callbacks. A real paste failure records a pending, unstaged wake;
successful pasting is recorded before the second safety check. An uncertain
staged prompt is retried through submission without another paste. These
implementation stages retain the existing response fields and CLI vocabulary.

Provider marker classification belongs to `agent_pane_state`: it retains the
existing update/busy/idle precedence, complete staged-hint checks and terminal
wrapping rules. `agent_tmux` continues to bind the current capture, provider and
registry through its existing API. These terminal observations do not prove
that a model received or completed work.

`AgentTmuxConfig.opencode_editor_readback` opts new pinned POSIX OpenCode mini
clients into native input verification. The startup coordinator qualifies an
unpublished empty client and binds its process before making the requested
session available. `opencode_composer_probe` compares content-free native
snapshots before a wake is pasted or submitted; `opencode_composer_files` bounds
and protects its local metadata. The callback in `opencode_composer` rejects
the editor transaction even after a successful observation, preserving the
native draft. Read-only status never invokes that callback. See the
[operator contract](opencode.md#wake-a-managed-mini-terminal) for the deliberate
external-editor limitation and the distinction between input consumption and
model completion.

`opencode_composer.process_stamp` normalises its process start timestamp to UTC
and English month names, so callback and controller locale settings cannot
invalidate the same live process receipt.

Both HTTP servers end a connection in order after its answer. A connection
whose request was not read to its end, for example one refused before its
body, is not closed at once: the answer's end is sent, the remaining request
bytes are discarded, and the socket is closed when the client closes. One
thread per server does this for all connections, so no request thread and no
admission slot is held meanwhile. It is bounded to two seconds and two
mebibytes per connection and to 64 connections at once; a connection over a
bound is closed at once.

::: synapse_channel.a2a_http_handler

::: synapse_channel.a2a_http_routes

::: synapse_channel.dashboard_handler_binding

::: synapse_channel.dashboard_http_pages

::: synapse_channel.dashboard_http_live

::: synapse_channel.pane_wake

::: synapse_channel.agent_pane_state

::: synapse_channel.opencode_composer

::: synapse_channel.opencode_composer_files

::: synapse_channel.opencode_composer_probe

::: synapse_channel.opencode_composer_startup

::: synapse_channel.opencode_composer_state

::: synapse_channel.lingering_close

::: synapse_channel.prompt_bind_http

## Operation catalogue admission

`admission_refusal` applies the same declared-row checks used by
`Catalogue.admit` and the operation inventory display. Passing these checks
grants no caller permission or consent. An executor must supply the current
executable fingerprint; inventory display uses the declared fingerprint.

::: synapse_channel.operations.catalogue
    options:
      members: [Catalogue, LegacyInventory, read_catalogue, admission_refusal]
      show_source: false

## Launch records

`synapse worker-session` keeps a record for each provider command it starts:
the first executable candidate found by `shutil.which` and the digest of its
bytes, the created process with its start identity, and its observed image.
The `executable` field is a lookup candidate, not proof of the file accepted
by the system. A direct POSIX launch can reject an executable-bit file with
an invalid format and start a later PATH candidate. Lookup and hashing happen
after launch and do not prevent replacement. For a script, the process image
may identify its interpreter without identifying the script that it read.
On POSIX, a launch
environment without `PATH` resolves the command using the default executable
search path used for a direct process start; it does
not inherit the launcher's `PATH` for the record. `run_worker_session(cwd=...)`
sets the provider working directory in both direct and tmux modes. The new
optional keyword-only `cwd` on `executable_identity` and `make_record` resolves
POSIX relative command paths and search-path entries against that directory;
an empty `PATH` entry also searches there. Existing calls default to the
launcher's directory. On Windows, the child directory changes while executable
lookup continues to use the launcher's directory, following
[`Popen` semantics](https://docs.python.org/3.12/library/subprocess.html#subprocess.Popen).
In a tmux-backed session the process recorded is the pane's. Of the command
only its name, the number of its words and a digest of all of them are kept,
since further words
can hold secrets.

File hashing limits reads to the allowance plus one byte for detecting
overflow. Content that exceeds the bound has no digest; its size is -1 when
the total cannot be established from that bounded read.

A record is a new owner-only file under `synapse/launch-records` in the state
directory the launch environment names, or under its home directory; an
environment that names neither keeps no record. The directory is made, or
proven, accessible to its owner alone before a record is put in it. The file
is owner-only from its creation: by permission bits on POSIX, and on Windows
by the access list it inherits from that directory, which names the one
account. A directory that is a link, or that belongs to another account, is
refused. A record that cannot be made
or written is reported on standard error and the session starts all the same.
Records are never replaced and nothing prunes them.

A record is the seat's own declaration, written under the same account as the
session, and its `evidence_class` says so. It is not an attestation: whoever
controls the account can write the same file, and nothing in it shows which
provider the process connected to.

::: synapse_channel.launch_record

## Advisory agent ranking

The explicit local projection retains source event sequences and grants no
execution authority. See [its journal and filtering contract](reputation.md).

::: synapse_channel.core.reputation
