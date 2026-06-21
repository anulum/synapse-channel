# CLI reference

The `synapse` command exposes nine subcommands.

| Command | What it does |
| --- | --- |
| `synapse hub` | Run the coordination hub. |
| `synapse worker` | Run a model worker that answers on the channel. |
| `synapse team` | Launch a hub plus one or two local workers in one shot. |
| `synapse send` | Connect, send one message, optionally await replies, and exit. |
| `synapse listen` | Connect and stream channel messages until interrupted. |
| `synapse relay` | Decode and print a lite relay log a hub mirrored to a file. |
| `synapse board` | Print the shared task/progress blackboard. |
| `synapse supervisor` | Run an LLM-free supervisor that re-offers stalled tasks. |
| `synapse manifest` | Print the capability manifest of advertised agents. |

## Hub options

```bash
synapse hub --port 8876
synapse hub --port 8876 --db ./synapse.db          # crash-safe persistence
synapse hub --port 8876 --rate 5 --burst 20        # per-agent rate limiting
synapse hub --port 8876 --relay-log ./feed.ndjson  # mirror the channel to a file
synapse hub --host 0.0.0.0 --token s3cret          # require a shared secret off-loopback
```

## Worker options

```bash
synapse worker --name FAST --provider ollama --model gemma3:4b
synapse worker --name OFFLINE --provider rule
synapse worker --name TIER --provider tiered --model small --heavy-model big
synapse worker --prefix remanentia/ --name FAST --provider rule
```

A `tiered` worker classifies each request and routes trivial requests to a cheap
rule path and hard requests to the heavy model.

`--prefix` is prepended to `--name` to form the identity the worker registers
under (here `remanentia/FAST`), so the same role can run under several projects on
one hub without a name clash. `synapse team --prefix remanentia/` namespaces a
whole team the same way; address a namespaced worker by its full identity, for
example `synapse send --target remanentia/FAST "status?"`.

## Observing

```bash
synapse listen --name USER
synapse board
synapse manifest
synapse relay ./feed.ndjson --cursor ./feed.cursor
```

## Managing the task plan

`synapse task` lets a human drive the shared blackboard from the command line —
the persistent plan, not the live leases (claiming/holding a lease belongs to a
running agent, since a lease is released when its holder disconnects):

```bash
synapse task declare BUILD --title "Compile the package"
synapse task declare TEST --title "Run the suite" --depends-on BUILD
synapse board                                  # BUILD ready, TEST blocked on it
synapse task update BUILD --status done        # TEST now unblocks
synapse task progress TEST "started" --kind note
```

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
`manifest`, and `task`.

Run any command with `--help` for its full set of options.
