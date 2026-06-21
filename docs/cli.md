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
```

A `tiered` worker classifies each request and routes trivial requests to a cheap
rule path and hard requests to the heavy model.

## Observing

```bash
synapse listen --name USER
synapse board
synapse manifest
synapse relay ./feed.ndjson --cursor ./feed.cursor
```

For a secured hub, pass `--token SECRET` to `worker`, `send`, `listen`, `board`,
and `manifest`.

Run any command with `--help` for its full set of options.
