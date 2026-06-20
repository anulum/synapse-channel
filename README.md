<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# SYNAPSE CHANNEL

A local-first coordination bus for multiple agents working in parallel. A single
WebSocket hub is the source of truth for **presence**, **work claims**, **chat**,
**task status**, and **resource offers**, so concurrent workers do not step on
each other or duplicate effort.

The bus is transport-light (one dependency, `websockets`), hub-centric by design
(one place owns presence, leases, and history), and runs entirely on the local
machine. Model workers reply on-channel through any OpenAI-compatible endpoint,
including a local Ollama server, with a deterministic rule-based fallback for
offline use.

## Install

```bash
python -m pip install -e ".[dev]"   # editable install with the dev toolchain
```

This installs the `synapse` command.

## Quick start

Launch a hub plus one or two local model workers in one command:

```bash
synapse team
```

Then, from another terminal, watch the channel or send a message:

```bash
synapse listen --name USER
synapse send --name USER --target FAST "what is the status of TASK-1?"
```

### Running pieces individually

```bash
synapse hub --port 8876
synapse hub --port 8876 --db ./synapse.db            # crash-safe: resumes leases + history on restart
synapse worker --name FAST --provider ollama --model gemma3:4b
synapse worker --name OFFLINE --provider rule        # no network, canned replies
```

### Durability

Passing `--db` backs the hub with an append-only SQLite event log (standard
library, WAL mode). Every claim, release, task update, resource offer, and chat
message is recorded, and the hub rebuilds its state by replaying the log on
start-up. The guarantee is split honestly by workload: the lease/claim path
commits at `synchronous=FULL` (durable across an OS crash); the high-volume
chat/history path commits at `synchronous=NORMAL` (durable across an application
crash, may lose the last commit on power loss).

## Coordination model

1. Claim before you work: an agent leases a task by id; a live lease blocks other
   agents from claiming the same task.
2. Declare a file scope on the claim (a `worktree` and `paths`); the hub refuses a
   claim whose files overlap another agent's live claim — this is how two agents
   are kept off the same files. Agents in different worktrees never contend.
3. Leases auto-expire, so a crashed agent never holds a claim forever, and each
   lease carries an epoch so a superseded agent cannot act on a dead claim.
4. Release on completion; status and an optional artefact reference can be
   attached while the task is in progress.
5. Presence, `who`, full state snapshots, and chat history are queryable at any
   time. After a reconnect, an agent resumes by `idem_key` (retried claims are not
   applied twice) and a `resume` cursor (fetch exactly the messages it missed).

See [`TEAM_PROTOCOL.md`](TEAM_PROTOCOL.md) for the working agreement and message
reference.

## Library use

```python
import asyncio
from synapse_channel import SynapseHub, SynapseAgent

async def main() -> None:
    hub = SynapseHub()
    asyncio.create_task(hub.serve("localhost", 8876))
    agent = SynapseAgent("ALPHA", uri="ws://localhost:8876")
    # ... drive the agent: claim, chat, request state ...
```

## Architecture

| Module | Responsibility |
| --- | --- |
| `state` | Presence, scoped task-claim leases, epochs, and resource offers (transport-agnostic). |
| `scoping` | Worktree- and path-overlap detection that keeps two agents off the same files. |
| `protocol` | The on-wire message envelope and message-type constants. |
| `hub` | The routing core: connections, names, history, broadcast. |
| `client` | The reusable async agent connection and coordination helpers. |
| `persistence` | Append-only SQLite event store (WAL) giving the hub a crash-durable spine. |
| `journal` | Records mutations as events and replays them to rebuild state on restart. |
| `ratelimit` | Per-agent token-bucket limiter so one runaway agent cannot swamp the hub. |
| `chat_backends` | Pluggable reply backends (OpenAI-compatible HTTP, rule-based). |
| `llm_worker` | An on-channel agent that answers addressed messages via a backend. |
| `launcher` | One-command local hub + worker startup. |
| `cli` | The unified `synapse` command. |

## Licence

Dual-licensed: AGPL-3.0-or-later, with a commercial licence available. See the
SPDX headers in each source file and contact `protoscience@anulum.li`.
