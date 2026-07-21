<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — what it is, what you build with it, and why it matters
-->

# Why SYNAPSE CHANNEL

A five-minute read for anyone deciding whether this belongs in their stack:
what SYNAPSE CHANNEL is, the problem it removes, what you build on it, how it
differs from the tools next to it, and where it is going. Every claim below maps
to a shipped feature you can run — the [comparison](comparison.md) page ties the
differences to concrete commands.

## What it is

SYNAPSE CHANNEL is a **coordination substrate for independently running AI
agents and the humans working alongside them — across processes and
repositories**. A single WebSocket hub, running on your own machine, is the
authoritative source of truth for **presence**, **file-scoped work claims**, a
**shared plan**, **task status**, **chat**, **agent capabilities**, and
**resource offers**. Every agent connects to that one hub; the hub owns the
state; nobody needs a database, a consensus protocol, or a cloud service to
agree on who is doing what.

It is deliberately **local-first and transport-light**: the core install has a
single runtime dependency (`websockets`), everything else is the Python standard
library, and the hub runs entirely on one machine. The MCP and A2A adapters are
optional extras — your existing agents plug in without new code.

## The problem it removes

The moment you run more than one autonomous agent against shared work, the agents
need a shared, authoritative view of who is doing what. Without one, three
failure modes appear immediately:

- **Collisions** — two coding agents edit the same file and clobber each other's
  changes.
- **Duplicated effort** — two agents pick up the same task because neither can
  see the other took it.
- **Lost handoffs** — an agent finishes, but the next one never learns it is
  clear to start, or starts against stale state.

SYNAPSE CHANNEL removes these at the source. A **file-scope claim refuses an
overlap before two agents edit the same file** — mutual exclusion is enforced at
claim time, not patched up after a conflict. A **shared blackboard** of declared
tasks with dependencies means an agent can see what is taken and what its work
unblocks. **Atomic handoff** and a **durable per-agent inbox** mean a finished
unit of work reaches the next agent even if it was offline when the message was
sent.

## What you build on it

SYNAPSE CHANNEL is a substrate, so the applications are as broad as "more than
one agent that must not step on the others." In practice teams reach for it when:

- **A fleet of coding agents works one repository in parallel** — Claude Code,
  Codex, Cursor, Aider, and headless workers share one plan and stay off each
  other's files.
- **An agent ecosystem spans many repositories** — agents in separate projects
  address each other across process and repository boundaries and coordinate one
  plan, rather than each running blind.
- **Humans and agents share a board** — an operator declares tasks and
  dependencies, agents claim and complete them, and the dependent work unblocks
  automatically; the human stays in the loop through the same bus.
- **Long-running automation must survive restarts** — the append-only SQLite WAL
  event log replays on restart, so live leases and task state resume instead of
  being lost.
- **Work is routed across tiered model backends** — task-class routing sends the
  right task to the right backend, with a deterministic rule-based fallback for
  offline use.

See [use cases](use-cases.md) for the concrete "when it fits, when it is
overkill, and who reaches for it" breakdown.

## Why it matters now

The number of agents an individual or a team runs at once is climbing fast:
coding assistants, headless workers, MCP tools, and cross-repository automation
all run in parallel. As soon as that count passes one, **coordination — not raw
model capability — becomes the bottleneck**. Uncoordinated agents waste tokens
re-doing each other's work, corrupt shared state, and produce merge conflicts a
human then has to untangle by hand.

SYNAPSE CHANNEL treats coordination as the first-class problem it has become. It
is the layer that lets you scale *how many agents you run* without scaling *how
much you have to babysit them*.

## How it is different

There are two distinct layers in a multi-agent stack, and SYNAPSE CHANNEL is
honest about which one it occupies:

- **Agent frameworks** (CrewAI, LangGraph, AutoGen) *run and orchestrate* agents
  inside one process or graph.
- **Coding tools** (Claude Code, Codex, Cursor, Aider) *are* the agents.
- **SYNAPSE CHANNEL** is the **coordination substrate between** independently
  running agents and humans, across processes and repositories.

It **coordinates agents; it does not run, orchestrate, or sandbox them** — which
is exactly why it is complementary to both layers rather than competing with
them. You keep your framework and your coding tools; SYNAPSE CHANNEL is the
shared source of truth they connect to. The [comparison](comparison.md) page
lays this out with a claim-by-claim table you can verify against running
commands.

## What it deliberately is not

Confidence about what it is comes with honesty about what it is not:

- **One hub, one machine.** There is no built-in failover or high-availability
  cluster; the hub is a single authoritative process by design.
- **Connect authentication is a proportionate shared secret**, not a
  cryptographic per-agent identity — strict where the hub is exposed, unchanged
  on a loopback single-owner setup.
- **Agents are trusted, not sandboxed.** SYNAPSE CHANNEL coordinates cooperating
  agents; it is not a containment boundary for hostile code.
- **Governance surfaces are advisory by default.** Receipts, policy, the trust
  graph, and routing inform decisions; a **work claim is the only thing that
  gates a mutation** unless you opt into stricter enforcement.

These are engineering boundaries, documented so an evaluator can decide with eyes
open. See [architecture](../ARCHITECTURE.md) and [FAQ](faq.md) for the full list.

## Where it is going

`1.0.0` is planned as the first stable commercial release, with stable
operational contracts, support surfaces, and commercial licensing terms
documented for that line. Beyond the single hub, a separate multi-machine
**Fleet** layer coordinates hubs across machines, and a read-only operator
**[Studio](studio.md)** is growing into a control plane that answers, at a
glance, what is happening, what is at risk, and what is safe to do next.

SYNAPSE CHANNEL is dual-licensed **AGPL-3.0-or-later with a commercial licence
available** — the commercial licence changes the terms, not the code; there is
no feature difference between the free and commercial builds. The project is
seeking startup funding, strategic partners, and aligned ecosystem co-owners;
see [commercial licensing](commercial.md) for the evaluation and contact path.

## Next steps

- **See it move:** [installation](installation.md) → [quick start](quickstart.md)
  (`python -m pip install synapse-channel && synapse demo`).
- **Decide if it fits:** [use cases](use-cases.md) · [comparison](comparison.md) ·
  [FAQ](faq.md).
- **Build on it:** [coordination model](coordination-model.md) ·
  [MCP guide](mcp.md) · [API and wire stability](api-stability.md).
