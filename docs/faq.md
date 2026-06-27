# FAQ

## What is SYNAPSE CHANNEL, in one sentence?

A local-first coordination bus for a fleet of AI agents working in parallel: a single
WebSocket hub is the shared source of truth for presence, file-scoped work claims, chat,
task status, a shared plan, capabilities, and resource offers.

## What problem does it actually solve?

When more than one agent works at once — on one repository or across many — they collide:
two edit the same file, two pick up the same task, work is duplicated, and state is lost on
a crash. SYNAPSE gives them one place to claim a file scope before editing, share a plan,
hand off, and resume after a restart. It coordinates agents; it does not run them.

## How is it different from CrewAI, LangGraph, or AutoGen?

Those are **orchestration frameworks** — they define and drive an agent's control flow
inside one process. SYNAPSE is a **coordination substrate** *between* independently running
agents (and humans), across processes and repositories. It is complementary: an agent built
with any of them can claim work and share presence over SYNAPSE. See the
[comparison](comparison.md) for the detail.

## Does it need a cloud account or a hosted service?

No. It runs entirely on the local machine over one WebSocket hub, with a single runtime
dependency (`websockets`). There is nothing to sign up for and no telemetry;
`synapse --version` is network-silent by default. Set `SYNAPSE_UPDATE_CHECK=1`
only if you want an opt-in, once-a-day PyPI newer-release check.

## Can a human use it, or is it only for agents?

Both. The `syn` commands (`syn arm`, `syn say`, `syn inbox`, `syn board`) are a thin,
identity-correct front end for the short loop a person or an agent runs each session, and
`synapse send` / `synapse listen` script cleanly.

## Which models can the workers use?

Any OpenAI-compatible endpoint, a local Ollama server, or a deterministic rule-based
fallback for offline use — selected with `synapse worker --provider {openai,ollama,rule,tiered}`.
`tiered` routes trivial requests to the rule backend and hard ones to a heavier model.

## Is it production-ready? What are the limits?

It is dogfooded daily and crash-durable — with `--db` the hub persists to a SQLite WAL event
log and resumes live leases and history on restart. The deliberate limits: it is **one hub
on one machine** (no built-in failover or horizontal scale), connect authentication is a
**proportionate shared secret** (not a cryptographic identity system), and **agents are
trusted** (the bus coordinates them, it does not sandbox them). See
[Known limitations](https://github.com/anulum/synapse-channel/blob/main/README.md#known-limitations).

## How does it keep two agents off the same files?

An agent **claims** a unit of work with a file **scope** (a set of path globs) before
touching it. The hub refuses a claim whose scope overlaps a live claim, so the file scopes
of two active claims never intersect. Claims are leases — they expire, can be renewed, and
release on commit via the optional git hooks. See [Git-native claims](git-claims.md).

## What happens if the hub restarts or crashes?

With `--db`, nothing is lost: the hub replays the durable event log and resumes active
leases, the plan, and history. Connected clients' waiters exit with code `3` and re-arm; the
durable feed and presence holders keep messages recoverable. Without `--db` the hub is purely
in-memory and starts empty.

## Why did my waiter wake on a message that was not for me?

It should not, on 0.42.0 or later — earlier versions had two routing bugs (a priority/CEO
message directed at one agent woke everyone, and a bare-project message woke every seat of a
multi-seat project). Upgrade, and see the wake entries in [Troubleshooting](troubleshooting.md).

## Is there an MCP integration?

Yes. `synapse mcp` runs a Model Context Protocol server over stdio, bridged to the hub, so an
MCP client can read the board, state, and manifest as resources. See [MCP server](mcp.md).

## How do I observe what is happening?

`synapse board` (the shared plan), `synapse state` (live claims and checkpoints), `synapse who`
(the online roster), `synapse manifest` (advertised capabilities), and `synapse listen` (a live
stream). For a file-based observer, `synapse hub --relay-log <file>` mirrors the channel to a
compact log that `synapse relay` decodes. An opt-in Prometheus `/metrics` and JSON `/health`
endpoint is available with `synapse hub --metrics`.

## What does it cost, and what is the licence?

The full package is free under the **AGPL-3.0** for open-source, research, internal, and
personal use. A **commercial licence** lifts the AGPL's network-copyleft for closed-source or
SaaS use — there is no feature difference between the builds. See [Commercial use](commercial.md).

## How do I report a bug or ask for help?

Open an issue at <https://github.com/anulum/synapse-channel/issues>, or see
[`SUPPORT.md`](https://github.com/anulum/synapse-channel/blob/main/SUPPORT.md) for the security
and contact channels.
